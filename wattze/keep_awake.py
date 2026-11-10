"""Windows display/system idle inhibition for active rides, including pauses."""
import ctypes
import sys

from wattze.state import lock, rec

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def keep_awake_loop(stop):
    if sys.platform != "win32":
        return
    try:
        set_state = ctypes.WinDLL("kernel32", use_last_error=True).SetThreadExecutionState
        set_state.argtypes = [ctypes.c_uint]
        set_state.restype = ctypes.c_uint
    except (OSError, AttributeError) as exc:
        print("Schermwakkerhouding niet beschikbaar: %s" % exc, flush=True)
        return
    active = False
    reported = False
    try:
        while not stop.is_set():
            with lock:
                wanted = bool(rec["active"])
            if wanted != active:
                flags = ES_CONTINUOUS
                if wanted:
                    flags |= ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
                try:
                    if not set_state(flags):
                        raise ctypes.WinError(ctypes.get_last_error())
                    active = wanted
                    reported = False
                except OSError as exc:
                    if not reported:
                        print("Schermwakkerhouding mislukt; wordt opnieuw geprobeerd: %s" % exc, flush=True)
                        reported = True
            stop.wait(1.0)
    finally:
        # Execution-state requirements belong to the calling thread, not the process.
        if active:
            try:
                if not set_state(ES_CONTINUOUS):
                    raise ctypes.WinError(ctypes.get_last_error())
            except OSError as exc:
                print("Vrijgeven schermwakkerhouding mislukt: %s" % exc, flush=True)
