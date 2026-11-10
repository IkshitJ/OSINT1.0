"""
Turns the sensor gates' frames into what the dashboard shows: each ghat's
live count on the map, and an entry in the overcrowding log when a ghat's
gate boards switch their alarm on or off.

A frame carries the gate boards' own numbers:

    inside  people inside the premises right now (entered - exited)
    in/out  people through the entry and exit gates
    limit   the overcrowding limit set on the board
    alert   true while the board's overcrowding alarm is on

The alarm decision is made by the firmware itself, so we log exactly when
the board switches it on and off rather than re-deciding here.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from server.alert_log import CLEARED, RAISED, AlertLog
from server.source_status import SourceStatus

SOURCE = "sensor"


class SensorWatch:
    def __init__(self, ghats: List[Dict[str, Any]], alerts: AlertLog, status: SourceStatus):
        self._names = {ghat["id"]: ghat["name"] for ghat in ghats}
        self._alerts = alerts
        self._status = status
        self._lock = threading.Lock()
        self._readings: Dict[str, Optional[Dict[str, Any]]] = {ghat["id"]: None for ghat in ghats}
        self._alarm_on: Dict[str, bool] = {}

    def handle_frame(self, ghat_id: str, frame: Optional[Dict[str, Any]]) -> None:
        """`frame` is a simulator frame, or None when that ghat's simulator has stopped."""
        if frame is None:
            reading = None
        elif frame.get("fw") == "cal":
            reading = {"mode": "calibration"}  # the calibration sketch doesn't count anyone
        else:
            reading = {
                "mode": "counting",
                "inside": int(frame.get("inside", 0)),
                "limit": int(frame.get("limit", 0)),
                "alarm": bool(frame.get("alert")),
                "entered": int(frame.get("in", 0)),
                "exited": int(frame.get("out", 0)),
            }

        with self._lock:
            self._readings[ghat_id] = reading
            sites = dict(self._readings)
            alarm_on = bool(reading and reading.get("alarm"))
            alarm_changed = reading is not None and reading["mode"] == "counting" and alarm_on != self._alarm_on.get(ghat_id, False)
            if alarm_changed:
                self._alarm_on[ghat_id] = alarm_on

        if any(sites.values()):
            self._status.online(SOURCE, {"sites": sites})
        else:
            self._status.offline(SOURCE, "the sensor simulators are not running")

        if not alarm_changed:
            return
        place, inside, limit = self._names[ghat_id], reading["inside"], reading["limit"]
        if alarm_on:
            message = f"{inside:,} people inside {place} (limit {limit:,}). Hold the entry gate; keep the exit open."
        else:
            message = f"Back under the limit: {inside:,} people inside {place} (limit {limit:,})."
        self._alerts.record(
            source=SOURCE,
            place=place,
            state=RAISED if alarm_on else CLEARED,
            count=inside,
            limit=limit,
            message=message,
        )
