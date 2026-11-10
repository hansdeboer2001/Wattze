import time

from wattze.state import *
from wattze.storage import save_config
from wattze import trainer_diagnostics

def dbg(msg):
    with lock:
        ctrl["dbg"] = time.strftime("%H:%M:%S") + " " + str(msg)[:160]


def set_gear(value=None, delta=0):
    with lock:
        v = gear["v"] + delta if value is None else int(value)
        v = max(1, min(gear["max"], v))
        changed = v != gear["v"]
        gear["v"] = v
        if changed:
            trainer_diagnostics.record("gear_changed")
    if changed:
        save_config()
        push(snapshot())
    return gear["v"]

def push(evt):
    with lock:
        for q in listeners:
            if len(q) < 100:
                q.append(evt)


def parse_ftms(data: bytes):
    flags = int.from_bytes(data[0:2], "little")
    o = 2
    speed = int.from_bytes(data[o:o + 2], "little") / 100
    o += 2
    if flags & (1 << 1):
        o += 2
    cad = None
    if flags & (1 << 2):
        cad = int.from_bytes(data[o:o + 2], "little") / 2
        o += 2
    if flags & (1 << 3):
        o += 2
    if flags & (1 << 4):
        o += 3
    if flags & (1 << 5):
        o += 2
    power = None
    if flags & (1 << 6):
        power = int.from_bytes(data[o:o + 2], "little", signed=True)
        o += 2
    return power, cad, speed


def on_data(power, cad, speed):
    now = time.time()
    p = power or 0
    with lock:
        state.update(power=p, cadence=cad or 0, speed=speed or 0,
                     updated=now)
        if ride["t0"] is None:
            ride["t0"] = now
        ride["samples"] += 1
        ride["sum_p"] += p
        ride["max_p"] = max(ride["max_p"], p)
        history.append((now, p))
        if len(history) > 900:
            del history[:len(history) - 900]
        avg = round(ride["sum_p"] / ride["samples"], 1)
    trainer_diagnostics.record("sample")
    push(snapshot())


def cb_ftms(_, data):
    p, c, s = parse_ftms(data)
    on_data(p, c, s)


def cb_cpm(_, data):
    on_data(int.from_bytes(data[2:4], "little", signed=True), None, None)


def snapshot():
    with lock:
        return {"power": state["power"], "cadence": state["cadence"],
                "speed": state["speed"], "hr": state.get("hr", 0),
                "connected": state["connected"], "t": time.time(),
                "avg": round(ride["sum_p"] / ride["samples"], 1) if ride["samples"] else 0,
                "max": ride["max_p"], "gear": gear["v"], "gear_max": gear["max"],
                "click": {k: click[k] for k in ("want", "connected", "device", "battery", "err", "addr")},
                "fan": {k: fan[k] for k in ("on", "host", "level", "connected", "err", "w", "mode", "coasting")}}

