"""Local archive of every finished KOM attempt.

The in-ride leaderboard in koms.json stays a top 20. This file keeps the full
history, including attempts that never make that list. Old records are copied
once and keep only the fields that were actually stored.
"""
import hashlib
import json
import math
import os
import re
import uuid

from wattze import state as app_state
from wattze.storage import load_koms

ARCHIVE_NAME = "kom-archief.json"
NOTICE = (
    "Eerder verwijderde pogingen en tijden die buiten de oude top 20 vielen, komen niet terug. "
    "Nieuwe volledige pogingen worden wel allemaal bewaard, ook buiten de ranglijst."
)
_ROUTE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_KEY = re.compile(r"^kom_(.+)_(\d+)$")


def archive_path():
    return os.path.join(app_state.RIDE_DIR, ARCHIVE_NAME)


def canonical_revision(ele, step, segs):
    """Route revision used by the Wattze server: sha256 hex of the canonical profile, 24 chars.

    Canonical JSON is json.dumps of ele/step/segs rounded to 6 decimals, with
    sort_keys=True and separators=(',', ':').
    """
    try:
        if not isinstance(ele, list) or len(ele) < 2 or not isinstance(segs, list) or not segs:
            return None
        ele_r = [round(_strict_num(v), 6) for v in ele]
        step_r = round(_strict_num(step), 6)
        if step_r <= 0:
            return None
        bounds = []
        for seg in segs:
            if not isinstance(seg, dict):
                return None
            bounds.append({"at": round(_strict_num(seg.get("at")), 6),
                           "end": round(_strict_num(seg.get("end")), 6)})
        canonical = {"ele": ele_r, "step": step_r, "segs": bounds}
        raw = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()[:24]
    except (TypeError, ValueError, OverflowError):
        return None


def route_revision(route):
    if not isinstance(route, dict):
        return None
    return canonical_revision(route.get("ele"), route.get("step"), route.get("segs"))


def revision_for_route_id(route_id):
    return route_revision(load_route(route_id))


def load_route(route_id):
    if not isinstance(route_id, str) or not _ROUTE_ID.fullmatch(route_id):
        return None
    path = os.path.join(app_state.ROUTE_DIR, route_id + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(data, dict) or data.get("id") != route_id:
        return None
    return data


def parse_kom_key(key):
    """Split kom_<name>|<km>|<seed>_<index> without guessing names the key does not contain."""
    info = {"route_name": None, "route_km": None, "route_seed": None, "segment_index": None}
    if not isinstance(key, str):
        return info
    match = _KEY.match(key)
    if not match:
        info["route_name"] = _text(key, 80)
        return info
    body, index = match.group(1), int(match.group(2))
    info["segment_index"] = index
    parts = body.rsplit("|", 2)
    if len(parts) == 3:
        info["route_name"] = _text(parts[0], 80)
        info["route_km"] = _text(parts[1], 20)
        info["route_seed"] = _text(parts[2], 20)
    else:
        info["route_name"] = _text(body, 80)
    return info


def empty_archive():
    return {"version": 1, "migrated": False, "attempts": []}


def load_archive():
    try:
        with open(archive_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, UnicodeError):
        return empty_archive()
    if not isinstance(data, dict):
        return empty_archive()
    attempts = data.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    return {"version": 1, "migrated": bool(data.get("migrated")),
            "attempts": [a for a in attempts if isinstance(a, dict)]}


def save_archive(data):
    path = archive_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "migrated": bool(data.get("migrated")),
                   "attempts": list(data.get("attempts") or [])}, f, ensure_ascii=False)
    os.replace(tmp, path)


def ensure_archive(koms=None):
    """Copy the current top-20 file into the archive once. Later leaderboard edits stay there."""
    data = load_archive()
    if data.get("migrated"):
        return data
    if koms is None:
        koms = load_koms()
    migrate_koms(data, koms)
    data["migrated"] = True
    save_archive(data)
    return data


def migrate_koms(data, koms):
    if not isinstance(koms, dict):
        return
    for key, entries in koms.items():
        if not isinstance(key, str) or not key.startswith("kom_") or not isinstance(entries, list):
            continue
        for entry in entries:
            _append_legacy(data, key, entry)


def record_attempt(key, entry, meta=None):
    """Archive one newly finished attempt. Invalid leaderboard rows are ignored."""
    data = ensure_archive()
    if not _valid_entry(entry) or not isinstance(key, str) or not key.startswith("kom_"):
        return data
    data["attempts"].append(_new_attempt(key, entry, meta if isinstance(meta, dict) else {}))
    save_archive(data)
    return data


def record_legacy(key, entry):
    """Archive an imported historical row, skipping duplicates and inventing nothing."""
    data = ensure_archive()
    _append_legacy(data, key, entry)
    save_archive(data)
    return data


