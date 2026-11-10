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
        if path in ("/game", "/klassementen", "/remote") or path.startswith("/online-kom/"):
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
                            ("multi", {"mode": "off", "server": ""}), ("gear", {"v": 6, "max": 12})):
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
        self.page.goto(self.base + "/klassementen")
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
        self.page.goto(self.base + "/klassementen")
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

    def open_game(self):
        self.page.goto(self.base + "/game")
        self.page.wait_for_function("typeof selectRoute === 'function' && typeof openKom === 'function'")
        self.page.evaluate("selectRoute(ROUTES.find(r => r.id === 'ui-official')); openKom();")
        self.page.wait_for_timeout(250)

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
