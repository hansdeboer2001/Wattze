import collections
import threading
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

from wattze.state import fan, lock, rec, state

send_lock = threading.Lock()

HYSTERESIS = 20.0
MIN_DWELL = 3.0
SAMPLE_SECS = 60.0
DISCONNECT_GRACE = 10.0


def fan_visible():
    with lock:
        lost = fan["disconnected_at"]
        return bool(fan["on"] and fan["host"] and
                    (fan["connected"] or (lost is not None and
                     time.monotonic() - lost < DISCONNECT_GRACE)))


def level_for(watts, cur, up, now=None, level_start=None):
    up = [0] + list(up)
    down = [0]
    prev = 0
    for w in up[1:]:
        down.append(max(prev, w - HYSTERESIS))
        prev = w
    lvl = cur
    while lvl < 3 and watts >= up[lvl + 1]:
        if now is not None and level_start is not None and now - level_start < MIN_DWELL:
            break
        lvl += 1
    while lvl > 0 and watts < down[lvl]:
        if now is not None and level_start is not None and now - level_start < MIN_DWELL:
            break
        lvl -= 1
    return lvl


def _post(url):
    req = Request(url, data=b"", method="POST")
    with urlopen(req, timeout=1.5) as r:
        r.read()


def fan_cmd(level):
    level = int(level)
    with send_lock:
        with lock:
            host = fan["host"]
            if fan.get("sent") == level and fan["connected"]:
                fan["level"] = level
                return
        if not host:
            raise RuntimeError("geen adres")
        _post("%s/number/fan_level/set?value=%d" % (host, level))
        with lock:
            fan["sent"] = level
            fan["level"] = level
            fan["connected"] = True
            fan["disconnected_at"] = None
            fan["err"] = ""


def fan_ping():
    with lock:
        host = fan["host"]
    if not host:
        raise RuntimeError("geen adres")
    _post(host + "/button/kickr_ping/press")
    with lock:
        fan["connected"] = True
        fan["disconnected_at"] = None
        fan["err"] = ""


def fan_failed(e):
    with lock:
        if fan["disconnected_at"] is None:
            fan["disconnected_at"] = time.monotonic()
        fan["connected"] = False
        # The ESP may have reset its output after missed pings; resend the level.
        fan["sent"] = None
        fan["err"] = ("%s: %s" % (type(e).__name__, e))[:80]


def fan_loop():
    last = None
    level_start = 0.0
    last_ping = 0.0
    was_rec = False
    coast_until = 0.0
    stop_hold = False
    samples = collections.deque(maxlen=int(SAMPLE_SECS / 0.5))
    while True:
        time.sleep(0.5)
        now = time.time()
        with lock:
            on = fan["on"]
            host = fan["host"]
            gp = rec.get("game_power") if rec.get("active") else None
            power = float(gp if isinstance(gp, (int, float)) else state.get("power") or 0)
            up = list(fan.get("w") or [80, 160, 240])
            forced = fan["force_until"] > now
            force_level = fan["force_level"]
            mode = fan.get("mode") or "auto"
            coast_s = max(0, int(fan.get("coast") or 300))
            rec_on = bool(rec.get("active"))
        samples.append(power)
        smooth = sum(samples) / len(samples)
        if mode in ("manual", "off_hold") and up and smooth >= up[0]:
            with lock:
                fan["mode"] = "auto"
                fan["force_until"] = 0.0
            mode = "auto"
        auto_lvl = level_for(smooth, 0 if last is None else last, up, now, level_start)
        stopped = was_rec and not rec_on
        if stopped and last not in (None, 0):
            coast_until = now + coast_s
            stop_hold = True
        elif (mode == "auto" and not forced and not stop_hold
              and last not in (None, 0) and auto_lvl == 0 and coast_until == 0):
            coast_until = now + coast_s
        elif not stop_hold and (auto_lvl > 0 or mode == "manual"):
            coast_until = 0.0
        if stop_hold and now >= coast_until:
            stop_hold = False
        was_rec = rec_on
        coasting = now < coast_until and last not in (None, 0) and mode != "off_hold"
        with lock:
            fan["coasting"] = bool(coasting)
        if not on or not host:
            if host and last not in (None, 0):
                try:
                    fan_cmd(0)
                except (OSError, URLError, TimeoutError, RuntimeError) as e:
                    fan_failed(e)
            with lock:
                fan["level"] = 0
                fan["coasting"] = False
                fan["connected"] = False
                fan["disconnected_at"] = None
                fan["err"] = "" if not on else ("geen adres" if not host else fan["err"])
            last = 0 if host else None
            level_start = now
            coast_until = 0.0
            stop_hold = False
            continue
        if coasting:
            lvl = int(last)
        elif forced and force_level is not None:
            lvl = int(force_level)
        elif mode == "off_hold":
            lvl = 0
        elif mode == "manual" and force_level is not None:
            lvl = int(force_level)
        else:
            lvl = level_for(smooth, 0 if last is None else last, up, now, level_start)
        try:
            with lock:
                needs_send = fan.get("sent") != lvl
            if lvl != last or needs_send:
                fan_cmd(lvl)
                last = lvl
                level_start = now
                last_ping = now
            elif now - last_ping >= 2.0:
                fan_ping()
                last_ping = now
        except (OSError, URLError, TimeoutError, RuntimeError) as e:
            fan_failed(e)
