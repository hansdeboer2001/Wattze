import json
import os
from urllib.parse import urlparse

from wattze.state import *

def load_routes():
    routes = []
    for fn in sorted(os.listdir(ROUTE_DIR)):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(ROUTE_DIR, fn), encoding="utf-8") as f:
                    routes.append(json.load(f))
            except Exception as e:
                print("route %s overgeslagen: %s" % (fn, e), flush=True)
    routes.sort(key=lambda r: (r.get("order", 99), r.get("name", "")))
    return routes


def load_koms():
    try:
        with open(KOM_FILE, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_koms(d):
    tmp = KOM_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, KOM_FILE)


def merge_kom(d, key, entries):
    lst = d.get(key, [])
    seen = {e.get("date") for e in lst}
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("time"), (int, float)) and e.get("date") not in seen:
            lst.append({"time": e["time"], "avgP": e.get("avgP", 0), "date": e.get("date"), "user": e.get("user", "")})
            seen.add(e.get("date"))
    lst.sort(key=lambda e: e["time"])
    d[key] = lst[:20]
    return d[key]


def slugify(s):
    s = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "-" for ch in str(s).strip().lower())
    return "-".join(filter(None, s.split("-")))[:60] or "rit"


def load_index():
    try:
        with open(INDEX_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_index(rides):
    try:
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(rides, f)
    except Exception:
        pass

def multi_host(raw):
    h = str(raw or "").strip().rstrip("/")
    if not h:
        return ""
    if "://" not in h:
        h = "http://" + h
    try:
        u = urlparse(h)
        if u.scheme not in ("http", "https") or not u.netloc:
            return ""
        return "%s://%s" % (u.scheme, u.netloc)
    except Exception:
        return ""


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                if d.get("kickr_addr"):
                    sel["kickr"] = str(d["kickr_addr"]).upper()
                if d.get("hr_off"):
                    sel["hr"] = "__off"
                elif d.get("hr_addr"):
                    sel["hr"] = str(d["hr_addr"]).upper()
                settings["username"] = str(d.get("username") or "")
                settings["weight"] = d.get("weight") or 85
                of = str(d.get("overlay_font") or "middel")
                settings["overlay_font"] = of if of in ("klein", "middel", "groot") else "middel"
                try:
                    settings["route_feel"] = max(0, min(100, int(d.get("route_feel", 100))))
                except (TypeError, ValueError):
                    settings["route_feel"] = 100
                try:
                    gm = int(d.get("vgear_max") or gear["max"])
                    gear["max"] = max(2, min(24, gm))
                except (TypeError, ValueError):
                    pass
                try:
                    gear["v"] = max(1, min(gear["max"], int(d.get("vgear") or gear["v"])))
                except (TypeError, ValueError):
                    pass
                click["want"] = not bool(d.get("click_off"))
                if d.get("click_addr"):
                    click["addr"] = str(d["click_addr"]).upper()
                role = str(d.get("multi_mode") or "off")
                if role not in ("off", "server"):
                    role = "off"
                multi["mode"] = role
                multi["server"] = MP_HOST
                fan["on"] = bool(d.get("fan_on"))
                fan["host"] = multi_host(d.get("fan_host"))
                try:
                    raw = d.get("fan_coast")
                    if raw is None or int(raw) == 30:
                        fan["coast"] = 300
                    else:
                        fan["coast"] = max(0, min(1800, int(raw)))
                except (TypeError, ValueError):
                    fan["coast"] = 300
                w = d.get("fan_w")
                if isinstance(w, list) and len(w) == 3:
                    try:
                        vals = [int(round(float(x))) for x in w]
                        if 1 <= vals[0] < vals[1] < vals[2] <= 2000:
                            fan["w"] = vals
                    except (TypeError, ValueError):
                        pass
    except Exception:
        pass


def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"kickr_addr": sel["kickr"],
                       "hr_addr": None if sel["hr"] in (None, "__off") else sel["hr"],
                       "hr_off": sel["hr"] == "__off",
                       "username": settings["username"], "weight": settings["weight"],
                        "overlay_font": settings["overlay_font"],
                        "route_feel": settings.get("route_feel", 100),
                        "vgear": gear["v"],
                       "vgear_max": gear["max"],
                       "click_off": not click["want"], "click_addr": click["addr"],
                         "multi_mode": multi["mode"], "multi_server": multi["server"],
                        "fan_on": fan["on"], "fan_host": fan["host"],
                        "fan_w": list(fan.get("w") or [80, 160, 240]),
                        "fan_coast": int(fan.get("coast") or 300)}, f)
    except Exception:
        pass


load_config()
