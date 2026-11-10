"""
Runs every CCTV camera: one background thread reads a frame from each camera,
finds the heads in all of them with ONE model call, then tracks, counts and
draws per camera and keeps each camera's latest picture ready to send.

Why one thread and one model call:
  * Four cameras cost the GPU little more than one (frames go through the
    model as a batch), so the feeds stay smooth on a laptop.
  * Counting no longer depends on somebody watching: the overcrowding log
    keeps working with every browser closed. With nobody watching, the
    cameras are analysed only about once a second to save power.
  * Video files play at their real speed however many people are watching
    (frames are skipped to keep up instead of slowing the video down).
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

import cv2
import numpy as np

from cctv.config import (
    ACCELERATOR_DEVICE,
    ANALYSIS_FPS,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_IOU_THRESHOLD,
    DEFAULT_MODEL,
    IDLE_ANALYSIS_FPS,
    STREAM_JPEG_QUALITY,
    STREAM_MAX_WIDTH,
)
from cctv.engine.detector import HeadDetector
from cctv.engine.pipeline import VideoPipeline

log = logging.getLogger("cctv")

# A viewer that polls single frames counts as watching for this long after its last request.
POLL_VIEWER_SECONDS = 5.0
# After a pause (nobody watching, a slow model call) never fast-forward a file by more than this.
MAX_CATCH_UP_FRAMES = 6


class CameraManager:
    """
    cameras: [{"id": "cam1", "name": "CAM-01 · RAM GHAT", "location": "...",
               "ghat_id": "ram-ghat", "source": "/path/or/rtsp/url" or None,
               "no_source_hint": "what to tell the operator when there is no video"}]
    """

    def __init__(self, cameras: List[Dict[str, Any]]):
        log.info("Loading the head detector for %d cameras...", len(cameras))
        self.detector = HeadDetector(
            model_name=DEFAULT_MODEL,
            conf_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
            iou_threshold=DEFAULT_IOU_THRESHOLD,
            device=ACCELERATOR_DEVICE,
        )
        self.cameras: Dict[str, Dict[str, Any]] = {camera["id"]: dict(camera) for camera in cameras}
        self.camera_ids: List[str] = list(self.cameras)
        self.pipelines: Dict[str, VideoPipeline] = {}

        # Guards the pipelines: the worker holds it while it analyses a round of
        # frames, API calls hold it while they change a camera.
        self.lock = threading.RLock()

        # Latest picture per camera: (sequence number, JPEG bytes). The number
        # goes up with every new picture so viewers can tell when to send one.
        self._latest: Dict[str, Tuple[int, bytes]] = {}
        self._new_frame = threading.Condition()
        self._standby_shown: Dict[str, Any] = {}

        self._frames_due: Dict[str, float] = {}
        self._last_read: Dict[str, float] = {}

        self._viewers = 0
        self._last_polled = 0.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        for camera_id, camera in self.cameras.items():
            pipeline = VideoPipeline(
                detector=self.detector,
                conf_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
                camera_id=camera_id,
                camera_name=camera["name"],
            )
            self.pipelines[camera_id] = pipeline
            self._latest[camera_id] = (0, b"")
            if camera.get("source"):
                pipeline.open_source(camera["source"])
            log.info("Camera %s (%s): %s", camera_id, camera["name"], camera.get("source") or "no video yet")

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="cctv-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)
        with self.lock:
            for pipeline in self.pipelines.values():
                pipeline.close()

    # --- viewers -----------------------------------------------------------------

    @contextlib.contextmanager
    def watching(self) -> Iterator[None]:
        """Hold this open while a live stream is being sent to someone."""
        with self._new_frame:
            self._viewers += 1
        self._wake.set()
        try:
            yield
        finally:
            with self._new_frame:
                self._viewers -= 1

    def is_watched(self) -> bool:
        return self._viewers > 0 or time.monotonic() - self._last_polled < POLL_VIEWER_SECONDS

    # --- pictures ----------------------------------------------------------------

    def latest_frame(self, camera_id: str) -> bytes:
        """The camera's newest picture as a JPEG (for viewers that ask frame by frame)."""
        self._last_polled = time.monotonic()
        self._wake.set()
        return self._latest.get(camera_id, (0, b""))[1]

    def frames_after(self, seen: Dict[str, int]) -> List[Tuple[str, int, bytes]]:
        """Every camera picture newer than the sequence numbers in `seen`."""
        fresh = []
        for camera_id in self.camera_ids:
            sequence, jpeg = self._latest[camera_id]
            if jpeg and sequence != seen.get(camera_id):
                fresh.append((camera_id, sequence, jpeg))
        return fresh

    def wait_for_frame(self, camera_id: str, seen: int, timeout: float = 1.0) -> Tuple[int, bytes]:
        """Blocks until the camera has a picture newer than `seen` (or the timeout passes)."""
        with self._new_frame:
            self._new_frame.wait_for(lambda: self._latest[camera_id][0] != seen, timeout=timeout)
            return self._latest[camera_id]

    # --- cameras -----------------------------------------------------------------

    def pipeline(self, camera_id: Optional[str]) -> Optional[VideoPipeline]:
        return self.pipelines.get(camera_id or self.camera_ids[0])

    def set_source(self, camera_id: str, source: Any) -> bool:
        with self.lock:
            opened = self.pipelines[camera_id].open_source(source)
            self.cameras[camera_id]["source"] = source if opened else None
            self._frames_due[camera_id] = 0.0
            self._last_read.pop(camera_id, None)
            self._standby_shown.pop(camera_id, None)
        self._wake.set()
        return opened

    def status(self) -> Dict[str, Any]:
        """Live numbers for every camera plus the totals, as the CCTV page and the dashboard expect."""
        cameras: Dict[str, Any] = {}
        totals = {"heads": 0, "unique": 0, "in": 0, "out": 0}
        with self.lock:
            for camera_id, pipeline in self.pipelines.items():
                camera = self.cameras[camera_id]
                metrics = dict(pipeline.current_metrics)
                metrics.update({
                    "camera_id": camera_id,
                    "name": pipeline.camera_name,
                    "location": camera.get("location", ""),
                    "ghat_id": camera.get("ghat_id"),
                    "has_video": pipeline.has_source,
                    "no_video_hint": camera.get("no_source_hint", ""),
                    "is_paused": pipeline.is_paused,
                    "total_unique_heads": len(pipeline.counter.seen_track_ids),
                    "tripwire": {
                        "enabled": pipeline.raw_counting_line is not None,
                        "pt1": pipeline.raw_counting_line[0] if pipeline.raw_counting_line else None,
                        "pt2": pipeline.raw_counting_line[1] if pipeline.raw_counting_line else None,
                    },
                    "roi": {
                        "enabled": pipeline.raw_roi_polygon is not None,
                        "points": pipeline.raw_roi_polygon if pipeline.raw_roi_polygon else None,
                    },
                })
                totals["heads"] += metrics.get("current_head_count", 0)
                totals["unique"] += metrics["total_unique_heads"]
                totals["in"] += metrics.get("in_count", 0)
                totals["out"] += metrics.get("out_count", 0)
                cameras[camera_id] = metrics

            first = self.pipelines[self.camera_ids[0]]
            status = dict(first.current_metrics)
            status.update({
                "cameras": cameras,
                "total_head_count_all": totals["heads"],
                "total_unique_heads_all": totals["unique"],
                "total_in_all": totals["in"],
                "total_out_all": totals["out"],
                "model_name": self.detector.model_name,
                "device": self.detector.device,
                "conf_threshold": first.conf_threshold,
                "is_paused": first.is_paused,
                "total_unique_heads": len(first.counter.seen_track_ids),
                "tripwire": cameras[self.camera_ids[0]]["tripwire"],
                "roi": cameras[self.camera_ids[0]]["roi"],
                "watched": self.is_watched(),
            })
        return status

    # --- the worker ----------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            watched = self.is_watched()
            try:
                self._analyse_round(started, real_time=watched)
            except Exception:
                log.exception("CCTV analysis round failed")
            interval = 1.0 / (ANALYSIS_FPS if watched else IDLE_ANALYSIS_FPS)
            remaining = interval - (time.monotonic() - started)
            self._wake.clear()
            if watched:
                self._stop.wait(max(remaining, 0.001))
            elif remaining > 0:
                self._wake.wait(remaining)  # a new viewer ends the idle wait at once

    def _analyse_round(self, now: float, real_time: bool) -> None:
        with self.lock:
            frames: List[np.ndarray] = []
            camera_ids: List[str] = []
            for camera_id, pipeline in self.pipelines.items():
                if pipeline.is_paused:
                    continue  # the last picture stays on screen
                if not pipeline.has_source:
                    self._show_standby(camera_id, "no-video")
                    continue
                frame = self._next_frame(camera_id, pipeline, now, real_time)
                if frame is None:
                    continue
                frames.append(frame)
                camera_ids.append(camera_id)

            if not frames:
                return
            detections = self.detector.detect_batch(frames)
            fresh: Dict[str, bytes] = {}
            for camera_id, frame, heads in zip(camera_ids, frames, detections):
                annotated, _ = self.pipelines[camera_id].apply_detections(frame, heads)
                fresh[camera_id] = _encode_jpeg(annotated)
                self._standby_shown.pop(camera_id, None)

        self._publish(fresh)

    def _next_frame(self, camera_id: str, pipeline: VideoPipeline, now: float, real_time: bool) -> Optional[np.ndarray]:
        skip = 0
        if real_time and pipeline.is_file:
            # How many frames of the file have "gone by" since we last read one.
            elapsed = now - self._last_read.get(camera_id, now - 1.0 / pipeline.source_fps)
            due = self._frames_due.get(camera_id, 0.0) + elapsed * pipeline.source_fps
            self._last_read[camera_id] = now
            if due < 1.0:
                self._frames_due[camera_id] = due
                return None  # the file has no new frame yet; keep the current picture
            if due > MAX_CATCH_UP_FRAMES:
                due = 1.0  # we were idle: carry on from here instead of fast-forwarding
            skip = int(due) - 1
            self._frames_due[camera_id] = due - int(due)
        else:
            self._last_read[camera_id] = now

        frame = pipeline.read_frame(skip)
        if frame is None:
            self._show_standby(camera_id, "lost")
        return frame

    def _show_standby(self, camera_id: str, reason: str) -> None:
        if self._standby_shown.get(camera_id) == reason:
            return
        self._standby_shown[camera_id] = reason
        camera = self.cameras[camera_id]
        if reason == "lost":
            frame = VideoPipeline.standby_frame("VIDEO SIGNAL LOST", f"{camera['name']}: waiting for the source")
        else:
            frame = VideoPipeline.standby_frame("NO FOOTAGE YET", camera.get("no_source_hint", ""))
        self._publish({camera_id: _encode_jpeg(frame)})

    def _publish(self, jpegs: Dict[str, bytes]) -> None:
        with self._new_frame:
            for camera_id, jpeg in jpegs.items():
                self._latest[camera_id] = (self._latest[camera_id][0] + 1, jpeg)
            self._new_frame.notify_all()


def _encode_jpeg(frame: np.ndarray) -> bytes:
    height, width = frame.shape[:2]
    if width > STREAM_MAX_WIDTH:
        scale = STREAM_MAX_WIDTH / width
        # Linear, not area: several times faster here, and the difference is invisible at this ratio.
        frame = cv2.resize(frame, (STREAM_MAX_WIDTH, int(height * scale)), interpolation=cv2.INTER_LINEAR)
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), STREAM_JPEG_QUALITY])
    return buffer.tobytes() if ok else b""
