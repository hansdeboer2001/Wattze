import asyncio
import struct
import threading
import time

from bleak import BleakClient, BleakScanner

from wattze.ble_scan import acquire as acquire_scan, release as release_scan, try_acquire as try_acquire_scan
from wattze.state import *
from wattze.telemetry import (cb_cpm, cb_ftms, dbg, expire_cadence, push,
                              reset_trainer_telemetry, snapshot)
from wattze import trainer_diagnostics

def kickr_match(d, adv):
    with lock:
        want = sel.get("kickr")
    if want:
        return (d.address or "").upper() == want.upper()
    uuids = []
    try:
        uuids = [str(u) for u in (getattr(adv, "service_uuids", None) or [])]
    except Exception:
        pass
    return classify_device(d.name, uuids) == "trainer"


FTMS_SVC = "00001826-0000-1000-8000-00805f9b34fb"


def classify_device(name, uuids):
    """Bepaal het soort Bluetooth-apparaat op basis van naam en service-UUID's."""
    nl = (name or "").lower()
    uuids = [str(u).lower() for u in (uuids or [])]
    is_click = CLICK_ASYNC in uuids or ("zwift" in nl and "click" in nl)
    is_hr = HR_SVC in uuids or any(k in nl for k in
        ("polar", "garmin", "tickr", "hrm", "coospo", "bryton", "moofit", "igpsport", "xoss"))
    is_tr = (not is_click) and (FTMS_SVC in uuids or any(k in nl for k in
        ("kickr", "wahoo", "tacx", "elite", "wattbike", "stages", "saris",
         "kinetic", "bkool", "magene", "thinkrider", "zwift")))
    if is_click:
        return "click"
    if is_hr and not is_tr:
        return "hr"
    return "trainer" if is_tr else "other"


async def scan_devices(timeout=12):
    scanned = await BleakScanner.discover(timeout=timeout, return_adv=True)
    pairs = scanned.values() if isinstance(scanned, dict) else scanned
    out = []
    for d, adv in pairs:
        name = d.name or ""
        uuids = []
        try:
            uuids = [str(u).lower() for u in (adv.service_uuids or [])]
        except Exception:
            pass
        if not uuids:
            try:
                uuids = [str(u).lower() for u in ((d.metadata or {}).get("uuids") or [])]
            except Exception:
                pass
        kind = classify_device(name, uuids)
        try:
            rssi = adv.rssi
        except Exception:
            rssi = None
        if rssi is None:
            try:
                rssi = d.rssi
            except Exception:
                rssi = None
        out.append({"address": d.address, "name": name or "(onbekend)",
                    "rssi": rssi, "kind": kind})
    out.sort(key=lambda x: ({"trainer": 0, "hr": 1, "click": 2, "other": 3}[x["kind"]],
                            -((x["rssi"] or -999))))
    return {"devices": out}


_scan_lock = threading.Lock()
_scan_cache = {"data": None, "t": 0.0}


def scan_devices_cached(timeout=2.0, max_age=None):
    """Korte scan met cache: één scan tegelijk, zodat 3s-polling niet stapelt."""
    cached = _scan_cache["data"]
    if cached is not None and max_age is not None:
        age = time.time() - _scan_cache["t"]
        if age < max_age:
            return dict(cached, cached=True, age=round(age, 1))
    if _scan_lock.acquire(blocking=False):
        try:
            if not try_acquire_scan(1.0):
                cached = _scan_cache["data"]
                if cached is not None:
                    return dict(cached, cached=True, busy=True, error="",
                                age=round(time.time() - _scan_cache["t"], 1))
                return {"devices": [], "error": "", "ts": time.time(),
                        "cached": True, "age": 0.0, "busy": True}
            try:
                try:
                    data = asyncio.run(scan_devices(timeout=timeout))
                    data["error"] = ""
                except Exception as e:
                    data = {"devices": [], "error": "%s: %s" % (type(e).__name__, e)}
                now = time.time()
                data["ts"] = now
                _scan_cache["data"] = data
                _scan_cache["t"] = now
            finally:
                release_scan()
        finally:
            _scan_lock.release()
        return dict(_scan_cache["data"], cached=False,
                    age=round(time.time() - _scan_cache["t"], 1))

    cached = _scan_cache["data"]
    if cached is None:
        if _scan_lock.acquire(timeout=max(0.1, timeout + 0.5)):
            _scan_lock.release()
            cached = _scan_cache["data"]
    if cached is None:
        return {"devices": [], "error": "", "ts": time.time(),
                "cached": True, "age": 0.0, "busy": True}
    return dict(cached, cached=True, age=round(time.time() - _scan_cache["t"], 1))

