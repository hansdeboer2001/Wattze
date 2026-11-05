#!/usr/bin/env python3
"""Zet een GPX-rit om naar een routebestand voor Ride.

Gebruik: python gpx_to_route.py [route_GPX/rit.gpx] [routes/little.json] [--step 25]

Zonder argumenten opent een venster om het GPX-bestand te kiezen. De route
komt dan in routes/<naam van het GPX-bestand>.json. Geef je ook geen route
mee op de opdrachtregel, dan wordt die naam automatisch bepaald.

Bestaat het routebestand al, dan blijven id/name/blurb/order/segs behouden
en worden alleen de hoogtes (step + ele) vervangen.
"""
import argparse
import json
import math
import os
import xml.etree.ElementTree as ET

NS = "{http://www.topografix.com/GPX/1/1}"
ROOT = os.path.dirname(os.path.abspath(__file__))


def pick_gpx():
    """Open een bestandsvenster en laat de gebruiker een GPX-bestand kiezen."""
    import tkinter as tk
    from tkinter import filedialog

    start = os.path.join(ROOT, "route_GPX")
    root = tk.Tk()
    root.withdraw()
    try:
        path = filedialog.askopenfilename(
            title="Kies een GPX-bestand",
            initialdir=start if os.path.isdir(start) else ROOT,
            filetypes=[("GPX-bestanden", "*.gpx"), ("Alle bestanden", "*.*")],
        )
    finally:
        root.destroy()
    return path


def haversine(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def read_gpx(path):
    pts = []
    for p in ET.parse(path).getroot().iter(NS + "trkpt"):
        ele = p.find(NS + "ele")
        if ele is not None:
            pts.append((float(p.get("lat")), float(p.get("lon")), float(ele.text)))
    return pts


def resample(pts, step):
    dist = [0.0]
    for i in range(1, len(pts)):
        dist.append(dist[-1] + haversine(pts[i - 1], pts[i]))
    out, j = [], 0
    for k in range(int(dist[-1] // step) + 1):
        x = k * step
        while j < len(dist) - 2 and dist[j + 1] < x:
            j += 1
        span = dist[j + 1] - dist[j]
        f = (x - dist[j]) / span if span > 0 else 0
        out.append(pts[j][2] + (pts[j + 1][2] - pts[j][2]) * f)
    return out, dist[-1]


def smooth(ele, r):
    n = len(ele)
    return [sum(ele[max(0, i - r):i + r + 1]) / len(ele[max(0, i - r):i + r + 1]) for i in range(n)]


def find_climbs(ele, step, min_rise=40, min_grade=2.0):
    """Klimmen zoeken: dal -> top met >= min_rise m stijging, vlakke stukken aan begin/eind eraf."""
    w = max(1, round(500 / step))  # trimvenster ~500 m
    climbs, lo, hi, up = [], 0, 0, True
    for i, v in enumerate(ele):
        if v < ele[lo]:
            lo = i
        if v > ele[hi]:
            hi = i
        if up and (v < ele[hi] - 8 or i == len(ele) - 1):
            a, b = lo, hi
            while a + w < b and ele[a + w] - ele[a] < 0.01 * w * step:
                a += 1
            while b - w > a and ele[b] - ele[b - w] < 0.01 * w * step:
                b -= 1
            rise = ele[b] - ele[a]
            if b > a and rise >= min_rise and rise / ((b - a) * step) * 100 >= min_grade:
                climbs.append((a * step, b * step, rise))
            up, lo = False, i
        elif not up and v > ele[lo] + 8:
            up, hi = True, i
    return climbs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gpx", nargs="?", help="GPX-bestand; leeg laten opent een keuzevenster")
    ap.add_argument("route", nargs="?", help="doel-JSON; standaard routes/<gpx-naam>.json")
    ap.add_argument("--step", type=int, default=25)
    ap.add_argument("--smooth", type=int, default=0, help="middelen over +/- N punten")
    ap.add_argument("--name", help="routenaam (nieuwe route)")
    ap.add_argument("--world", default="", help="wereld voor de blurb, bijv. Watopia (nieuwe route)")
    ap.add_argument("--koms", action="store_true", help="KOM's opnieuw bepalen uit de klimmen")
    a = ap.parse_args()

    if not a.gpx:
        a.gpx = pick_gpx()
        if not a.gpx:
            print("geen bestand gekozen")
            return
    if not a.route:
        stem = os.path.splitext(os.path.basename(a.gpx))[0]
        a.route = os.path.join(ROOT, "routes", stem + ".json")

    ele, total = resample(read_gpx(a.gpx), a.step)
    if a.smooth:
        ele = smooth(ele, a.smooth)
    ele = [round(e, 1) for e in ele]
    gain = sum(max(0, ele[i] - ele[i - 1]) for i in range(1, len(ele)))
    grades = [(ele[i + 2] - ele[i - 2]) / (4 * a.step) * 100 for i in range(2, len(ele) - 2)]
    print("afstand %.0f m, %d punten, %.0f hm, hoogte %.1f..%.1f m, helling %.1f..%.1f %%"
          % (total, len(ele), gain, min(ele), max(ele), min(grades), max(grades)))

    climbs = find_climbs(ele, a.step)
    for i, (s, e, rise) in enumerate(climbs):
        print("klim %d: %.2f -> %.2f km, +%.0f m, %.1f %%" % (i + 1, s / 1000, e / 1000, rise, rise / (e - s) * 100))

    if os.path.isfile(a.route):
        with open(a.route, encoding="utf-8") as f:
            r = json.load(f)
    else:
        rid = os.path.splitext(os.path.basename(a.route))[0]
        blurb = ("%.1f km · %.0f hm" % (total / 1000, gain)).replace(".", ",")
        r = {"id": rid, "order": 99, "name": a.name or rid,
             "blurb": (a.world + " · " if a.world else "") + blurb, "segs": None}
        a.koms = True
    if a.koms:
        r["segs"] = [{"at": s, "end": e, "label": "KOM %d" % (i + 1)} for i, (s, e, _) in enumerate(climbs)] or None
    r.pop("grades", None)
    r["step"] = a.step
    r["ele"] = ele

    os.makedirs(os.path.dirname(os.path.abspath(a.route)), exist_ok=True)
    with open(a.route, "w", encoding="utf-8") as f:
        f.write("{\n")
        keys = list(r)
        for k in keys:
            f.write("  %s: %s%s\n" % (json.dumps(k), json.dumps(r[k], ensure_ascii=False, separators=(",", ":")),
                                      "," if k != keys[-1] else ""))
        f.write("}\n")
    print("geschreven:", a.route)


if __name__ == "__main__":
    main()
