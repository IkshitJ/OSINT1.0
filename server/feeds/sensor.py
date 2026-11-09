"""
Reads the Sensor app's live feed (the load-cell gates at Ram Ghat).

The Sensor app streams one JSON frame every 40 ms over Server-Sent Events at
`/api/stream`. Each frame carries the gate boards' own numbers:

    inside  people inside the premises right now (entered - exited)
    in/out  people through the entry and exit gates
    limit   the overcrowding limit set on the board
    alert   1 while the board's overcrowding alarm is on

The alarm decision is made by the firmware itself, so we log exactly when
the board switches it on and off rather than re-deciding here.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request

from server.alert_log import CLEARED, RAISED, AlertLog
from server.source_status import SourceStatus

log = logging.getLogger(__name__)

SOURCE = "sensor"
RECONNECT_DELAY_SECONDS = 3
# The Sensor app sends a keep-alive every 5 s, so 15 s of silence means it is gone.
READ_TIMEOUT_SECONDS = 15


class SensorFeed(threading.Thread):
    def __init__(self, base_url: str, place: str, alerts: AlertLog, status: SourceStatus):
        super().__init__(name="sensor-feed", daemon=True)
        self._stream_url = f"{base_url}/api/stream"
        self._place = place
        self._alerts = alerts
        self._status = status
        self._alarm_on = False

    def run(self) -> None:
        while True:
            try:
                self._listen()
                self._status.offline(SOURCE, "stream ended")
            except Exception as error:  # network errors, bad frames, restarts
                log.warning("Sensor feed unavailable: %s", error)
                self._status.offline(SOURCE, str(error))
            time.sleep(RECONNECT_DELAY_SECONDS)

    def _listen(self) -> None:
        with urllib.request.urlopen(self._stream_url, timeout=READ_TIMEOUT_SECONDS) as response:
            log.info("Connected to Sensor feed at %s", self._stream_url)
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").strip()
                if line.startswith("data:"):
                    self._handle_frame(json.loads(line[len("data:"):]))

    def _handle_frame(self, frame: dict) -> None:
        # The calibration sketch doesn't count anyone; ignore its frames.
        if frame.get("fw") == "cal":
            self._status.online(SOURCE, {"mode": "calibration"})
            return

        inside = int(frame.get("inside", 0))
        limit = int(frame.get("limit", 0))
        alarm_on = bool(frame.get("alert"))

        self._status.online(SOURCE, {
            "mode": "counting",
            "inside": inside,
            "limit": limit,
            "alarm": alarm_on,
            "entered": int(frame.get("in", 0)),
            "exited": int(frame.get("out", 0)),
        })

        if alarm_on == self._alarm_on:
            return
        self._alarm_on = alarm_on

        if alarm_on:
            message = f"{inside:,} people inside {self._place} (limit {limit:,}). Hold the entry gate; keep the exit open."
        else:
            message = f"Back under the limit: {inside:,} people inside {self._place} (limit {limit:,})."
        self._alerts.record(
            source=SOURCE,
            place=self._place,
            state=RAISED if alarm_on else CLEARED,
            count=inside,
            limit=limit,
            message=message,
        )
