import time
from typing import List, Dict, Any, Optional, Tuple
import numpy as np


class HeadCounter:
    """
    Manages live headcount analytics:
    1. Instantaneous in-frame head count
    2. ROI (Region of Interest) filtered head count
    3. Line-crossing (tripwire) entry / exit counter
    4. Peak count & temporal history
    """

    def __init__(
        self,
        counting_line: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None,
        roi_polygon: Optional[List[Tuple[float, float]]] = None
    ):
        # Tripwire line: ((x1, y1), (x2, y2))
        self.counting_line = counting_line
        # ROI polygon: [(x1, y1), (x2, y2), ...]
        self.roi_polygon = roi_polygon

        # State metrics
        self.current_count: int = 0
        self.roi_count: int = 0
        self.peak_count: int = 0
        self.in_count: int = 0
        self.out_count: int = 0
        self.crossed_ids: Dict[int, str] = {}  # track_id -> "IN" / "OUT"
        self.seen_track_ids: set = set()
        self.history: List[Dict[str, Any]] = []  # [{time, count}]
        self.max_history_len = 300

    def set_counting_line(self, pt1: Tuple[float, float], pt2: Tuple[float, float]):
        self.counting_line = (pt1, pt2)

    def set_roi_polygon(self, points: List[Tuple[float, float]]):
        self.roi_polygon = points

    @staticmethod
    def _point_in_polygon(point: Tuple[float, float], polygon: List[Tuple[float, float]]) -> bool:
        """Ray-casting algorithm for point-in-polygon check."""
        x, y = point
        n = len(polygon)
        inside = False
        p1x, p1y = polygon[0]
        for i in range(n + 1):
            p2x, p2y = polygon[i % n]
            if y > min(p1y, p2y):
                if y <= max(p1y, p2y):
                    if x <= max(p1x, p2x):
                        if p1y != p2y:
                            xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or x <= xinters:
                            inside = not inside
            p1x, p1y = p2x, p2y
        return inside

    @staticmethod
    def _ccw(A, B, C):
        return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])

    @classmethod
    def _intersect(cls, A, B, C, D):
        """Return True if line segments AB and CD intersect."""
        return cls._ccw(A, C, D) != cls._ccw(B, C, D) and cls._ccw(A, B, C) != cls._ccw(A, B, D)

    def _check_line_cross(self, prev_pt: Tuple[float, float], curr_pt: Tuple[float, float]) -> Optional[str]:
        """
        Check if trajectory crossed the counting line.
        Returns 'IN', 'OUT', or None.
        """
        if not self.counting_line:
            return None

        line_p1, line_p2 = self.counting_line
        if self._intersect(prev_pt, curr_pt, line_p1, line_p2):
            # Determine direction using vector cross product
            # Line vector: L = (x2 - x1, y2 - y1)
            # Motion vector: M = (curr_x - prev_x, curr_y - prev_y)
            dx = line_p2[0] - line_p1[0]
            dy = line_p2[1] - line_p1[1]

            # Cross product of Line and Motion
            cross_product = dx * (curr_pt[1] - line_p1[1]) - dy * (curr_pt[0] - line_p1[0])

            if cross_product > 0:
                return "IN"
            else:
                return "OUT"
        return None

    def update(self, tracked_heads: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Update counts based on tracked heads.
        """
        self.current_count = len(tracked_heads)
        if self.current_count > self.peak_count:
            self.peak_count = self.current_count

        active_roi_count = 0

        for head in tracked_heads:
            t_id = head["track_id"]
            self.seen_track_ids.add(t_id)
            center = head["center"]

            # ROI count
            if self.roi_polygon and len(self.roi_polygon) >= 3:
                if self._point_in_polygon(center, self.roi_polygon):
                    active_roi_count += 1
            else:
                active_roi_count = self.current_count

            # Line crossing check
            history = head.get("history", [])
            if len(history) >= 2 and self.counting_line and t_id not in self.crossed_ids:
                prev_center = history[-2]
                cross_dir = self._check_line_cross(prev_center, center)
                if cross_dir == "IN":
                    self.in_count += 1
                    self.crossed_ids[t_id] = "IN"
                elif cross_dir == "OUT":
                    self.out_count += 1
                    self.crossed_ids[t_id] = "OUT"

        self.roi_count = active_roi_count

        # Record temporal snapshot
        now = time.time()
        if not self.history or (now - self.history[-1]["time"]) >= 0.5:
            self.history.append({
                "time": now,
                "count": self.current_count,
                "roi_count": self.roi_count,
                "in": self.in_count,
                "out": self.out_count
            })
            if len(self.history) > self.max_history_len:
                self.history.pop(0)

        return {
            "current_head_count": self.current_count,
            "roi_head_count": self.roi_count,
            "peak_head_count": self.peak_count,
            "total_unique_heads": len(self.seen_track_ids),
            "in_count": self.in_count,
            "out_count": self.out_count,
            "net_flow": self.in_count - self.out_count
        }

    def reset(self):
        self.current_count = 0
        self.roi_count = 0
        self.peak_count = 0
        self.in_count = 0
        self.out_count = 0
        self.crossed_ids.clear()
        self.seen_track_ids.clear()
        self.history.clear()

