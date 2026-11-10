import asyncio
import ipaddress
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from wattze.ble_wattze import scan_devices, scan_devices_cached
from wattze import strava, online_kom, trainer_diagnostics
from wattze.fan import fan_cmd, fan_failed, fan_visible
from wattze.pages import *
from wattze.rides import *
from wattze.state import *
from wattze.storage import *
from wattze.telemetry import *

def _shutdown_app():
    time.sleep(0.4)
    try:
        fan_cmd(0)
    except Exception:
        pass
    try:
        rec_stop()
    except Exception:
        pass
    os._exit(0)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, "application/json", json.dumps(obj).encode())

    def _online_kom(self, method):
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                raise ValueError()
            host = self.headers.get("Host", "")
            parsed = urlparse("http://" + host)
            if (parsed.hostname not in ("localhost", "127.0.0.1", "::1") or
                    (parsed.port or 80) != self.server.server_address[1] or parsed.username or
                    parsed.password or parsed.path or parsed.query or parsed.fragment):
                raise ValueError()
            origin = self.headers.get("Origin")
            if origin is not None and origin != "http://" + host:
                raise ValueError()
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise ValueError()
        except ValueError:
            self.close_connection = True
            return self._json({"ok": False, "error": "Online KOM requires a same-origin loopback connection"}, 403)
        body = None
        if method == "POST":
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self.close_connection = True
                return self._json({"ok": False, "error": "application/json is required"}, 415)
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 < n <= 2 * 1024 * 1024 or self.headers.get("Transfer-Encoding"):
                    raise ValueError()
                body = json.loads(self.rfile.read(n))
                if not isinstance(body, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                self.close_connection = True
                return self._json({"ok": False, "error": "Invalid online KOM JSON body"}, 400)
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        result, status = online_kom.handle(method, parsed.path[len("/online-kom"):], body, query)
        return self._json(result, status)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/online-kom/"):
            return self._online_kom("GET")
        if path == "/":
            remote_url = "http://%s:%d/afstandsbediening" % (lan_ip(), PORT)
            page = (PAGE.replace("__LANURL__", remote_url)
                    .replace("__FONTSCALE__", "%g" % font_scale()))
            self._send(200, "text/html; charset=utf-8", render_page(page).encode())
        elif path == "/live":
            self._send(200, "text/html; charset=utf-8", render_page(PAGE_LIVE).encode())
        elif path == "/progress":
            self._send(200, "text/html; charset=utf-8", render_page(PAGE_PROGRESS).encode())
        elif path == "/start":
            remote_url = "http://%s:%d/afstandsbediening" % (lan_ip(), PORT)
            page = PAGE_START.replace("__LANURL__", remote_url)
            self._send(200, "text/html; charset=utf-8", render_page(page).encode())
        elif path == "/game":
            mpage = {"mode": multi["mode"], "server": multi["server"],
                     "name": settings["username"]}
            page = (load_page("game.html").replace("__ROUTES__", json.dumps(load_routes(), separators=(",", ":")))
                    .replace("__SETTINGS__", json.dumps(settings).replace("</", "<\\/"))
                    .replace("__MULTI__", json.dumps(mpage, separators=(",", ":")).replace("</", "<\\/"))
                    .replace("__LANURL__", "http://%s:%d" % (lan_ip(), PORT))
                    .replace("__FONTSCALE__", "%g" % font_scale())
                    .replace("__VGEAR__", str(gear["v"]))
                    .replace("__VGEAR_MAX__", str(gear["max"]))
                    .replace("__KOMS__", json.dumps(load_koms(), ensure_ascii=False).replace("</", "<\\/")))
            self._send(200, "text/html; charset=utf-8", render_page(page).encode())
        elif self.path in ("/afstandsbediening", "/remote"):
            self._send(200, "text/html; charset=utf-8", load_page("remote.html").encode())
        elif self.path == "/gear":
            self._json({"ok": True, "v": gear["v"], "max": gear["max"]})
        elif self.path == "/click":
            with lock:
                c = {k: click[k] for k in ("want", "connected", "device", "battery", "err", "addr")}
            self._json({"ok": True, **c})
        elif self.path == "/blokjes":
            self._send(200, "text/html; charset=utf-8", render_page(load_page("blokjes.html")).encode())
        elif self.path == "/ritten":
            self._send(200, "text/html; charset=utf-8", render_page(PAGE_RITTEN).encode())
        elif self.path == "/instellingen":
            page = load_page("settings.html").replace("__LANURL__", "http://%s:%d" % (lan_ip(), PORT))
            self._send(200, "text/html; charset=utf-8", render_page(page).encode())
        elif path == "/klassementen":
            self._send(200, "text/html; charset=utf-8", render_page(load_page("online-kom.html")).encode())
        elif self.path == "/settings":
            with lock:
                fstat = {k: fan[k] for k in ("on", "host", "level", "connected", "err", "w", "force_level", "force_until", "mode", "coast", "coasting")}
            self._json({**settings, "multi": dict(multi), "fan": fstat,
                        "vgear": gear["v"], "vgear_max": gear["max"]})
        elif path == "/progress/data":
            self._json(progress_summary())
        elif path == "/progress/rides":
            self._json({"rides": progress_rides()})
        elif self.path == "/devices":
            with lock:
                sel_now = dict(sel)
                kickr_conn = state["connected"]
                kickr_addr = state.get("kickr_addr", "")
                kickr_name = state.get("kickr_name", "")
                hr_conn = state.get("hr_connected", False)
                hr_addr = state.get("hr_addr", "")
                hr_name = state.get("hr_name", "")
                click_now = {k: click[k] for k in ("want", "connected", "device", "battery", "err", "addr")}
                fan_now = ({k: fan[k] for k in ("on", "host", "level", "connected", "err")}
                           if fan.get("host") else None)
                connected_any = bool(kickr_conn or hr_conn or click_now.get("connected"))
            try:
                raw = scan_devices_cached(max_age=10.0 if connected_any else 2.5)
            except Exception as e:
                return self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)
            devices = []
            present = set()
            for d in raw.get("devices", []):
                if d.get("kind") not in ("trainer", "hr", "click"):
                    continue
                addr = (d.get("address") or "").upper()
                item = dict(d)
                item["seen"] = True
                if d["kind"] == "trainer":
                    item["selected"] = bool(sel_now.get("kickr") and addr == sel_now["kickr"].upper())
                    item["connected"] = bool(kickr_conn and kickr_addr and addr == kickr_addr.upper())
                elif d["kind"] == "hr":
                    hr_sel = sel_now.get("hr")
                    item["selected"] = bool(hr_sel and hr_sel != "__off" and addr == hr_sel.upper())
                    item["connected"] = bool(hr_conn and hr_addr and addr == hr_addr.upper())
                else:
                    item["selected"] = bool(click_now.get("addr") and addr == str(click_now["addr"]).upper())
                    item["connected"] = bool(click_now.get("connected") and item["selected"])
                devices.append(item)
                present.add(addr)

            def add_missing(value, name, kind, selected):
                a = (value or "").upper()
                if not a or a in present:
                    return
                present.add(a)
                if kind == "trainer":
                    conn = bool(kickr_conn and kickr_addr and a == kickr_addr.upper())
                elif kind == "hr":
                    conn = bool(hr_conn and hr_addr and a == hr_addr.upper())
                else:
                    conn = bool(click_now.get("connected")
                                and a == str(click_now.get("addr") or "").upper())
                devices.append({"address": a, "name": name or "(onbekend)",
                                "rssi": None, "kind": kind, "selected": bool(selected),
                                "connected": conn, "seen": False})

            if sel_now.get("kickr"):
                add_missing(sel_now["kickr"], kickr_name, "trainer", True)
            if kickr_conn and kickr_addr:
                add_missing(kickr_addr, kickr_name, "trainer", False)
            hr_sel = sel_now.get("hr")
            if hr_sel and hr_sel != "__off":
                add_missing(hr_sel, hr_name, "hr", True)
            if hr_conn and hr_addr:
                add_missing(hr_addr, hr_name, "hr", False)
            if click_now.get("addr"):
                add_missing(click_now["addr"], click_now.get("device") or "Zwift Click", "click", True)

            order = {"trainer": 0, "hr": 1, "click": 2}
            devices.sort(key=lambda x: (order.get(x.get("kind"), 9),
                                        0 if x.get("connected") else (1 if x.get("selected") else 2)))
            self._json({"ok": True, "devices": devices, "ts": raw.get("ts"),
                        "age": raw.get("age", 0.0), "cached": raw.get("cached", False),
                        "busy": raw.get("busy", False), "error": raw.get("error", ""), "sel": sel_now,
                        "click": click_now, "fan": fan_now,
                        "remote": "http://%s:%d/afstandsbediening" % (lan_ip(), PORT)})
        elif urlparse(self.path).path == "/qr":
            q = parse_qs(urlparse(self.path).query)
            text = (q.get("text") or [""])[0][:512]
            if not text:
                return self._send(400, "text/plain", b"text?")
            try:
                import io
                import segno
            except ImportError:
                return self._send(503, "text/plain", b"segno ontbreekt")
            try:
                buf = io.BytesIO()
                segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2)
            except Exception as e:
                return self._send(500, "text/plain", ("qr-fout: %s" % e).encode())
            self._send(200, "image/svg+xml", buf.getvalue())
        elif self.path == "/ritten/list":
            rides = load_index()
            for r in rides:
                r["shot"] = os.path.isfile(os.path.join(RIDE_DIR, str(r.get("file", ""))[:-4] + ".png"))
            self._json({"rides": rides})
        elif path == "/ritten/screenshot":
            fn = os.path.basename(parse_qs(urlparse(self.path).query).get("f", [""])[0])
            spath = os.path.join(RIDE_DIR, fn)
            if not fn.endswith(".png") or not os.path.isfile(spath):
                return self._send(404, "text/plain", b"not found")
            with open(spath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Disposition", f'attachment; filename="{fn}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/uploadstrava":
            self._send(200, "text/html; charset=utf-8", render_page(load_page("uploadstrava.html")).encode())
        elif path == "/strava/status":
            t = strava.load_tokens()
            self._json({"connected": bool(t.get("access_token")), "athlete": t.get("athlete") or "",
                        "configured": bool(strava.STRAVA_CLIENT_ID and strava.STRAVA_CLIENT_SECRET)})
        elif path == "/strava/auth":
            if not (strava.STRAVA_CLIENT_ID and strava.STRAVA_CLIENT_SECRET):
                return self._send(500, "text/plain; charset=utf-8",
                                  "STRAVA_CLIENT_ID/STRAVA_CLIENT_SECRET ontbreken (zie .env.example)".encode())
            host = self.headers.get("Host") or "localhost:%d" % PORT
            self.send_response(302)
            self.send_header("Location", strava.auth_url("http://%s/strava/callback" % host))
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/strava/callback":
            code = parse_qs(urlparse(self.path).query).get("code", [""])[0]
            if not code:
                return self._send(400, "text/plain; charset=utf-8", "Geen toestemming van Strava".encode())
            try:
                strava.exchange_code(code)
            except Exception as e:
                return self._send(502, "text/plain; charset=utf-8", ("Strava-fout: %s" % e).encode())
            self.send_response(302)
            self.send_header("Location", "/uploadstrava")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path.startswith("/ritten/download"):
            q = parse_qs(urlparse(self.path).query)
            fn = os.path.basename(q.get("f", [""])[0])
            path = os.path.join(RIDE_DIR, fn)
            if not fn.endswith(".tcx") or not os.path.isfile(path):
                return self._send(404, "text/plain", b"not found")
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.garmin.tcx+xml")
            self.send_header("Content-Disposition", f'attachment; filename="{fn}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api":
            with lock:
                expire_cadence()
                snap = dict(state)
                snap["avg"] = round(ride["sum_p"] / ride["samples"], 1) if ride["samples"] else 0
                snap["max"] = ride["max_p"]
                snap["hist"] = [p for _, p in history[-180:]]
                snap["ctrl"] = dict(ctrl)
                snap["trainer_diagnostics"] = trainer_diagnostics.status()
                snap["gear"] = gear["v"]
                snap["click"] = {k: click[k] for k in ("want", "connected", "device", "battery", "err", "addr")}
                now2 = time.time()
                snap["dev"] = {
                    "kickr": {"connected": state["connected"], "label": state["device"],
                              "addr": state.get("kickr_addr", ""),
                              "age": round(now2 - state.get("updated", 0), 1),
                              "err": state.get("kickr_err", "")},
                    "hr": {"connected": state.get("hr_connected", False),
                           "label": state.get("hr_dev", ""),
                           "addr": state.get("hr_addr", ""),
                           "bpm": state.get("hr", 0),
                           "age": round(now2 - state.get("hr_t", 0), 1)}}
                snap["sel"] = dict(sel)
                snap["fan"] = {k: fan[k] for k in ("on", "host", "level", "connected", "err", "w", "force_level", "force_until", "mode", "coast", "coasting")}
                snap["fan"]["visible"] = fan_visible()
            self._json(snap)
        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            q = []
            with lock:
                listeners.append(q)
            try:
                last_beat = 0.0
                while True:
                    if q:
                        with lock:
                            evt = q.pop(0)
                        self.wfile.write(("data: " + json.dumps(evt) + "\n\n").encode())
                        self.wfile.flush()
                    elif time.time() - last_beat >= 3.0:
                        last_beat = time.time()
                        self.wfile.write(("data: " + json.dumps(snapshot()) + "\n\n").encode())
                        self.wfile.flush()
                    else:
                        time.sleep(0.2)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with lock:
                    if q in listeners:
                        listeners.remove(q)
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        if urlparse(self.path).path.startswith("/online-kom/"):
            return self._online_kom("POST")
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json({"ok": False, "error": "bad json"}, 400)
        if self.path == "/trainer-diagnostics":
            if not isinstance(body, dict) or body.get("cmd") not in ("start", "stop"):
                return self._json({"ok": False, "error": "cmd?"}, 400)
            result = (trainer_diagnostics.start() if body["cmd"] == "start"
                      else trainer_diagnostics.stop())
            return self._json({"ok": not bool(result["error"]), **result})
        if self.path == "/shutdown":
            threading.Thread(target=_shutdown_app, daemon=True).start()
            return self._json({"ok": True, "bye": True})
        if self.path == "/control":
            cmd = body.get("cmd")
            with lock:
                if cmd == "start":
                    ctrl["want_sim"] = True
                    ctrl["want_erg"] = False
                elif cmd == "stop":
                    ctrl["want_sim"] = False
                    ctrl["want_erg"] = False
                elif cmd == "start_erg":
                    ctrl["want_erg"] = True
                    ctrl["want_sim"] = False
                elif cmd == "stop_erg":
                    ctrl["want_erg"] = False
                else:
                    return self._json({"ok": False, "error": "cmd?"}, 400)
                trainer_diagnostics.record("control", command=cmd)
            return self._json({"ok": True, "want_sim": ctrl["want_sim"],
                               "want_erg": ctrl["want_erg"]})
        if self.path == "/gear":
            try:
                if "v" in body:
                    v = int(round(float(body["v"])))
                else:
                    v = gear["v"] + int(round(float(body.get("delta", 0))))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "v?"}, 400)
            set_gear(value=v)
            return self._json({"ok": True, "v": gear["v"], "max": gear["max"]})
        if self.path == "/click":
            if "on" in body:
                with lock:
                    click["want"] = bool(body.get("on"))
                    if not click["want"]:
                        click.update(connected=False, plus=False, minus=False)
                save_config()
                push(snapshot())
            if isinstance(body.get("addr"), str):
                with lock:
                    click["addr"] = body["addr"].strip().upper() or None
                save_config()
            with lock:
                c = {k: click[k] for k in ("want", "connected", "device", "battery", "err", "addr")}
            return self._json({"ok": True, **c})
        if self.path == "/grade":
            try:
                g = max(-12.0, min(18.0, float(body.get("grade", 0))))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "grade?"}, 400)
            with lock:
                ctrl["target_grade"] = g
                if isinstance(body.get("ele"), (int, float)):
                    rec["game_ele"] = float(body["ele"])
                if isinstance(body.get("dist"), (int, float)):
                    rec["game_dist"] = float(body["dist"])
                if isinstance(body.get("spd"), (int, float)):
                    rec["game_spd"] = float(body["spd"])
                trainer_diagnostics.record("grade_requested", source=trainer_diagnostics.grade_source(body))
            return self._json({"ok": True, "target_grade": g})
        if self.path == "/progress/exclusions":
            files = body.get("files")
            if not isinstance(files, list):
                return self._json({"ok": False, "error": "files?"}, 400)
            try:
                rides = set_progress_excluded(files)
            except OSError:
                return self._json({"ok": False, "error": "opslaan mislukt"}, 500)
            return self._json({"ok": True, "rides": rides, "progress": progress_summary()})
        if self.path == "/routes/save":
            name = str(body.get("name") or "").strip()[:40]
            ele, segs = body.get("ele"), body.get("segs") or None
            if not name or not isinstance(ele, list) or len(ele) < 10 or \
                    not all(isinstance(v, (int, float)) for v in ele):
                return self._json({"ok": False, "error": "naam of hoogteprofiel ontbreekt"}, 400)
            step = int(body.get("step") or 25)
            rid = "eigen-" + slugify(name)
            path = os.path.join(ROUTE_DIR, rid + ".json")
            if os.path.exists(path):
                return self._json({"ok": False, "error": "er bestaat al een route met deze naam"}, 409)
            total = (len(ele) - 1) * step
            gain = sum(max(0, ele[i] - ele[i - 1]) for i in range(1, len(ele)))
            r = {"id": rid, "order": 50, "name": name,
                 "blurb": ("Eigen · %.1f km · %.0f hm" % (total / 1000, gain)).replace(".", ",", 1),
                 "segs": segs, "step": step, "ele": [round(v, 1) for v in ele]}
            with open(path, "w", encoding="utf-8") as f:
                f.write("{\n")
                keys = list(r)
                for k in keys:
                    f.write("  %s: %s%s\n" % (json.dumps(k), json.dumps(r[k], ensure_ascii=False, separators=(",", ":")),
                                              "," if k != keys[-1] else ""))
                f.write("}\n")
            return self._json({"ok": True, "id": rid})
        if self.path == "/ritten/pos":
            with lock:
                if not rec["active"]:
                    for k in ("game_dist", "game_ele", "game_spd", "game_power", "game_cad"):
                        rec[k] = None
                    return self._json({"ok": True})
                for k in ("dist", "ele", "spd", "power", "cad"):
                    v = body.get(k)
                    if isinstance(v, (int, float)):
                        rec["game_" + k] = float(v)
                    elif k in ("power", "cad"):
                        rec["game_" + k] = None  # val terug op vermogen/cadans van de trainer
            return self._json({"ok": True})
        if self.path == "/erg/power":
            try:
                p = max(0, min(1000, int(float(body.get("power", 0)))))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "power?"}, 400)
            with lock:
                ctrl["target_power"] = p
            return self._json({"ok": True, "target_power": p})
        if self.path == "/devices/scan":
            try:
                return self._json(asyncio.run(scan_devices(timeout=12)))
            except Exception as e:
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
        if self.path == "/devices/select":
            changed = False
            with lock:
                if "kickr" in body:
                    k = body.get("kickr")
                    sel["kickr"] = str(k).upper() if k else None
                    rebind["kickr"] = True
                    changed = True
                if "hr" in body:
                    h = body.get("hr")
                    sel["hr"] = "__off" if h == "__off" else (str(h).upper() if h else None)
                    rebind["hr"] = True
                    changed = True
            if changed:
                save_config()
            return self._json({"ok": True, "sel": dict(sel)})
        if self.path == "/settings":
            try:
                w = float(body.get("weight", settings["weight"]))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "gewicht?"}, 400)
            of = str(body.get("overlay_font", settings["overlay_font"]))
            if of not in ("klein", "middel", "groot"):
                return self._json({"ok": False, "error": "tekstgrootte?"}, 400)
            try:
                rf = int(body.get("route_feel", settings.get("route_feel", 100)))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "routegevoel?"}, 400)
            if not 0 <= rf <= 100:
                return self._json({"ok": False, "error": "routegevoel moet 0-100% zijn"}, 400)
            gm = None
            if "vgear_max" in body:
                try:
                    gm = int(body.get("vgear_max"))
                except (TypeError, ValueError):
                    return self._json({"ok": False, "error": "aantal versnellingen?"}, 400)
                if not 2 <= gm <= 24:
                    return self._json({"ok": False, "error": "aantal versnellingen moet 2-24 zijn"}, 400)
            mode = str(body.get("multi_mode", multi["mode"]))
            if mode not in ("off", "server"):
                return self._json({"ok": False, "error": "multiplayer-modus?"}, 400)
            multi["mode"], multi["server"] = mode, MP_HOST
            settings["username"] = str(body.get("username", settings["username"])).strip()[:24]
            settings["weight"] = max(50.0, min(140.0, w))
            settings["overlay_font"] = of
            settings["route_feel"] = rf
            if "fan_w" in body:
                raw = body.get("fan_w")
                try:
                    vals = [int(round(float(x))) for x in raw]
                except (TypeError, ValueError):
                    return self._json({"ok": False, "error": "wattages?"}, 400)
                if len(vals) != 3 or not (1 <= vals[0] < vals[1] < vals[2] <= 2000):
                    return self._json({"ok": False, "error": "stand 1 < stand 2 < stand 3"}, 400)
                fan["w"] = vals
            if "fan_on" in body:
                with lock:
                    fan["on"] = bool(body.get("fan_on"))
                    if not fan["on"]:
                        fan["disconnected_at"] = None
            if "fan_host" in body:
                with lock:
                    host = multi_host(body.get("fan_host"))
                    if host != fan["host"]:
                        fan.update(host=host, connected=False, sent=None, disconnected_at=None)
            if "fan_coast" in body:
                try:
                    fan["coast"] = max(0, min(1800, int(body.get("fan_coast"))))
                except (TypeError, ValueError):
                    return self._json({"ok": False, "error": "uitdraaitijd?"}, 400)
            gear_changed = False
            if gm is not None:
                with lock:
                    if gm != gear["max"]:
                        gear["max"] = gm
                        gear["v"] = max(1, min(gm, (gm + 1) // 2))
                        gear_changed = True
                    else:
                        v = max(1, min(gm, gear["v"]))
                        if v != gear["v"]:
                            gear["v"] = v
                            gear_changed = True
            save_config()
            if gear_changed:
                push(snapshot())
            with lock:
                 fstat = {k: fan[k] for k in ("on", "host", "level", "connected", "err", "w", "force_level", "force_until", "mode", "coast", "coasting")}
            return self._json({"ok": True, **settings, "multi": dict(multi), "fan": fstat,
                               "vgear": gear["v"], "vgear_max": gear["max"]})
        if self.path == "/fan":
            try:
                level = int(body.get("level"))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "stand?"}, 400)
            if level not in (0, 1, 2, 3):
                return self._json({"ok": False, "error": "stand?"}, 400)
            hold = bool(body.get("hold"))
            with lock:
                if not fan["host"]:
                    return self._json({"ok": False, "error": "geen adres"}, 400)
                fan["force_level"] = level
                if hold:
                    fan["mode"] = "off_hold" if level == 0 else "manual"
                    fan["force_until"] = 0.0
                else:
                    fan["mode"] = "auto"
                    fan["force_until"] = time.time() + 20
            try:
                fan_cmd(level)
            except Exception as e:
                fan_failed(e)
                return self._json({"ok": False, "error": fan["err"]}, 502)
            with lock:
                mode = fan.get("mode") or "auto"
            return self._json({"ok": True, "level": level, "mode": mode})
        if self.path in ("/koms/record", "/koms/import", "/koms/clear"):
            with kom_lock:
                d = load_koms()
                if self.path == "/koms/record":
                    key = str(body.get("key", ""))
                    if not key.startswith("kom_"):
                        return self._json({"ok": False, "error": "key?"}, 400)
                    merge_kom(d, key, [body.get("entry")])
                elif self.path == "/koms/import":
                    for key, entries in (body.get("data") or {}).items():
                        if str(key).startswith("kom_") and isinstance(entries, list):
                            merge_kom(d, str(key), entries)
                else:
                    prefix = str(body.get("prefix", ""))
                    if not prefix.startswith("kom_"):
                        return self._json({"ok": False, "error": "prefix?"}, 400)
                    d = {k: v for k, v in d.items() if not k.startswith(prefix)}
                save_koms(d)
            return self._json({"ok": True, "koms": d})
        if self.path == "/ritten/start":
            name = str(body.get("name") or "")
            rtype = str(body.get("type") or "rit")
            return self._json({"ok": True, "name": rec_start(name, rtype)})
        if self.path == "/ritten/pause":
            return self._json({"ok": bool(rec_pause())})
        if self.path == "/ritten/resume":
            return self._json({"ok": bool(rec_resume())})
        if self.path == "/ritten/stop":
            s = rec_stop()
            return self._json(s or {"ok": False, "error": "geen opname"})
        if self.path == "/ritten/delete":
            fn = os.path.basename(str(body.get("file") or ""))
            path = os.path.join(RIDE_DIR, fn)
            if not fn.endswith(".tcx") or not os.path.isfile(path):
                return self._json({"ok": False, "error": "niet gevonden"}, 404)
            try:
                os.remove(path)
            except OSError:
                return self._json({"ok": False, "error": "wissen mislukt"}, 500)
            try:
                os.remove(path[:-4] + ".png")
            except OSError:
                pass
            save_index([r for r in load_index() if r.get("file") != fn])
            return self._json({"ok": True})
        if self.path == "/strava/upload":
            fn = os.path.basename(str(body.get("file") or ""))
            path = os.path.join(RIDE_DIR, fn)
            if not fn.endswith(".tcx") or not os.path.isfile(path):
                return self._json({"ok": False, "error": "niet gevonden"}, 404)
            try:
                res = strava.upload_tcx(path, str(body.get("name") or ""),
                                        str(body.get("description") or "")[:2000])
                activity_id, err = strava.wait_for_activity(res.get("id")) if res.get("id") else (None, None)
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 502)
            if err:
                return self._json({"ok": False, "error": err}, 502)
            return self._json({"ok": True, "activity_id": activity_id,
                               "url": "https://www.strava.com/activities/%s" % activity_id if activity_id else ""})
        if self.path == "/ritten/screenshot":
            fn = os.path.basename(str(body.get("file") or ""))
            data = str(body.get("png") or "")
            prefix = "data:image/png;base64,"
            if not fn.endswith(".tcx") or not os.path.isfile(os.path.join(RIDE_DIR, fn)) or not data.startswith(prefix):
                return self._json({"ok": False, "error": "ongeldig"}, 400)
            import base64
            try:
                png = base64.b64decode(data[len(prefix):], validate=True)
            except ValueError:
                return self._json({"ok": False, "error": "ongeldig"}, 400)
            with open(os.path.join(RIDE_DIR, fn[:-4] + ".png"), "wb") as f:
                f.write(png)
            return self._json({"ok": True})
        return self._json({"ok": False}, 404)