def public_archive(data=None):
    if data is None:
        data = ensure_archive()
    return {"ok": True, "migrated": bool(data.get("migrated")), "notice": NOTICE,
            "attempts": [_public(a) for a in data.get("attempts") or []]}


def _append_legacy(data, key, entry):
    if not isinstance(key, str) or not key.startswith("kom_") or not _valid_entry(entry):
        return
    if _has_same_result(data, key, entry):
        return
    parsed = parse_kom_key(key)
    attempt = {
        "id": str(uuid.uuid4()),
        "key": key,
        "route_id": None,
        "route_name": parsed["route_name"],
        "route_km": parsed["route_km"],
        "route_seed": parsed["route_seed"],
        "segment_index": parsed["segment_index"],
        "segment_name": None,
        "revision": None,
        "date": _date(entry.get("date")),
        "time": _number(entry.get("time")),
        "avgP": _number(entry.get("avgP")) if "avgP" in entry else None,
        "user": entry.get("user") if isinstance(entry.get("user"), str) else None,
        "simulated": None,
        "erg": None,
        "paused": None,
        "legacy": True,
    }
    data["attempts"].append(attempt)


def _new_attempt(key, entry, meta):
    parsed = parse_kom_key(key)
    route_id = meta.get("route_id")
    if route_id == "free" or not isinstance(route_id, str) or not _ROUTE_ID.fullmatch(route_id):
        route_id = None
    file_revision = revision_for_route_id(route_id) if route_id else None
    course = meta.get("course") if isinstance(meta.get("course"), dict) else None
    course_revision = None
    if course is not None:
        ele = course.get("ele")
        if isinstance(ele, list) and len(ele) <= 100000:
            course_revision = route_revision(course)
    # A supplied course that differs from the file is its own variant.
    if course_revision and course_revision != file_revision:
        revision = course_revision
    else:
        revision = file_revision or course_revision
    km = meta.get("route_km")
    km_text = None
    km_num = _number(km)
    if km_num is not None:
        km_text = ("%.2f" % km_num)
    seed = meta.get("route_seed")
    seed_text = None
    if type(seed) is int or (type(seed) is float and math.isfinite(seed) and seed.is_integer()):
        seed_text = str(int(seed))
    elif isinstance(seed, str):
        seed_text = _text(seed, 20)
    index = meta.get("segment_index")
    if type(index) is not int or isinstance(index, bool) or index < 0 or index > 10000:
        index = parsed["segment_index"]
    return {
        "id": str(uuid.uuid4()),
        "key": key,
        "route_id": route_id,
        "route_name": _text(meta.get("route_name"), 80) or parsed["route_name"],
        "route_km": km_text or parsed["route_km"],
        "route_seed": seed_text if seed_text is not None else parsed["route_seed"],
        "segment_index": index,
        "segment_name": _text(meta.get("segment_name"), 80),
        "revision": revision,
        "date": _date(entry.get("date")),
        "time": _number(entry.get("time")),
        "avgP": _number(entry.get("avgP")) if "avgP" in entry else None,
        "user": entry.get("user") if isinstance(entry.get("user"), str) else None,
        "simulated": _flag(meta.get("simulated")),
        "erg": _flag(meta.get("erg")),
        "paused": _flag(meta.get("paused")),
        "legacy": False,
    }


def _public(attempt):
    keys = ("id", "key", "route_id", "route_name", "route_km", "route_seed", "segment_index",
            "segment_name", "revision", "date", "time", "avgP", "user", "simulated", "erg",
            "paused", "legacy")
    return {k: attempt.get(k) for k in keys}


def _has_same_result(data, key, entry):
    date, time = _date(entry.get("date")), _number(entry.get("time"))
    for attempt in data["attempts"]:
        if attempt.get("key") == key and attempt.get("date") == date and attempt.get("time") == time:
            return True
    return False


def _valid_entry(entry):
    return isinstance(entry, dict) and _number(entry.get("time")) is not None


def _strict_num(value):
    if type(value) is bool or type(value) not in (int, float):
        raise TypeError("not a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("not finite")
    return number


def _number(value):
    try:
        return _strict_num(value)
    except (TypeError, ValueError):
        return None


def _date(value):
    if type(value) in (int, float) and type(value) is not bool and math.isfinite(float(value)):
        return value
    if isinstance(value, str):
        text = _text(value, 40)
        return text
    return None


def _flag(value):
    if type(value) is bool:
        return value
    return None


def _text(value, limit):
    if not isinstance(value, str):
        return None
    cleaned = "".join(ch for ch in value if ord(ch) >= 32 or ch == "\t").strip()
    if not cleaned:
        return None
    return cleaned[:limit]