# ---- FTMS control point ----
cp_evt = None       # asyncio.Event, per connectie aangemaakt
cp_result = None    # (op, resultcode)


def cb_cp(_, data):
    global cp_result
    with lock:
        if len(data) >= 3 and data[0] == 0x80:
            ctrl["last_cp"] = {"op": data[1], "result": data[2],
                               "t": time.time()}
            cp_result = (data[1], data[2])
    if cp_evt is not None:
        cp_evt.set()


async def cp_request(client, payload, timeout=5):
    """Schrijf control-op en wacht op indication-respons. Retourneert
    (op, result) of None bij timeout. Result 0x01 = succes."""
    global cp_result
    cp_result = None
    cp_evt.clear()
    # Observe the encoded SIM command without changing control decisions or retries.
    is_sim = len(payload) == 7 and payload[0] == 0x11
    if is_sim:
        with lock:
            ctrl["sim_command"] = {"grade": None, "t": None, "status": "writing"}
    trainer_diagnostics.record("ftms_write", opcode=payload[0])
    try:
        await client.write_gatt_char(CP_UUID, bytearray(payload), response=True)
    except Exception as exc:
        if is_sim:
            with lock:
                ctrl["sim_command"] = {"grade": None, "t": None, "status": "write_error"}
        trainer_diagnostics.record("ftms_write_error", opcode=payload[0], error_type=type(exc).__name__)
        raise
    if is_sim:
        command = {"grade": struct.unpack_from("<h", bytes(payload), 3)[0] / 100.0,
                   "t": time.time(), "status": "pending"}
        with lock:
            ctrl["sim_command"] = command
    trainer_diagnostics.record("ftms_sent", opcode=payload[0])
    try:
        await asyncio.wait_for(cp_evt.wait(), timeout)
    except asyncio.TimeoutError:
        if is_sim:
            with lock:
                ctrl["sim_command"] = dict(command, status="timeout")
        trainer_diagnostics.record("ftms_timeout", opcode=payload[0])
        return None
    if is_sim:
        status = "unconfirmed"
        if cp_result and cp_result[0] == 0x11:
            status = "confirmed" if cp_result[1] == 0x01 else "rejected"
        with lock:
            ctrl["sim_command"] = dict(command, status=status,
                                       result=cp_result[1] if cp_result and cp_result[0] == 0x11 else None)
    trainer_diagnostics.record("ftms_response", opcode=payload[0],
                               response=list(cp_result) if cp_result else None)
    return cp_result


def sim_payload(grade_pct):
    g = max(-10.0, min(16.0, grade_pct))
    gi = int(round(g * 100))
    return bytes([0x11, 0x00, 0x00]) + struct.pack("<h", gi) + bytes([40, 51])


def erg_payload(watts):
    w = max(0, min(1000, int(watts)))
    return bytes([0x42]) + struct.pack("<H", w)  # Wahoo proprietary ERG


def erg_payload_ftms(watts):
    w = max(0, min(2000, int(watts)))
    return bytes([0x05]) + struct.pack("<h", w)  # FTMS Set Target Power


wahoo_evt = None  # asyncio.Event, per connectie


