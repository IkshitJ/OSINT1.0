"""
Fan-out of live events to every open browser.

Each browser connection subscribes and gets its own queue. Publishers never
block: if a browser stops reading and its queue fills up, that browser just
misses events and catches up from the snapshot when it reconnects.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Dict, Set, Tuple

Event = Tuple[str, Dict[str, Any]]  # (event name, JSON-serialisable payload)


class EventHub:
    def __init__(self, queue_size: int = 200):
        self._queue_size = queue_size
        self._subscribers: Set["queue.Queue[Event]"] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> "queue.Queue[Event]":
        q: "queue.Queue[Event]" = queue.Queue(maxsize=self._queue_size)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: "queue.Queue[Event]") -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, name: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait((name, payload))
            except queue.Full:
                pass
