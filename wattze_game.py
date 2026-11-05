#!/usr/bin/env python3
"""Wattze: lokale wielerapp voor een smart trainer (BLE FTMS -> http://localhost:8080).

Pagina's: / = dashboard, /game = Ride (stuurt helling naar de trainer).
"""
import asyncio
import os
import sys
import threading
import time
import traceback
from http.server import ThreadingHTTPServer

from wattze.ble_click import click_loop
from wattze.fan import fan_loop
from wattze.ble_hr import hr_loop
from wattze.ble_wattze import ble_loop
from wattze.http_handler import Handler
from wattze.pages import lan_ip
from wattze.rides import rec_sample_loop
from wattze.state import LOG_FILE, PORT
from wattze.storage import load_config

def log(msg):
    try:
        print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)
    except Exception:
        pass


def _thread_exc(args):
    log("THREAD-CRASH %s" % (args.thread.name if args.thread else "?"))
    try:
        traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
    except Exception:
        pass


threading.excepthook = _thread_exc


def _main_exc(exc_type, exc, tb):
    log("CRASH in hoofdthread")
    try:
        traceback.print_exception(exc_type, exc, tb)
    except Exception:
        pass


sys.excepthook = _main_exc


class KickrServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        log("HTTP-FOUT van %s" % (client_address,))
        try:
            traceback.print_exc()
        except Exception:
            pass


def setup_logging():
    try:
        f = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
    except Exception:
        return
    log("=== start (pid %d, poort %d) ===" % (os.getpid(), PORT))


def main():
    load_config()
    setup_logging()
    threading.Thread(target=lambda: asyncio.run(ble_loop()), daemon=True).start()
    threading.Thread(target=lambda: asyncio.run(hr_loop()), daemon=True).start()
    threading.Thread(target=lambda: asyncio.run(click_loop()), daemon=True).start()
    threading.Thread(target=rec_sample_loop, daemon=True).start()
    threading.Thread(target=fan_loop, daemon=True).start()
    url = "http://localhost:%d/start?boot=1" % PORT
    frozen = getattr(sys, "frozen", False)
    try:
        srv = KickrServer(("0.0.0.0", PORT), Handler)
    except OSError:
        log("Poort %d is al in gebruik" % PORT)
        if frozen:
            import webbrowser
            webbrowser.open(url)
        return
    log("Start: http://localhost:%d/start  Dashboard: http://localhost:%d/" % (PORT, PORT))
    log("Telefoon: http://%s:%d/afstandsbediening" % (lan_ip(), PORT))
    if frozen:
        def _open_browser():
            time.sleep(0.8)
            import webbrowser
            webbrowser.open(url)
        threading.Thread(target=_open_browser, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
