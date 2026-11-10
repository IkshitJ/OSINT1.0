"""
Whether each data source is working, plus its latest headline numbers.

The dashboard header shows one status pill per source, and the map shows the
latest readings on the ghat markers; both come from here.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from server.event_hub import EventHub


class SourceStatus:
    # Readings arrive many times a second; browsers only need about one per second.
    MIN_PUBLISH_INTERVAL = 1.0

    def __init__(self, hub: EventHub):
        self._hub = hub
        self._sources: Dict[str, Dict[str, Any]] = {}
        self._last_published: Dict[str, float] = {}
        self._lock = threading.Lock()

    def online(self, source: str, reading: Optional[Dict[str, Any]] = None) -> None:
        self._set(source, online=True, reading=reading, error=None)

    def offline(self, source: str, error: str) -> None:
        self._set(source, online=False, reading=None, error=error)

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {name: dict(state) for name, state in self._sources.items()}

    def _set(self, source: str, *, online: bool, reading: Optional[Dict[str, Any]], error: Optional[str]) -> None:
        now = time.time()
        state = {"source": source, "online": online, "reading": reading, "error": error, "updated_ms": int(now * 1000)}
        with self._lock:
            previous = self._sources.get(source)
            self._sources[source] = state
            went_online_or_offline = previous is None or previous["online"] != online
            due = now - self._last_published.get(source, 0) >= self.MIN_PUBLISH_INTERVAL
            if not (went_online_or_offline or due):
                return
            self._last_published[source] = now
        self._hub.publish("source", state)
