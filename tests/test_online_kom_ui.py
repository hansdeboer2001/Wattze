"""Optional real-Edge UI tests. No trainer, persisted settings, or online API.

Run with: python -m unittest discover -s tests -p test_online_kom_ui.py -v
Requires optional playwright on PYTHONPATH and an existing Edge installation;
neither a production dependency nor a downloaded browser is required.
"""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from wattze import http_handler, pages


EDGE = Path(os.environ.get("WATTZE_TEST_EDGE", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"))
MALICIOUS = '<img src=x onerror="window.remoteNameExecuted=true"> & Rider'
TOKEN = "ui-test-only-secret-recovery-token"
ROUTE = {"id": "ui-official", "name": "UI Test Climb", "ele": [100, 110, 120, 130, 140],
         "step": 25, "segs": [{"at": 0, "end": 100}]}


class FixtureHandler(http_handler.Handler):
    """Only page rendering reaches the app; every other endpoint is inert."""

    def _online_kom(self, method):
        parsed = urlsplit(self.path)
        suffix = parsed.path.removeprefix("/online-kom/")
        body = None
        if method == "POST":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        fixture = self.server.fixture
        fixture["calls"].append((method, suffix, body, parse_qs(parsed.query)))
        if suffix == "register":
            fixture["profile"] = {"id": "self", "name": body["name"], "friend_code": "abcdef123456",
                                  "share_general": False, "share_friends": False, "consent_version": 0}
            fixture["recovery_pending"] = True
            return self._json({"ok": True, "profile": fixture["profile"], "token": TOKEN})
        if suffix == "restore":
            fixture["profile"] = {"id": "self", "name": "Restored", "friend_code": "abcdef123456",
                                  "share_general": False, "share_friends": False, "consent_version": 0}
        if suffix == "recovery":
            return self._json({"ok": True, "token": TOKEN})
        if suffix == "recovery/confirm":
            fixture["recovery_pending"] = False
        if suffix == "profile":
            fixture["profile"].update(body)
        if suffix == "status":
            return self._json({"ok": True, "configured": fixture["profile"] is not None,
                               "profile": fixture["profile"], "pending": fixture["pending"],
                               "recovery_pending": fixture.get("recovery_pending", False)})
        if suffix == "me":
            if fixture.get("me_error"):
                return self._json({"ok": False, "error": "Fixture offline"}, 503)
            return self._json({"ok": True, "profile": fixture["profile"],
                               **{k: fixture[k] for k in ("incoming", "friends", "outgoing")}})
        if suffix == "friends/request":
            fixture["outgoing"].append({"id": "requested", "name": "Requested rider"})
        if suffix == "friends/accept":
            person = next(p for p in fixture["incoming"] if p["id"] == body["id"])
            fixture["incoming"].remove(person)
            fixture["friends"].append(person)
        if suffix == "friends/remove":
            for key in ("incoming", "friends", "outgoing"):
                fixture[key][:] = [p for p in fixture[key] if p["id"] != body["id"]]
        if suffix == "leaderboard":
            if fixture.get("board_error"):
                return self._json({"ok": False, "error": "Fixture leaderboard offline"}, 503)
            return self._json({"ok": True, "entries": [{"id": "remote", "name": MALICIOUS,
                                                        "time": 65.5, "avgP": 234}]})
        if suffix == "retry":
            sent = fixture["pending"]
            fixture["pending"] = 0
            return self._json({"ok": True, "sent": sent, "pending": 0})
        return self._json({"ok": True, "profile": fixture["profile"]})

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/game", "/klassementen", "/klassementen/profiel", "/remote") or path.startswith("/online-kom/"):
            return super().do_GET()
        if path == "/events":
            return self._send(200, "text/event-stream", b'data: {"power":0,"cadence":0,"hr":0}\n\n')
        if path == "/api":
            return self._json({"power": 0, "cadence": 0, "hr": 0})
        return self._json({"ok": True})

    def do_POST(self):
        if urlsplit(self.path).path.startswith("/online-kom/"):
            return self._online_kom("POST")
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        return self._json({"ok": True})


@unittest.skipUnless(sync_playwright and EDGE.is_file(), "Optional UI tests require playwright and installed Edge")
class OnlineKOMBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        temp = cls.stack.enter_context(tempfile.TemporaryDirectory(prefix="wattze-ui-"))
        # Patch only in this test process; never read or write the user's settings.
        for name, value in (("load_routes", lambda: [ROUTE]), ("load_koms", lambda: {}),
                            ("lan_ip", lambda: "127.0.0.1"),
                            ("settings", {"username": "UI Rider", "weight": 85, "overlay_font": "middel"}),
                            ("multi", {"server": ""}), ("gear", {"v": 6, "max": 12})):
            cls.stack.enter_context(patch.object(http_handler, name, value))
        cls.stack.enter_context(patch.object(pages, "settings", {"overlay_font": "middel"}))
        # Fail closed if a new page-serving branch accidentally reaches the real API.
        cls.stack.enter_context(patch.object(http_handler.online_kom, "handle",
                                            side_effect=AssertionError("Real online API forbidden")))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.stop_server)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port
        cls.pw = cls.stack.enter_context(sync_playwright())
        cls.browser = cls.pw.chromium.launch_persistent_context(
            str(Path(temp) / "edge-profile"), executable_path=str(EDGE), headless=True,
            args=["--no-first-run", "--disable-background-networking", "--disable-component-update"],
            service_workers="block", viewport={"width": 1440, "height": 1000})
        cls.stack.callback(cls.browser.close)

    @classmethod
    def stop_server(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.server.fixture = {"profile": None, "pending": 2, "calls": [],
                               "incoming": [{"id": "accept", "name": MALICIOUS},
                                            {"id": "reject", "name": "Reject rider"}],
                               "friends": [{"id": "remove", "name": "Remove rider"}],
                               "outgoing": [{"id": "cancel", "name": "Cancel rider"}]}
        self.errors = []
        self.external = []
        self.page = self.browser.new_page()
        self.page.set_viewport_size({"width": 1440, "height": 1000})
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("console", self.console_message)
        self.page.on("dialog", lambda dialog: dialog.accept())
        self.page.route("**/*", self.route_request)
        self.addCleanup(self.page.close)
        self.page.add_init_script("localStorage.clear(); sessionStorage.clear();")

    def route_request(self, route):
        if route.request.url.startswith(self.base + "/"):
            route.continue_()
        else:
            self.external.append(route.request.url)
            route.abort()

    def console_message(self, message):
        if message.type != "error":
            return
        fixture = self.server.fixture
        if ((fixture.get("me_error") or fixture.get("board_error")) and
                message.text == "Failed to load resource: the server responded with a status of 503 (Service Unavailable)"):
            return
        self.errors.append(message.text)

    def tearDown(self):
        self.assertEqual(self.external, [], "Page attempted an external request")
        self.assertEqual(self.errors, [], "Browser JavaScript/console errors")

    def ready(self):
        self.page.wait_for_function("typeof busy !== 'undefined' && !busy")

    def submit(self, form):
        self.page.locator(form).evaluate("form => form.requestSubmit()")
        self.ready()

    def register(self, fail_refresh=False):
        self.server.fixture["me_error"] = fail_refresh
        self.page.goto(self.base + "/klassementen/profiel")
        self.ready()
        self.page.locator("#newName").fill("UI Rider")
        self.submit("#registerForm")

    def assert_safe_name(self, selector):
        self.assertIn(MALICIOUS, self.page.locator(selector).inner_text())
        self.assertEqual(self.page.locator(selector + " img").count(), 0)
        self.assertFalse(self.page.evaluate("Boolean(window.remoteNameExecuted)"))

    def test_registration_secret_survives_refresh_failure_and_dismisses(self):
        self.register(fail_refresh=True)
        self.assertTrue(self.page.locator("#recovery").is_visible())
        self.assertEqual(self.page.locator("#recoveryToken").inner_text(), TOKEN)
        self.assertIn("Fixture offline", self.page.locator("#message").inner_text())
        self.server.fixture["me_error"] = False
        self.page.locator("#refresh").click()
        self.ready()
        self.assertEqual(self.page.locator("#recoveryToken").inner_text(), TOKEN)
        self.assert_safe_name("#incoming")
        self.page.locator("#hideRecovery").click()
        self.ready()
        self.assertFalse(self.page.locator("#recovery").is_visible())
        self.assertNotIn(TOKEN, self.page.locator("body").inner_text())
        self.assertEqual(self.page.locator("#recoveryToken").text_content(), "")
        self.page.reload()
        self.ready()
        self.assertFalse(self.page.locator("#recovery").is_visible())

    def test_restore_profile_preferences_friend_actions_and_retry(self):
        self.page.goto(self.base + "/klassementen/profiel")
        self.ready()
        self.page.locator("summary").click()
        self.page.locator("#restoreToken").fill("  " + TOKEN + "  ")
        self.submit("#restoreForm")
        self.assertEqual(self.page.locator("#restoreToken").input_value(), "")
        self.assertFalse(self.page.locator("#recovery").is_visible())
        self.assert_safe_name("#incoming")
        self.page.locator("#profileName").fill("Updated Rider")
        self.page.locator("#shareGeneral").check()
        self.page.locator("#shareFriends").check()
        self.submit("#profileForm")
        self.assertEqual(self.server.fixture["profile"]["name"], "Updated Rider")
        self.assertTrue(self.server.fixture["profile"]["share_friends"])
        self.page.locator("#friendInput").fill("123456abcdef")
        self.submit("#friendForm")
        self.assertEqual(self.page.locator("#friendInput").input_value(), "")
        for container, name, label in (("incoming", MALICIOUS, "Accepteren"),
                                       ("incoming", "Reject rider", "Weigeren"),
                                       ("friends", "Remove rider", "Verwijderen"),
                                       ("outgoing", "Cancel rider", "Intrekken")):
            row = self.page.locator("#" + container + " .person").filter(has_text=name)
            row.get_by_role("button", name=label, exact=True).click()
            self.ready()
        self.assertEqual([p["id"] for p in self.server.fixture["friends"]], ["accept"])
        self.page.locator("#retry").click()
        self.ready()
        self.assertIn("0 resterend", self.page.locator("#message").inner_text())
        calls = self.server.fixture["calls"]
        self.assertIn(("POST", "restore", {"token": TOKEN}, {}), calls)
        self.assertIn(("POST", "friends/request", {"code": "123456abcdef"}, {}), calls)
        for path, person in (("friends/accept", "accept"), ("friends/remove", "reject"),
                             ("friends/remove", "remove"), ("friends/remove", "cancel")):
            self.assertIn(("POST", path, {"id": person}, {}), calls)

    def test_klassementen_overviews_and_segment_detail(self):
        board = {"routeId": "route & hill", "revision": "a" * 24, "segment": 0,
                 "leader": MALICIOUS, "time": 65.5}
        self.page.route("**/online-kom/overview?*", lambda r: r.fulfill(json={"boards": [board]}))
        self.server.fixture["profile"] = {"id": "remote"}
        self.page.goto(self.base + "/klassementen")
        for scope, tab in (("general", "General"), ("friends", "Friends")):
            with self.page.expect_request(self.base + "/online-kom/overview?scope=" + scope):
                self.page.locator("#tab" + tab).click()
            panel = self.page.locator("#" + scope)
            button = panel.get_by_role("button", name="Segment 1", exact=False)
            button.wait_for()
            self.assertIn("1:05", button.inner_text())
            self.assert_safe_name("#" + scope)
            button.click()
            panel.get_by_text("(jij)", exact=False).wait_for()
            self.assertEqual(panel.locator("tbody td").all_inner_texts(),
                             ["1", MALICIOUS + " (jij)", "1:05", "234 W", "—"])
            self.assert_safe_name("#" + scope)
            query = {"scope": [scope], "route_id": [board["routeId"]],
                     "revision": [board["revision"]], "segment": ["0"]}
            self.assertIn(("GET", "leaderboard", None, query), self.server.fixture["calls"])
            panel.get_by_text("Terug naar het overzicht").click()
            button.wait_for()
        self.page.locator("#tabMine").click()
        self.assertEqual(self.page.locator("#general, #friends").all_text_contents(), ["", ""])

    def test_klassementen_late_private_response_empty_and_error(self):
        held = []
        self.page.route("**/online-kom/overview?scope=friends", lambda r: held.append(r))
        self.page.route("**/online-kom/overview?scope=general", lambda r: r.fulfill(json={"boards": []}))
        self.page.goto(self.base + "/klassementen?tab=vrienden", wait_until="domcontentloaded")
        self.page.locator("#friends").get_by_text("Klassement laden…").wait_for()
        self.page.locator("#tabGeneral").click()
        self.page.locator("#general").get_by_text("Nog geen gedeelde resultaten.").wait_for()
        held.pop().fulfill(json={"boards": [{"routeId": "private", "revision": "rev", "segment": 1,
                                             "leader": MALICIOUS, "time": 80}]})
        self.page.unroute("**/online-kom/overview?scope=friends")
        self.page.route("**/online-kom/overview?scope=friends", lambda r: r.fulfill(json={"ok": False, "error": "Offline"}))
        self.page.locator("#tabFriends").click()
        self.page.locator("#friends").get_by_text("Laden mislukt: Offline").wait_for()
        self.assertNotIn(MALICIOUS, self.page.locator("main").text_content())
        self.assertEqual(self.page.locator("#general").text_content(), "")

    def open_game(self):
        self.page.goto(self.base + "/game")
        self.page.wait_for_function("typeof selectRoute === 'function' && typeof openKom === 'function'")
        self.page.evaluate("selectRoute(ROUTES.find(r => r.id === 'ui-official')); openKom();")
        self.page.wait_for_timeout(250)

    def test_start_ride_enters_fullscreen(self):
        self.page.goto(self.base + "/game")
        started = self.page.evaluate("""() => {
            let called = false;
            ride.requestFullscreen = () => { called = true; return Promise.resolve(); };
            document.getElementById('btnStart').click();
            return called && running;
        }""")
        self.assertTrue(started)

    def test_game_local_general_friends_safe_names_and_offline_fallback(self):
        self.open_game()
        self.assertIn("Nog geen resultaten", self.page.locator("#komBody").inner_text())
        self.page.evaluate("KOMS[komKeyOf(0)] = [{user:'Local rider', time:70, avgP:200, date:'2026-01-01'}]; openKom();")
        self.assertIn("Local rider", self.page.locator("#komBody").inner_text())
        for selector, scope in (("#komGeneral", "general"), ("#komFriends", "friends")):
            self.page.locator(selector).click()
            self.page.wait_for_function("document.querySelector('#komBody table') !== null")
            self.assert_safe_name("#komBody")
            self.assertIn("234 W", self.page.locator("#komBody").inner_text())
            self.assertEqual(self.page.locator(selector).get_attribute("aria-pressed"), "true")
            self.assertFalse(self.page.locator("#btnKomReset").is_visible())
            self.assertEqual(self.server.fixture["calls"][-1][3]["scope"], [scope])
        self.server.fixture["board_error"] = True
        self.page.locator("#komGeneral").click()
        self.page.wait_for_function("document.querySelector('#komBody').textContent.includes('Fixture leaderboard offline')")
        self.page.locator("#komLocal").click()
        self.assertIn("Local rider", self.page.locator("#komBody").inner_text())
        self.assertTrue(self.page.locator("#btnKomReset").is_visible())

    def test_simulated_erg_unshared_and_custom_attempts_excluded(self):
        self.open_game()
        self.page.evaluate("onlineKomSharing = true")
        for flags in ("rideSimUsed=true", "simOn=true", "ergOn=true", "onlineKomSharing=false", "segOnlinePaused=true",
                      "route='eigen-custom'", "route='free'"):
            self.page.evaluate("rideSimUsed=false; simOn=false; ergOn=false; segOnlinePaused=false; onlineKomSharing=true; route='ui-official'; "
                               + flags + "; publishOnlineKom(segs[0], 60, 210)")
        self.page.wait_for_timeout(200)
        self.assertFalse(any(c[1] == "attempt" for c in self.server.fixture["calls"]))
        self.page.evaluate("route='ui-official'; onlineKomSharing=true; publishOnlineKom(segs[0], 60, 210)")
        self.page.wait_for_function("document.querySelector('#komOnlineStatus').textContent === 'Online KOM gedeeld.'")
        attempts = [c for c in self.server.fixture["calls"] if c[1] == "attempt"]
        self.assertEqual(len(attempts), 1)
        self.assertIs(attempts[0][2]["simulated"], False)

    def test_empty_queue_retry_stays_disabled(self):
        self.register()
        self.page.locator("#hideRecovery").click()
        self.ready()
        self.page.locator("#retry").click()
        self.ready()
        self.assertEqual(self.server.fixture["pending"], 0)
        self.assertTrue(self.page.locator("#retry").is_disabled(),
                         "Retry should stay disabled after localStatus reports an empty queue")

    def test_recovery_is_redisplayed_after_reload_until_confirmed(self):
        self.register()
        self.page.reload()
        self.ready()
        self.assertTrue(self.page.locator("#recovery").is_visible())
        self.assertEqual(self.page.locator("#recoveryToken").text_content(), TOKEN)
        self.page.locator("#hideRecovery").click()
        self.ready()
        self.assertFalse(self.server.fixture["recovery_pending"])
        self.page.reload()
        self.ready()
        self.assertFalse(self.page.locator("#recovery").is_visible())

    def test_pause_and_sim_during_pause_mark_attempt_ineligible(self):
        self.open_game()
        self.page.evaluate("""() => {
            const original = finishRide;
            finishRide = () => { running = false; };
            curSeg = 0; running = true; segOnlinePaused = false;
            pauseRide();
            finishRide = original;
        }""")
        self.assertTrue(self.page.evaluate("segOnlinePaused"))
        self.page.evaluate("simOn=false; rideSimUsed=false; document.getElementById('simToggle').click()")
        self.assertTrue(self.page.evaluate("rideSimUsed"))

    def pause_test_ride(self):
        self.page.goto(self.base + "/game")
        self.page.evaluate("""() => {
            selectRoute(ROUTES.find(r => r.id === 'ui-official'));
            startRide();
            window.testWall = Date.now();
            Date.now = () => window.testWall;
            t0 = Date.now() - 60000; simT = 60; dist = 40; spd = 0;
            pauseRide();
        }""")
        self.assertFalse(self.page.locator("#finish").is_visible())
        self.assertEqual(self.page.evaluate("[running, ridePaused]"), [False, True])
        self.page.locator("#btnMenu").click()
        self.assertEqual(self.page.locator("#btnMenuPause").text_content(), "Hervatten")

    def test_end_paused_ride_clears_resume_message_and_excludes_pause_time(self):
        stops = []

        def stop_request(route):
            stops.append(route.request.method)
            route.fulfill(json={"ok": True, "file": "fixture.tcx", "duration": 60, "distance": 40})

        self.page.route("**/ritten/stop", stop_request)
        self.pause_test_ride()
        self.page.evaluate("window.testWall += 600000")
        self.page.locator("#btnMenuEnd").click()
        self.page.wait_for_function("pendingRide !== null")
        self.assertEqual(self.page.evaluate("[running, ridePaused]"), [False, False])
        self.assertEqual(self.page.locator("#finishTitle").text_content(), "Rit gestopt")
        self.assertEqual(self.page.locator("#fRecord").text_content(), "Handmatig gestopt na 1:00")
        self.assertFalse(self.page.locator("#finishGoRow").is_visible())
        self.assertEqual(self.page.evaluate("Math.floor((Date.now()-t0)/1000)"), 60)
        self.assertIn("1:00", self.page.locator("#fStats").text_content())
        self.page.evaluate("document.getElementById('btnMenuEnd').click(); document.getElementById('btnMenuPause').click()")
        self.assertEqual(self.page.evaluate("[running, ridePaused]"), [False, False])
        self.assertEqual(stops, ["POST"])
        self.page.locator("#btnFinishSave").click()
        self.assertIn("Opgeslagen", self.page.locator("#finishSaveInfo").text_content())
        self.assertNotIn("hervatten", self.page.locator("#fRecord").text_content())

    def test_short_paused_ride_still_ends_without_recording_to_save(self):
        stops = []

        def stop_request(route):
            stops.append(route.request.method)
            route.fulfill(json={"ok": False, "error": "geen opname"})

        self.page.route("**/ritten/stop", stop_request)
        self.pause_test_ride()
        self.page.locator("#btnMenuEnd").click()
        self.page.wait_for_timeout(100)
        self.assertEqual(stops, ["POST"])
        self.assertEqual(self.page.locator("#finishTitle").text_content(), "Rit gestopt")
        self.assertFalse(self.page.locator("#finishGoRow").is_visible())
        self.assertFalse(self.page.locator("#finishSave").is_visible())
        self.assertFalse(self.page.evaluate("ridePaused || running"))

    def test_paused_ride_remains_resumable_until_explicit_end(self):
        resumes = []

        def resume_request(route):
            resumes.append(route.request.method)
            route.fulfill(json={"ok": True})

        self.page.route("**/ritten/resume", resume_request)
        self.pause_test_ride()
        self.page.evaluate("window.testWall += 300000")
        self.page.locator("#btnMenuPause").click()
        self.page.wait_for_timeout(100)
        self.assertEqual(resumes, ["POST"])
        self.assertEqual(self.page.evaluate("[running, ridePaused]"), [True, False])
        self.assertEqual(self.page.evaluate("Math.floor((Date.now()-t0)/1000)"), 60)
        self.assertFalse(self.page.locator("#finish").is_visible())

    def test_completed_ride_uses_terminal_summary_without_resume_controls(self):
        self.page.goto(self.base + "/game")
        self.page.evaluate("startRide(); finishT=60; finishRide()")
        self.assertIn("Gefeliciteerd!", self.page.locator("#finishTitle").text_content())
        self.assertFalse(self.page.locator("#finishGoRow").is_visible())
        self.assertNotIn("hervatten", self.page.locator("#fRecord").text_content())
        self.assertFalse(self.page.evaluate("ridePaused || running"))
        self.assertFalse(self.page.locator("#btnMenu").is_visible())

    def test_ride_screenshot_contains_power_summary_and_graph_at_mobile_and_desktop_sizes(self):
        self.page.goto(self.base + "/game")
        for width in (1440, 390):
            self.page.set_viewport_size({"width": width, "height": 900})
            self.page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            for powers in ([100, 200, 300], [250], []):
                with self.subTest(width=width, powers=powers):
                    result = self.page.evaluate("""async powers => {
                        running=false;
                        course.name='Screenshot test route';
                        finishT=60; dist=course.total+500;
                        sumP=powers.reduce((a,b)=>a+b,0);cntP=powers.length;
                        maxP=powers.length?Math.max(...powers):0;
                        ridePwr=powers.map((p,t)=>({p,t}));
                        const labels=[],graphs=[];
                        const fill=CanvasRenderingContext2D.prototype.fillText;
                        const paint=paintRidePwr;
                        CanvasRenderingContext2D.prototype.fillText=function(text,...args){
                            labels.push(String(text));return fill.call(this,text,...args);
                        };
                        paintRidePwr=(canvas,samples,size)=>{
                            graphs.push(samples.map(s=>s.p));return paint(canvas,samples,size);
                        };
                        const sourceWidth=cv.width,sourceHeight=cv.height;
                        let png;
                        try{png=captureShot();}finally{
                            CanvasRenderingContext2D.prototype.fillText=fill;paintRidePwr=paint;
                        }
                        const image=new Image();image.src=png;await image.decode();
                        const check=document.createElement('canvas');
                        check.width=image.width;check.height=image.height;
                        const ctx=check.getContext('2d');ctx.drawImage(image,0,0);
                        return {labels,graphs,width:image.width,height:image.height,
                            sourceWidth,sourceHeight,
                            footer:Array.from(ctx.getImageData(0,image.height-1,1,1).data),
                            expectedDistance:course.km.toFixed(1)+' km',
                            png:png.startsWith('data:image/png;base64,')};
                    }""", powers)
                    self.assertTrue(result["png"])
                    self.assertEqual(result["width"], result["sourceWidth"])
                    self.assertGreater(result["height"], result["sourceHeight"])
                    self.assertEqual(result["footer"], [11, 14, 20, 255])
                    self.assertEqual(result["graphs"], [powers])
                    self.assertIn("Gemiddeld vermogen", result["labels"])
                    self.assertIn("Maximaal vermogen", result["labels"])
                    self.assertIn(f"{round(sum(powers) / len(powers)) if powers else 0} W", result["labels"])
                    self.assertIn(f"{max(powers) if powers else 0} W", result["labels"])
                    self.assertTrue(any(result["expectedDistance"] + " · 1:00" in text
                                        for text in result["labels"]))
                    if not powers:
                        self.assertIn("Geen vermogensdata", result["labels"])
        self.assertEqual(self.errors, [])

    def test_local_npcs_start_ahead_and_behind_without_popping_into_view(self):
        self.page.goto(self.base + "/game")
        traffic = self.page.evaluate("""() => {
            startRide();
            const ahead = riders.filter(r => !r.world && !r.fromBehind);
            const behind = riders.filter(r => !r.world && r.fromBehind);
            const chaser = behind[0];
            chaser.wx = dist + NPC_EDGE + 20;
            recycleLocalRider(chaser);
            const dropped = ahead[0];
            dropped.wx = dist - NPC_EDGE - 20;
            recycleLocalRider(dropped);
            const droppedAhead = dropped.wx >= dist + NPC_EDGE && !dropped.fromBehind;
            dropped.world = true;
            dropped.wx = dist + 900;
            const protectedWx = dropped.wx;
            recycleLocalRider(dropped);
            const oncomer = oncomers[0];
            oncomer.wx = dist - NPC_EDGE - 10;
            recycleOncomer(oncomer);
            return {
                ahead: ahead.length, behind: behind.length, oncoming: oncomers.length,
                aheadNear: ahead.filter(r => r !== dropped).every(r => r.wx > dist && r.wx < dist + 700),
                behindFar: behind.filter(r => r !== chaser).every(r => r.wx <= dist - NPC_EDGE),
                oncomingNear: oncomers.every(r => r.wx > dist && r.wx < dist + 1400),
                chaserBehind: chaser.wx <= dist - NPC_EDGE && chaser.fromBehind,
                droppedAhead: droppedAhead && protectedWx === dropped.wx,
                oncomerAhead: oncomer.wx >= dist + NPC_EDGE
            };
        }""")
        self.assertEqual(traffic["ahead"], 6)
        self.assertEqual(traffic["behind"], 3)
        self.assertEqual(traffic["oncoming"], 8)
        self.assertTrue(traffic["aheadNear"])
        self.assertTrue(traffic["behindFar"])
        self.assertTrue(traffic["oncomingNear"])
        self.assertTrue(traffic["chaserBehind"])
        self.assertTrue(traffic["droppedAhead"])
        self.assertTrue(traffic["oncomerAhead"])

    def test_foreground_buildings_are_larger_without_scaling_the_background(self):
        self.page.goto(self.base + "/game")
        scenery = self.page.evaluate("""() => {
            startRide();
            Wref = 800;
            const houses = [], mills = [], silos = [], lamps = [];
            const origHouse = scHouse, origMill = scMill, origSilo = scSilo, origLamp = scLamp;
            scHouse = (x, y, w, h, wall, roof, o) => houses.push({x, s: o && o.s, w});
            scMill = (x, y, s) => mills.push(s);
            scSilo = (x, y, s) => silos.push(s);
            scLamp = (x, y, s) => lamps.push(s);
            const near = (value, expected) => Math.abs(value - expected) < 0.001;
            const fs = FRONT_HOUSE;
            for (const kind of [0, 1, 2, 3, 5, 4]) drawMark(kind, dist);
            const alps = isAlps;
            isAlps = () => true;
            const beforeAlps = houses.length;
            drawMark(1, dist);
            drawMark(3, dist);
            isAlps = alps;
            const riderX = Wref * 0.37;
            const at = (screenX) => dist + (screenX - riderX) / XS;
            const beforeEdge = houses.length;
            drawMark(4, at(-400));
            const inside = houses.length > beforeEdge;
            drawMark(4, at(-500));
            const outside = houses.length === beforeEdge + 1;
            scHouse = origHouse; scMill = origMill; scSilo = origSilo; scLamp = origLamp;
            const village = houses.slice(0, 5);
            const gaps = village.slice(1).map((house, i) => house.x - village[i].x - (house.w * house.s + village[i].w * village[i].s) / 2);
            return {
                factor: fs, edge: FRONT_EDGE,
                village: village.every(house => near(house.s, 2.15 * fs)),
                gaps: gaps.every(gap => near(gap, 3)),
                farm: near(houses[5].s, 2.35 * fs) && near(houses[6].s, 2.2 * fs) && near(silos[0], 1.7 * fs),
                barn: near(houses[7].s, 2.45 * fs),
                mill: near(mills[0], 2.15 * fs),
                row: houses.slice(8, 12).every(house => near(house.s, 2.05 * fs)),
                single: near(houses[12].s, 2.5 * fs),
                alpsFarm: near(houses[beforeAlps].s, 2.35 * fs) && near(houses[beforeAlps + 1].s, 2.2 * fs),
                alpsChalet: near(houses[beforeAlps + 2].s, 2.4 * fs),
                lamps: lamps.every(s => s === 1.15 || s === 1.1),
                back: drawBackLife.toString().includes("s:0.62") && !drawBackLife.toString().includes("FRONT_HOUSE"),
                chapel: drawFrontScenery.toString().includes("1.35*FRONT_HOUSE"),
                inside, outside
            };
        }""")
        self.assertEqual(scenery["factor"], 1.45)
        self.assertEqual(scenery["edge"], 420)
        for key in ("village", "gaps", "farm", "barn", "mill", "row", "single", "alpsFarm", "alpsChalet", "lamps", "back", "chapel", "inside", "outside"):
            self.assertTrue(scenery[key], key)

    def test_visual_speed_details_scroll_with_distance_without_changing_ride_state(self):
        self.page.goto(self.base + "/game")
        result = self.page.evaluate("""() => {
            running=false;dist=1000;spd=30/3.6;
            ctx=cv.getContext('2d');
            const before=[dist,spd,power,cadence,simT];
            const rock=paintRock,moves=ctx.moveTo,records=[];
            paintRock=(x,y,s,k)=>records.push({x,y,s,k});
            const details=()=>{
                records.length=0;drawRoadsideDetails(1600);
                return records.map(r=>({...r}));
            };
            let first,still,advanced;
            try{
                first=details();nowT+=10;still=details();
                dist+=1;advanced=details();dist-=1;
            }finally{paintRock=rock;}
            const paint=paintPastureAnimal,animals=[],shifts=[];
            paintPastureAnimal=(kind,x,y,size,seed)=>animals.push({kind,x,size,seed});
            try{
                for(const width of [390,1600])for(let d=0;d<=2200;d+=100){
                    dist=d;animals.length=0;drawForegroundAnimals(width);
                    const initial=animals.slice();
                    dist=d+1;animals.length=0;drawForegroundAnimals(width);
                    for(const a of initial){
                        const b=animals.find(b=>b.kind===a.kind&&b.seed===a.seed);
                        if(b)shifts.push({kind:a.kind,width,dx:a.x-b.x,size:a.size,nextSize:b.size});
                    }
                }
            }finally{paintPastureAnimal=paint;dist=before[0];}
            const lines=()=>{
                const points=[];
                ctx.moveTo=(x,y)=>points.push([x,y]);
                try{drawSpeedLines(800,500);}finally{ctx.moveTo=moves;}
                return points;
            };
            spd=25/3.6;const slow=lines();
            spd=30/3.6;const normal=lines();nowT+=10;const paused=lines();
            const after=[dist,spd,power,cadence,simT];
            return {xs:XS,first,still,advanced,shifts,slow,normal,paused,before,after};
        }""")
        self.assertAlmostEqual(result["xs"], 7 * 1.4)
        self.assertEqual(result["before"], result["after"])
        self.assertEqual(result["first"], result["still"])
        for scale, depth in ((0.4, 1), (0.7, 1.45)):
            matching = [(a, b) for a in result["first"] for b in result["advanced"]
                        if a["s"] == scale and a["k"] == b["k"] and a["s"] == b["s"]]
            self.assertTrue(matching, f"Missing scenery layer {depth}")
            for a, b in matching:
                self.assertAlmostEqual(a["x"] - b["x"], result["xs"] * depth)
        self.assertEqual(result["slow"], [])
        self.assertEqual(len(result["normal"]), 8)
        self.assertEqual(result["normal"], result["paused"])
        for width in (390, 1600):
            self.assertEqual({s["kind"] for s in result["shifts"] if s["width"] == width}, {0, 1, 2})
        for shift in result["shifts"]:
            self.assertAlmostEqual(shift["dx"], result["xs"] * 1.45,
                                   msg="Animals should pass at the same speed as the nearby grass")
            self.assertEqual(shift["size"], shift["nextSize"])
        self.assertEqual(self.errors, [])

    def test_foreground_animals_animate_beside_road_and_freeze_when_paused(self):
        self.pause_test_ride()
        result = self.page.evaluate("""() => {
            const originalCtx=ctx,paint=paintPastureAnimal,originalDist=dist;
            const canvas=document.createElement('canvas');canvas.width=220;canvas.height=170;
            ctx=canvas.getContext('2d');
            const images=[];
            try{
                for(let kind=0;kind<3;kind++){
                    const render=t=>{
                        ctx.clearRect(0,0,220,170);
                        paintPastureAnimal(kind,110,125,2,17,t);
                        return canvas.toDataURL();
                    };
                    images.push([render(0),render(2),render(2)]);
                }
            }finally{ctx=originalCtx;}
            const animals=[];
            paintPastureAnimal=(kind,x,y,scale,seed,t)=>animals.push({kind,x,y,scale,seed,t,
                clearance:y-yAt0(dist+(x-1440*.37)/XS)});
            try{
                for(dist=0;dist<=2200;dist+=100)drawForegroundAnimals(1440);
            }finally{paintPastureAnimal=paint;dist=originalDist;}
            window.animalPauseTime=simT;
            return {images,animals,time:simT,state:[dist,spd,power,cadence]};
        }""")
        for kind, images in enumerate(result["images"]):
            self.assertNotEqual(images[0], images[1], f"Animal {kind} should animate")
            self.assertEqual(images[1], images[2], f"Animal {kind} should freeze at the same time")
        self.assertEqual({animal["kind"] for animal in result["animals"]}, {0, 1, 2})
        for animal in result["animals"]:
            self.assertGreaterEqual(animal["clearance"], 76)
            self.assertGreaterEqual(animal["x"], -110)
            self.assertLessEqual(animal["x"], 1550)
            self.assertEqual(animal["t"], result["time"])
            self.assertEqual(animal["scale"], 1.45 if animal["kind"] == 2 else 1.15)
        cow_pairs = [(a, b) for a, b in zip(result["animals"], result["animals"][1:])
                     if a["kind"] == b["kind"] == 1 and abs(b["seed"] - a["seed"] - 3.1) < .001]
        self.assertTrue(cow_pairs)
        for a, b in cow_pairs:
            self.assertGreater(b["x"] - a["x"], 110, "Neighboring cows should not overlap")
        self.page.wait_for_timeout(250)
        self.assertTrue(self.page.evaluate("ridePaused && simT === window.animalPauseTime"))
        self.assertEqual(self.page.evaluate("[dist,spd,power,cadence]"), result["state"])
        self.assertEqual(self.errors, [])

    def test_animal_sizes_are_proportional_to_bike_and_to_each_other(self):
        self.page.goto(self.base + "/game")
        sizes = self.page.evaluate("""() => {
            const saved=ctx,canvas=document.createElement('canvas');
            canvas.width=300;canvas.height=220;ctx=canvas.getContext('2d');
            const bounds=draw=>{
                ctx.clearRect(0,0,300,220);draw();
                const pixels=ctx.getImageData(0,0,300,220).data;
                let left=300,right=0,top=220,bottom=0;
                for(let y=0;y<220;y++)for(let x=0;x<300;x++){
                    if(pixels[(y*300+x)*4+3]<40)continue;
                    left=Math.min(left,x);right=Math.max(right,x);
                    top=Math.min(top,y);bottom=Math.max(bottom,y);
                }
                return {width:right-left+1,height:bottom-top+1};
            };
            try{
                return {rider:bounds(()=>drawBike(150,137,0,0,1,0,null,null,false,0)),
                    sheep:bounds(()=>paintPastureAnimal(0,150,150,1,0,0)),
                    cow:bounds(()=>paintPastureAnimal(1,150,150,1,0,0)),
                    rabbit:bounds(()=>paintPastureAnimal(2,150,150,1,0,0)),
                    nearCow:bounds(()=>paintPastureAnimal(1,150,150,1.15,0,0)),
                    farCow:bounds(()=>paintPastureAnimal(1,150,150,.85,0,0))};
            }finally{ctx=saved;}
        }""")
        self.assertGreater(sizes["cow"]["height"], sizes["sheep"]["height"] * 1.8)
        self.assertLess(sizes["cow"]["height"], sizes["sheep"]["height"] * 2.2)
        self.assertLess(sizes["rabbit"]["height"], sizes["sheep"]["height"] * .6)
        self.assertGreater(sizes["cow"]["width"], sizes["rider"]["width"] * 1.2)
        self.assertLess(sizes["cow"]["height"], sizes["rider"]["height"])
        self.assertGreater(sizes["cow"]["height"], sizes["rider"]["height"] * .7)
        self.assertLess(sizes["sheep"]["height"], sizes["rider"]["height"] * .5)
        self.assertLess(sizes["rabbit"]["height"], sizes["rider"]["height"] * .25)
        self.assertGreater(sizes["nearCow"]["height"], sizes["farCow"]["height"] * 1.25)
        self.assertEqual(self.errors, [])

    def test_fullscreen_right_overlays_grow_without_covering_each_other(self):
        self.page.goto(self.base + "/game")
        self.page.evaluate("document.getElementById('overlay').style.display='none'")
        layout = self.page.evaluate("""() => {
            startRide();
            drawClimb();
            const map = document.getElementById('map');
            const climb = document.getElementById('climb');
            const pwr = document.getElementById('pwr');
            const css = [...document.styleSheets].flatMap(sheet => {
                try { return [...sheet.cssRules].map(rule => rule.cssText); }
                catch (e) { return []; }
            }).join('\\n');
            const zoom = css.includes('zoom: 1.16') || css.includes('zoom:1.16');
            const windowed = {
                map: getComputedStyle(map).height,
                climb: getComputedStyle(climb).height,
                width: getComputedStyle(map).width
            };
            const orig = fsOn;
            const boxes = (short) => {
                fsOn = () => true;
                ride.style.height = short ? '640px' : '900px';
                document.getElementById('ride').classList.toggle('erg-fs', false);
                placeRightOverlays();
                const rr = ride.getBoundingClientRect();
                const rel = (el) => {
                    const r = el.getBoundingClientRect();
                    return {top: r.top - rr.top, bottom: r.bottom - rr.top, width: r.width, height: r.height};
                };
                const normal = {map: rel(map), climb: rel(climb), pwr: rel(pwr)};
                ride.classList.add('erg-fs');
                map.style.top = '142px';
                placeRightOverlays();
                const erg = {map: rel(map), climb: rel(climb), pwr: rel(pwr)};
                ride.classList.remove('erg-fs');
                map.style.top = '';
                fsOn = orig;
                placeRightOverlays();
                return {normal, erg, cleared: !map.style.height && !climb.style.top};
            };
            const tall = boxes(false);
            const low = boxes(true);
            ride.style.height = '';
            return {zoom, windowed, tall, low, rideWidth: ride.getBoundingClientRect().width};
        }""")
        self.assertTrue(layout["zoom"])
        self.assertEqual(layout["windowed"]["map"], "110px")
        self.assertEqual(layout["windowed"]["climb"], "110px")
        self.assertLess(float(layout["windowed"]["width"].rstrip("px")), layout["rideWidth"] * 0.28)

        def clear(name, mode):
            boxes = layout[name][mode]
            self.assertGreater(boxes["map"]["height"], 150 if name == "tall" else 100, name + " " + mode)
            self.assertGreaterEqual(boxes["climb"]["top"], boxes["map"]["bottom"] - 1, name + " " + mode)
            self.assertLessEqual(boxes["climb"]["bottom"], boxes["pwr"]["top"] + 1, name + " " + mode)
            self.assertGreater(boxes["map"]["width"], layout["rideWidth"] * 0.30, name + " " + mode)

        for name in ("tall", "low"):
            for mode in ("normal", "erg"):
                clear(name, mode)
            self.assertTrue(layout[name]["cleared"])

    def test_draft_stops_above_three_percent(self):
        self.page.goto(self.base + "/game")
        draft = self.page.evaluate("""() => {
            startRide();
            peloton = []; pelotonOn = []; oncomers = []; multiOthers = [];
            const rider = riders.find(r => !r.fromBehind) || riders[0];
            riders = [rider];
            spd = 5; power = 200;
            const orig = gradeAt;
            const sample = (grade) => {
                gradeAt = () => grade;
                rider.wx = dist + 2;
                rider.spd = 8;
                stepPhysics(0.16);
                return drafting;
            };
            const atThree = sample(3);
            const steep = sample(3.1);
            gradeAt = () => 1;
            let easy = 0;
            for (let i = 0; i < 12; i++) easy = sample(1);
            gradeAt = orig;
            return {atThree, steep, easy};
        }""")
        self.assertGreater(draft["atThree"], 0)
        self.assertEqual(draft["steep"], 0)
        self.assertGreater(draft["easy"], 0.15)

    def test_ride_menu_pauses_scene_and_saves_route_feel_without_ending(self):
        settings, pauses = [], []
        self.page.route("**/settings", lambda route: settings.append(route.request.post_data_json) or route.fulfill(json={"ok": True}))
        self.page.route("**/ritten/pause", lambda route: pauses.append(route.request.method) or route.fulfill(json={"ok": True}))
        self.page.goto(self.base + "/game")
        self.assertEqual(self.page.locator("#btnStop").count(), 0)
        self.page.evaluate("""() => {
            startRide();
            dist = 120; simT = 30; crank = 1.25; dayPhase = 0.2; spd = 4;
        }""")
        self.page.locator("#btnMenu").click()
        self.page.locator("#menuFeel").fill("35")
        self.page.locator("#btnMenuClose").click()
        self.page.wait_for_timeout(400)
        self.assertEqual(settings[-1]["route_feel"], 35)
        self.assertEqual(self.page.evaluate("[running, ridePaused, SETTINGS.route_feel]"), [True, False, 35])
        self.assertFalse(self.page.locator("#rideMenu").is_visible())
        self.page.locator("#btnMenu").click()
        self.page.locator("#btnMenuPause").click()
        self.page.wait_for_function("ridePaused")
        frozen = self.page.evaluate("[dist, simT, crank, dayPhase]")
        self.page.wait_for_timeout(350)
        self.assertEqual(pauses, ["POST"])
        self.assertEqual(self.page.evaluate("[dist, simT, crank, dayPhase]"), frozen)
        self.assertFalse(self.page.locator("#finish").is_visible())
        self.page.locator("#btnMenuClose").click()
        self.assertEqual(self.page.evaluate("[running, ridePaused]"), [False, True])
        self.assertFalse(self.page.locator("#rideMenu").is_visible())
        graph = self.page.evaluate("""() => {
            const ride = document.getElementById('ride').getBoundingClientRect();
            const pwr = document.getElementById('pwr').getBoundingClientRect();
            const menu = document.getElementById('btnMenu').getBoundingClientRect();
            return {left: (pwr.left - ride.left) / ride.width, width: pwr.width / ride.width,
                    menuLeft: menu.left - ride.left, menuRight: menu.right, pwrLeft: pwr.left,
                    menuBottom: ride.bottom - menu.bottom};
        }""")
        self.assertAlmostEqual(graph["left"], 0.20, delta=0.02)
        self.assertAlmostEqual(graph["width"], 0.60, delta=0.02)
        self.assertLess(graph["menuRight"], graph["pwrLeft"] + 1)
        self.assertGreaterEqual(self.page.locator("#btnMenu").evaluate("el => parseFloat(getComputedStyle(el).fontSize)"), 18)
        self.assertLess(graph["menuBottom"], 40)

    def test_sim_trainer_grade_diagnostics_without_enabling_simulation(self):
        data = {"connected": True, "ctrl": {"want_sim": True,
                "sim_command": {"grade": 16, "status": "confirmed", "result": 1}}}
        self.page.route("**/api", lambda route: route.fulfill(json=data))
        self.page.goto(self.base + "/game")
        self.assertFalse(self.page.locator("#btnSim").is_visible())
        self.page.goto(self.base + "/game?sim=1")
        self.page.evaluate("document.getElementById('overlay').style.display='none'")
        self.page.locator("#btnSim").click()
        self.page.wait_for_function("document.getElementById('simTrainerGrade').textContent.includes('+16,00% (bevestigd)')")
        self.assertFalse(self.page.evaluate("simOn"))
        for status, label in (("pending", "wacht op bevestiging"), ("timeout", "timeout, niet bevestigd"),
                              ("unconfirmed", "niet bevestigd"), ("rejected", "afgewezen, code 5"),
                              ("write_error", "schrijffout, verzending niet vastgesteld"),
                              ("writing", "wordt verstuurd")):
            with self.subTest(status=status):
                grade = None if status in ("writing", "write_error") else 0
                cmd = {"grade": grade, "status": status, "result": 5}
                payload = {"connected": True, "ctrl": {"want_sim": True, "sim_command": cmd}}
                self.page.evaluate("data => updateSimTrainerGrade(data)", payload)
                value = "-" if grade is None else "0,00%"
                self.assertEqual(self.page.locator("#simTrainerGrade").text_content(),
                                 "Trainer-SIM: %s (%s)" % (value, label))
        for payload, expected in ((None, "server niet bereikbaar"), ({}, "trainer niet verbonden"),
                                  ({"connected": True, "ctrl": {"want_erg": True}}, "ERG (geen SIM-helling)"),
                                  ({"connected": True, "ctrl": {}}, "SIM niet actief"),
                                  ({"connected": True, "ctrl": {"want_sim": True}}, "nog geen gegevens")):
            self.page.evaluate("data => updateSimTrainerGrade(data)", payload)
            self.assertIn(expected, self.page.locator("#simTrainerGrade").text_content())
        for width, height in ((1440, 1000), (390, 844)):
            self.page.set_viewport_size({"width": width, "height": height})
            bounds = self.page.locator("#simPanel").bounding_box()
            self.assertGreaterEqual(bounds["x"], 0)
            self.assertLessEqual(bounds["x"] + bounds["width"], width)
            self.assertLessEqual(self.page.locator("#simPanel").evaluate("el => el.scrollWidth"),
                                 bounds["width"])
        self.assertFalse(self.page.evaluate("simOn"))

    def test_gear_range_and_route_feel_on_load_and_live_gear_count_changes(self):
        self.page.goto(self.base + "/game")
        results = self.page.evaluate("""() => {
            const originalFetch = window.fetch, results = [];
            let posted;
            window.fetch = (url, options) => {
                if(url === '/grade') {
                    posted = JSON.parse(options.body);
                    return Promise.resolve(new Response('{}'));
                }
                return originalFetch(url, options);
            };
            try {
                for(const count of [VGEAR_MAX, 16, 24]) {
                    if(count !== VGEAR_MAX)
                        es.onmessage({data:JSON.stringify({power:0,cadence:0,gear:1,gear_max:count})});
                    for(const feel of [0, 100]) {
                        SETTINGS.route_feel = feel;
                        for(const terrain of [-5, 0, 5]) {
                            for(const selected of [1, count]) {
                                vgear = selected; lastPostT = -10; lastPostG = -999;
                                postGrade(terrain);
                                results.push({count, feel, terrain, selected, grade:posted.grade});
                            }
                        }
                    }
                }
            } finally { window.fetch = originalFetch; }
            return results;
        }""")
        self.assertEqual({row["count"] for row in results}, {12, 16, 24})
        for row in results:
            with self.subTest(**row):
                offset = -8 if row["selected"] == 1 else 8
                expected = max(-12, min(18, row["terrain"] * row["feel"] / 100 + offset))
                self.assertAlmostEqual(row["grade"], expected)

    def test_trainer_diagnostic_log_controls_and_browser_calculation(self):
        status = {"active": False, "error": "", "bytes": 0}
        commands, grades = [], []
        self.page.route("**/api", lambda route: route.fulfill(json={"trainer_diagnostics": status}))

        def diagnostic_request(route):
            cmd = route.request.post_data_json["cmd"]
            commands.append(cmd)
            status["active"] = cmd == "start"
            route.fulfill(json={"ok": True, **status})

        def grade_request(route):
            grades.append(route.request.post_data_json)
            route.fulfill(json={"ok": True})

        self.page.route("**/trainer-diagnostics", diagnostic_request)
        self.page.route("**/grade", grade_request)
        self.page.goto(self.base + "/game?sim=1")
        self.page.evaluate("document.getElementById('overlay').style.display='none'")
        self.page.locator("#btnSim").click()
        self.page.locator("#trainerDiagToggle").click()
        self.page.wait_for_function("trainerDiagActive && !trainerDiagBusy")
        self.assertEqual(self.page.locator("#trainerDiagToggle").text_content(), "Stop diagnoselog")
        self.assertIn("Log actief", self.page.locator("#trainerDiagStatus").text_content())
        self.page.evaluate("SETTINGS.route_feel=0; lastPostT=-10; lastPostG=-999; postGrade(4)")
        self.page.wait_for_timeout(100)
        self.assertEqual(grades[-1]["diagnostic"]["route_grade"], 4)
        self.assertEqual(grades[-1]["diagnostic"]["route_feel"], 0)
        self.assertEqual(grades[-1]["grade"], grades[-1]["diagnostic"]["gear_offset"])
        self.page.locator("#trainerDiagToggle").click()
        self.page.wait_for_function("!trainerDiagActive && !trainerDiagBusy")
        self.page.evaluate("lastPostT=-10; lastPostG=-999; postGrade(4)")
        self.page.wait_for_timeout(100)
        self.assertNotIn("diagnostic", grades[-1])
        self.assertEqual(commands, ["start", "stop"])
        self.assertFalse(self.page.evaluate("simOn"))

    def test_fan_grace_and_recovery_on_game_and_remote(self):
        current = {"on": True, "connected": True, "visible": True, "level": 2}
        self.page.route("**/api", lambda route: route.fulfill(json={
            "power": 0, "cadence": 0, "hr": 0, "fan": current}))
        self.page.route("**/gear", lambda route: route.fulfill(json={"v": 6, "max": 12}))
        for path, selector in (("/game", "#zFan"), ("/remote", "#fanBox")):
            with self.subTest(path=path):
                current.update(on=True, connected=True, visible=True)
                self.page.goto(self.base + path)
                if path == "/game":
                    self.page.evaluate("document.getElementById('overlay').style.display='none'")
                self.page.wait_for_function("selector => getComputedStyle(document.querySelector(selector)).display !== 'none'",
                                            arg=selector)
                for connected, visible in ((False, True), (False, True), (True, True),
                                            (False, False), (True, True)):
                    current.update(connected=connected, visible=visible)
                    self.page.evaluate("fan => es.onmessage({data:JSON.stringify({power:0,cadence:0,fan})})",
                                       current)
                    self.assertEqual(self.page.locator(selector).is_visible(), visible)
                    if visible and path == "/game":
                        self.assertEqual(self.page.locator("#zFanN").text_content(), "2" if connected else "-")
                    if visible and path == "/remote":
                        self.assertEqual(self.page.locator("#fanBtns button.on").count(), 1 if connected else 0)
                        if not connected:
                            self.assertIn("verbinding herstellen", self.page.locator("#fanBox .fl").text_content())
                current.update(on=False, connected=False, visible=False)
                self.page.evaluate("fan => es.onmessage({data:JSON.stringify({power:0,cadence:0,fan})})", current)
                self.assertFalse(self.page.locator(selector).is_visible())

    def test_remote_close_link_is_hidden_only_on_coarse_touch(self):
        self.page.goto(self.base + "/remote")
        self.page.set_viewport_size({"width": 700, "height": 500})
        self.assertTrue(self.page.locator("#btnHome").is_visible())
        self.assertTrue(self.page.locator("#down").is_visible())
        browser = self.pw.chromium.launch(executable_path=str(EDGE), headless=True,
                                           args=["--no-first-run", "--disable-background-networking"])
        self.addCleanup(browser.close)
        for width, height in ((390, 844), (844, 390)):
            with self.subTest(width=width, height=height):
                context = browser.new_context(viewport={"width": width, "height": height},
                                               is_mobile=True, has_touch=True, device_scale_factor=2)
                page = context.new_page()
                page.goto(self.base + "/remote")
                self.assertTrue(page.evaluate("matchMedia('(hover: none) and (pointer: coarse)').matches"))
                self.assertFalse(page.locator("#btnHome").is_visible())
                self.assertTrue(page.locator("#down").is_visible())
                self.assertTrue(page.locator("#up").is_visible())
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                context.close()

    def test_desktop_and_mobile_layout(self):
        self.register()
        for width, height in ((1440, 1000), (390, 844)):
            self.page.set_viewport_size({"width": width, "height": height})
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width,
                                 "Profile page horizontal overflow at %dpx" % width)
        self.page.locator("#hideRecovery").click()
        self.ready()
        self.open_game()
        for width, height in ((1440, 1000), (390, 844)):
            self.page.set_viewport_size({"width": width, "height": height})
            for scope in ("Local", "General", "Friends"):
                with self.subTest(width=width, scope=scope):
                    self.page.locator("#kom" + scope).click()
                    if scope != "Local":
                        self.page.wait_for_function("document.querySelector('#komBody table') !== null")
                    bounds = self.page.locator("#kom > div").bounding_box()
                    self.assertGreaterEqual(bounds["x"], 0,
                                            "KOM panel clipped on left at %dpx: %r" % (width, bounds))
                    self.assertLessEqual(bounds["x"] + bounds["width"], width,
                                         "KOM panel clipped on right at %dpx: %r" % (width, bounds))
                    self.assertLessEqual(self.page.locator("#kom").evaluate("el => el.scrollWidth"), width,
                                         "KOM modal horizontal overflow at %dpx" % width)
                    self.page.locator("#btnKomClose").scroll_into_view_if_needed()
                    self.assertTrue(self.page.locator("#btnKomClose").is_visible())


if __name__ == "__main__":
    unittest.main()
