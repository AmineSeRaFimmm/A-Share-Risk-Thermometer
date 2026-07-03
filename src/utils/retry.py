from __future__ import annotations
import time
from collections.abc import Callable

def retry_call(fn: Callable, times: int = 3, sleep_seconds: float = 2.0):
    last_error = None
    for i in range(times):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - source adapters must isolate flaky vendors
            last_error = exc
            if i < times - 1:
                time.sleep(sleep_seconds)
    raise last_error
