"""Opt-in, bounded trainer trace. Never changes trainer control state."""
import json
import math
import os
import queue
import threading
import time

from wattze.state import RIDE_DIR, ctrl, gear, lock, settings, state

LOG_PATH = os.path.join(RIDE_DIR, "trainer_diagnostics.jsonl")
MAX_BYTES = 5 * 1024 * 1024
MAX_SECONDS = 30 * 60
_status = {"active": False, "error": "", "bytes": 0}
_started = 0.0
_sampled = 0.0
_session = 0
_truncate_next = False
_queue = queue.Queue(maxsize=256)
_worker = None


def _write_log():
    failed_session = None
    while True:
        session, path, line, truncate = _queue.get()
        try:
            if session != failed_session:
                with open(path, "w" if truncate else "a", encoding="utf-8", newline="\n") as output:
                    output.write(line)
        except OSError as exc:
            failed_session = session
            with lock:
                if session == _session:
                    _status.update(active=False, error=str(exc)[:200])
        finally:
            _queue.task_done()


def status():
    with lock:
        if _status["active"] and time.monotonic() - _started >= MAX_SECONDS:
            _status.update(active=False, error="Diagnoselimiet bereikt (30 minuten).")
        return dict(_status)


def start():
    global _started, _sampled, _session, _truncate_next, _worker
    with lock:
        if _status["active"]:
            return status()
        if _worker is None:
            _worker = threading.Thread(target=_write_log, name="trainer-diagnostics", daemon=True)
            _worker.start()
        _status.update(active=True, error="", bytes=0)
        _session += 1
        _truncate_next = True
        _started = time.monotonic()
        _sampled = 0.0
        record("start")
        return status()


def stop():
    with lock:
        record("stop")
        _status["active"] = False
        return status()


def record(event, **details):
    global _sampled, _truncate_next
    with lock:
        if not _status["active"]:
            return
        now = time.monotonic()
        if now - _started >= MAX_SECONDS or _status["bytes"] >= MAX_BYTES:
            _status.update(active=False, error="Diagnoselimiet bereikt (30 minuten of 5 MB).")
            return
        if event == "sample":
            if now - _sampled < 1:
                return
            _sampled = now
        row = {"t": time.time(), "elapsed": round(now - _started, 3), "event": event,
               "connected": state["connected"], "gear": gear["v"], "gear_max": gear["max"],
               "route_feel_setting": settings.get("route_feel", 100),
               "power": state["power"], "cadence": state["cadence"],
               "speed": state["speed"],
               **{k: ctrl[k] for k in ("want_sim", "want_erg", "sim_active", "erg_active",
                                       "target_grade", "target_power", "sim_command")},
               **details}
        try:
            line = json.dumps(row, ensure_ascii=True, allow_nan=False) + "\n"
            size = len(line.encode("utf-8"))
            if _status["bytes"] + size > MAX_BYTES:
                _status.update(active=False, error="Diagnoselimiet bereikt (5 MB).")
                return
            # Never wait for disk I/O in BLE callbacks or while holding shared state.
            _queue.put_nowait((_session, LOG_PATH, line, _truncate_next))
            _truncate_next = False
            _status["bytes"] += size
        except queue.Full:
            _status.update(active=False, error="Diagnoselog gestopt: schrijfwachtrij vol.")
        except (OSError, ValueError, TypeError) as exc:
            _status.update(active=False, error=str(exc)[:200])


def grade_source(body):
    """Only retain numeric calculation inputs, never arbitrary HTTP content."""
    source = body.get("diagnostic")
    if not isinstance(source, dict):
        return {}
    return {key: value for key in ("route_grade", "route_feel", "gear", "gear_max", "gear_offset")
            if isinstance((value := source.get(key)), (int, float))
            and not isinstance(value, bool) and -1000000 <= value <= 1000000
            and math.isfinite(value)}
