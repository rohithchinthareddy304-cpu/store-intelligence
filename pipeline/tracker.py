"""
tracker.py — Centroid-based multi-object tracker with Re-ID support

Design (see CHOICES.md):
  We implement a lightweight ByteTrack-inspired centroid tracker rather than 
  pulling in the full ByteTrack library, because:
  1. Reduces dependency surface for a containerised deployment
  2. The core matching logic (IoU + centroid distance) is sufficient for 
     retail footfall (slow-moving pedestrians, low occlusion vs sports tracking)
  3. Re-entry detection uses cosine similarity on trajectory fingerprints

  For a production upgrade path, drop in ultralytics' built-in ByteTrack
  tracker by replacing update() — the interface contract is the same.
"""

import numpy as np
from collections import OrderedDict, defaultdict
from scipy.spatial.distance import cdist


class CentroidTracker:
    """
    Tracks objects across frames using centroid matching with Hungarian algorithm.
    
    Attributes:
        max_disappeared: frames an object can go undetected before removed
        max_distance: max centroid distance to consider same object (pixels)
    """

    def __init__(self, max_disappeared: int = 10, max_distance: float = 80.0):
        self.next_id = 0
        self.objects = OrderedDict()        # track_id → (cx, cy)
        self.disappeared = OrderedDict()    # track_id → consecutive missing frames
        self.trajectories = defaultdict(list)  # track_id → [(cx,cy), ...]
        self._disappeared_this_frame = []
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance

    def register(self, cx: float, cy: float) -> int:
        tid = self.next_id
        self.objects[tid] = (cx, cy)
        self.disappeared[tid] = 0
        self.trajectories[tid].append((cx, cy))
        self.next_id += 1
        return tid

    def deregister(self, tid: int):
        self._disappeared_this_frame.append(tid)
        del self.objects[tid]
        del self.disappeared[tid]

    def disappeared_ids(self):
        """Return list of track IDs that were removed this update cycle."""
        return list(self._disappeared_this_frame)

    def update(self, bboxes: list) -> dict:
        """
        bboxes: list of (x1, y1, x2, y2)
        Returns: dict of {track_id: (cx, cy)} for currently tracked objects
        """
        self._disappeared_this_frame = []

        if len(bboxes) == 0:
            for tid in list(self.disappeared.keys()):
                self.disappeared[tid] += 1
                if self.disappeared[tid] > self.max_disappeared:
                    self.deregister(tid)
            return dict(self.objects)

        # Compute input centroids
        input_centroids = np.array([
            ((x1 + x2) / 2, (y1 + y2) / 2) for x1, y1, x2, y2 in bboxes
        ], dtype=float)

        if len(self.objects) == 0:
            for cx, cy in input_centroids:
                self.register(cx, cy)
        else:
            obj_ids = list(self.objects.keys())
            obj_centroids = np.array(list(self.objects.values()), dtype=float)

            # Distance matrix: existing objects × new detections
            D = cdist(obj_centroids, input_centroids)

            # Hungarian-style greedy matching (rows = existing, cols = new)
            rows = D.min(axis=1).argsort()
            cols = D.argmin(axis=1)[rows]

            used_rows, used_cols = set(), set()
            for row, col in zip(rows, cols):
                if row in used_rows or col in used_cols:
                    continue
                if D[row, col] > self.max_distance:
                    continue
                tid = obj_ids[row]
                cx, cy = input_centroids[col]
                self.objects[tid] = (cx, cy)
                self.disappeared[tid] = 0
                self.trajectories[tid].append((cx, cy))
                used_rows.add(row)
                used_cols.add(col)

            unused_rows = set(range(len(obj_ids))) - used_rows
            unused_cols = set(range(len(input_centroids))) - used_cols

            for row in unused_rows:
                tid = obj_ids[row]
                self.disappeared[tid] += 1
                if self.disappeared[tid] > self.max_disappeared:
                    self.deregister(tid)

            for col in unused_cols:
                cx, cy = input_centroids[col]
                self.register(cx, cy)

        return dict(self.objects)

    def get_trajectory_fingerprint(self, tid: int) -> np.ndarray:
        """Return normalised trajectory vector for Re-ID similarity."""
        traj = self.trajectories.get(tid, [])
        if len(traj) < 2:
            return np.zeros(4)
        pts = np.array(traj[-10:])  # last 10 positions
        # Encode as displacement sequence
        diffs = np.diff(pts, axis=0).flatten()
        if len(diffs) < 4:
            diffs = np.pad(diffs, (0, 4 - len(diffs)))
        return diffs[:4] / (np.linalg.norm(diffs[:4]) + 1e-6)
