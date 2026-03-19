import time
import threading


class RateLimiter:
    """Simple sliding-window rate limiter."""

    def __init__(self, requests_per_minute: int) -> None:
        self._rpm = requests_per_minute
        self._interval = 60.0 / requests_per_minute
        self._lock = threading.Lock()
        self._last_call: float = 0.0

    def acquire(self) -> None:
        """Block until the next request slot is available."""
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            wait = self._interval - elapsed
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
