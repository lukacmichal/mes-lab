"""How the MES itself is doing: failed requests in the last minutes. Shown on
every page, because when the MES fails, the interlocked stations stop."""

from __future__ import annotations

import threading
import time
from collections import deque


class ErrorWindow:
    def __init__(self, window_s: float = 600):
        self.window_s = window_s
        self.items: deque[tuple[float, str]] = deque()
        self.lock = threading.Lock()

    def add(self, path: str) -> None:
        with self.lock:
            self.items.append((time.monotonic(), path))
            self._trim()

    def _trim(self) -> None:
        limit = time.monotonic() - self.window_s
        while self.items and self.items[0][0] < limit:
            self.items.popleft()

    def summary(self) -> dict:
        with self.lock:
            self._trim()
            paths: dict[str, int] = {}
            for _, path in self.items:
                paths[path] = paths.get(path, 0) + 1
        return {"count": sum(paths.values()), "paths": paths, "window_min": int(self.window_s // 60)}


ERRORS = ErrorWindow()
