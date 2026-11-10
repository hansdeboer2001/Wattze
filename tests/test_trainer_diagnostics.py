"""Opt-in diagnostics tests using temporary files, without trainer hardware."""
from copy import deepcopy
from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, mock_open, patch

# Prevent state import from creating or migrating application data directories.
with patch("os.makedirs"), patch("os.rename"):
    from wattze import trainer_diagnostics as diagnostics
    from wattze.http_handler import Handler


class TrainerDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        diagnostics._queue.join()
        with diagnostics.lock:
            saved = [(target, deepcopy(target)) for target in
                     (diagnostics._status, diagnostics.state, diagnostics.ctrl,
                      diagnostics.settings, diagnostics.gear)]
            started, sampled = diagnostics._started, diagnostics._sampled
            # Keep session tokens unique while the daemon survives between tests.

        def restore():
            with diagnostics.lock:
                for target, value in saved:
                    target.clear()
                    target.update(value)
                diagnostics._started = started
                diagnostics._sampled = sampled

        self.addCleanup(restore)
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "diagnostics.jsonl"
        self.enterContext(patch.object(diagnostics, "LOG_PATH", str(self.path)))
        self.clock = Mock(monotonic=Mock(return_value=100.0),
                          time=Mock(return_value=1700000000.0))
        self.enterContext(patch.object(diagnostics, "time", self.clock))
        # Cleanups run LIFO: drain before restoring patches or deleting the file.
        self.addCleanup(diagnostics._queue.join)
        with diagnostics.lock:
            diagnostics._status.update(active=False, error="", bytes=0)
            diagnostics._started = diagnostics._sampled = 0.0
            diagnostics.state.update(
                connected=True, power=245, cadence=88.5, speed=32.25,
                device="PRIVATE_DEVICE", kickr_name="PRIVATE_TRAINER",
                kickr_addr="AA:BB:CC:DD:EE:FF", hr_name="PRIVATE_HR",
                hr_addr="11:22:33:44:55:66")
            diagnostics.settings.update(route_feel=75, username="PRIVATE_USER")
            diagnostics.gear.update(v=9, max=24)
            diagnostics.ctrl.update(
                want_sim=True, want_erg=False, sim_active=True, erg_active=False,
                target_grade=3.5, target_power=200,
                sim_command={"grade": 3.5, "t": 1699999999.0,
                             "status": "confirmed", "result": 1})

    def rows(self, check_bytes=True):
        diagnostics._queue.join()
        raw = self.path.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"\r", raw)
        if check_bytes:
            self.assertEqual(diagnostics.status()["bytes"], len(raw))
        return [json.loads(line) for line in raw.decode("utf-8").splitlines()]

    def assert_disabled(self):
        diagnostics._queue.join()
        result = diagnostics.status()
        self.assertFalse(result["active"])
        self.assertTrue(result["error"])
        before = self.path.read_bytes() if self.path.exists() else None
        diagnostics.record("ignored")
        diagnostics.stop()
        diagnostics._queue.join()
        self.assertEqual(self.path.read_bytes() if self.path.exists() else None,
                         before)

    @contextmanager
    def blocked_first_write(self, fail=False):
        entered = threading.Event()
        release = threading.Event()
        writes = []
        real_open = open

        @contextmanager
        def delayed_open(*args, **kwargs):
            with real_open(*args, **kwargs) as output:
                def write(line):
                    if not entered.is_set():
                        entered.set()
                        release.wait()
                        if fail:
                            raise OSError("old session failed")
                    writes.append(json.loads(line)["event"])
                    return output.write(line)

                yield Mock(write=write)

        with patch.object(diagnostics, "open", delayed_open, create=True):
            try:
                yield entered, release, writes
            finally:
                release.set()
                diagnostics._queue.join()

    def test_blocked_disk_write_does_not_block_record_or_state_lock(self):
        with self.blocked_first_write() as (entered, release, writes):
            diagnostics.start()
            self.assertTrue(entered.wait(2), "worker did not reach disk write")
            finished = threading.Event()

            def record():
                diagnostics.record("sample")
                finished.set()

            producer = threading.Thread(target=record, daemon=True)
            producer.start()
            try:
                self.assertTrue(finished.wait(1), "record waited for disk I/O")
                acquired = diagnostics.lock.acquire(timeout=1)
                try:
                    self.assertTrue(acquired, "disk write held the state lock")
                    queued = dict(diagnostics._status)
                finally:
                    if acquired:
                        diagnostics.lock.release()
                self.assertTrue(queued["active"])
                self.assertGreater(queued["bytes"], 0)
                self.assertEqual(writes, [])
                self.assertFalse(release.is_set())
            finally:
                release.set()
                producer.join(2)
        self.assertFalse(producer.is_alive())
        self.assertEqual(writes, ["start", "sample"])
        self.assertEqual([row["event"] for row in self.rows()], writes)

    def test_bounded_queue_full_disables_without_enqueuing_more(self):
        self.assertGreater(diagnostics._queue.maxsize, 0)
        with self.blocked_first_write() as (entered, release, writes):
            diagnostics.start()
            self.assertTrue(entered.wait(2), "worker did not reach disk write")
            for _ in range(diagnostics._queue.maxsize):
                diagnostics.record("queued")
            self.assertTrue(diagnostics._queue.full())
            before = diagnostics.status()
            self.assertTrue(before["active"])
            diagnostics.record("overflow")
            result = diagnostics.status()
            self.assertFalse(result["active"])
            self.assertIn("wachtrij vol", result["error"])
            self.assertEqual(result["bytes"], before["bytes"])
            diagnostics.record("ignored")
            diagnostics.stop()
            self.assertEqual(diagnostics._queue.qsize(), diagnostics._queue.maxsize)
            self.assertFalse(release.is_set())
        self.assertEqual(writes, ["start"] + ["queued"] * diagnostics._queue.maxsize)
        self.assertEqual([row["event"] for row in self.rows()], writes)
        self.assert_disabled()

    def test_stop_then_new_start_preserves_queued_write_order(self):
        with self.blocked_first_write() as (entered, release, writes):
            diagnostics.start()
            self.assertTrue(entered.wait(2), "worker did not reach disk write")
            diagnostics.record("old")
            self.assertFalse(diagnostics.stop()["active"])
            old_session = diagnostics._session
            self.clock.monotonic.return_value = 200.0
            self.assertTrue(diagnostics.start()["active"])
            self.assertGreater(diagnostics._session, old_session)
            diagnostics.record("new")
            self.assertFalse(release.is_set())
        self.assertEqual(writes, ["start", "old", "stop", "start", "new"])
        self.assertEqual([row["event"] for row in self.rows()], ["start", "new"])
        self.assertTrue(diagnostics.status()["active"])

    def test_old_session_write_failure_does_not_disable_new_session(self):
        with self.blocked_first_write(fail=True) as (entered, release, writes):
            diagnostics.start()
            self.assertTrue(entered.wait(2), "worker did not reach disk write")
            diagnostics.record("old")
            diagnostics.stop()
            old_session = diagnostics._session
            self.assertTrue(diagnostics.start()["active"])
            self.assertGreater(diagnostics._session, old_session)
            diagnostics.record("new")
            self.assertFalse(release.is_set())
        self.assertTrue(diagnostics.status()["active"])
        self.assertEqual(diagnostics.status()["error"], "")
        self.assertEqual(writes, ["start", "new"])
        self.assertEqual([row["event"] for row in self.rows()], writes)

    def test_disabled_creates_no_files_and_status_is_a_copy(self):
        result = diagnostics.status()
        result["active"] = True
        diagnostics.record("sample")
        diagnostics.record("grade_requested", source={"route_grade": 3})
        self.assertEqual(diagnostics.stop(),
                         {"active": False, "error": "", "bytes": 0})
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_start_snapshot_stop_contains_exact_telemetry_without_private_data(self):
        shared_before = deepcopy((diagnostics.state, diagnostics.ctrl,
                                  diagnostics.settings, diagnostics.gear))
        self.assertTrue(diagnostics.start()["active"])
        self.clock.monotonic.return_value = 101.2344
        diagnostics.record("sample")
        self.clock.monotonic.return_value = 102.0
        self.assertFalse(diagnostics.stop()["active"])
        rows = self.rows()
        self.assertEqual([row["event"] for row in rows],
                         ["start", "sample", "stop"])
        expected = {
            "t": 1700000000.0, "elapsed": 1.234, "event": "sample",
            "connected": True, "gear": 9, "gear_max": 24,
            "route_feel_setting": 75, "power": 245, "cadence": 88.5,
            "speed": 32.25, "want_sim": True, "want_erg": False,
            "sim_active": True, "erg_active": False, "target_grade": 3.5,
            "target_power": 200,
            "sim_command": {"grade": 3.5, "t": 1699999999.0,
                            "status": "confirmed", "result": 1},
        }
        self.assertEqual(rows[1], expected)
        self.assertEqual(rows[0], dict(expected, event="start", elapsed=0.0))
        self.assertEqual(rows[2], dict(expected, event="stop", elapsed=2.0))
        raw = self.path.read_text(encoding="utf-8")
        for secret in ("PRIVATE_", "AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"):
            self.assertNotIn(secret, raw)
        self.assertEqual((diagnostics.state, diagnostics.ctrl,
                          diagnostics.settings, diagnostics.gear), shared_before)

    def test_repeated_start_preserves_file_clocks_and_status(self):
        diagnostics.start()
        self.clock.monotonic.return_value = 101.0
        diagnostics.record("sample")
        diagnostics._queue.join()
        before = self.path.read_bytes()
        status = diagnostics.status()
        self.clock.monotonic.return_value = 105.0
        self.assertEqual(diagnostics.start(), status)
        diagnostics._queue.join()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(diagnostics._started, 100.0)
        self.assertEqual(diagnostics._sampled, 101.0)

    def test_stop_when_already_disabled_does_not_append(self):
        diagnostics.start()
        stopped = diagnostics.stop()
        diagnostics._queue.join()
        before = self.path.read_bytes()
        self.assertEqual(diagnostics.stop(), stopped)
        diagnostics.record("sample")
        diagnostics._queue.join()
        self.assertEqual(self.path.read_bytes(), before)

    def test_new_session_after_stop_resets_file_and_sampling(self):
        diagnostics.start()
        diagnostics.record("sample")
        diagnostics.stop()
        self.clock.monotonic.return_value = 200.0
        self.assertTrue(diagnostics.start()["active"])
        self.assertEqual(diagnostics._started, 200.0)
        self.assertEqual(diagnostics._sampled, 0.0)
        self.assertEqual([row["event"] for row in self.rows()], ["start"])

    def test_missing_route_feel_defaults_to_one_hundred(self):
        diagnostics.settings.pop("route_feel", None)
        diagnostics.start()
        self.assertEqual(self.rows()[0]["route_feel_setting"], 100)

    def test_grade_source_retains_only_whitelisted_finite_numbers(self):
        expected = {"route_grade": -3.25, "route_feel": 75,
                    "gear": 9, "gear_max": 24, "gear_offset": 0}
        body = {"diagnostic": dict(expected, token="SECRET", name="SECRET",
                                   address="SECRET", grade=18),
                "token": "SECRET"}
        before = deepcopy(body)
        source = diagnostics.grade_source(body)
        self.assertEqual(source, expected)
        self.assertEqual(body, before)
        diagnostics.start()
        diagnostics.record("grade_requested", source=source)
        self.assertEqual(self.rows()[-1]["source"], expected)
        self.assertNotIn("SECRET", self.path.read_text(encoding="utf-8"))

    def test_grade_source_excludes_invalid_values_for_every_whitelisted_key(self):
        for key in ("route_grade", "route_feel", "gear", "gear_max", "gear_offset"):
            for value in (True, False, float("nan"), float("inf"),
                          -float("inf"), 10 ** 1000, None, "3.5", [], {}, [1]):
                with self.subTest(key=key, value=value):
                    self.assertEqual(diagnostics.grade_source(
                        {"diagnostic": {key: value}}), {})

    def test_grade_source_handles_missing_or_malformed_diagnostic(self):
        self.assertEqual(diagnostics.grade_source({}), {})
        for value in (None, True, 1, 2.5, "bad", [], [1]):
            with self.subTest(value=value):
                self.assertEqual(diagnostics.grade_source({"diagnostic": value}), {})

    def test_sample_throttles_at_one_hz_but_other_events_are_not_throttled(self):
        diagnostics.start()
        for now in (100.0, 100.1, 100.999, 101.0, 101.999, 102.0):
            self.clock.monotonic.return_value = now
            diagnostics.record("sample")
            diagnostics.record("ftms_write", opcode=17)
        rows = self.rows()
        self.assertEqual([row["elapsed"] for row in rows
                          if row["event"] == "sample"], [0.0, 1.0, 2.0])
        self.assertEqual(sum(row["event"] == "ftms_write" for row in rows), 6)

    def test_time_limit_disables_at_exact_boundary_even_for_throttled_sample(self):
        diagnostics.start()
        self.clock.monotonic.return_value = 100.0 + diagnostics.MAX_SECONDS - 0.001
        diagnostics.record("sample")
        diagnostics._queue.join()
        before = self.path.read_bytes()
        self.clock.monotonic.return_value = 100.0 + diagnostics.MAX_SECONDS
        diagnostics.record("sample")
        self.assert_disabled()
        self.assertEqual(self.path.read_bytes(), before)

    def test_status_expires_idle_session_without_telemetry(self):
        diagnostics.start()
        diagnostics._queue.join()
        before = self.path.read_bytes()
        self.clock.monotonic.return_value += diagnostics.MAX_SECONDS
        self.assertFalse(diagnostics.status()["active"])
        self.assertIn("30 minuten", diagnostics.status()["error"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_size_limit_allows_exact_fit_then_disables(self):
        diagnostics.start()
        diagnostics._queue.join()
        size = len(self.path.read_bytes())
        with patch.object(diagnostics, "MAX_BYTES", size * 2):
            diagnostics.record("start")
            self.assertTrue(diagnostics.status()["active"])
            diagnostics._queue.join()
            self.assertEqual(self.path.stat().st_size, size * 2)
            diagnostics.record("start")
            self.assert_disabled()
        self.assertEqual(len(self.rows()), 2)

    def test_size_limit_rejects_whole_row_before_overflow(self):
        diagnostics.start()
        diagnostics._queue.join()
        before = self.path.read_bytes()
        with patch.object(diagnostics, "MAX_BYTES", len(before) * 2 - 1):
            diagnostics.record("start")
            self.assert_disabled()
        self.assertEqual(self.path.read_bytes(), before)
        self.rows()

    def test_start_open_failure_is_bounded_and_fail_closed(self):
        with patch("builtins.open", side_effect=OSError("x" * 250)):
            diagnostics.start()
            diagnostics._queue.join()
            result = diagnostics.status()
        self.assertEqual(result["error"], "x" * 200)
        self.assertGreater(result["bytes"], 0)
        self.assert_disabled()
        self.assertFalse(self.path.exists())

    def test_initial_record_write_failure_is_fail_closed(self):
        output = mock_open()
        output.return_value.write.side_effect = OSError("write failed")
        with patch("builtins.open", output):
            diagnostics.start()
            diagnostics._queue.join()
        output.assert_called_once_with(str(self.path), "w", encoding="utf-8", newline="\n")
        output.return_value.write.assert_called_once()
        self.assertEqual(diagnostics.status()["error"], "write failed")
        self.assert_disabled()
        self.assertFalse(self.path.exists())

    def test_record_open_and_write_failures_disable_without_raising(self):
        for stage in ("open", "write"):
            with self.subTest(stage=stage):
                diagnostics.start()
                diagnostics._queue.join()
                before = self.path.read_bytes()
                queued_before = diagnostics.status()["bytes"]
                output = mock_open()
                if stage == "open":
                    output.side_effect = OSError("open failed")
                else:
                    output.return_value.write.side_effect = OSError("write failed")
                with patch("builtins.open", output):
                    diagnostics.record("sample")
                    diagnostics._queue.join()
                self.assertEqual(diagnostics.status()["error"], stage + " failed")
                self.assertGreater(diagnostics.status()["bytes"], queued_before)
                self.assert_disabled()
                self.assertEqual(self.path.read_bytes(), before)
                self.rows(check_bytes=False)

    def test_unserializable_or_nonfinite_details_disable_without_raising(self):
        for value in (object(), float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                diagnostics.start()
                diagnostics._queue.join()
                before = self.path.read_bytes()
                diagnostics.record("bad", value=value)
                self.assert_disabled()
                self.assertEqual(self.path.read_bytes(), before)
                self.rows()

    def request(self, path, body=None, raw=None):
        handler = object.__new__(Handler)
        handler.path = path
        handler._json = Mock()
        if body is None and raw is None:
            handler.do_GET()
        else:
            payload = raw if raw is not None else json.dumps(body).encode("utf-8")
            handler.rfile = BytesIO(payload)
            handler.headers = {"Content-Length": str(len(payload))}
            handler.do_POST()
        handler._json.assert_called_once()
        return handler._json.call_args

    def test_http_start_api_status_and_stop(self):
        response = self.request("/trainer-diagnostics", {"cmd": "start"})
        self.assertEqual(response.args, ({"ok": True, **diagnostics.status()},))
        self.assertTrue(diagnostics.status()["active"])
        response = self.request("/api")
        self.assertEqual(response.args[0]["trainer_diagnostics"], diagnostics.status())
        response = self.request("/trainer-diagnostics", {"cmd": "stop"})
        self.assertEqual(response.args, ({"ok": True, **diagnostics.status()},))
        self.assertFalse(diagnostics.status()["active"])
        self.assertEqual([row["event"] for row in self.rows()], ["start", "stop"])

    def test_http_rejects_bad_json_and_invalid_commands_without_files(self):
        for raw in (b"{", b"[]", b"null", b"true", b"{}",
                    b'{"cmd":"unknown"}', b'{"cmd":1}'):
            with self.subTest(raw=raw):
                response = self.request("/trainer-diagnostics", raw=raw)
                self.assertFalse(response.args[0]["ok"])
                self.assertEqual(response.args[1], 400)
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_http_grade_records_filtered_source_and_control_event(self):
        diagnostics.start()
        response = self.request("/grade", {
            "grade": 4.25, "diagnostic": {"route_grade": 5, "gear": 9,
                                          "token": "SECRET", "gear_offset": True},
            "name": "SECRET", "address": "SECRET"})
        self.assertEqual(response.args, ({"ok": True, "target_grade": 4.25},))
        row = self.rows()[-1]
        self.assertEqual(row["event"], "grade_requested")
        self.assertEqual(row["source"], {"route_grade": 5, "gear": 9})
        self.assertEqual(row["target_grade"], 4.25)
        self.request("/control", {"cmd": "start_erg", "token": "SECRET"})
        row = self.rows()[-1]
        self.assertEqual(row["event"], "control")
        self.assertEqual(row["command"], "start_erg")
        self.assertTrue(row["want_erg"])
        self.assertFalse(row["want_sim"])
        self.assertNotIn("SECRET", self.path.read_text(encoding="utf-8"))

    def test_http_start_io_failure_is_reported_by_later_poll(self):
        with patch("builtins.open", side_effect=OSError("no access")):
            response = self.request("/trainer-diagnostics", {"cmd": "start"})
            immediate = response.args[0]
            self.assertEqual(immediate["ok"], immediate["active"])
            self.assertIn(immediate["error"], ("", "no access"))
            diagnostics._queue.join()
        polled = self.request("/api").args[0]["trainer_diagnostics"]
        self.assertFalse(polled["active"])
        self.assertEqual(polled["error"], "no access")
        self.assertGreater(polled["bytes"], 0)
        self.assert_disabled()


if __name__ == "__main__":
    unittest.main()
