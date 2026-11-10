"""Trainer packet parsing and freshness, without BLE or persistent writes."""
from copy import deepcopy
from itertools import product
import struct
import unittest
from unittest.mock import Mock, patch

# State imports otherwise create or migrate the application's ride directory.
with patch("os.makedirs"), patch("os.rename"):
    from wattze import telemetry
    from wattze.http_handler import Handler


def ftms(flags, power=-123, cadence=161, speed=2345):
    fields = {1: b"\x11\x22", 2: struct.pack("<H", cadence),
              3: b"\x33\x44", 4: b"\x55\x66\x77", 5: b"\x88\x99",
              6: struct.pack("<h", power), 7: bytes(2), 8: bytes(5),
              9: bytes(1), 10: bytes(1), 11: bytes(2), 12: bytes(2)}
    return (struct.pack("<H", flags) +
            (b"" if flags & 1 else struct.pack("<H", speed)) +
            b"".join(value for bit, value in fields.items() if flags & (1 << bit)))


def cpm(revs=None, ticks=0, flags=0, power=-123):
    if revs is not None:
        flags |= 1 << 5
    fields = {0: b"\x91", 2: b"\x92\x93", 4: b"\x94" * 6,
              5: struct.pack("<HH", revs or 0, ticks), 6: bytes(4),
              7: bytes(4), 8: bytes(3), 9: bytes(2), 10: bytes(2), 11: bytes(2)}
    return (struct.pack("<Hh", flags, power) +
            b"".join(value for bit, value in fields.items() if flags & (1 << bit)))


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        with telemetry.lock:
            saved = [(target, deepcopy(target)) for target in
                     (telemetry.state, telemetry.ride)]
            history = deepcopy(telemetry.history)
            cadence_at, crank = telemetry._cadence_at, telemetry._crank_sample

        def restore():
            with telemetry.lock:
                for target, value in saved:
                    target.clear()
                    target.update(value)
                telemetry.history[:] = history
                telemetry._cadence_at = cadence_at
                telemetry._crank_sample = crank

        self.addCleanup(restore)
        self.clock = Mock(monotonic=Mock(return_value=100.0),
                          time=Mock(return_value=1700000000.0))
        self.enterContext(patch.object(telemetry, "time", self.clock))
        self.push = self.enterContext(patch.object(telemetry, "push"))
        self.record = self.enterContext(patch.object(telemetry.trainer_diagnostics,
                                                   "record"))
        self.enterContext(patch.object(telemetry, "save_config"))
        telemetry.reset_trainer_telemetry()
        telemetry.ride.update(t0=None, samples=0, sum_p=0.0, max_p=0)
        telemetry.history.clear()

    def captured(self):
        return deepcopy((telemetry.state, telemetry.ride, telemetry.history,
                         telemetry._cadence_at, telemetry._crank_sample))

    def assert_rejected(self, callback, packet):
        before = self.captured()
        pushes, records = self.push.call_count, self.record.call_count
        callback(None, packet)
        self.assertEqual(self.captured(), before)
        self.assertEqual(self.push.call_count, pushes)
        self.assertEqual(self.record.call_count, records)

    def test_ftms_speed_present_or_omitted_and_zero_or_missing(self):
        for omitted in (0, 1):
            with self.subTest(omitted=omitted):
                self.assertEqual(telemetry.parse_ftms(ftms(omitted)),
                                 (None, None, None if omitted else 23.45))
                flags = omitted | (1 << 2) | (1 << 6)
                self.assertEqual(telemetry.parse_ftms(ftms(flags)),
                                 (-123, 80.5, None if omitted else 23.45))
                self.assertEqual(telemetry.parse_ftms(ftms(flags, 0, 0, 0)),
                                 (0, 0, None if omitted else 0))

    def test_ftms_all_preceding_offsets_and_trailing_lengths(self):
        for omitted, *present in product((0, 1), repeat=5):
            prefix = sum(value << bit for bit, value in zip((1, 3, 4, 5), present))
            for trailing in (0, *(1 << bit for bit in range(7, 13)),
                             sum(1 << bit for bit in range(7, 13))):
                flags = omitted | prefix | (1 << 2) | (1 << 6) | trailing
                with self.subTest(flags=flags):
                    packet = ftms(flags)
                    self.assertEqual(telemetry.parse_ftms(packet),
                                     (-123, 80.5, None if omitted else 23.45))
                    self.assertEqual(telemetry.parse_ftms(packet + b"\xff"),
                                     telemetry.parse_ftms(packet))

    def test_ftms_every_truncated_required_packet_is_rejected_atomically(self):
        telemetry.on_data(210, 82, 30)
        telemetry._crank_sample = (10, 1024, 100.0)
        # Even an expired cadence must not be changed by malformed input.
        self.clock.monotonic.return_value = 106.0
        for flags in (0, 1, *(1 << bit for bit in range(1, 13)), 0x1ffe, 0x1fff):
            packet = ftms(flags)
            for length in range(len(packet)):
                with self.subTest(flags=flags, length=length):
                    with self.assertRaises(ValueError):
                        telemetry.parse_ftms(packet[:length])
                    self.assert_rejected(telemetry.cb_ftms, packet[:length])

    def test_on_data_preserves_missing_metrics_and_counts_only_power(self):
        telemetry.on_data(None, 81, 25)
        self.assertEqual(telemetry.ride,
                         dict(t0=None, samples=0, sum_p=0.0, max_p=0))
        self.assertEqual(telemetry.history, [])
        telemetry.on_data(200, None, None)
        self.clock.time.return_value += 1
        telemetry.on_data(None, None, None)
        self.assertEqual((telemetry.state["power"], telemetry.state["cadence"],
                          telemetry.state["speed"]), (200, 81, 25))
        self.assertEqual(telemetry.state["updated"], 1700000001.0)
        self.assertEqual(telemetry.ride["samples"], 1)
        self.assertEqual(telemetry.history, [(1700000000.0, 200)])
        telemetry.on_data(0, 0, 0)
        self.assertEqual(telemetry.ride,
                         dict(t0=1700000000.0, samples=2, sum_p=200.0, max_p=200))
        self.assertEqual(telemetry.history[-1], (1700000001.0, 0))
        self.assertEqual(telemetry.snapshot()["avg"], 100)
        self.assertEqual((telemetry.state["power"], telemetry.state["cadence"],
                          telemetry.state["speed"]), (0, 0, 0))
        self.assertEqual(self.record.call_args_list, [(('sample',), {})] * 4)
        self.assertEqual(self.push.call_count, 4)
        self.assertEqual(self.push.call_args.args[0]["cadence"], 0)

    def test_cadence_expires_at_five_seconds_via_data_snapshot_and_api(self):
        handler = object.__new__(Handler)
        handler.path = "/api"
        handler._json = Mock()
        with patch("wattze.http_handler.time", self.clock), \
                patch.object(telemetry.trainer_diagnostics, "status", return_value={}):
            for route in ("on_data", "snapshot", "api"):
                with self.subTest(route=route):
                    self.clock.monotonic.return_value = 100.0
                    telemetry.on_data(200, 80, 25)
                    telemetry._crank_sample = (10, 1024, 100.0)

                    def read():
                        if route == "on_data":
                            telemetry.on_data(None, None, None)
                            return self.push.call_args.args[0]
                        if route == "snapshot":
                            return telemetry.snapshot()
                        Handler.do_GET(handler)
                        return handler._json.call_args.args[0]

                    self.clock.monotonic.return_value = 104.999
                    self.assertEqual(read()["cadence"], 80)
                    self.assertEqual(telemetry._cadence_at, 100.0)
                    self.clock.monotonic.return_value = 105.0
                    result = read()
                    self.assertEqual(result["cadence"], 0)
                    self.assertEqual((result["power"], result["speed"]), (200, 25))
                    self.assertIsNone(telemetry._cadence_at)
                    self.assertIsNone(telemetry._crank_sample)

    def test_reset_clears_metrics_freshness_and_crank_baseline(self):
        telemetry.on_data(200, 80, 25)
        telemetry.cb_cpm(None, cpm(10, 1024))
        ride, history = deepcopy(telemetry.ride), list(telemetry.history)
        telemetry.reset_trainer_telemetry()
        for key in ("power", "cadence", "speed", "updated"):
            self.assertEqual(telemetry.state[key], 0)
        self.assertIsNone(telemetry._cadence_at)
        self.assertIsNone(telemetry._crank_sample)
        self.assertEqual(telemetry.ride, ride)
        self.assertEqual(telemetry.history, history)
        telemetry.cb_cpm(None, cpm(12, 2560))
        self.assertEqual(telemetry.state["cadence"], 0)
        self.assertIsNone(telemetry._cadence_at)

    def test_cpm_optional_prefixes_metadata_and_trailing_fields(self):
        for present in product((0, 1), repeat=5):
            flags = sum(value << bit for bit, value in zip((0, 1, 2, 3, 4), present))
            for trailing in (0, *(1 << bit for bit in range(6, 12)), 0xfc0):
                with self.subTest(flags=flags, trailing=trailing):
                    telemetry.reset_trainer_telemetry()
                    telemetry.cb_cpm(None, cpm(10, 1024, flags | trailing))
                    self.assertEqual(telemetry.state["cadence"], 0)
                    self.assertIsNone(telemetry._cadence_at)
                    self.assertEqual(telemetry._crank_sample, (10, 1024, 100.0))
                    telemetry.cb_cpm(None, cpm(12, 2560, flags | trailing))
                    self.assertEqual(telemetry.state["cadence"], 80)
                    self.assertEqual(telemetry.state["power"], -123)

    def test_cpm_uint16_revolution_and_event_time_wraps(self):
        for revs, ticks in ((65535, 1024), (10, 65000), (65535, 65000)):
            with self.subTest(revs=revs, ticks=ticks):
                telemetry.reset_trainer_telemetry()
                telemetry.cb_cpm(None, cpm(revs, ticks))
                telemetry.cb_cpm(None, cpm((revs + 2) & 0xffff,
                                          (ticks + 1536) & 0xffff))
                self.assertEqual(telemetry.state["cadence"], 80)

    def test_cpm_duplicates_and_absent_crank_do_not_refresh_cadence(self):
        telemetry.cb_cpm(None, cpm(10, 1024))
        telemetry.cb_cpm(None, cpm(12, 2560))
        baseline = telemetry._crank_sample
        for now, packet in ((102.0, cpm(12, 2560)), (104.999, cpm(power=0))):
            self.clock.monotonic.return_value = now
            telemetry.cb_cpm(None, packet)
            self.assertEqual(telemetry.state["cadence"], 80)
            self.assertEqual(telemetry._cadence_at, 100.0)
            self.assertEqual(telemetry._crank_sample, baseline)
        self.assertEqual(telemetry.state["power"], 0)
        self.clock.monotonic.return_value = 105.0
        telemetry.cb_cpm(None, cpm())
        self.assertEqual(telemetry.state["cadence"], 0)
        self.assertIsNone(telemetry._crank_sample)

    def test_cpm_invalid_deltas_skip_cadence_and_recover_from_new_baseline(self):
        for revs, ticks in ((13, 2560), (12, 4096), (14, 2561), (1, 1)):
            with self.subTest(revs=revs, ticks=ticks):
                self.clock.monotonic.return_value = 100.0
                telemetry.reset_trainer_telemetry()
                telemetry.cb_cpm(None, cpm(10, 1024))
                telemetry.cb_cpm(None, cpm(12, 2560))
                self.clock.monotonic.return_value = 101.0
                telemetry.cb_cpm(None, cpm(revs, ticks))
                self.assertEqual(telemetry.state["cadence"], 80)
                self.assertEqual(telemetry._cadence_at, 100.0)
                self.assertEqual(telemetry._crank_sample, (revs, ticks, 101.0))
                self.clock.monotonic.return_value = 102.0
                telemetry.cb_cpm(None, cpm(revs + 2, ticks + 1536))
                self.assertEqual(telemetry.state["cadence"], 80)
                self.assertEqual(telemetry._cadence_at, 102.0)

    def test_cpm_gap_at_least_thirty_seconds_only_seeds_baseline(self):
        for gap in (30.0, 31.0):
            with self.subTest(gap=gap):
                telemetry.reset_trainer_telemetry()
                self.clock.monotonic.return_value = 100.0
                telemetry.cb_cpm(None, cpm(10, 1024))
                self.clock.monotonic.return_value += gap
                telemetry.cb_cpm(None, cpm(12, 2560))
                self.assertEqual(telemetry.state["cadence"], 0)
                self.assertIsNone(telemetry._cadence_at)
                self.assertEqual(telemetry._crank_sample, (12, 2560, 100.0 + gap))
                telemetry.cb_cpm(None, cpm(14, 4096))
                self.assertEqual(telemetry.state["cadence"], 80)

    def test_cpm_every_truncated_required_packet_is_rejected_atomically(self):
        telemetry.on_data(210, 82, 30)
        telemetry._crank_sample = (10, 1024, 100.0)
        self.clock.monotonic.return_value = 106.0
        for flags in (0, 2, 8, 10, *(1 << bit for bit in range(12)), 0xfff):
            packet = cpm(flags=flags)
            for length in range(len(packet)):
                with self.subTest(flags=flags, length=length):
                    self.assert_rejected(telemetry.cb_cpm, packet[:length])


if __name__ == "__main__":
    unittest.main()
