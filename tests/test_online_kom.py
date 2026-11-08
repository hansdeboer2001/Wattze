"""Offline backend and real local HTTP security tests for online KOM."""
import hashlib
import http.client
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from wattze import online_kom as kom
from wattze.http_handler import Handler


PROFILE = {"id": "rider-1", "name": "Rider", "friend_code": "ABC123",
           "share_general": True, "share_friends": False, "consent_version": 1}
TOKEN = "private-recovery-token"


class OnlineKOMTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"WATTZE_KOM_HOST": ""})
        env.start()
        self.addCleanup(env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for key in ("RIDE_DIR", "ROUTE_DIR"):
            p = patch.object(kom.app_state, key, self.temp.name)
            p.start()
            self.addCleanup(p.stop)
        self.opener_patch = patch.object(kom.request, "build_opener")
        self.opener = self.opener_patch.start().return_value
        self.addCleanup(self.opener_patch.stop)
        self.route = {"id": "official", "ele": [100, 110.1234567, 120, 110], "step": 25,
                      "segs": [{"at": 0, "end": 75}]}
        self.write_route()
        self.attempt = {"route_id": "official", "segment": 0, "time": 12.5, "avgP": 210,
                        "simulated": False, "course": {"pts": [0, 10.1234567, 20, 10],
                                                        "step": 25, "segs": [{"at": 0, "end": 75}]}}
        self.reply({"ok": True})

    def write_route(self):
        with open(os.path.join(self.temp.name, "official.json"), "w", encoding="utf-8") as f:
            json.dump(self.route, f)

    def identity(self, **prefs):
        kom._save({"token": TOKEN, "profile": {**PROFILE, **prefs}, "pending": []})

    def reply(self, value):
        self.opener.open.side_effect = None
        self.opener.open.return_value.__enter__.return_value.read.return_value = json.dumps({"ok": True, **value}).encode()

    def call(self, suffix, body=None, method="POST", query=None):
        return kom.handle(method, suffix, body, query)

    def test_status_is_local_and_secret_free(self):
        self.assertEqual(self.call("/status", method="GET"),
                          ({"ok": True, "configured": False, "profile": None, "pending": 0,
                            "recovery_pending": False}, 200))
        self.identity()
        result, status = self.call("/status", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(result["configured"])
        self.assertEqual(result["profile"], PROFILE)
        self.assertNotIn(TOKEN, json.dumps(result))
        self.opener.open.assert_not_called()

    def test_kom_host_default_and_override_leave_multiplayer_unchanged(self):
        original_host = kom.app_state.MP_HOST
        original_multi = dict(kom.app_state.multi)
        for value, expected in (("", original_host), ("  ", original_host),
                                (" https://test.wattze.com/ ", "https://test.wattze.com")):
            with self.subTest(value=value), patch.dict(os.environ, {"WATTZE_KOM_HOST": value}):
                kom._remote("/capabilities")
                self.assertEqual(self.opener.open.call_args.args[0].full_url,
                                 expected + "/api/kom/v1/capabilities")
        self.assertEqual(kom.app_state.MP_HOST, original_host)
        self.assertEqual(kom.app_state.multi, original_multi)

    def test_requests_identify_wattze_to_cloudflare(self):
        kom._remote("/capabilities")
        req = self.opener.open.call_args.args[0]
        self.assertEqual(req.get_header("User-agent"), "Wattze/1.0 (+https://wattze.com)")
        self.assertEqual(req.get_header("Accept"), "application/json")

    def test_cloudflare_denial_is_not_reported_as_profile_failure(self):
        self.opener.open.side_effect = HTTPError(
            "https://test.wattze.com", 403, "Forbidden", {}, io.BytesIO(b"error code: 1010"))
        with self.assertRaises(kom.OnlineError) as caught:
            kom._remote("/capabilities")
        self.assertEqual(caught.exception.status, 403)
        self.assertIn("Cloudflare", str(caught.exception))

    def test_testserver_identity_and_queue_are_isolated(self):
        self.identity()
        production = kom._load()
        production["pending"] = [{"attempt_id": "production-only"}]
        kom._save(production)
        with patch.dict(os.environ, {"WATTZE_KOM_HOST": "https://test.wattze.com"}):
            self.assertFalse(self.call("/status", method="GET")[0]["configured"])
            self.reply({"token": "test-only-token", "profile": PROFILE})
            self.assertEqual(self.call("/register", {"name": "Rider"})[1], 200)
            req = self.opener.open.call_args.args[0]
            self.assertIsNone(req.get_header("Authorization"))
            self.assertEqual(kom._load()["pending"], [])
            self.assertEqual(kom._load()["token"], "test-only-token")
            with patch.dict(os.environ, {"WATTZE_KOM_HOST": "https://other.example"}):
                self.assertFalse(self.call("/status", method="GET")[0]["configured"])
        self.assertEqual(kom._load(), production)

    def test_kom_host_rejects_non_https_and_non_origin_urls(self):
        for host in ("http://test.wattze.com", "https://user:secret@test.wattze.com",
                     "https://test.wattze.com/path", "https://test.wattze.com?x=1",
                     "https://test.wattze.com#fragment"):
            with self.subTest(host=host), patch.dict(os.environ, {"WATTZE_KOM_HOST": host}):
                with self.assertRaises(kom.OnlineError):
                    kom._remote("/capabilities")
        self.opener.open.assert_not_called()

    def test_opt_in_only_and_no_legacy_upload(self):
        self.assertEqual(self.call("/attempt", self.attempt)[0]["skipped"], "not_registered")
        self.identity(share_general=False)
        self.assertEqual(self.call("/attempt", self.attempt)[0]["skipped"], "sharing_disabled")
        self.call("/retry", {})
        self.opener.open.assert_not_called()

    def test_register_token_only_once_and_no_identity_overwrite(self):
        self.reply({"token": TOKEN, "profile": PROFILE})
        result, status = self.call("/register", {"name": "Rider"})
        self.assertEqual(status, 200)
        self.assertEqual(result["token"], TOKEN)
        self.assertEqual(kom._load()["token"], TOKEN)
        self.assertEqual(self.call("/register", {"name": "Other"})[1], 409)
        self.assertEqual(self.call("/restore", {"token": "other"})[1], 409)
        self.assertEqual(self.opener.open.call_count, 1)

    def test_restore_bearer_not_in_body_or_response(self):
        self.reply({"profile": PROFILE, "token": "do-not-forward"})
        result, status = self.call("/restore", {"token": TOKEN})
        self.assertEqual(status, 200)
        req = self.opener.open.call_args.args[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(json.loads(req.data), {})
        self.assertNotIn("token", result)
        self.assertFalse(kom._load()["recovery_pending"])
        self.assertEqual(self.call("/recovery", method="GET")[1], 409)

    def test_registration_recovery_survives_lost_local_response_until_confirmed(self):
        self.reply({"token": TOKEN, "profile": PROFILE})
        self.call("/register", {"name": "Rider"})  # Browser loses this response.
        status = self.call("/status", method="GET")[0]
        self.assertTrue(status["recovery_pending"])
        self.assertNotIn(TOKEN, json.dumps(status))
        for _ in range(2):
            self.assertEqual(self.call("/recovery", method="GET"), ({"ok": True, "token": TOKEN}, 200))
        self.reply({"profile": {**PROFILE, "name": "Renamed"}})
        self.call("/me", method="GET")
        self.call("/profile", {"name": "Renamed", "share_general": True, "share_friends": False})
        self.call("/attempt", self.attempt)
        self.call("/retry")
        self.assertTrue(kom._load()["recovery_pending"])
        self.assertEqual(self.call("/recovery/confirm", {"token": TOKEN})[1], 400)
        self.assertEqual(self.call("/recovery/confirm", {})[1], 200)
        self.assertFalse(self.call("/status", method="GET")[0]["recovery_pending"])
        self.assertEqual(self.call("/recovery", method="GET")[1], 409)
        self.assertEqual(kom._load()["token"], TOKEN)

    def test_profile_requires_nonnegative_integer_consent_version(self):
        for value in (None, True, -1, 0.0, "0"):
            with self.subTest(value=value), self.assertRaises(kom.OnlineError):
                kom._profile({**PROFILE, "consent_version": value})
        with self.assertRaises(kom.OnlineError):
            kom._profile({k: v for k, v in PROFILE.items() if k != "consent_version"})

    def test_me_syncs_and_filters_remote_credentials(self):
        self.identity()
        changed = {**PROFILE, "name": "New"}
        self.reply({"profile": changed, "friends": [{"id": "f", "token": TOKEN}],
                    "incoming": [], "outgoing": [], "token": TOKEN})
        result, status = self.call("/me", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(kom._load()["profile"], changed)
        self.assertEqual(result["friends"], [{"id": "f"}])
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_attempt_protocol_revision_and_timeout(self):
        self.identity()
        result, status = self.call("/attempt", self.attempt)
        self.assertEqual((status, result["queued"]), (200, False))
        req = self.opener.open.call_args.args[0]
        payload = json.loads(req.data)
        canonical = {"ele": [round(float(v), 6) for v in self.route["ele"]], "step": 25.0,
                     "segs": [{"at": 0.0, "end": 75.0}]}
        revision = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        self.assertEqual(payload["revision"], revision)
        self.assertEqual(payload["ruleset"], "wattze-1")
        for key in ("consent_version", "share_general", "share_friends"):
            self.assertEqual(payload[key], PROFILE[key])
        self.assertEqual(set(payload), {"attempt_id", "route_id", "revision", "segment", "time", "avgP",
                                        "simulated", "ruleset", "consent_version", "share_general", "share_friends"})
        self.assertEqual(req.get_header("Idempotency-key"), payload["attempt_id"])
        self.assertIs(payload["simulated"], False)
        self.assertEqual(str(uuid.UUID(payload["attempt_id"])), payload["attempt_id"])
        self.assertNotIn("course", payload)
        self.assertTrue(req.full_url.endswith("/api/kom/v1/attempts"))
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 3)
        self.assertEqual(kom._load()["pending"], [])

    def test_simulated_strict_bool(self):
        self.identity()
        self.assertEqual(self.call("/attempt", {**self.attempt, "simulated": True})[0]["skipped"], "simulated")
        for value in (None, 0, 1, "false", "true"):
            self.assertEqual(self.call("/attempt", {**self.attempt, "simulated": value})[1], 400)
        self.opener.open.assert_not_called()

    def test_browser_overrides_and_invalid_values_rejected(self):
        self.identity()
        cases = [{"route_id": "eigen-test"}, {"route_id": "../official"}, {"segment": True},
                 {"segment": -1}, {"time": float("nan")}, {"avgP": -1}, {"course": None}]
        for key, value in (("pts", [100, 110.1234567, 120, 110]), ("step", 24),
                           ("segs", [{"at": 0, "end": 74}]), ("pts", [0, 10, 20])):
            cases.append({"course": {**self.attempt["course"], key: value}})
        for override in cases:
            with self.subTest(override=override):
                self.assertEqual(self.call("/attempt", {**self.attempt, **override})[1], 400)
        self.opener.open.assert_not_called()

    def test_explicit_valid_official_end_required(self):
        self.identity()
        for seg in ({"at": 0}, {"at": 0, "end": 76}, {"at": 25, "end": 25}):
            self.route["segs"] = [seg]
            self.write_route()
            self.assertEqual(self.call("/attempt", self.attempt)[1], 400)
        self.opener.open.assert_not_called()

    def test_transient_queue_persists_and_retry_reuses_uuid(self):
        self.identity()
        self.opener.open.side_effect = URLError("offline " + TOKEN)
        result, status = self.call("/attempt", self.attempt)
        self.assertEqual(status, 200)
        self.assertTrue(result["queued"])
        queued = kom._load()["pending"][0]
        self.reply({"ok": True})
        result, status = self.call("/retry", {})
        self.assertEqual((status, result["sent"], result["pending"]), (200, 1, 0))
        self.assertEqual(json.loads(self.opener.open.call_args.args[0].data), queued)

    def test_only_transient_errors_queue(self):
        self.identity()
        for code, pending in ((400, 0), (401, 0), (404, 0), (429, 1), (503, 2)):
            self.opener.open.side_effect = HTTPError("https://wattze.com", code, TOKEN, {}, None)
            result, status = self.call("/attempt", self.attempt)
            self.assertEqual(len(kom._load()["pending"]), pending)
            self.assertNotIn(TOKEN, json.dumps(result))
            if code == 404:
                self.assertIn("upgrade", result["error"])

    def test_ambiguous_success_keeps_initial_and_retried_attempt_uuid(self):
        for raw in (b"not json", b'{"ok":tru', b"[]", b"{}", b'{"ok":false}',
                    b'{"ok":1}', b"x" * (kom.MAX_RESPONSE + 1)):
            with self.subTest(raw=raw[:40]):
                self.identity()
                self.opener.open.return_value.__enter__.return_value.read.return_value = raw
                result, status = self.call("/attempt", self.attempt)
                self.assertEqual((status, result["queued"]), (200, True))
                queued = kom._load()["pending"]
                self.assertEqual(self.call("/retry")[0]["pending"], 1)
                self.assertEqual(kom._load()["pending"], queued)
                self.assertEqual(json.loads(self.opener.open.call_args.args[0].data), queued[0])
                self.reply({"ok": True})
                self.assertEqual(self.call("/retry")[0]["sent"], 1)

    def test_truncated_http_response_and_redirect_keep_attempt(self):
        for failure in (http.client.IncompleteRead(b'{"ok":', 5),
                        HTTPError("https://kom.example", 302, "redirect", {}, None)):
            with self.subTest(failure=type(failure).__name__):
                self.identity()
                self.opener.open.side_effect = failure
                self.assertTrue(self.call("/attempt", self.attempt)[0]["queued"])
                queued = kom._load()["pending"]
                self.assertEqual(self.call("/retry")[0]["pending"], 1)
                self.assertEqual(kom._load()["pending"], queued)
                self.reply({"ok": True})
                self.assertEqual(self.call("/retry")[0]["sent"], 1)

    def test_bounded_safe_dutch_server_errors(self):
        cases = [
            (404, {"error": "friend code not found"}, 404, "Vriendcode"),
            (404, {"error": "official route not found"}, 404, "route"),
            (404, {"error": "segment not found"}, 404, "segment"),
            (404, {"error": "not found"}, 503, "upgrade"),
            (409, {"error": "route revision mismatch"}, 409, "routeversie"),
            (401, {"error": "invalid bearer token"}, 401, "herstelcode"),
            (409, {"error": "relationship already exists"}, 409, "bestaat al"),
            (409, {"error": "storage unavailable"}, 503, "serveropslag"),
            (400, {"error": TOKEN}, 400, "geweigerd"),
            (400, {"error": [TOKEN]}, 400, "geweigerd"),
            (400, [TOKEN], 400, "geweigerd"),
            (400, b"not JSON " + TOKEN.encode(), 400, "geweigerd"),
            (400, b"[" * 2000, 400, "geweigerd"),
            (404, b"<html>Not Found</html>", 503, "upgrade"),
            (409, {"error": "storage unavailable", "detail": "x" * kom.MAX_ERROR_RESPONSE},
             409, "geweigerd"),
        ]
        for code, body, expected_status, text in cases:
            with self.subTest(code=code, text=text):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                stream = MagicMock(wraps=io.BytesIO(raw))
                self.opener.open.side_effect = HTTPError("https://wattze.com", code, TOKEN, {}, stream)
                with self.assertRaises(kom.OnlineError) as caught:
                    kom._remote("/me")
                self.assertEqual(caught.exception.status, expected_status)
                self.assertIn(text.lower(), str(caught.exception).lower())
                self.assertNotIn(TOKEN, str(caught.exception))
                stream.read.assert_called_once_with(kom.MAX_ERROR_RESPONSE + 1)
                stream.close.assert_called_once()

    def test_public_leaderboard_without_registration(self):
        self.reply({"entries": []})
        query = {"route_id": "official", "segment": "0"}
        for scope in (None, "general"):
            result, status = self.call("/leaderboard", method="GET",
                                       query={**query, **({"scope": scope} if scope else {})})
            self.assertEqual((status, result["entries"]), (200, []))
            self.assertIsNone(self.opener.open.call_args.args[0].get_header("Authorization"))
        self.opener.open.reset_mock()
        self.assertEqual(self.call("/leaderboard", method="GET", query={**query, "scope": "friends"})[1], 409)
        self.opener.open.assert_not_called()

    def test_storage_unavailable_queues_and_retry_stops(self):
        self.identity()
        def unavailable(*args, **kwargs):
            raise HTTPError("https://wattze.com", 409, "Conflict", {},
                            io.BytesIO(b'{"ok":false,"error":"storage unavailable"}'))
        self.opener.open.side_effect = unavailable
        result, status = self.call("/attempt", self.attempt)
        self.assertEqual((status, result["queued"], result["pending"]), (200, True, 1))
        pending = kom._load()["pending"]
        result, status = self.call("/retry")
        self.assertEqual((status, result["sent"], result["pending"]), (200, 0, 1))
        self.assertEqual(kom._load()["pending"], pending)

    def test_retry_is_bounded_and_preserves_remaining_order(self):
        self.identity()
        self.opener.open.side_effect = URLError("offline")
        self.call("/attempt", self.attempt)
        data = kom._load()
        data["pending"] = [{**data["pending"][0], "attempt_id": str(uuid.uuid4())} for _ in range(45)]
        kom._save(data)
        self.reply({"ok": True})
        self.opener.open.reset_mock()
        for sent, remaining in ((20, 25), (20, 5), (5, 0)):
            before = self.opener.open.call_count
            result, status = self.call("/retry")
            self.assertEqual((status, result["sent"], result["pending"]), (200, sent, remaining))
            self.assertEqual(self.opener.open.call_count - before, sent)
            self.assertEqual(kom._load()["pending"], data["pending"][45 - remaining:])

    def test_concurrent_offline_attempts_do_not_lose_outbox_entries(self):
        self.identity()
        self.opener.open.side_effect = URLError("offline")
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.call("/attempt", self.attempt)))
                   for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertTrue(all(status == 200 and result["queued"] for result, status in results))
        pending = kom._load()["pending"]
        self.assertEqual(len(pending), 8)
        self.assertEqual(len({a["attempt_id"] for a in pending}), 8)

    def test_retry_rejects_changed_route_and_drops_stale_attempt(self):
        self.identity()
        self.opener.open.side_effect = URLError("offline")
        self.call("/attempt", self.attempt)
        self.route["ele"][1] += 1
        self.write_route()
        self.opener.open.reset_mock()
        self.assertEqual(self.call("/retry", {})[1], 400)
        self.assertEqual(kom._load()["pending"], [])
        self.opener.open.assert_not_called()

    def test_outbox_bound_and_profile_timeout_privacy(self):
        self.identity()
        data = kom._load()
        data["pending"] = [{"attempt_id": str(i)} for i in range(1000)]
        kom._save(data)
        self.assertEqual(self.call("/attempt", self.attempt)[0]["skipped"], "outbox_full")
        self.opener.open.assert_not_called()
        self.opener.open.side_effect = TimeoutError(TOKEN)
        result, status = self.call("/profile", {"name": "Rider", "share_general": False, "share_friends": False})
        self.assertEqual(status, 503)
        self.assertEqual(kom._load()["pending"], [])
        self.assertFalse(kom._load()["profile"]["share_general"])
        self.opener.open.reset_mock()
        self.assertEqual(self.call("/attempt", self.attempt)[0]["skipped"], "sharing_disabled")
        self.opener.open.assert_not_called()

    def test_profile_and_friend_forwarding(self):
        self.identity()
        profile = {**PROFILE, "share_friends": True}
        self.reply({"profile": profile})
        self.assertEqual(self.call("/profile", {k: profile[k] for k in ("name", "share_general", "share_friends")})[1], 200)
        self.assertEqual(kom._load()["profile"], profile)
        self.reply({"ok": True})
        for suffix, payload in (("/friends/request", {"code": "ABC"}),
                                ("/friends/accept", {"id": "friend"}),
                                ("/friends/remove", {"id": "friend"})):
            self.assertEqual(self.call(suffix, payload)[1], 200)
            req = self.opener.open.call_args.args[0]
            self.assertTrue(req.full_url.endswith(suffix))
            self.assertEqual(json.loads(req.data), payload)

    def test_leaderboard_computes_revision_checks_supplied(self):
        self.identity()
        query = {"route_id": "official", "segment": "0", "scope": "friends"}
        self.reply({"entries": []})
        result, status = self.call("/leaderboard", method="GET", query=query)
        self.assertEqual(status, 200)
        self.assertEqual(len(result["revision"]), 24)
        self.assertIn("revision=" + result["revision"], self.opener.open.call_args.args[0].full_url)
        self.assertEqual(self.call("/leaderboard", method="GET", query={**query, "revision": "bad"})[1], 400)

    def test_response_limits_malformed_and_redirect(self):
        self.identity()
        response = self.opener.open.return_value.__enter__.return_value
        for raw in (b"x" * (kom.MAX_RESPONSE + 1), b"not json", b"[]"):
            response.read.return_value = raw
            result, status = self.call("/me", method="GET")
            self.assertEqual(status, 502)
            self.assertNotIn(TOKEN, json.dumps(result))
        self.assertIsNone(kom._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid"))

    def test_atomic_replace_failure_preserves_identity(self):
        self.identity()
        with patch.object(kom.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                kom._save({"token": "changed", "profile": PROFILE, "pending": []})
        self.assertEqual(kom._load()["token"], TOKEN)
        self.assertFalse(any(name.startswith(".online-kom-") for name in os.listdir(self.temp.name)))


class OnlineKOMIntegrationTests(unittest.TestCase):
    """Client wire requests dispatched by the sibling server, not canned replies."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.server_root = cls.root.parent / "wattze-server"
        source = cls.server_root / "kickr" / "kom_api.py"
        if not source.is_file():
            raise unittest.SkipTest("Cross-component tests require ../wattze-server/kickr/kom_api.py")
        spec = importlib.util.spec_from_file_location("server_kom_api", source)
        cls.server_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.server_module)

    def setUp(self):
        env = patch.dict(os.environ, {"WATTZE_KOM_HOST": ""})
        env.start()
        self.addCleanup(env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.api = self.server_module.KomAPI(self.temp.name, self.server_root / "routes")
        for key, value in (("RIDE_DIR", self.temp.name), ("ROUTE_DIR", str(self.root / "routes")),
                           ("MP_HOST", "https://kom.example")):
            p = patch.object(kom.app_state, key, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(kom.request, "build_opener")
        self.opener = p.start().return_value
        self.addCleanup(p.stop)
        self.opener.open.side_effect = self.dispatch_request
        self.query = {"route_id": "grebbeberg", "segment": "0"}
        with (self.root / "routes" / "grebbeberg.json").open(encoding="utf-8") as f:
            route = json.load(f)
        self.attempt = {"route_id": route["id"], "segment": 0, "time": 120, "avgP": 210,
                        "simulated": False, "course": {"pts": [e - route["ele"][0] for e in route["ele"]],
                                                        "step": route["step"], "segs": route["segs"]}}

    def dispatch_request(self, req, timeout):
        self.assertEqual(timeout, kom.TIMEOUT)
        url = urlsplit(req.full_url)
        body = self.server_module.strict_json(req.data) if req.data else {}
        try:
            result = self.api.dispatch(req.get_method(), url.path, parse_qs(url.query), body,
                                       req.get_header("Authorization"), "127.0.0.1")
        except self.server_module.ApiError as exc:
            raise HTTPError(req.full_url, exc.status, "Rejected", {},
                            io.BytesIO(json.dumps({"ok": False, "error": exc.message}).encode())) from None
        except (sqlite3.Error, OSError):
            # Same storage-error envelope as kom_api.handle.
            raise HTTPError(req.full_url, 409, "Conflict", {},
                            io.BytesIO(b'{"ok":false,"error":"storage unavailable"}')) from None
        return io.BytesIO(json.dumps(result).encode())

    def call(self, suffix, body=None, method="POST", query=None):
        result, status = kom.handle(method, suffix, body, query)
        self.assertEqual(status, 200, result)
        return result

    def register(self, name="Rider"):
        return self.call("/register", {"name": name})

    def share(self, name="Rider", general=True, friends=True):
        return self.call("/profile", {"name": name, "share_general": general, "share_friends": friends})

    def board(self, scope="general"):
        return self.call("/leaderboard", method="GET", query={**self.query, "scope": scope})

    def test_register_restore_profile_attempt_and_public_leaderboard(self):
        registered = self.register()
        self.assertFalse(registered["profile"]["share_general"])
        token = registered["token"]
        os.unlink(Path(self.temp.name) / "online-kom.json")
        restored = self.call("/restore", {"token": token})
        self.assertEqual(restored["profile"], registered["profile"])
        self.assertNotIn("token", restored)
        self.share(name="Renamed")
        self.assertFalse(self.call("/attempt", self.attempt)["queued"])
        board = self.board()
        self.assertEqual(board["revision"], self.api.catalog["grebbeberg"][0])
        self.assertEqual([(e["name"], e["time"], e["avgP"]) for e in board["entries"]],
                         [("Renamed", 120, 210)])
        self.assertEqual(self.call("/me", method="GET")["profile"]["name"], "Renamed")
        os.unlink(Path(self.temp.name) / "online-kom.json")
        self.assertEqual(self.board()["entries"], board["entries"])
        self.assertIsNone(self.opener.open.call_args.args[0].get_header("Authorization"))
        self.assertEqual(kom.handle("GET", "/leaderboard", query={**self.query, "scope": "friends"})[1], 409)

    def test_friends_request_accept_remove_and_scope_visibility(self):
        alice = self.register("Alice")
        self.share("Alice", general=False)
        self.call("/attempt", self.attempt)
        alice_data = kom._load()
        os.unlink(Path(self.temp.name) / "online-kom.json")
        bob = self.register("Bob")
        self.share("Bob")
        self.assertEqual(self.board("friends")["entries"], [])
        self.call("/friends/request", {"code": alice["profile"]["friend_code"]})
        self.assertEqual(self.call("/me", method="GET")["outgoing"][0]["id"], alice["profile"]["id"])
        bob_data = kom._load()
        kom._save(alice_data)
        self.assertEqual(self.call("/me", method="GET")["incoming"][0]["id"], bob["profile"]["id"])
        self.call("/friends/accept", {"id": bob["profile"]["id"]})
        kom._save(bob_data)
        self.assertEqual(self.board("friends")["entries"][0]["id"], alice["profile"]["id"])
        self.assertEqual(self.board()["entries"], [])
        self.call("/friends/remove", {"id": alice["profile"]["id"]})
        self.assertEqual(self.board("friends")["entries"], [])

    def test_server_domain_errors_are_not_upgrade_errors(self):
        self.register()
        result, status = kom.handle("POST", "/friends/request", {"code": "000000000000"})
        self.assertEqual(status, 404)
        self.assertIn("Vriendcode", result["error"])
        self.assertNotIn("upgrade", result["error"])

    def test_shipped_routes_match_revisions_or_reject_invalid_local_metadata(self):
        self.assertTrue(self.api.catalog)
        local_catalog = self.server_module.route_catalog(self.root / "routes")
        self.assertTrue(local_catalog)
        for route_id, (revision, segments) in self.api.catalog.items():
            with self.subTest(route=route_id):
                if route_id not in local_catalog:
                    with self.assertRaises(kom.OnlineError):
                        kom._route(route_id, 0)
                    continue
                for segment in range(len(segments)):
                    self.assertEqual(kom._route(route_id, segment), revision)
                result = self.call("/leaderboard", method="GET",
                                   query={"route_id": route_id, "segment": "0"})
                self.assertEqual(result["revision"], revision)
                self.assertEqual(result["entries"], [])
                self.assertIsNone(self.opener.open.call_args.args[0].get_header("Authorization"))

    def test_missing_endpoint_still_explains_upgrade(self):
        with self.assertRaises(kom.OnlineError) as caught:
            kom._remote("/unknown")
        self.assertEqual(caught.exception.status, 503)
        self.assertIn("upgrade", str(caught.exception))
        self.assertFalse(caught.exception.transient)

    def test_server_rejected_attempt_is_not_queued(self):
        self.register()
        self.share()
        result, status = kom.handle("POST", "/attempt", {**self.attempt, "avgP": 0})
        self.assertEqual(status, 400)
        self.assertIn("vermogen", result["error"])
        self.assertEqual(kom._load()["pending"], [])
        self.assertEqual(self.board()["entries"], [])

    def test_server_route_errors_are_not_upgrade_errors(self):
        _, segments = self.api.catalog["grebbeberg"]
        self.api.catalog["grebbeberg"] = ("0" * 24, segments)
        result, status = kom.handle("GET", "/leaderboard", query=self.query)
        self.assertEqual(status, 409)
        self.assertIn("routeversie", result["error"])
        del self.api.catalog["grebbeberg"]
        result, status = kom.handle("GET", "/leaderboard", query=self.query)
        self.assertEqual(status, 404)
        self.assertIn("route", result["error"])
        self.assertNotIn("upgrade", result["error"])

    def test_storage_failure_queues_then_real_dispatch_retries_idempotently(self):
        self.register()
        self.share()
        with patch.object(self.server_module.sqlite3, "connect", side_effect=sqlite3.OperationalError("private")):
            result = self.call("/attempt", self.attempt)
        self.assertTrue(result["queued"])
        queued = kom._load()["pending"][0]
        self.assertEqual(self.call("/retry")["sent"], 1)
        req = self.opener.open.call_args.args[0]
        self.assertEqual(json.loads(req.data), queued)
        self.assertEqual(req.get_header("Idempotency-key"), queued["attempt_id"])
        self.dispatch_request(req, kom.TIMEOUT).close()
        with closing(sqlite3.connect(self.api.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
        self.assertEqual(len(self.board()["entries"]), 1)

    def test_cross_installation_queued_consent_never_gains_later_scope(self):
        self.register()
        for toggle in (False, True):
            with self.subTest(off_on=toggle):
                self.share(general=False, friends=True)
                with patch.object(self.opener, "open", side_effect=URLError("offline")):
                    self.assertTrue(self.call("/attempt", self.attempt)["queued"])
                installation_a = kom._load()
                queued = installation_a["pending"][0]
                # Another PC with the same identity changes consent without syncing A.
                if toggle:
                    self.share(general=False, friends=False)
                    self.share(general=False, friends=True)
                else:
                    self.share(general=True, friends=True)
                kom._save(installation_a)
                result, status = kom.handle("POST", "/retry", {})
                self.assertEqual(status, 409, result)
                self.assertIn("gewijzigd", result["error"])
                self.assertEqual(kom._load()["pending"], [])
                self.assertEqual(json.loads(self.opener.open.call_args.args[0].data), queued)
                self.assertEqual(self.board()["entries"], [])
                self.assertEqual(self.board("friends")["entries"], [])

    def test_committed_attempt_with_lost_success_retries_without_republishing(self):
        self.register()
        self.share()
        dispatch = self.dispatch_request
        def lose_response(req, timeout):
            dispatch(req, timeout).close()
            return io.BytesIO(b'{"ok":tr')
        with patch.object(self.opener, "open", side_effect=lose_response):
            self.assertTrue(self.call("/attempt", self.attempt)["queued"])
        installation_a = kom._load()
        self.share(general=False, friends=False)
        self.share()
        kom._save(installation_a)
        self.assertEqual(self.call("/retry")["sent"], 1)
        self.assertEqual(self.board()["entries"], [])
        self.assertEqual(self.board("friends")["entries"], [])


class OnlineHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def send(self, method="GET", path="/online-kom/status", headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request(method, path, body, headers or {})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_guards_both_methods_before_dispatch(self):
        with patch.object(kom, "handle", return_value=({"ok": True}, 200)) as handle:
            for method in ("GET", "POST"):
                for headers in ({"Host": "evil.invalid"}, {"Origin": "https://evil.invalid"},
                                {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"},
                                {"Host": "127.0.0.1:1"}):
                    with self.subTest(method=method, headers=headers):
                        self.assertEqual(self.send(method, headers=headers, body="{}")[0], 403)
            handle.assert_not_called()

    def test_lan_peer_denied(self):
        handler = object.__new__(Handler)
        handler.client_address = ("192.168.1.23", 12345)
        handler._json = MagicMock()
        with patch.object(kom, "handle") as handle:
            handler._online_kom("GET")
            self.assertEqual(handler._json.call_args.args[1], 403)
            handle.assert_not_called()

    def test_post_json_required_and_shape_bounded(self):
        with patch.object(kom, "handle", return_value=({"ok": True}, 200)) as handle:
            for content in ("text/plain", "application/x-www-form-urlencoded", ""):
                self.assertEqual(self.send("POST", headers={"Content-Type": content}, body="{}")[0], 415)
            for body in ("[]", "null", "bad"):
                self.assertEqual(self.send("POST", headers={"Content-Type": "application/json"}, body=body)[0], 400)
            handle.assert_not_called()
            self.assertEqual(self.send("POST", "/online-kom/retry", {"Content-Type": "application/json"}, "{}")[0], 200)
            handle.assert_called_once_with("POST", "/retry", {}, {})

    def test_same_origin_get_dispatch(self):
        host = "127.0.0.1:%d" % self.server.server_port
        with patch.object(kom, "handle", return_value=({"ok": True}, 200)) as handle:
            self.assertEqual(self.send(path="/online-kom/leaderboard?route_id=official&segment=0",
                                       headers={"Host": host, "Origin": "http://" + host})[0], 200)
            handle.assert_called_once_with("GET", "/leaderboard", None, {"route_id": "official", "segment": "0"})


if __name__ == "__main__":
    unittest.main()
