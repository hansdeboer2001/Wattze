"""Opt-in online KOM proxy. Credentials never enter application settings."""
import hashlib
import http.client
import importlib
import json
import math
import os
import re
import tempfile
import threading
import uuid
from urllib import error, parse, request

app_state = importlib.import_module("wattze.state")
_lock = threading.RLock()
MAX_PENDING = 1000
MAX_RETRY = 20
MAX_RESPONSE = 1024 * 1024
MAX_ERROR_RESPONSE = 8192
TIMEOUT = 3

# Only fixed server messages are safe to display; never echo remote details.
_SERVER_ERRORS = {
    "friend code not found": "Vriendcode niet gevonden. Controleer de code.",
    "invalid friend code": "Ongeldige vriendcode.",
    "cannot friend yourself": "Je kunt jezelf niet als vriend toevoegen.",
    "relationship already exists": "Deze vriendschap of dit vriendschapsverzoek bestaat al.",
    "no incoming request": "Er is geen inkomend vriendschapsverzoek van deze renner.",
    "invalid rider id": "Ongeldige renner.",
    "official route not found": "De officiele route is niet gevonden op de server.",
    "segment not found": "Het KOM-segment is niet gevonden op de server.",
    "route revision mismatch": "De routeversie wijkt af van de server. Werk de routes bij.",
    "invalid route id": "Ongeldige route.",
    "invalid revision": "Ongeldige routeversie.",
    "invalid segment": "Ongeldig KOM-segment.",
    "invalid scope": "Ongeldig klassement.",
    "invalid query": "Ongeldige klassementsaanvraag.",
    "bearer token required": "Herstel je online KOM-profiel met je herstelcode.",
    "invalid bearer token": "Ongeldige herstelcode voor online KOM.",
    "invalid authorization": "Ongeldige aanmeldgegevens voor online KOM.",
    "invalid name": "Ongeldige naam. Gebruik 1 tot 40 tekens zonder controletekens.",
    "consent must be boolean": "Ongeldige instellingen voor het delen van resultaten.",
    "implausible time": "De opgegeven KOM-tijd is niet aannemelijk.",
    "invalid average power": "Het gemiddelde vermogen moet tussen 1 en 2500 watt liggen.",
    "unsupported attempt rules": "Deze KOM-poging gebruikt niet-ondersteunde regels.",
    "invalid attempt UUID": "Ongeldig kenmerk van de KOM-poging.",
    "attempt id conflict": "Dit kenmerk is al gebruikt voor een andere KOM-poging.",
    "consent changed": "De instellingen voor delen zijn gewijzigd. Deze oude poging wordt niet gedeeld.",
    "rate limit exceeded": "Te veel aanvragen. Probeer het later opnieuw.",
    "storage unavailable": "De serveropslag is tijdelijk niet beschikbaar. Probeer het later opnieuw.",
    "invalid fields": "De aanvraag bevat ongeldige velden.",
    "invalid request": "Ongeldige aanvraag voor online KOM.",
    "invalid JSON": "Ongeldige aanvraag voor online KOM.",
}


class OnlineError(Exception):
    def __init__(self, message, status=400, transient=False):
        super().__init__(message)
        self.status = status
        self.transient = transient


def _profile(value):
    if not isinstance(value, dict) or not all(isinstance(value.get(k), str)
                                             for k in ("id", "name", "friend_code")):
        raise OnlineError("Ongeldig antwoord van de online KOM-server", 502)
    if any(type(value.get(k)) is not bool for k in ("share_general", "share_friends")):
        raise OnlineError("Ongeldig antwoord van de online KOM-server", 502)
    if type(value.get("consent_version")) is not int or value["consent_version"] < 0:
        raise OnlineError("Ongeldig antwoord van de online KOM-server", 502)
    return {k: value[k] for k in ("id", "name", "friend_code", "share_general", "share_friends", "consent_version")}


def _host():
    return (os.environ.get("WATTZE_KOM_HOST", "").strip() or app_state.MP_HOST).rstrip("/")


def _storage_path():
    host = _host()
    # wattze.com keeps the production profile. Every other host, including the
    # current default, keeps its own file so an existing test login is reused.
    filename = "online-kom.json" if host == "https://wattze.com" else (
        "online-kom-" + hashlib.sha256(host.encode()).hexdigest()[:16] + ".json")
    return os.path.join(app_state.RIDE_DIR, filename)


