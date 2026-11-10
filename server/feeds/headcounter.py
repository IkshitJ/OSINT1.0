"""
Watches the CCTV head counts for overcrowding.

The cameras report how many heads are in view right now; they have no idea
of "too many". So every couple of seconds we compare each camera's count with
its limit from the settings and log when a camera goes over or comes back under.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict

from server.alert_log import CLEARED, RAISED, AlertLog, OverLimitLatch
from server.source_status import SourceStatus

log = logging.getLogger(__name__)

SOURCE = "headcounter"


class HeadcounterWatch(threading.Thread):
    def __init__(
        self,
        read_status: Callable[[], Dict[str, Any]],
        poll_seconds: float,
        default_limit: int,
        camera_limits: Dict[str, int],
        hold_seconds: float,
        alerts: AlertLog,
        status: SourceStatus,
    ):
        super().__init__(name="headcounter-watch", daemon=True)
        self._read_status = read_status
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
                self._check_once()
            except Exception as error:
                log.warning("CCTV head counts unavailable: %s", error)
                self._status.offline(SOURCE, str(error))
            time.sleep(self._poll_seconds)

    def _check_once(self) -> None:
        status = self._read_status()
        readings = []
        for camera_id, camera in status.get("cameras", {}).items():
            name = camera.get("name") or camera_id
            count = int(camera.get("current_head_count", 0))
            limit = self._camera_limits.get(camera_id, self._default_limit)
            has_video = bool(camera.get("has_video"))
            readings.append({
                "camera_id": camera_id,
                "ghat_id": camera.get("ghat_id"),
                "name": name,
                "count": count,
                "limit": limit,
                "has_video": has_video,
            })
            if has_video:
                self._check_camera(camera_id, camera.get("location") or name, count, limit)

        self._status.online(SOURCE, {
            "cameras": readings,
            "total_in_view": int(status.get("total_head_count_all", 0)),
        })

    def _check_camera(self, camera_id: str, place: str, count: int, limit: int) -> None:
        latch = self._latches.setdefault(camera_id, OverLimitLatch(self._hold_seconds))
        if not latch.update(count, limit):
            return

        if latch.is_over:
            message = f"{count} heads in view on the {place} camera (limit {limit})."
        else:
            message = f"Back under the limit: {count} heads in view on the {place} camera (limit {limit})."
        self._alerts.record(
            source=SOURCE,
            place=place,
            state=RAISED if latch.is_over else CLEARED,
            count=count,
            limit=limit,
            message=message,
        )
