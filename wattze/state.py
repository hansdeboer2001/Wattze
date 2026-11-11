import os
import sys
import threading

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    ROOT = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ROOT = APP_DIR

BIKE_DATA = "00002ad2-0000-1000-8000-00805f9b34fb"  # FTMS Indoor Bike Data
CPM = "00002a63-0000-1000-8000-00805f9b34fb"        # Cycling Power Measurement
CP_UUID = "00002ad9-0000-1000-8000-00805f9b34fb"   # FTMS Control Point
WAHOO_UUID = "a026e005-0a7d-4ab3-97fa-f1500f9feb8b"  # Wahoo trainer control
WAHOO_UNLOCK = bytes([0x20, 0xEE, 0xFC])
PORT = int(os.environ.get("KICKR_PORT") or 8080)

# Zwift Click (virtuele versnelling): eigen service, plus/minus als protobuf-notificaties
CLICK_ASYNC = "00000002-19ca-4651-86e5-fa29dcdd09d1"
CLICK_RX = "00000003-19ca-4651-86e5-fa29dcdd09d1"
CLICK_TX = "00000004-19ca-4651-86e5-fa29dcdd09d1"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"

HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"  # Heart Rate Measurement
HR_SVC = "0000180d-0000-1000-8000-00805f9b34fb"

# ---- gedeelde state ----
state = {"power": 0, "cadence": 0.0, "speed": 0.0, "hr": 0,
         "connected": False, "device": "", "kickr_addr": "", "kickr_name": "",
         "hr_connected": False, "hr_dev": "", "hr_addr": "", "hr_name": "",
         "hr_t": 0.0, "kickr_err": ""}
ride = {"t0": None, "samples": 0, "sum_p": 0.0, "max_p": 0}
history = []  # [(t, power)], max 900
listeners = []
lock = threading.RLock()

# ---- control state (SIM-modus) ----
ctrl = {"want_sim": False, "sim_active": False, "target_grade": 0.0,
        "last_sent": None, "last_sent_t": 0.0, "last_cp": None, "dbg": "-",
        "want_erg": False, "erg_active": False, "target_power": 0,
        "last_erg": None, "last_erg_t": 0.0, "last_wahoo": None,
        "sim_command": None}

# ---- ritten recorder (1 Hz) + TCX-export ----
RIDE_DIR = os.path.join(APP_DIR, "WattzeRitten")
_legacy = os.path.join(os.path.expanduser("~"), "Documents", "WattzeRitten")
if not os.path.isdir(RIDE_DIR) and os.path.isdir(_legacy):
    try:
        os.rename(_legacy, RIDE_DIR)
    except OSError:
        pass
os.makedirs(RIDE_DIR, exist_ok=True)
INDEX_FILE = os.path.join(RIDE_DIR, "rides.json")

rec = {"active": False, "name": "", "type": "", "t0": 0.0,
       "samples": [], "last_d": 0.0, "game_dist": None,
       "game_ele": None, "game_spd": None, "game_power": None, "game_cad": None}


ROUTE_DIR = os.path.join(ROOT, "routes")
PAGE_DIR = os.path.join(ROOT, "pages")
LOG_FILE = os.path.join(RIDE_DIR if getattr(sys, "frozen", False) else ROOT, "wattze.log")

# ---- KOM-tijden (koms.json): {"kom_<route>|<km>|<seed>_<idx>": [{time, avgP, date, user}, ...]} ----
KOM_FILE = os.path.join(RIDE_DIR, "koms.json")
kom_lock = threading.Lock()

# ---- apparaatvoorkeur (blijft bewaard tussen starts) ----
CONFIG_FILE = os.path.join(RIDE_DIR, "config.json")
sel = {"kickr": None, "hr": None}  # adressen, of "__off" voor HR-uit
settings = {"username": "", "weight": 85, "overlay_font": "middel", "route_feel": 100}
rebind = {"kickr": False, "hr": False}
gear = {"v": 12, "max": 24}  # virtuele versnelling, gedeeld met de telefoonafstandsbediening
click = {"want": True, "connected": False, "device": "", "addr": None,
         "battery": None, "err": "", "plus": False, "minus": False}

# ---- ventilator via ESPHome (4 knoppen: uit, 1, 2, 3) ----
fan = {"on": False, "host": "", "level": 0, "connected": False, "err": "",
       "force_level": None, "force_until": 0.0, "w": [80, 160, 240],
       "mode": "auto", "coast": 300, "coasting": False,
       "disconnected_at": None}

# ---- multiplayer: vaste host, niet instelbaar ----
MP_HOST = "https://test.wattze.com"
multi = {"server": MP_HOST}
