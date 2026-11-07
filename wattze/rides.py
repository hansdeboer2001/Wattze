import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from wattze.state import *
from wattze.storage import load_index, save_index, slugify

TCX_NS = "{http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2}"

def fmt_time(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_tcx(samples):
    NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
    TPX = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"
    ET.register_namespace("", NS)
    ET.register_namespace("tpx", TPX)
    tcd = ET.Element(f"{{{NS}}}TrainingCenterDatabase")
    acts = ET.SubElement(tcd, "Activities")
    act = ET.SubElement(acts, "Activity", Sport="Biking")
    ET.SubElement(act, "Id").text = fmt_time(samples[0][0])
    lap = ET.SubElement(act, "Lap", StartTime=fmt_time(samples[0][0]))
    dur = max(1.0, samples[-1][0] - samples[0][0])
    ET.SubElement(lap, "TotalTimeSeconds").text = f"{dur:.1f}"
    ET.SubElement(lap, "DistanceMeters").text = f"{samples[-1][4]:.1f}"
    ET.SubElement(lap, "MaximumSpeed").text = f"{max(s[3] for s in samples):.2f}"
    joules = sum(s[1] for s in samples) * (dur / len(samples))
    ET.SubElement(lap, "Calories").text = f"{joules / 4184 / 0.24:.0f}"
    hrs = [s[6] for s in samples if s[6] > 0]
    if hrs:
        ah = ET.SubElement(lap, "AverageHeartRateBpm")
        ET.SubElement(ah, "Value").text = str(round(sum(hrs) / len(hrs)))
        mh = ET.SubElement(lap, "MaximumHeartRateBpm")
        ET.SubElement(mh, "Value").text = str(max(hrs))
    ET.SubElement(lap, "Intensity").text = "Active"
    ET.SubElement(lap, "TriggerMethod").text = "Manual"
    track = ET.SubElement(lap, "Track")
    for (t, p, c, sp, d, e, h) in samples:
        tp = ET.SubElement(track, "Trackpoint")
        ET.SubElement(tp, "Time").text = fmt_time(t)
        ET.SubElement(tp, "DistanceMeters").text = f"{d:.1f}"
        ET.SubElement(tp, "AltitudeMeters").text = f"{e:.1f}"
        ET.SubElement(tp, "Cadence").text = str(max(0, c))
        if h > 0:
            hb = ET.SubElement(tp, "HeartRateBpm")
            ET.SubElement(hb, "Value").text = str(h)
        ext = ET.SubElement(tp, "Extensions")
        tpx = ET.SubElement(ext, f"{{{TPX}}}TPX")
        ET.SubElement(tpx, f"{{{TPX}}}Watts").text = str(max(0, p))
        ET.SubElement(tpx, f"{{{TPX}}}Speed").text = f"{sp:.2f}"
    return ET.tostring(tcd, encoding="utf-8", xml_declaration=True)


def rec_start(name, rtype):
    with lock:
        if rec["active"]:
            rec_stop()
        now = time.time()
        rec.update(active=True, paused=False,
                   name=name or datetime.now().strftime("Rit %Y-%m-%d %H:%M"),
                   type=rtype or "rit", t0=now, samples=[],
                   last_d=0.0, game_dist=None, game_ele=None, game_spd=None,
                   game_power=None, game_cad=None)
        return rec["name"]


def rec_pause():
    with lock:
        if not rec["active"]:
            return False
        rec["paused"] = True
        for k in ("game_dist", "game_ele", "game_spd", "game_power", "game_cad"):
            rec[k] = None
        return True


def rec_resume():
    with lock:
        if not rec["active"]:
            return False
        rec["paused"] = False
        return True


def rec_stop():
    with lock:
        if not rec["active"]:
            return None
        rec["active"] = False
        rec["paused"] = False
        for k in ("game_dist", "game_ele", "game_spd", "game_power", "game_cad"):
            rec[k] = None
        samples = rec["samples"]
        name, rtype, t0 = rec["name"], rec["type"], rec["t0"]
        if len(samples) < 5:
            return None
        dur = max(1.0, samples[-1][0] - samples[0][0])
        dist = samples[-1][4]
        avg = sum(s[1] for s in samples) / len(samples)
        mx = max(s[1] for s in samples)
        hrs = [s[6] for s in samples if s[6] > 0]
        avg_hr = round(sum(hrs) / len(hrs)) if hrs else 0
        stamp = datetime.fromtimestamp(t0).strftime("%Y-%m-%d_%H%M%S")
        fname = f"{stamp}_{slugify(name)}.tcx"
        path = os.path.join(RIDE_DIR, fname)
        i = 1
        while os.path.exists(path):
            i += 1
            fname = f"{stamp}_{slugify(name)}_{i}.tcx"
            path = os.path.join(RIDE_DIR, fname)
        with open(path, "wb") as f:
            f.write(build_tcx(samples))
        rides = load_index()
        rides.insert(0, {"file": fname, "name": name, "type": rtype,
                         "date": datetime.fromtimestamp(t0).strftime("%Y-%m-%d %H:%M"),
                         "duration": round(dur), "distance": round(dist),
                         "avg_w": round(avg), "max_w": mx, "avg_hr": avg_hr,
                         "size": os.path.getsize(path)})
        save_index(rides)
        return {"ok": True, "file": fname, "name": name, "duration": round(dur),
                "distance": round(dist), "avg_w": round(avg), "max_w": mx,
                "avg_hr": avg_hr}


def rec_sample_loop():
    while True:
        time.sleep(1.0)
        with lock:
            if not rec["active"] or rec.get("paused"):
                continue
            now = time.time()
            if rec["type"] == "game" and rec["game_dist"] is not None:
                d = rec["game_dist"]
                e = rec["game_ele"] or 0.0
                kmh = rec["game_spd"] if rec["game_spd"] is not None else state["speed"]
            else:
                d = rec["last_d"] + state["speed"] / 3.6
                e = 0.0
                kmh = state["speed"]
            rec["last_d"] = d
            pw = rec["game_power"] if rec["game_power"] is not None else state["power"]
            cad = rec["game_cad"] if rec["game_cad"] is not None else state["cadence"]
            rec["samples"].append((now, int(pw), int(round(cad)),
                                  round(kmh / 3.6, 2), round(d, 1), round(e, 1),
                                  int(state.get("hr", 0))))


def _tcx_power_samples(path):
    try:
        root = ET.parse(path).getroot()
        out = []
        for tp in root.iter(TCX_NS + "Trackpoint"):
            ts = tp.find(TCX_NS + "Time")
            if ts is None or not ts.text:
                continue
            try:
                t = datetime.fromisoformat(ts.text.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            watts = 0
            for node in tp.iter():
                if node.tag.endswith("Watts"):
                    try:
                        watts = max(0, float(node.text or 0))
                    except (TypeError, ValueError):
                        pass
                    break
            out.append((t, watts))
        return out
    except (OSError, ET.ParseError):
        return []


def _best_power_window(samples, seconds):
    best = 0
    left = 0
    total = 0.0
    for right, (end, watts) in enumerate(samples):
        total += watts
        while left <= right and end - samples[left][0] > seconds:
            total -= samples[left][1]
            left += 1
        if end - samples[left][0] < seconds - 1:
            continue
        window = samples[left:right + 1]
        if any(window[i][0] - window[i - 1][0] > 2.0 for i in range(1, len(window))):
            continue
        best = max(best, round(total / len(window)))
    return best


def progress_summary():
    durations = [(5, "5 sec"), (60, "1 min"), (300, "5 min"), (1200, "20 min")]
    records = {label: {"seconds": seconds, "power": 0, "recent_power": 0,
                        "file": "", "date": "", "name": "", "recent_file": "",
                        "recent_date": "", "recent_name": ""}
               for seconds, label in durations}
    rides = load_index()
    totals = {"rides": 0, "duration": 0, "distance": 0, "avg_w": 0}
    recent_since = time.time() - 30 * 86400
    for ride in rides:
        path = os.path.join(RIDE_DIR, os.path.basename(str(ride.get("file") or "")))
        if not os.path.isfile(path):
            continue
        totals["rides"] += 1
        totals["duration"] += int(ride.get("duration") or 0)
        totals["distance"] += int(ride.get("distance") or 0)
        totals["avg_w"] += int(ride.get("avg_w") or 0)
        samples = _tcx_power_samples(path)
        for seconds, label in durations:
            power = _best_power_window(samples, seconds)
            if power > records[label]["power"]:
                records[label].update(power=power, file=ride.get("file", ""),
                                      date=ride.get("date", ""), name=ride.get("name", ""))
            if samples and samples[0][0] >= recent_since and power > records[label]["recent_power"]:
                records[label].update(recent_power=power, recent_file=ride.get("file", ""),
                                      recent_date=ride.get("date", ""), recent_name=ride.get("name", ""))
    if totals["rides"]:
        totals["avg_w"] = round(totals["avg_w"] / totals["rides"])
    return {"records": list(records.values()), "totals": totals}
