"""
Polls the Headcounter app (CCTV head counting) for per-camera counts.

`GET /api/status` returns, for every camera, how many heads are in view right
now (`current_head_count`). Headcounter itself never reports an overcrowding
alert, so we compare each camera's count with the limits from config and log
when a camera goes over or comes back under.

Note: Headcounter only runs detection while someone is watching a camera's
video. If nobody has the feed open, its counts stop changing.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from typing import Dict

from server.alert_log import CLEARED, RAISED, AlertLog, OverLimitLatch
from server.source_status import SourceStatus

log = logging.getLogger(__name__)

SOURCE = "headcounter"
REQUEST_TIMEOUT_SECONDS = 5


class HeadcounterFeed(threading.Thread):
    def __init__(
        self,
        base_url: str,
        poll_seconds: float,
        default_limit: int,
        camera_limits: Dict[str, int],
        hold_seconds: float,
        alerts: AlertLog,
        status: SourceStatus,
    ):
        super().__init__(name="headcounter-feed", daemon=True)
        self._status_url = f"{base_url}/api/status"
        self._poll_seconds = poll_seconds
        self._default_limit = default_limit
        self._camera_limits = camera_limits
        self._hold_seconds = hold_seconds
        self._alerts = alerts
        self._status = status
        self._latches: Dict[str, OverLimitLatch] = {}

    def run(self) -> None:
        while True:
            try:
                self._poll_once()
            except Exception as error:
                log.warning("Headcounter unavailable: %s", error)
                self._status.offline(SOURCE, str(error))
            time.sleep(self._poll_seconds)

    def _poll_once(self) -> None:
        with urllib.request.urlopen(self._status_url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = json.load(response)

        cameras = status.get("cameras", {})
        readings = []
        for camera_id, camera in cameras.items():
            name = camera.get("name") or camera_id
            count = int(camera.get("current_head_count", 0))
            limit = self._camera_limits.get(camera_id, self._default_limit)
            readings.append({"camera_id": camera_id, "name": name, "count": count, "limit": limit})
            self._check_camera(camera_id, name, count, limit)

        self._status.online(SOURCE, {
            "cameras": readings,
            "total_in_view": int(status.get("total_head_count_all", 0)),
        })

    def _check_camera(self, camera_id: str, name: str, count: int, limit: int) -> None:
        latch = self._latches.setdefault(camera_id, OverLimitLatch(self._hold_seconds))
        if not latch.update(count, limit):
            return

        if latch.is_over:
            message = f"{count} heads in view on {name} (limit {limit})."
        else:
            message = f"Back under the limit: {count} heads in view on {name} (limit {limit})."
        self._alerts.record(
            source=SOURCE,
            place=name,
            state=RAISED if latch.is_over else CLEARED,
            count=count,
            limit=limit,
            message=message,
        )
