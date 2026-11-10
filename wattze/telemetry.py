import time

from wattze.state import *
from wattze.storage import save_config
from wattze import trainer_diagnostics
from wattze.fan import fan_visible

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
    if len(data) < 2:
        raise ValueError("Incomplete FTMS flags")
    flags = int.from_bytes(data[0:2], "little")
    sizes = (2, 2, 2, 3, 2, 2, 2, 5, 1, 1, 2, 2)
    required = 2 + (0 if flags & 1 else 2)
    required += sum(size for bit, size in enumerate(sizes, 1) if flags & (1 << bit))
    if len(data) < required:
        raise ValueError("Incomplete FTMS data")
    o = 2
    speed = None
    # FTMS bit 0 means More Data: instantaneous speed is omitted when set.
    if not flags & 1:
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


CADENCE_TIMEOUT = 5.0
_cadence_at = None
_crank_sample = None


def reset_trainer_telemetry():
    global _cadence_at, _crank_sample
    with lock:
        _cadence_at = None
        _crank_sample = None
        state.update(power=0, cadence=0, speed=0, updated=0.0)


def expire_cadence():
    global _cadence_at, _crank_sample
    with lock:
        if _cadence_at is None or time.monotonic() - _cadence_at < CADENCE_TIMEOUT:
            return False
        _cadence_at = None
        _crank_sample = None
        changed = state["cadence"] != 0
        state["cadence"] = 0
        return changed


def on_data(power, cad, speed):
    global _cadence_at
    now = time.time()
    with lock:
        expire_cadence()
        state["updated"] = now
        if cad is not None:
            state["cadence"] = cad
            _cadence_at = time.monotonic()
        if speed is not None:
            state["speed"] = speed
        if power is not None:
            state["power"] = power
            if ride["t0"] is None:
                ride["t0"] = now
            ride["samples"] += 1
            ride["sum_p"] += power
            ride["max_p"] = max(ride["max_p"], power)
            history.append((now, power))
            if len(history) > 900:
                del history[:len(history) - 900]
    trainer_diagnostics.record("sample")
    push(snapshot())


def cb_ftms(_, data):
    try:
        p, c, s = parse_ftms(data)
    except ValueError:
        return
    on_data(p, c, s)


def cb_cpm(_, data):
    global _crank_sample
    if len(data) < 4:
        return
    flags = int.from_bytes(data[:2], "little")
    sizes = ((0, 1), (2, 2), (4, 6), (5, 4), (6, 4), (7, 4),
             (8, 3), (9, 2), (10, 2), (11, 2))
    if len(data) < 4 + sum(size for bit, size in sizes if flags & (1 << bit)):
        return
    cad = None
    with lock:
        expire_cadence()
        if flags & (1 << 5):
            o = 4 + sum(size for bit, size in sizes[:3] if flags & (1 << bit))
            revs = int.from_bytes(data[o:o + 2], "little")
            ticks = int.from_bytes(data[o + 2:o + 4], "little")
            now = time.monotonic()
            previous = _crank_sample
            if previous is None or now - previous[2] >= 30:
                _crank_sample = (revs, ticks, now)
            elif (revs, ticks) != previous[:2]:
                _crank_sample = (revs, ticks, now)
                # Crank counters wrap at 16 bits; event time is in 1/1024 s.
                dr = (revs - previous[0]) & 0xffff
                dt = (ticks - previous[1]) & 0xffff
                if dr and dt:
                    rpm = 60 * 1024 * dr / dt
                    if 0 < rpm <= 300:
                        cad = rpm
        on_data(int.from_bytes(data[2:4], "little", signed=True), cad, None)


def snapshot():
    with lock:
        expire_cadence()
        return {"power": state["power"], "cadence": state["cadence"],
                "speed": state["speed"], "hr": state.get("hr", 0),
                "connected": state["connected"], "t": time.time(),
                "avg": round(ride["sum_p"] / ride["samples"], 1) if ride["samples"] else 0,
                "max": ride["max_p"], "gear": gear["v"], "gear_max": gear["max"],
                "click": {k: click[k] for k in ("want", "connected", "device", "battery", "err", "addr")},
                "fan": {"visible": fan_visible(),
                        **{k: fan[k] for k in ("on", "host", "level", "connected", "err", "w", "mode", "coasting")}}}
