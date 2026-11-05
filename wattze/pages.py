import os
import re
import socket

from wattze.state import *

FONT_SCALES = {"klein": 1.0, "middel": 1.2, "groot": 1.45}
FONT_SIZE_RE = re.compile(r"font-size:\s*(\d+(?:\.\d+)?)px")


def font_scale():
    return FONT_SCALES.get(settings.get("overlay_font", "middel"), 1.2)


def render_page(page):
    ts = font_scale()
    page = FONT_SIZE_RE.sub(lambda m: "font-size:calc(%spx*var(--ts))" % m.group(1), page)
    return page.replace('<meta charset="utf-8">',
                        '<meta charset="utf-8"><style>:root{--ts:%g}</style>' % ts, 1)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def load_page(name):
    with open(os.path.join(PAGE_DIR, name), encoding="utf-8") as f:
        return f.read()


def _load(name):
    return load_page(name)


PAGE = _load("dashboard.html")
PAGE_LIVE = _load("live.html")
PAGE_START = _load("start.html")
PAGE_RITTEN = _load("ritten.html")
