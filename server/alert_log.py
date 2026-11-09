"""
The shared overcrowding log.

Feeds call `record()` when a place goes over its limit ("raised") or comes back
under it ("cleared"). The log keeps the most recent events, and pushes each new
one to every open dashboard through the event hub.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Deque, Dict, List, Optional

from server.event_hub import EventHub

RAISED = "raised"
CLEARED = "cleared"


@dataclass(frozen=True)
class AlertEvent:
    id: int
    time_ms: int       # when it happened, Unix epoch milliseconds
    source: str        # "sensor" or "headcounter"
    place: str         # human-readable location, e.g. "Ram Ghat" or a camera name
    state: str         # RAISED or CLEARED
    count: int         # people counted at that moment
    limit: int         # the limit that was crossed
    message: str


class AlertLog:
    def __init__(self, hub: EventHub, max_events: int):
        self._hub = hub
        self._events: Deque[AlertEvent] = deque(maxlen=max_events)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def record(self, *, source: str, place: str, state: str, count: int, limit: int, message: str) -> AlertEvent:
        event = AlertEvent(
            id=next(self._ids),
            time_ms=int(time.time() * 1000),
            source=source,
            place=place,
            state=state,
            count=count,
            limit=limit,
            message=message,
        )
        with self._lock:
            self._events.append(event)
        self._hub.publish("alert", asdict(event))
        return event

    def recent(self) -> List[Dict[str, Any]]:
        """All kept events, oldest first."""
        with self._lock:
            return [asdict(e) for e in self._events]


class OverLimitLatch:
    """
    Tracks whether one count is over its limit, without flapping.

    Two rules keep a noisy count (e.g. a camera's per-frame head count) from
    raising and clearing an alert every few seconds:

    - Hysteresis: it goes over at the limit, but only comes back under below
      95 % of it (the same rule the Sensor firmware uses).
    - Hold time: the count must stay on the new side for `hold_seconds`
      before the state changes. A single noisy reading resets the wait.
    """

    CLEAR_RATIO = 0.95

    def __init__(self, hold_seconds: float = 0.0):
        self.hold_seconds = hold_seconds
        self.is_over = False
        self._changing_since: Optional[float] = None  # when the count first moved to the other side

    def update(self, count: int, limit: int, now: Optional[float] = None) -> bool:
        """Feeds a new count; returns True if the over/under state just changed."""
        now = time.monotonic() if now is None else now

        if limit <= 0:
            wants_over = False
        elif self.is_over:
            wants_over = count >= limit * self.CLEAR_RATIO
        else:
            wants_over = count >= limit

        if wants_over == self.is_over:
            self._changing_since = None
            return False

        if self._changing_since is None:
            self._changing_since = now
        if now - self._changing_since < self.hold_seconds:
            return False

        self.is_over = wants_over
        self._changing_since = None
        return True
