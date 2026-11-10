"""Fan visibility, recovery and safety without network or persistent writes."""
from copy import deepcopy
from io import BytesIO
import json
import unittest
from unittest.mock import Mock, call, patch

# Importing state would otherwise create or migrate the ride directory.
with patch("os.makedirs"), patch("os.rename"):
    from wattze import fan as control, http_handler, telemetry
    from wattze.state import settings, multi


class StopLoop(BaseException):
    pass


class FanTests(unittest.TestCase):
    def setUp(self):
        with control.lock:
            saved = [(target, deepcopy(target)) for target in
                     (control.fan, control.rec, control.state, settings, multi)]

        def restore():
            with control.lock:
                for target, value in saved:
                    target.clear()
                    target.update(value)

        self.addCleanup(restore)
        self.clock = Mock(time=Mock(return_value=100.0),
                          monotonic=Mock(return_value=0.0))
        for module in (control, http_handler, telemetry):
            self.enterContext(patch.object(module, "time", self.clock))
        self.post = self.enterContext(patch.object(control, "_post"))
        self.enterContext(patch.object(http_handler, "save_config"))
        self.enterContext(patch.object(http_handler.trainer_diagnostics, "status",
                                       return_value={}))
        self.enterContext(patch.object(http_handler, "expire_cadence"))
        self.enterContext(patch.object(telemetry, "expire_cadence"))
        self.enterContext(patch.object(http_handler, "push"))
        control.fan.clear()
        control.fan.update(on=True, host="http://fan.test", level=2,
                           connected=True, sent=2, err="", force_level=None,
                           force_until=0.0, w=[80, 160, 240], mode="auto",
                           coast=5, coasting=False, disconnected_at=None)
        control.rec.update(active=False, game_power=None)
        control.state["power"] = 200

    def request(self, path, body=None):
        handler = object.__new__(http_handler.Handler)
        handler.path = path
        handler._json = Mock()
        if body is None:
            handler.do_GET()
        else:
            data = json.dumps(body).encode()
            handler.headers = {"Content-Length": str(len(data))}
            handler.rfile = BytesIO(data)
            handler.do_POST()
        return handler._json

    def run_loop(self, steps):
        steps = iter(steps)

        def sleep(delay):
            self.assertEqual(delay, 0.5)
            try:
                now, action = next(steps)
            except StopIteration:
                raise StopLoop
            self.clock.time.return_value = now
            if action is not None:
                action()

        self.clock.sleep.side_effect = sleep
        with self.assertRaises(StopLoop):
            control.fan_loop()

    def test_visible_requires_enabled_host_and_connection(self):
        for on in (False, True):
            for host in ("", "http://fan.test"):
                for connected in (False, True):
                    with self.subTest(on=on, host=host, connected=connected):
                        control.fan.update(on=on, host=host, connected=connected,
                                           disconnected_at=None)
                        self.assertEqual(control.fan_visible(),
                                         bool(on and host and connected))

    def test_disabled_or_missing_host_hides_immediately_during_grace(self):
        control.fan_failed(OSError("offline"))
        self.assertTrue(control.fan_visible())
        for change in ({"on": False}, {"host": ""}):
            with self.subTest(change=change):
                control.fan.update(on=True, host="http://fan.test")
                control.fan.update(change)
                self.assertFalse(control.fan_visible())

    def test_initial_unconnected_without_timer_is_hidden(self):
        control.fan.update(connected=False, disconnected_at=None)
        self.assertFalse(control.fan_visible())
        self.clock.monotonic.assert_not_called()

    def test_failure_starts_monotonic_timer_once_including_zero(self):
        for start in (0.0, 100.0):
            with self.subTest(start=start):
                control.fan.update(connected=True, disconnected_at=None,
                                   sent=2, level=2, force_level=2)
                self.clock.monotonic.reset_mock()
                self.clock.monotonic.return_value = start
                control.fan_failed(OSError("first"))
                self.assertEqual(control.fan["disconnected_at"], start)
                self.clock.monotonic.return_value = start + 9
                control.fan_failed(TimeoutError("again"))
                self.clock.monotonic.assert_called_once_with()
                self.assertEqual(control.fan["disconnected_at"], start)
                self.assertFalse(control.fan["connected"])
                self.assertIsNone(control.fan["sent"])
                self.assertEqual(control.fan["level"], 2)
                self.assertEqual(control.fan["force_level"], 2)
                self.assertEqual(control.fan["err"], "TimeoutError: again")
                self.clock.monotonic.return_value = start + 9.999
                self.assertTrue(control.fan_visible())
                self.clock.monotonic.return_value = start + 10
                self.assertFalse(control.fan_visible())

    def test_successful_command_or_ping_clears_timer_and_recovers_immediately(self):
        for operation, url in ((lambda: control.fan_cmd(2),
                                "/number/fan_level/set?value=2"),
                               (control.fan_ping, "/button/kickr_ping/press")):
            with self.subTest(url=url):
                control.fan_failed(OSError("offline"))
                self.clock.monotonic.return_value += 10
                self.assertFalse(control.fan_visible())
                self.post.reset_mock()
                operation()
                self.post.assert_called_once_with("http://fan.test" + url)
                self.assertTrue(control.fan["connected"])
                self.assertIsNone(control.fan["disconnected_at"])
                self.assertEqual(control.fan["err"], "")
                self.assertTrue(control.fan_visible())

    def test_disconnected_cached_level_is_sent_but_healthy_cache_is_not(self):
        control.fan.update(connected=False, disconnected_at=0.0, sent=2)
        control.fan_cmd(2)
        self.post.assert_called_once_with(
            "http://fan.test/number/fan_level/set?value=2")
        self.assertEqual(control.fan["sent"], 2)
        self.assertEqual(control.fan["level"], 2)
        self.assertIsNone(control.fan["disconnected_at"])
        control.fan_cmd(2)
        self.assertEqual(self.post.call_count, 1)

    def test_loop_reasserts_same_level_after_ping_failure(self):
        control.fan["sent"] = None
        self.post.side_effect = [None, OSError("ping failed"), None]

        def check_failure():
            self.assertFalse(control.fan["connected"])
            self.assertIsNone(control.fan["sent"])
            self.assertEqual(control.fan["level"], 2)
            self.assertEqual(control.fan["disconnected_at"], 0.0)

        self.run_loop([(100, None), (102, None), (102.5, check_failure)])
        self.assertEqual(self.post.call_args_list, [
            call("http://fan.test/number/fan_level/set?value=2"),
            call("http://fan.test/button/kickr_ping/press"),
            call("http://fan.test/number/fan_level/set?value=2")])
        self.assertTrue(control.fan["connected"])
        self.assertIsNone(control.fan["disconnected_at"])

    def test_loop_reasserts_same_level_when_cache_invalidated_while_connected(self):
        def invalidate():
            self.assertTrue(control.fan["connected"])
            control.fan["sent"] = None

        self.run_loop([(100, None), (100.5, invalidate)])
        self.post.assert_called_once_with(
            "http://fan.test/number/fan_level/set?value=2")
        self.assertEqual(control.fan["level"], 2)
        self.assertEqual(control.fan["sent"], 2)

    def test_disabled_loop_attempts_stop_and_clears_timer_even_if_stop_fails(self):
        def disable():
            control.fan_failed(OSError("lost"))
            control.fan["on"] = False
            self.post.side_effect = OSError("stop failed")

        self.run_loop([(100, None), (100.5, disable)])
        self.post.assert_called_once_with(
            "http://fan.test/number/fan_level/set?value=0")
        self.assertEqual(control.fan["level"], 0)
        self.assertFalse(control.fan["connected"])
        self.assertFalse(control.fan["coasting"])
        self.assertIsNone(control.fan["disconnected_at"])
        self.assertEqual(control.fan["err"], "")
        self.assertFalse(control.fan_visible())

    def test_recording_stop_coasts_until_boundary_then_sends_zero(self):
        control.rec.update(active=True, game_power=200)

        def stop_recording():
            control.rec["active"] = False
            control.state["power"] = 0

        def check_coasting():
            self.assertTrue(control.fan["coasting"])
            self.assertEqual(control.fan["level"], 2)

        self.run_loop([(100, None), (101, stop_recording),
                       (105.999, check_coasting), (106, check_coasting)])
        self.assertEqual(self.post.call_args_list, [
            call("http://fan.test/button/kickr_ping/press"),
            call("http://fan.test/number/fan_level/set?value=0")])
        self.assertFalse(control.fan["coasting"])
        self.assertEqual(control.fan["level"], 0)

    def test_api_and_snapshot_expose_identical_visibility_at_boundary(self):
        control.fan_failed(OSError("offline"))
        for now, expected in ((9.999, True), (10.0, False)):
            with self.subTest(now=now):
                self.clock.monotonic.return_value = now
                api = self.request("/api").call_args.args[0]
                snapshot = telemetry.snapshot()
                self.assertIs(api["fan"]["visible"], expected)
                self.assertIs(snapshot["fan"]["visible"], expected)

    def test_http_fan_failure_starts_timer_and_invalidates_cache(self):
        self.post.side_effect = OSError("command failed")
        for now in (0.0, 9.0):
            self.clock.monotonic.return_value = now
            response = self.request("/fan", {"level": 3, "hold": True})
            response.assert_called_once_with(
                {"ok": False, "error": "OSError: command failed"}, 502)
            self.assertEqual(control.fan["disconnected_at"], 0.0)
            self.assertIsNone(control.fan["sent"])
            self.assertFalse(control.fan["connected"])
            self.assertEqual(control.fan["force_level"], 3)
            self.assertEqual(control.fan["mode"], "manual")
        self.assertEqual(self.post.call_args_list, [
            call("http://fan.test/number/fan_level/set?value=3")] * 2)
        self.clock.monotonic.return_value = 10.0
        self.assertFalse(control.fan_visible())

    def test_settings_host_change_or_disable_resets_disconnect_timer(self):
        for body in ({"fan_host": "http://replacement.test"}, {"fan_on": False}):
            with self.subTest(body=body):
                control.fan.update(on=True, connected=True, sent=2,
                                   disconnected_at=None)
                control.fan_failed(OSError("offline"))
                response = self.request("/settings", body)
                self.assertTrue(response.call_args.args[0]["ok"])
                self.assertIsNone(control.fan["disconnected_at"])
                self.assertFalse(control.fan_visible())
                if "fan_host" in body:
                    self.assertEqual(control.fan["host"], body["fan_host"])
                    self.assertFalse(control.fan["connected"])
                    self.assertIsNone(control.fan["sent"])


if __name__ == "__main__":
    unittest.main()
