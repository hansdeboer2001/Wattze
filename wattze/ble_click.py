import asyncio

from bleak import BleakClient, BleakScanner

from wattze.ble_scan import acquire as acquire_scan, release as release_scan
from wattze.state import *
from wattze.telemetry import push, set_gear, snapshot

# ---- Zwift Click (virtuele versnelling) ----
def pb_varints(data):
    """Lees de varint-velden uit een klein protobuf-bericht (ClickKeyPadStatus)."""
    out = {}
    i, n = 0, len(data)
    while i < n:
        key, shift = 0, 0
        while i < n:
            b = data[i]
            i += 1
            key |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        else:
            break
        field, wire = key >> 3, key & 7
        if wire == 0:
            val, shift = 0, 0
            while i < n:
                b = data[i]
                i += 1
                val |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            out[field] = val
        elif wire == 1:
            i += 8
        elif wire == 2:
            ln, shift = 0, 0
            while i < n:
                b = data[i]
                i += 1
                ln |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            i += ln
        elif wire == 5:
            i += 4
        else:
            break
    return out


def cb_click_async(_, data):
    data = bytes(data or b"")
    if not data:
        return
    typ, fields = data[0], pb_varints(data[1:])
    if typ == 55:  # ClickKeyPadStatus: 0 = ingedrukt, 1 = losgelaten
        with lock:
            up = fields[1] == 0 if 1 in fields else click["plus"]
            dn = fields[2] == 0 if 2 in fields else click["minus"]
            shift_up = up and not click["plus"]
            shift_dn = dn and not click["minus"]
            click["plus"], click["minus"] = up, dn
        if shift_up:
            set_gear(delta=1)
        if shift_dn:
            set_gear(delta=-1)
    elif typ == 25 and 2 in fields:  # batterijniveau
        with lock:
            click["battery"] = int(fields[2])


def cb_click_battery(_, data):
    if data:
        with lock:
            click["battery"] = int(data[0])


def cb_click_tx(_, data):
    pass


def click_match(d, adv):
    with lock:
        want = click["addr"]
    if want:
        return (d.address or "").upper() == want.upper()
    name = (d.name or "").lower()
    return "zwift" in name and "click" in name


async def click_loop():
    await asyncio.sleep(8)
    while True:
        with lock:
            want, addr = click["want"], click["addr"]
        if not want:
            await asyncio.sleep(1)
            continue
        try:
            await acquire_scan()
            try:
                dev = None
                if addr:
                    try:
                        dev = await BleakScanner.find_device_by_address(addr, timeout=4)
                    except Exception:
                        dev = None
                if not dev:
                    dev = await BleakScanner.find_device_by_filter(click_match, timeout=4)
            finally:
                release_scan()
            if not dev:
                with lock:
                    click.update(connected=False, err="niet gevonden - druk op een knop")
                await asyncio.sleep(10 if addr else 8)
                continue
            async with BleakClient(dev) as client:
                await client.start_notify(CLICK_ASYNC, cb_click_async)
                await client.start_notify(CLICK_TX, cb_click_tx)
                try:
                    await client.start_notify(BATTERY_LEVEL, cb_click_battery)
                except Exception:
                    pass
                await client.write_gatt_char(CLICK_RX, b"RideOn", response=True)
                with lock:
                    click.update(connected=True, device=dev.name or "Zwift Click",
                                 addr=dev.address, err="", plus=False, minus=False)
                push(snapshot())
                while client.is_connected:
                    with lock:
                        want = click["want"]
                    if not want:
                        break
                    await asyncio.sleep(0.5)
        except Exception as e:
            with lock:
                click.update(connected=False, err=f"{type(e).__name__}: {e}"[:120])
        finally:
            with lock:
                click.update(connected=False, plus=False, minus=False)
            push(snapshot())
        await asyncio.sleep(3)
