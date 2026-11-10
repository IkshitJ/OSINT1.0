"""
Multi-Object Head Tracker Module
Associates detections across consecutive frames to maintain persistent IDs and trajectories.
"""

import math
from typing import Dict, List, Tuple, Any
from collections import deque

from cctv.config import (
    MAX_TRACKING_DISTANCE,
    MAX_MISSED_FRAMES,
    TRAJECTORY_HISTORY_LENGTH
)


class HeadTrack:
    """Represents a single tracked head identity across time."""
    def __init__(
        self,
        track_id: int,
        bbox: List[float],
        center: Tuple[float, float],
        confidence: float,
        history_len: int = TRAJECTORY_HISTORY_LENGTH
    ):
        self.track_id = track_id
        self.bbox = bbox
        self.center = center
        self.confidence = confidence
        self.history = deque(maxlen=history_len)
        self.history.append(center)
        self.frames_since_seen = 0
        self.total_seen = 1

    def update(self, bbox: List[float], center: Tuple[float, float], confidence: float):
        self.bbox = bbox
        self.center = center
        self.confidence = confidence
        self.history.append(center)
        self.frames_since_seen = 0
        self.total_seen += 1


class HeadTracker:
    """
    Tracks detected heads across frames using combined IoU and Euclidean distance metrics.
    Maintains persistent IDs and prevents count duplication.
    """

    def __init__(
        self,
        max_distance: float = MAX_TRACKING_DISTANCE,
        max_missed_frames: int = MAX_MISSED_FRAMES,
        history_length: int = TRAJECTORY_HISTORY_LENGTH
    ):
        self.next_id = 1
        self.tracks: Dict[int, HeadTrack] = {}
        self.max_distance = max_distance
        self.max_missed_frames = max_missed_frames
        self.history_length = history_length

    @staticmethod
    def _distance(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

    @staticmethod
    def _compute_iou(boxA: List[float], boxB: List[float]) -> float:
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])

        interArea = max(0.0, xB - xA) * max(0.0, yB - yA)
        boxAArea = max(0.0, boxA[2] - boxA[0]) * max(0.0, boxA[3] - boxA[1])
        boxBArea = max(0.0, boxB[2] - boxB[0]) * max(0.0, boxB[3] - boxB[1])

        denominator = float(boxAArea + boxBArea - interArea)
        if denominator <= 0:
            return 0.0
        return interArea / denominator

    def update(self, detections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Updates active tracks with new frame detections.
        Returns detections enriched with 'track_id', 'history', and 'total_seen'.
        """
        for track in self.tracks.values():
            track.frames_since_seen += 1

        unmatched_detections = list(range(len(detections)))
        matched_track_ids = set()

        # Step 1: Match existing tracks with detections based on IoU and Distance
        if self.tracks and detections:
            cost_matrix = []
            track_keys = list(self.tracks.keys())

            for d_idx in unmatched_detections:
                det = detections[d_idx]
                det_center = det["center"]
                det_box = det["bbox"]

                row = []
                for t_id in track_keys:
                    track = self.tracks[t_id]
                    dist = self._distance(det_center, track.center)
                    iou = self._compute_iou(det_box, track.bbox)

                    if dist <= self.max_distance or iou > 0.15:
                        score = dist - (iou * 40.0)
                    else:
                        score = 1e9
                    row.append((score, t_id, d_idx))
                cost_matrix.extend(row)

            cost_matrix.sort(key=lambda x: x[0])

            matched_dets = set()
            for score, t_id, d_idx in cost_matrix:
                if score >= 1e9:
                    break
                if t_id not in matched_track_ids and d_idx not in matched_dets:
                    matched_track_ids.add(t_id)
                    matched_dets.add(d_idx)
                    det = detections[d_idx]
                    self.tracks[t_id].update(det["bbox"], det["center"], det["confidence"])

            unmatched_detections = [d for d in unmatched_detections if d not in matched_dets]

        # Step 2: Initialize new tracks for unmatched detections
        for d_idx in unmatched_detections:
            det = detections[d_idx]
            new_track = HeadTrack(
                track_id=self.next_id,
                bbox=det["bbox"],
                center=det["center"],
                confidence=det["confidence"],
                history_len=self.history_length
            )
            self.tracks[self.next_id] = new_track
            self.next_id += 1

        # Step 3: Remove lost tracks
        dead_ids = [
            t_id for t_id, track in self.tracks.items()
            if track.frames_since_seen > self.max_missed_frames
        ]
        for t_id in dead_ids:
            del self.tracks[t_id]

        # Step 4: Format output
        tracked_results = []
        for t_id, track in self.tracks.items():
            if track.frames_since_seen == 0:
                tracked_results.append({
                    "track_id": t_id,
                    "bbox": track.bbox,
                    "center": track.center,
                    "confidence": track.confidence,
                    "history": list(track.history),
                    "total_seen": track.total_seen
                })

        return tracked_results

    def reset(self):
        self.next_id = 1
        self.tracks.clear()
