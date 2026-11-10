"""Keep-awake transitions without real power, BLE, sleeps or persistence."""
import ctypes
import threading
import unittest
from unittest.mock import Mock, call, patch

# State imports otherwise create or migrate the application's ride directory.
with patch("os.makedirs"), patch("os.rename"):
    from wattze import keep_awake, rides


class StepStop:
    """Run one callback after each poll, then stop when steps are exhausted."""

    def __init__(self, *steps, stopped=False):
        self.steps = iter(steps)
        self.stopped = stopped
        self.waits = []

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.waits.append(timeout)
        try:
            step = next(self.steps)
        except StopIteration:
            self.stopped = True
        else:
            if step is not None:
                step()
        return self.stopped


class KeepAwakeTests(unittest.TestCase):
    def setUp(self):
        with keep_awake.lock:
            saved = keep_awake.rec.copy()
            keep_awake.rec.update(active=False, paused=False, samples=[])

        def restore():
            with keep_awake.lock:
                keep_awake.rec.clear()
                keep_awake.rec.update(saved)

        self.addCleanup(restore)
        self.enterContext(patch.object(keep_awake.sys, "platform", "win32"))
        self.print = self.enterContext(patch("builtins.print"))
        self.api = Mock(return_value=1)
        self.dll = self.enterContext(patch.object(
            keep_awake.ctypes, "WinDLL", create=True,
            return_value=Mock(SetThreadExecutionState=self.api)))
        self.last_error = self.enterContext(patch.object(
            keep_awake.ctypes, "get_last_error", create=True, return_value=5))
        self.win_error = self.enterContext(patch.object(
            keep_awake.ctypes, "WinError", create=True,
            side_effect=lambda code: OSError(code, "mock power failure")))
        self.open = self.enterContext(patch(
            "builtins.open", side_effect=AssertionError("unexpected persistence")))
        self.load_index = self.enterContext(patch.object(rides, "load_index"))
        self.save_index = self.enterContext(patch.object(rides, "save_index"))

    def test_real_ride_transitions_only_call_api_on_active_changes(self):
        def check(active, paused, calls):
            self.assertEqual(keep_awake.rec["active"], active)
            self.assertEqual(keep_awake.rec["paused"], paused)
            self.assertEqual(self.api.call_args_list, calls)

        def start():
            check(False, False, [])
            rides.rec_start("keep awake test", "rit")
            # Four samples exercise the real short-ride, non-persistence path.
            keep_awake.rec["samples"] = [(0, 100, 80, 5, 0, 0, 0)] * 4

        def pause():
            check(True, False, [call(0x80000003)])
            self.assertTrue(rides.rec_pause())

        def resume():
            check(True, True, [call(0x80000003)])
            self.assertTrue(rides.rec_resume())

        def stop_ride():
            check(True, False, [call(0x80000003)])
            self.assertIsNone(rides.rec_stop())

        stop = StepStop(
            None, start, None, pause, None, resume, None, stop_ride, None)
        keep_awake.keep_awake_loop(stop)

        check(False, False, [call(0x80000003), call(0x80000000)])
        self.assertEqual(stop.waits, [1.0] * 10)
        self.dll.assert_called_once_with("kernel32", use_last_error=True)
        self.assertEqual(self.api.argtypes, [ctypes.c_uint])
        self.assertIs(self.api.restype, ctypes.c_uint)
        self.print.assert_not_called()
        self.open.assert_not_called()
        self.load_index.assert_not_called()
        self.save_index.assert_not_called()

    def test_stop_while_active_releases_in_finally(self):
        rides.rec_start("stop active", "rit")
        keep_awake.keep_awake_loop(StepStop(None, None))
        self.assertTrue(keep_awake.rec["active"])
        self.assertEqual(self.api.call_args_list,
                         [call(0x80000003), call(0x80000000)])
        self.print.assert_not_called()

    def test_stop_before_start_does_not_call_execution_state(self):
        rides.rec_start("already stopped", "rit")
        stop = StepStop(stopped=True)
        keep_awake.keep_awake_loop(stop)
        self.api.assert_not_called()
        self.assertEqual(stop.waits, [])

    def test_failed_activation_retries_and_reports_once(self):
        for failure in (0, OSError("activation failed")):
            with self.subTest(failure=failure):
                self.api.reset_mock()
                self.print.reset_mock()
                self.api.side_effect = [failure, failure, 1, 1]
                rides.rec_start("retry activation", "rit")
                keep_awake.keep_awake_loop(StepStop(None, None, None))
                self.assertEqual(self.api.call_args_list,
                                 [call(0x80000003)] * 3 + [call(0x80000000)])
                self.print.assert_called_once()
                self.assertIn("wordt opnieuw geprobeerd",
                              self.print.call_args.args[0])
                self.assertEqual(self.print.call_args.kwargs, {"flush": True})
        self.assertEqual(self.last_error.call_count, 2)
        self.assertEqual(self.win_error.call_args_list, [call(5), call(5)])

    def test_failed_release_retries_and_reports_once(self):
        for failure in (0, OSError("release failed")):
            with self.subTest(failure=failure):
                self.api.reset_mock()
                self.print.reset_mock()
                self.api.side_effect = [1, failure, failure, 1]
                rides.rec_start("retry release", "rit")
                keep_awake.keep_awake_loop(
                    StepStop(rides.rec_stop, None, None, None))
                self.assertEqual(self.api.call_args_list,
                                 [call(0x80000003)] + [call(0x80000000)] * 3)
                self.print.assert_called_once()
                self.assertIn("wordt opnieuw geprobeerd",
                              self.print.call_args.args[0])

    def test_success_resets_failure_reporting(self):
        rides.rec_start("separate failures", "rit")
        self.api.side_effect = [0, 1, 0, 1]
        keep_awake.keep_awake_loop(StepStop(None, rides.rec_stop, None))
        self.assertEqual(self.api.call_args_list,
                         [call(0x80000003)] * 2 + [call(0x80000000)] * 2)
        self.assertEqual(self.print.call_count, 2)

    def test_failed_activation_never_releases_unacquired_state(self):
        rides.rec_start("never acquired", "rit")
        self.api.return_value = 0
        keep_awake.keep_awake_loop(StepStop(None))
        self.assertEqual(self.api.call_args_list, [call(0x80000003)] * 2)
        self.print.assert_called_once()

    def test_cleanup_failure_is_logged_without_escaping(self):
        for failure in (0, OSError("cleanup failed")):
            with self.subTest(failure=failure):
                self.api.reset_mock()
                self.print.reset_mock()
                self.api.side_effect = [1, failure]
                rides.rec_start("cleanup failure", "rit")
                keep_awake.keep_awake_loop(StepStop())
                self.assertEqual(self.api.call_args_list,
                                 [call(0x80000003), call(0x80000000)])
                self.print.assert_called_once()
                self.assertIn("Vrijgeven schermwakkerhouding mislukt",
                              self.print.call_args.args[0])
                self.assertEqual(self.print.call_args.kwargs, {"flush": True})

    def test_unavailable_dll_or_api_returns_gracefully(self):
        for error in (OSError("no DLL"), AttributeError("no API")):
            with self.subTest(error=error):
                self.print.reset_mock()
                self.dll.side_effect = error
                stop = StepStop()
                keep_awake.keep_awake_loop(stop)
                self.api.assert_not_called()
                self.assertEqual(stop.waits, [])
                self.print.assert_called_once()
                self.assertIn("niet beschikbaar", self.print.call_args.args[0])
                self.assertEqual(self.print.call_args.kwargs, {"flush": True})

    def test_non_windows_does_not_load_dll(self):
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                with patch.object(keep_awake.sys, "platform", platform):
                    keep_awake.keep_awake_loop(StepStop())
        self.dll.assert_not_called()
        self.api.assert_not_called()
        self.print.assert_not_called()

    def test_event_stop_releases_on_same_worker_thread(self):
        stop = threading.Event()
        activated = threading.Event()
        calls = []
        errors = []

        def set_state(flags):
            calls.append((threading.get_ident(), flags))
            if flags == 0x80000003:
                activated.set()
            return 1

        def run():
            try:
                keep_awake.keep_awake_loop(stop)
            except BaseException as exc:
                errors.append(exc)

        rides.rec_start("worker ownership", "rit")
        self.api.side_effect = set_state
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        try:
            self.assertTrue(activated.wait(5), "worker did not activate")
        finally:
            stop.set()
            worker.join(5)
        self.assertFalse(worker.is_alive(), "worker did not stop")
        self.assertEqual(errors, [])
        self.assertNotEqual(worker.ident, threading.get_ident())
        self.assertEqual(calls, [(worker.ident, 0x80000003),
                                 (worker.ident, 0x80000000)])
        self.print.assert_not_called()


if __name__ == "__main__":
    unittest.main()