def _load():
    try:
        with open(_storage_path(), encoding="utf-8") as f:
            data = json.load(f)
        token = data["token"]
        profile = _profile(data["profile"])
        pending = data.get("pending", [])
        recovery_pending = data.get("recovery_pending", False)
        if (not isinstance(token, str) or not token or not isinstance(pending, list)
                or type(recovery_pending) is not bool):
            raise ValueError()
        return {"token": token, "profile": profile, "pending": pending[-MAX_PENDING:],
                "recovery_pending": recovery_pending}
    except FileNotFoundError:
        return {"token": "", "profile": None, "pending": [], "recovery_pending": False}
    except (ValueError, KeyError, TypeError, OnlineError):
        # Do not silently replace a damaged identity with a new registration.
        raise OnlineError("De lokale online KOM-opslag is beschadigd. Herstel eerst het lokale bestand", 500)


def _save(data):
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=app_state.RIDE_DIR,
                                         prefix=".online-kom-", delete=False) as f:
            name = f.name
            json.dump(data, f, separators=(",", ":"), allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, _storage_path())
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _remote(suffix, token="", body=None):
    host = _host()
    url = parse.urlsplit(host)
    if (url.scheme != "https" or not url.netloc or url.username or url.password
            or url.query or url.fragment or url.path):
        raise OnlineError("De online KOM-server moet HTTPS gebruiken", 503, True)
    headers = {"Accept": "application/json", "User-Agent": "Wattze/1.0 (+https://wattze.com)"}
    if token:
        headers["Authorization"] = "Bearer " + token
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        if suffix == "/attempts":
            headers["Idempotency-Key"] = body["attempt_id"]
        payload = json.dumps(body, allow_nan=False).encode()
    req = request.Request(host + "/api/kom/v1" + suffix, data=payload, headers=headers)
    try:
        # Never redirect bearer credentials, including to another HTTPS host.
        with request.build_opener(_NoRedirect()).open(req, timeout=TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise OnlineError("Het antwoord van de online KOM-server is te groot", 502, True)
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise OnlineError("Ongeldig antwoord van de online KOM-server", 502, True)
        return result
    except error.HTTPError as exc:
        message = ""
        try:
            raw = exc.read(MAX_ERROR_RESPONSE + 1)
            if len(raw) <= MAX_ERROR_RESPONSE:
                result = json.loads(raw)
                if isinstance(result, dict) and isinstance(result.get("error"), str):
                    message = result["error"]
        except (ValueError, UnicodeError, RecursionError, OSError, http.client.HTTPException):
            pass
        finally:
            exc.close()
        if exc.code == 404 and message in ("", "not found"):
            raise OnlineError("Online KOM is nog niet beschikbaar op deze server; een server-upgrade is nodig", 503) from None
        if exc.code == 403 and not message:
            raise OnlineError("Toegang tot de online KOM-server is geblokkeerd (HTTP 403). Controleer de Cloudflare-beveiliging.", 403) from None
        transient = (not 400 <= exc.code < 500 or exc.code in (408, 425, 429)
                     or (exc.code == 409 and message == "storage unavailable"))
        status = 503 if transient else (401 if exc.code in (401, 403) else
                                       exc.code if exc.code in (404, 409) else 400)
        raise OnlineError(_SERVER_ERRORS.get(message,
                          "Online KOM is tijdelijk niet beschikbaar" if transient else
                          "De aanvraag voor online KOM is geweigerd"), status, transient) from None
    except (error.URLError, TimeoutError, OSError, http.client.HTTPException):
        raise OnlineError("Online KOM is offline. Probeer het later opnieuw", 503, True) from None
    except (ValueError, UnicodeError, RecursionError):
        raise OnlineError("Ongeldig antwoord van de online KOM-server", 502, True) from None


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise OnlineError("Ongeldige getalswaarde")
    return float(value)


def _route(route_id, segment, revision=None, course=None):
    if not isinstance(route_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", route_id) or route_id.lower().startswith("eigen-"):
        raise OnlineError("Een officiele route is vereist")
    try:
        with open(os.path.join(app_state.ROUTE_DIR, route_id + ".json"), encoding="utf-8") as f:
            route = json.load(f)
    except (OSError, ValueError):
        raise OnlineError("Officiele route niet gevonden") from None
    if not isinstance(route, dict) or route.get("id") != route_id:
        raise OnlineError("Ongeldige officiele route")
    ele, segs = route.get("ele"), route.get("segs", [])
    if not isinstance(ele, list) or len(ele) < 2 or not isinstance(segs, list) or not segs:
        raise OnlineError("De officiele route heeft geen expliciete KOM-grenzen")
    ele = [_number(v) for v in ele]
    step = _number(route.get("step"))
    if step <= 0:
        raise OnlineError("Ongeldige puntafstand in de route")
    boundaries = []
    for s in segs:
        if not isinstance(s, dict):
            raise OnlineError("Ongeldig KOM-segment")
        at, end = _number(s.get("at")), _number(s.get("end"))
        if not 0 <= at < end <= (len(ele) - 1) * step:
            raise OnlineError("De officiele route heeft ongeldige KOM-grenzen")
        boundaries.append({"at": round(at, 6), "end": round(end, 6)})
    if type(segment) is not int or not 0 <= segment < len(segs):
        raise OnlineError("Ongeldig KOM-segment")
    canonical = {"ele": [round(v, 6) for v in ele], "step": round(step, 6), "segs": boundaries}
    actual = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    if revision is not None and revision != actual:
        raise OnlineError("De routeversie komt niet overeen")
    if course is not None:
        pts = course.get("pts") if isinstance(course, dict) else None
        if not isinstance(pts, list) or len(pts) != len(ele):
            raise OnlineError("Het parcours in de browser wijkt af van de officiele route")
        if any(abs(_number(v) - (e - ele[0])) > 1e-6 for v, e in zip(pts, ele)):
            raise OnlineError("Het parcours in de browser wijkt af van de officiele route")
        if abs(_number(course.get("step")) - step) > 1e-6:
            raise OnlineError("De puntafstand in de browser komt niet overeen")
        browser_segs = course.get("segs")
        if not isinstance(browser_segs, list) or len(browser_segs) != len(segs):
            raise OnlineError("De KOM-grenzen in de browser komen niet overeen")
        for browser, official in zip(browser_segs, segs):
            if not isinstance(browser, dict) or any(abs(_number(browser.get(k)) - _number(official[k])) > 1e-6 for k in ("at", "end")):
                raise OnlineError("De KOM-grenzen in de browser komen niet overeen")
    return actual


def _sharing(data):
    p = data["profile"] or {}
    return bool(data["token"] and (p.get("share_general") or p.get("share_friends")))


def _public(value):
    """Allow remote display data, but never forward credential fields."""
    if isinstance(value, dict):
        return {k: _public(v) for k, v in value.items()
                if not any(word in k.lower() for word in ("token", "secret", "authorization", "password", "credential"))}
    if isinstance(value, list):
        return [_public(v) for v in value]
    return value


def handle(method, suffix, body=None, query=None):
    """Called only after the HTTP handler's loopback and same-origin checks."""
    try:
        with _lock:
            return _handle(method, suffix, body or {}, query or {}), 200
    except OnlineError as exc:
        return {"ok": False, "error": str(exc)}, exc.status
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return {"ok": False, "error": "Online KOM kon de aanvraag niet voltooien"}, 500


def _handle(method, suffix, body, query):
    data = _load()
    if method == "GET" and suffix == "/status":
        return {"ok": True, "configured": bool(data["token"]), "profile": data["profile"],
                "pending": len(data["pending"]), "recovery_pending": data["recovery_pending"]}
    if method == "GET" and suffix == "/recovery":
        if not data["recovery_pending"]:
            raise OnlineError("Er is geen herstelcode om te bevestigen", 409)
        return {"ok": True, "token": data["token"]}
    if method == "POST" and suffix == "/recovery/confirm":
        if body:
            raise OnlineError("Ongeldige aanvraag voor online KOM")
        if not data["token"]:
            raise OnlineError("Registreer of herstel eerst een online KOM-profiel", 409)
        data["recovery_pending"] = False
        _save(data)
        return {"ok": True}
    if method == "POST" and suffix in ("/register", "/restore"):
        if data["token"]:
            raise OnlineError("Er is al een online KOM-profiel ingesteld. Vervangen is niet toegestaan", 409)
        if suffix == "/register":
            name = _text(body, "name", 80)
            result = _remote(suffix, body={"name": name})
            token = _text(result, "token", 4096)
        else:
            token = _text(body, "token", 4096)
            result = _remote(suffix, token, {})
        data.update(token=token, profile=_profile(result.get("profile")), pending=[],
                    recovery_pending=suffix == "/register")
        _save(data)
        result = {"ok": True, "profile": data["profile"]}
        if suffix == "/register":
            result["token"] = token
        return result
    if method == "POST" and suffix == "/attempt":
        if type(body.get("simulated")) is not bool:
            raise OnlineError("De simulatie-instelling moet een boolean zijn")
        if body["simulated"]:
            return {"ok": True, "skipped": "simulated", "pending": len(data["pending"])}
    public_board = method == "GET" and suffix == "/leaderboard" and query.get("scope", "general") == "general"
    if not data["token"] and not public_board:
        if method == "POST" and suffix in ("/attempt", "/retry"):
            return {"ok": True, "skipped": "not_registered", "pending": 0}
        raise OnlineError("Registreer of herstel eerst een online KOM-profiel", 409)
    if method == "GET" and suffix == "/me":
        result = _remote("/me", data["token"])
        profile = _profile(result.get("profile"))
        if profile != data["profile"]:
            data.update(profile=profile, pending=[])
            _save(data)
        return {"ok": True, "profile": profile, **{k: _public(result.get(k, [])) for k in ("friends", "incoming", "outgoing")}}
    if method == "GET" and suffix == "/leaderboard":
        try:
            segment = int(query.get("segment", ""))
        except (ValueError, TypeError):
            raise OnlineError("Ongeldig KOM-segment") from None
        route_id = query.get("route_id")
        revision = _route(route_id, segment, query.get("revision"))
        scope = query.get("scope", "general")
        if scope not in ("general", "friends"):
            raise OnlineError("Ongeldig klassement")
        result = _public(_remote("/leaderboard?" + parse.urlencode({"route_id": route_id, "revision": revision,
                          "segment": segment, "scope": scope}), data["token"]))
        return {**result, "ok": True, "revision": revision}
    if method == "POST" and suffix == "/profile":
        payload = {"name": _text(body, "name", 80)}
        for k in ("share_general", "share_friends"):
            if type(body.get(k)) is not bool:
                raise OnlineError("De instellingen voor delen moeten booleans zijn")
            payload[k] = body[k]
        # Clear before the request: even a timeout may have changed remote scopes.
        data["pending"] = []
        data["profile"] = {**data["profile"], "share_general": False, "share_friends": False}
        _save(data)
        result = _remote(suffix, data["token"], payload)
        data["profile"] = _profile(result.get("profile"))
        _save(data)
        return {"ok": True, "profile": data["profile"]}
    if method == "POST" and suffix in ("/friends/request", "/friends/accept", "/friends/remove"):
        key = "code" if suffix.endswith("request") else "id"
        return {**_public(_remote(suffix, data["token"], {key: _text(body, key, 128)})), "ok": True}
    if method == "POST" and suffix == "/attempt":
        if not _sharing(data):
            return {"ok": True, "skipped": "sharing_disabled", "pending": len(data["pending"])}
        if not isinstance(body.get("course"), dict):
            raise OnlineError("Het parcours uit de browser is vereist")
        revision = _route(body.get("route_id"), body.get("segment"), course=body["course"])
        seconds, power = _number(body.get("time")), _number(body.get("avgP"))
        if seconds <= 0 or power < 0:
            raise OnlineError("Ongeldige tijd of ongeldig vermogen voor de KOM-poging")
        attempt = {"attempt_id": str(uuid.uuid4()), "route_id": body["route_id"], "revision": revision,
                   "segment": body["segment"], "time": seconds, "avgP": power,
                   "simulated": False, "ruleset": "wattze-1",
                   **{k: data["profile"][k] for k in ("consent_version", "share_general", "share_friends")}}
        if len(data["pending"]) >= MAX_PENDING:
            return {"ok": True, "skipped": "outbox_full", "pending": len(data["pending"])}
        # Persist first so a lost response can be retried with the same UUID.
        data["pending"].append(attempt)
        _save(data)
        try:
            _remote("/attempts", data["token"], attempt)
        except OnlineError as exc:
            if exc.transient:
                return {"ok": True, "queued": True, "pending": len(data["pending"])}
            data["pending"].remove(attempt)
            _save(data)
            raise
        data["pending"].remove(attempt)
        _save(data)
        return {"ok": True, "queued": False, "pending": len(data["pending"])}
    if method == "POST" and suffix == "/retry":
        if not _sharing(data):
            data["pending"] = []
            _save(data)
            return {"ok": True, "sent": 0, "pending": 0}
        sent = 0
        for attempt in data["pending"][:MAX_RETRY]:
            try:
                _route(attempt["route_id"], attempt["segment"], attempt["revision"])
                if attempt.get("simulated") is not False or attempt.get("ruleset") != "wattze-1":
                    raise OnlineError("Ongeldige KOM-poging in de wachtrij")
                _remote("/attempts", data["token"], attempt)
                sent += 1
            except OnlineError as exc:
                if exc.transient:
                    break
                data["pending"].remove(attempt)
                _save(data)
                raise
            data["pending"].remove(attempt)
            _save(data)
        return {"ok": True, "sent": sent, "pending": len(data["pending"])}
    raise OnlineError("Onbekend online KOM-eindpunt", 404)


def _text(body, key, limit):
    value = body.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise OnlineError("Ongeldige waarde voor " + key)
    return value.strip()
