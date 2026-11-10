"""SIM command diagnostics, without BLE hardware or persistent writes."""
import asyncio
from copy import deepcopy
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# state has directory creation/migration side effects on its first import.
with patch("os.makedirs"), patch("os.rename"):
    from wattze import ble_wattze as ble


class TrainerGradeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with ble.lock:
            saved_ctrl = deepcopy(ble.ctrl)
            saved_state = deepcopy(ble.state)
            saved_rebind = deepcopy(ble.rebind)

        def restore_state():
            with ble.lock:
                for target, saved in ((ble.ctrl, saved_ctrl),
                                      (ble.state, saved_state),
                                      (ble.rebind, saved_rebind)):
                    target.clear()
                    target.update(saved)

        self.addCleanup(restore_state)
        for name, value in (("cp_evt", asyncio.Event()), ("cp_result", None),
                            ("wahoo_evt", None)):
            patcher = patch.object(ble, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.previous_command = {"grade": 9.0, "t": 10.0,
                                 "status": "confirmed", "result": 1}
        with ble.lock:
            ble.ctrl.update(sim_command=deepcopy(self.previous_command),
                            last_sent=7.25, last_sent_t=123.5)
        self.client = SimpleNamespace(write_gatt_char=AsyncMock())

    def assert_control_unchanged(self):
        self.assertEqual(ble.ctrl["last_sent"], 7.25)
        self.assertEqual(ble.ctrl["last_sent_t"], 123.5)

    def assert_write(self, payload):
        self.client.write_gatt_char.assert_awaited_once_with(
            ble.CP_UUID, bytearray(payload), response=True)
        self.assertIsInstance(self.client.write_gatt_char.await_args.args[1], bytearray)
        self.assert_control_unchanged()

    async def respond(self, payload, response):
        self.client.write_gatt_char.reset_mock()

        async def write(*args, **kwargs):
            ble.cb_cp(None, response)

        self.client.write_gatt_char.side_effect = write
        result = await ble.cp_request(self.client, payload, timeout=0.1)
        self.assert_write(payload)
        return result

    def test_sim_payload_clamp_rounding_and_real_zero(self):
        for grade, encoded in ((-12, -1000), (18, 1600),
                               (1.234, 123), (1.236, 124),
                               (-1.236, -124), (0, 0)):
            with self.subTest(grade=grade):
                payload = ble.sim_payload(grade)
                self.assertEqual(payload, b"\x11\x00\x00" +
                                 struct.pack("<h", encoded) + b"\x28\x33")
                self.assertEqual(struct.unpack_from("<h", payload, 3)[0], encoded)
        self.assert_control_unchanged()

    async def test_writing_pending_then_confirmed_without_control_changes(self):
        payload = ble.sim_payload(18)
        original = bytes(payload)
        write_started = asyncio.Event()
        release_write = asyncio.Event()
        response_waiting = asyncio.Event()
        event = asyncio.Event()

        async def wait_for_response():
            response_waiting.set()
            await event.wait()

        ble.cp_evt = SimpleNamespace(clear=event.clear, set=event.set,
                                     wait=wait_for_response)
        ble.cp_result = (0x11, 5)
        event.set()

        async def write(*args, **kwargs):
            self.assertEqual(ble.ctrl["sim_command"],
                             {"grade": None, "t": None, "status": "writing"})
            self.assertIsNone(ble.cp_result)
            self.assertFalse(event.is_set())
            self.assert_control_unchanged()
            write_started.set()
            await release_write.wait()

        self.client.write_gatt_char.side_effect = write
        task = asyncio.create_task(ble.cp_request(self.client, payload, timeout=1))

        async def cancel_task():
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(cancel_task)
        await asyncio.wait_for(write_started.wait(), 1)
        self.assertFalse(task.done())
        release_write.set()
        await asyncio.wait_for(response_waiting.wait(), 1)
        pending = dict(ble.ctrl["sim_command"])
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["grade"], 16.0)
        self.assertIsInstance(pending["t"], float)
        self.assertFalse(task.done())
        self.assert_control_unchanged()
        ble.cb_cp(None, b"\x80\x11\x01")
        self.assertEqual(await asyncio.wait_for(task, 1), (0x11, 1))
        self.assertEqual(ble.ctrl["sim_command"],
                         dict(pending, status="confirmed", result=1))
        self.assertEqual(payload, original)
        self.assert_write(original)

    async def test_zero_is_confirmed_as_zero_not_missing(self):
        self.assertEqual(await self.respond(ble.sim_payload(0), b"\x80\x11\x01"),
                         (0x11, 1))
        command = ble.ctrl["sim_command"]
        self.assertEqual(command["grade"], 0.0)
        self.assertIsNotNone(command["grade"])
        self.assertEqual(command["status"], "confirmed")
        self.assertEqual(command["result"], 1)

    async def test_rejected_code_five_keeps_encoded_grade(self):
        self.assertEqual(await self.respond(ble.sim_payload(-12), b"\x80\x11\x05"),
                         (0x11, 5))
        command = ble.ctrl["sim_command"]
        self.assertEqual(command["grade"], -10.0)
        self.assertEqual(command["status"], "rejected")
        self.assertEqual(command["result"], 5)
        self.assertIsInstance(command["t"], float)

    async def test_timeout_preserves_sent_grade_and_timestamp(self):
        payload = ble.sim_payload(1.236)
        self.assertIsNone(await ble.cp_request(self.client, payload, timeout=0.01))
        command = ble.ctrl["sim_command"]
        self.assertEqual(command["grade"], 1.24)
        self.assertEqual(command["status"], "timeout")
        self.assertIsInstance(command["t"], float)
        self.assertNotIn("result", command)
        self.assert_write(payload)

    async def test_wrong_opcode_is_unconfirmed_but_return_is_preserved(self):
        result = await self.respond(ble.sim_payload(3), b"\x80\x05\x01")
        self.assertEqual(result, (0x05, 1))
        command = ble.ctrl["sim_command"]
        self.assertEqual(command["status"], "unconfirmed")
        self.assertEqual(command["grade"], 3.0)
        self.assertIsNone(command["result"])

    async def test_malformed_response_is_unconfirmed(self):
        for response in (b"", b"\x80", b"\x80\x11", b"\x81\x11\x01"):
            with self.subTest(response=response):
                ble.cp_result = (0x11, 1)
                self.assertIsNone(await self.respond(ble.sim_payload(2), response))
                self.assertIsNone(ble.cp_result)
                command = ble.ctrl["sim_command"]
                self.assertEqual(command["status"], "unconfirmed")
                self.assertEqual(command["grade"], 2.0)
                self.assertIsNone(command["result"])

    async def test_write_error_clears_old_grade_and_reraises(self):
        error = OSError("fake BLE write failure")
        self.client.write_gatt_char.side_effect = error
        payload = ble.sim_payload(4)
        with self.assertRaises(OSError) as raised:
            await ble.cp_request(self.client, payload)
        self.assertIs(raised.exception, error)
        self.assertEqual(ble.ctrl["sim_command"],
                         {"grade": None, "t": None, "status": "write_error"})
        self.assert_write(payload)

    async def test_non_sim_commands_leave_diagnostics_unchanged(self):
        for payload in ([0x00], [0x07], [0x08, 0x01],
                        ble.erg_payload_ftms(200), b"\x11", b"\x11" + bytes(7)):
            with self.subTest(payload=payload):
                previous = ble.ctrl["sim_command"]
                self.assertEqual(await self.respond(payload, bytes([0x80, payload[0], 1])),
                                 (payload[0], 1))
                self.assertIs(ble.ctrl["sim_command"], previous)
                self.assertEqual(previous, self.previous_command)

    async def test_non_sim_timeout_and_write_error_leave_diagnostics_unchanged(self):
        previous = ble.ctrl["sim_command"]
        self.assertIsNone(await ble.cp_request(self.client, [0x00], timeout=0.01))
        self.assertIs(ble.ctrl["sim_command"], previous)
        self.assert_write([0x00])
        self.client.write_gatt_char.reset_mock()
        self.client.write_gatt_char.side_effect = OSError("fake write failure")
        with self.assertRaises(OSError):
            await ble.cp_request(self.client, [0x07])
        self.assertIs(ble.ctrl["sim_command"], previous)
        self.assert_write([0x07])

    async def test_ble_loop_resets_on_erg_disconnect_and_reconnect(self):
        clients = []
        writes = []
        sleeps = []
        with ble.lock:
            ble.ctrl.update(want_sim=True, want_erg=False, target_grade=4.0,
                            target_power=200, last_sent_t=0, last_erg_t=0)
            ble.rebind["kickr"] = False

        def make_client(device):
            client = MagicMock()
            client.is_connected = True
            client.services.get_characteristic.side_effect = (
                lambda uuid: object() if uuid == ble.CP_UUID else None)

            async def enter():
                # A stale value must also be cleared when a new connection starts.
                ble.ctrl["sim_command"] = deepcopy(self.previous_command)
                return client

            async def write(uuid, payload, response):
                writes.append(bytes(payload))
                ble.cb_cp(None, bytes([0x80, payload[0], 1]))

            async def stop_notify(uuid):
                self.assertIsNone(ble.ctrl["sim_command"])

            client.__aenter__ = AsyncMock(side_effect=enter)
            client.__aexit__ = AsyncMock(return_value=False)
            client.start_notify = AsyncMock()
            client.stop_notify = AsyncMock(side_effect=stop_notify)
            client.write_gatt_char = AsyncMock(side_effect=write)
            clients.append(client)
            return client

        async def sleep(delay):
            sleeps.append(delay)
            if len(sleeps) == 1:
                self.assertEqual(ble.ctrl["sim_command"]["status"], "confirmed")
                self.assertEqual(ble.ctrl["sim_command"]["grade"], 4.0)
                self.assertTrue(ble.ctrl["sim_active"])
                ble.ctrl.update(want_sim=False, want_erg=True)
            elif len(sleeps) == 2:
                self.assertIsNone(ble.ctrl["sim_command"])
                self.assertFalse(ble.ctrl["sim_active"])
                self.assertTrue(ble.ctrl["erg_active"])
                clients[0].is_connected = False
            elif len(sleeps) == 3:
                self.assertEqual(delay, 3)
                self.assertFalse(ble.state["connected"])
                self.assertIsNone(ble.ctrl["sim_command"])
                ble.ctrl.update(want_sim=False, want_erg=False)
            else:
                self.assertEqual(len(clients), 2)
                self.assertTrue(ble.state["connected"])
                self.assertIsNone(ble.ctrl["sim_command"])
                self.assertFalse(ble.ctrl["sim_active"])
                self.assertFalse(ble.ctrl["erg_active"])
                raise asyncio.CancelledError

        device = SimpleNamespace(name="Fake trainer", address="FA:KE")
        with patch.object(ble, "BleakClient", side_effect=make_client), \
                patch.object(ble.BleakScanner, "find_device_by_filter",
                             new=AsyncMock(return_value=device)) as scanner, \
                patch.object(ble, "acquire_scan", new=AsyncMock()), \
                patch.object(ble, "release_scan"), \
                patch.object(ble.asyncio, "sleep", new=AsyncMock(side_effect=sleep)), \
                patch.object(ble, "dbg"), patch.object(ble, "push"), \
                patch.object(ble, "snapshot", return_value={}):
            with self.assertRaises(asyncio.CancelledError):
                await ble.ble_loop()
        self.assertEqual(scanner.await_count, 2)
        self.assertEqual(sleeps, [0.2, 0.2, 3, 0.2])
        self.assertEqual(writes, [b"\x00", b"\x07", ble.sim_payload(4),
                                  b"\x08\x01", b"\x00", b"\x07",
                                  ble.erg_payload_ftms(200)])
        for client in clients:
            client.stop_notify.assert_awaited_once_with(ble.CP_UUID)
        self.assertIsNone(ble.ctrl["sim_command"])


if __name__ == "__main__":
    unittest.main()