def cb_wahoo(_, data):
    with lock:
        ctrl["last_wahoo"] = {"data": list(bytes(data)), "t": time.time()}
    if wahoo_evt is not None:
        wahoo_evt.set()


async def ble_loop():
    global cp_evt, wahoo_evt
    while True:
        cp_evt = asyncio.Event()
        wahoo_evt = asyncio.Event()
        try:
            await acquire_scan()
            try:
                dev = await BleakScanner.find_device_by_filter(kickr_match, timeout=15)
            finally:
                release_scan()
            if not dev:
                with lock:
                    if sel.get("kickr"):
                        state["device"] = "zoeken: " + sel["kickr"]
                await asyncio.sleep(3)
                continue
            with lock:
                state["device"] = f"{dev.name} ({dev.address})"
                state["kickr_addr"] = (dev.address or "").upper()
                state["kickr_name"] = dev.name or ""
            async with BleakClient(dev) as client:
                with lock:
                    state["connected"] = True
                    state["kickr_err"] = ""
                trainer_diagnostics.record("connected")
                reset_trainer_telemetry()
                svcs = client.services
                use_ftms = svcs.get_characteristic(BIKE_DATA) is not None
                use_cpm = svcs.get_characteristic(CPM) is not None
                use_cp = svcs.get_characteristic(CP_UUID) is not None
                use_wahoo = svcs.get_characteristic(WAHOO_UUID) is not None
                if use_ftms:
                    await client.start_notify(BIKE_DATA, cb_ftms)
                elif use_cpm:
                    await client.start_notify(CPM, cb_cpm)
                if use_cp:
                    await client.start_notify(CP_UUID, cb_cp)
                if use_wahoo:
                    await client.start_notify(WAHOO_UUID, cb_wahoo)
                sim_on = False
                erg_on = False
                wahoo_ready = False
                ctrl_held = False
                with lock:
                    ctrl["sim_active"] = False
                    ctrl["erg_active"] = False
                    ctrl["last_sent"] = None
                    ctrl["last_erg"] = None
                    ctrl["sim_command"] = None

                async def ensure_control():
                    nonlocal ctrl_held
                    if ctrl_held:
                        return True
                    try:
                        r = await cp_request(client, [0x00])
                        if r and r[1] == 0x01:
                            await cp_request(client, [0x07])
                            ctrl_held = True
                            return True
                        dbg(f"control denied {r}")
                    except Exception as e:
                        dbg(f"control-ERR {type(e).__name__}: {e}")
                    return False

                async def stop_workout():
                    nonlocal ctrl_held
                    try:
                        await cp_request(client, [0x08, 0x01])
                    except Exception as e:
                        dbg(f"stop-ERR {type(e).__name__}: {e}")
                    ctrl_held = False

                try:
                    while client.is_connected and not rebind["kickr"]:
                        with lock:
                            want_sim = ctrl["want_sim"]
                            want_erg = ctrl["want_erg"]
                            tg = ctrl["target_grade"]
                            tp = ctrl["target_power"]
                        if use_cp:
                            if want_erg and not want_sim:
                                if sim_on:
                                    await stop_workout()
                                    sim_on = False
                                    with lock:
                                        ctrl["sim_active"] = False
                                        ctrl["last_sent"] = None
                                        ctrl["sim_command"] = None
                                if not erg_on and await ensure_control():
                                    erg_on = True
                                    with lock:
                                        ctrl["erg_active"] = True
                                        ctrl["last_erg"] = None
                            elif want_sim and not want_erg:
                                if erg_on:
                                    await stop_workout()
                                    erg_on = False
                                    with lock:
                                        ctrl["erg_active"] = False
                                        ctrl["last_erg"] = None
                                if not sim_on and await ensure_control():
                                    sim_on = True
                                    with lock:
                                        ctrl["sim_active"] = True
                                        ctrl["last_sent"] = None
                                        ctrl["sim_command"] = None
                            else:
                                if sim_on or erg_on:
                                    await stop_workout()
                                    sim_on = False
                                    erg_on = False
                                    with lock:
                                        ctrl["sim_active"] = False
                                        ctrl["erg_active"] = False
                                        ctrl["last_sent"] = None
                                        ctrl["last_erg"] = None
                                        ctrl["sim_command"] = None
                            if sim_on:
                                with lock:
                                    ls = ctrl["last_sent"]
                                    lst = ctrl["last_sent_t"]
                                now = time.time()
                                if (ls is None or abs(tg - ls) >= 0.3) and (now - lst) >= 1.0:
                                    try:
                                        r = await cp_request(client, sim_payload(tg))
                                        dbg(f"sim-resp grade={tg} -> {r}")
                                        with lock:
                                            if r and r[1] == 0x01:
                                                ctrl["last_sent"] = tg
                                                ctrl["last_sent_t"] = now
                                            elif r and r[1] == 0x05:
                                                sim_on = False
                                                ctrl_held = False
                                                ctrl["sim_active"] = False
                                    except Exception as e:
                                        dbg(f"sim-ERR {type(e).__name__}: {e}")
                            if erg_on:
                                with lock:
                                    le = ctrl["last_erg"]
                                    let_ = ctrl["last_erg_t"]
                                now = time.time()
                                if le is None or abs(tp - le) >= 2 or (now - let_) >= 4.0:
                                    if use_wahoo:
                                        try:
                                            if not wahoo_ready:
                                                wahoo_evt.clear()
                                                await client.write_gatt_char(
                                                    WAHOO_UUID, WAHOO_UNLOCK, response=True)
                                                try:
                                                    await asyncio.wait_for(wahoo_evt.wait(), 3)
                                                    wahoo_ready = True
                                                    dbg("wahoo unlock ok")
                                                except asyncio.TimeoutError:
                                                    dbg("wahoo unlock TIMEOUT")
                                            if wahoo_ready:
                                                await client.write_gatt_char(
                                                    WAHOO_UUID, erg_payload(tp), response=True)
                                                with lock:
                                                    ctrl["last_erg"] = tp
                                                    ctrl["last_erg_t"] = now
                                        except Exception as e:
                                            dbg(f"wahoo-ERR {type(e).__name__}: {e}")
                                    elif use_cp:
                                        try:
                                            r = await cp_request(client, erg_payload_ftms(tp))
                                            dbg(f"erg-ftms {tp} -> {r}")
                                            with lock:
                                                if r and r[1] == 0x01:
                                                    ctrl["last_erg"] = tp
                                                    ctrl["last_erg_t"] = now
                                        except Exception as e:
                                            dbg(f"erg-ftms-ERR {type(e).__name__}: {e}")
                        if expire_cadence():
                            push(snapshot())
                        trainer_diagnostics.record("sample")
                        await asyncio.sleep(0.2)
                finally:
                    reset_trainer_telemetry()
                    with lock:
                        ctrl["sim_command"] = None
                    try:
                        if use_ftms:
                            await client.stop_notify(BIKE_DATA)
                        elif use_cpm:
                            await client.stop_notify(CPM)
                        if use_cp:
                            await client.stop_notify(CP_UUID)
                        if use_wahoo:
                            await client.stop_notify(WAHOO_UUID)
                    except Exception:
                        pass
        except Exception as e:
            dbg(f"loop-ERR {type(e).__name__}: {e}")
            with lock:
                state["kickr_err"] = f"{type(e).__name__}: {e}"[:120]
        with lock:
            reset_trainer_telemetry()
            state["connected"] = False
            state.update(power=0, cadence=0, speed=0, kickr_addr="", kickr_name="")
            ctrl["sim_active"] = False
            ctrl["last_sent"] = None
            ctrl["sim_command"] = None
            rebind["kickr"] = False
        trainer_diagnostics.record("disconnected")
        push(snapshot())
        await asyncio.sleep(3)
