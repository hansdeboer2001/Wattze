import json
import os
import time
import urllib.parse
import urllib.request
import uuid

STRAVA_CLIENT_ID = "294425"
STRAVA_CLIENT_SECRET = "5da497ad056a250280439454b89be990f41aed9d"
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_UPLOAD_URL = "https://www.strava.com/api/v3/uploads"

def _tokens():
    return getattr(_tokens, "data", {
        "access_token": None, "refresh_token": None,
        "expires_at": 0, "athlete": ""
    })

def _save_tokens(data):
    _tokens.data = data
    try:
        from wattze.state import CONFIG_FILE
        cfg = {}
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        cfg["strava_access_token"] = data.get("access_token")
        cfg["strava_refresh_token"] = data.get("refresh_token")
        cfg["strava_expires_at"] = data.get("expires_at")
        cfg["strava_athlete"] = data.get("athlete")
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception:
        pass

def load_tokens():
    try:
        from wattze.state import CONFIG_FILE
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("strava_access_token"):
                data = {
                    "access_token": cfg.get("strava_access_token"),
                    "refresh_token": cfg.get("strava_refresh_token"),
                    "expires_at": int(cfg.get("strava_expires_at") or 0),
                    "athlete": cfg.get("strava_athlete") or ""
                }
                _tokens.data = data
                return data
    except Exception:
        pass
    return _tokens()

def auth_url(redirect_uri):
    params = {
        "client_id": STRAVA_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "activity:write"
    }
    return STRAVA_AUTH_URL + "?" + urllib.parse.urlencode(params)

def exchange_code(code):
    data = urllib.parse.urlencode({
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code"
    }).encode()
    req = urllib.request.Request(STRAVA_TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        j = json.loads(resp.read().decode())
    athlete = ""
    if isinstance(j.get("athlete"), dict):
        a = j["athlete"]
        athlete = ((a.get("firstname") or "") + " " + (a.get("lastname") or "")).strip()
    tokens = {
        "access_token": j.get("access_token"),
        "refresh_token": j.get("refresh_token"),
        "expires_at": int(j.get("expires_at") or 0),
        "athlete": athlete
    }
    _save_tokens(tokens)
    return tokens

def refresh_token():
    data = load_tokens()
    if not data.get("refresh_token"):
        return None
    payload = urllib.parse.urlencode({
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": data["refresh_token"]
    }).encode()
    req = urllib.request.Request(STRAVA_TOKEN_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        j = json.loads(resp.read().decode())
    tokens = {
        "access_token": j.get("access_token"),
        "refresh_token": j.get("refresh_token") or data["refresh_token"],
        "expires_at": int(j.get("expires_at") or 0),
        "athlete": data.get("athlete") or ""
    }
    _save_tokens(tokens)
    return tokens

def get_token():
    data = load_tokens()
    if not data.get("access_token"):
        return None
    if time.time() > (data.get("expires_at") or 0) - 60:
        refreshed = refresh_token()
        if refreshed:
            return refreshed.get("access_token")
        return None
    return data.get("access_token")

def upload_tcx(file_path, name=""):
    token = get_token()
    if not token:
        raise RuntimeError("Niet verbonden met Strava")
    if not os.path.isfile(file_path):
        raise RuntimeError("Bestand niet gevonden")
    with open(file_path, "rb") as f:
        file_data = f.read()
    boundary = uuid.uuid4().hex
    parts = []
    def add_field(k, v):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    add_field("data_type", "tcx")
    add_field("name", name or os.path.basename(file_path))
    add_field("trainer", "1")
    add_field("external_id", os.path.basename(file_path))
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{os.path.basename(file_path)}\"\r\nContent-Type: application/vnd.garmin.tcx+xml\r\n\r\n".encode())
    parts.append(file_data)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(STRAVA_UPLOAD_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())
