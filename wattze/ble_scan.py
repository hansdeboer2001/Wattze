import asyncio
import threading

_lock = threading.Lock()


async def acquire():
    """Wacht tot geen andere BLE-scan bezig is; overlappende scans verstoren de verbinding."""
    while not _lock.acquire(blocking=False):
        await asyncio.sleep(0.2)


def try_acquire(timeout=0.0):
    if timeout and timeout > 0:
        return _lock.acquire(timeout=timeout)
    return _lock.acquire(blocking=False)


def release():
    _lock.release()
