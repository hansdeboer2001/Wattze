import asyncio
import time

from bleak import BleakClient, BleakScanner

from wattze.ble_scan import acquire as acquire_scan, release as release_scan
from wattze.state import *
from wattze.telemetry import dbg, push, snapshot

# ---- hartslagmeter (BLE Heart Rate Service 0x180D) ----

def hr_match(d, adv):
    with lock:
        want = sel.get("hr")
    if want and want != "__off":
        return (d.address or "").upper() == want.upper()
    try:
        if HR_SVC in [u.lower() for u in (adv.service_uuids or [])]:
            return True
    except Exception:
        pass
    n = (d.name or "").lower()
    if "kickr" in n:
        return False
    return any(k in n for k in ("polar", "garmin", "wahoo", "tickr", "hrm"))


def cb_hr(_, data):
    if not data:
        return
    hr = data[1] if not (data[0] & 0x01) else int.from_bytes(data[1:3], "little")
    with lock:
        state["hr"] = hr
        state["hr_t"] = time.time()
    push(snapshot())


async def hr_loop():
    while True:
        try:
            with lock:
                want_hr = sel.get("hr")
            if want_hr == "__off":
                with lock:
                    state["hr"] = 0
                    state["hr_connected"] = False
                    state["hr_dev"] = "uitgeschakeld"
                    state["hr_addr"] = ""
                    state["hr_name"] = ""
                await asyncio.sleep(10)
                continue
            await acquire_scan()
            try:
                dev = await BleakScanner.find_device_by_filter(hr_match, timeout=6)
            finally:
                release_scan()
            if not dev:
                with lock:
                    if sel.get("hr") and sel.get("hr") != "__off":
                        state["hr_dev"] = "zoeken: " + sel["hr"]
                await asyncio.sleep(15 if want_hr is None else 8)
                continue
            async with BleakClient(dev) as client:
                with lock:
                    state["hr_dev"] = f"{dev.name} ({dev.address})"
                    state["hr_connected"] = True
                    state["hr_addr"] = (dev.address or "").upper()
                    state["hr_name"] = dev.name or ""
                if client.services.get_characteristic(HR_UUID) is None:
                    dbg("hr: geen HR-karakteristiek")
                    await asyncio.sleep(2)
                    continue
                await client.start_notify(HR_UUID, cb_hr)
                dbg(f"hr verbonden: {dev.name}")
                while client.is_connected and not rebind["hr"]:
                    await asyncio.sleep(1)
                try:
                    await client.stop_notify(HR_UUID)
                except Exception:
                    pass
        except Exception as e:
            dbg(f"hr-ERR {type(e).__name__}: {e}")
        with lock:
            state["hr"] = 0
            state["hr_connected"] = False
            state["hr_addr"] = ""
            state["hr_name"] = ""
            rebind["hr"] = False
        push(snapshot())
        await asyncio.sleep(5)
