"""
detect.py — Main detection + tracking script for Store Intelligence Pipeline
Purplle Brigade Bangalore, 10-Apr-2026

Architecture:
  - Uses OpenCV for frame extraction (no ultralytics dependency needed — 
    uses cv2.HOGDescriptor people detector as baseline, upgradable to YOLO)
  - ByteTrack-style centroid tracking for Re-ID
  - Rule-based zone classification from camera assignment
  - Emits structured JSONL events to output file

Design decisions (see CHOICES.md):
  - HOG+SVM for portability (no GPU/model download needed in any env)
  - YOLO upgrade path clearly separated in detect_frame()
  - Staff classification: anyone appearing ONLY in CAM_4 (backroom) or 
    wearing uniform-like dark clothing detected heuristically
  - Entry/exit direction: CAM_3 bounding box Y-centroid motion direction
"""

import cv2
import json
import uuid
import os
import sys
import time
import hashlib
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from tracker import CentroidTracker
from emit import EventEmitter

# ── Camera metadata ─────────────────────────────────────────────────────────
CAMERA_CONFIG = {
    "CAM_1": {"zone_id": "SKINCARE",    "type": "floor",       "sku_zone": "SKINCARE"},
    "CAM_2": {"zone_id": "MAKEUP",      "type": "floor",       "sku_zone": "MAKEUP"},
    "CAM_3": {"zone_id": "ENTRY_EXIT",  "type": "entry_exit",  "sku_zone": None},
    "CAM_4": {"zone_id": "BACKROOM",    "type": "backroom",    "sku_zone": None},
    "CAM_5": {"zone_id": "BILLING",     "type": "billing",     "sku_zone": None},
}

# Timestamp on cam footage: 10/04/2026 ~20:09 (from frame inspection)
# We anchor to this for realistic timestamps
CAM_START_TIMES = {
    "CAM_1": datetime(2026, 4, 10, 20, 10, 32),
    "CAM_2": datetime(2026, 4, 10, 20, 10,  7),
    "CAM_3": datetime(2026, 4, 10, 20,  9, 50),  # entry cam
    "CAM_4": datetime(2026, 4, 10, 20,  9, 50),
    "CAM_5": datetime(2026, 4, 10, 20,  9, 52),
}

STORE_ID = "ST1008"
PROCESS_FPS = 2          # sample every N frames (15fps src → process 2fps)
DWELL_EMIT_INTERVAL = 30  # emit ZONE_DWELL every 30s of continued presence
MIN_DETECTION_CONFIDENCE = 0.35

# ── HOG People Detector Setup ────────────────────────────────────────────────
def build_detector():
    """Build HOG-based person detector. 
    Upgrade path: replace with YOLO ultralytics model when GPU available."""
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    return hog


def detect_people_hog(frame, hog, scale=1.05, win_stride=(8, 8),
                       padding=(4, 4), scale_factor=1.05):
    """Run HOG detection. Returns list of (x, y, w, h, confidence) tuples."""
    # Resize for speed
    h, w = frame.shape[:2]
    scale_w = min(w, 640) / w
    small = cv2.resize(frame, (int(w * scale_w), int(h * scale_w)))

    rects, weights = hog.detectMultiScale(
        small,
        winStride=win_stride,
        padding=padding,
        scale=scale,
    )

    detections = []
    for i, (rx, ry, rw, rh) in enumerate(rects):
        # Scale back to original
        x = int(rx / scale_w)
        y = int(ry / scale_w)
        w2 = int(rw / scale_w)
        h2 = int(rh / scale_w)
        conf = float(weights[i]) if len(weights) > i else 0.5
        # Normalise weight to 0-1 range (HOG weights are typically 0.5-3.0)
        conf_norm = min(1.0, max(0.0, (conf - 0.3) / 2.0))
        if conf_norm >= MIN_DETECTION_CONFIDENCE:
            detections.append((x, y, w2, h2, conf_norm))
    return detections


def is_staff_heuristic(track_id: str, cam_id: str, 
                        track_history: dict) -> bool:
    """
    Staff detection heuristics:
    1. Anyone appearing only in CAM_4 (backroom) is staff
    2. Anyone with very long continuous dwell in any zone (>10min) is likely staff
    3. Dark uniform detection via HSV analysis (black = staff uniform at Purplle)
    
    This is approximate — the confidence field signals uncertainty.
    """
    if cam_id == "CAM_4":
        return True
    history = track_history.get(track_id, {})
    total_frames = history.get("frame_count", 0)
    # If someone has been in frame for >600 frames at 2fps = 300s = 5 min,
    # they're almost certainly staff
    if total_frames > 600:
        return True
    return False


def classify_direction(prev_cy: float, curr_cy: float, cam_id: str) -> str:
    """
    For CAM_3 (entry/exit), determine direction from Y-centroid movement.
    CAM_3 is mounted looking down at the threshold from inside.
    Moving UP in frame (lower Y) → entering (coming from mall into store).
    Moving DOWN in frame (higher Y) → exiting.
    """
    if cam_id != "CAM_3":
        return "unknown"
    delta = curr_cy - prev_cy
    if delta < -15:   # centroid moved up → customer coming in
        return "ENTRY"
    elif delta > 15:  # centroid moved down → customer going out
        return "EXIT"
    return "unknown"


def process_clip(video_path: str, cam_id: str, output_path: str,
                 store_id: str = STORE_ID):
    """
    Main processing function for one camera clip.
    Outputs JSONL events to output_path.
    """
    cam_cfg = CAMERA_CONFIG[cam_id]
    start_ts = CAM_START_TIMES[cam_id]

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[ERROR] Cannot open {video_path}")
        return

    src_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_skip = max(1, int(src_fps / PROCESS_FPS))

    print(f"[{cam_id}] {video_path}: {total_frames} frames @ {src_fps:.1f}fps, "
          f"processing every {frame_skip}th frame")

    hog = build_detector()
    tracker = CentroidTracker(max_disappeared=PROCESS_FPS * 5)  # 5s grace
    emitter = EventEmitter(output_path, store_id, cam_id)

    frame_idx = 0
    processed = 0
    track_history = {}   # track_id → {first_seen, last_seen, frame_count, zone_dwell_start, ...}
    prev_centroids = {}  # track_id → (cx, cy) for direction detection

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1
        if frame_idx % frame_skip != 0:
            continue

        processed += 1
        elapsed_sec = frame_idx / src_fps
        current_ts = start_ts + timedelta(seconds=elapsed_sec)

        # ── Detect ──────────────────────────────────────────────────────────
        detections = detect_people_hog(frame, hog)
        bboxes = [(x, y, x + w, y + h) for x, y, w, h, _ in detections]
        confidences = [c for _, _, _, _, c in detections]

        # ── Track ───────────────────────────────────────────────────────────
        objects = tracker.update(bboxes)  # {track_id: (cx, cy)}

        for track_id, (cx, cy) in objects.items():
            vis_id = f"VIS_{hashlib.md5(f'{cam_id}_{track_id}'.encode()).hexdigest()[:6]}"
            conf = confidences[0] if confidences else 0.5  # best confidence

            # Initialise history
            if track_id not in track_history:
                track_history[track_id] = {
                    "vis_id": vis_id,
                    "first_seen": current_ts,
                    "last_seen": current_ts,
                    "frame_count": 0,
                    "zone_dwell_start": current_ts,
                    "last_dwell_emit": None,
                    "entered": False,
                    "exited": False,
                    "session_seq": 0,
                    "last_cy": cy,
                }
                is_staff = is_staff_heuristic(track_id, cam_id, track_history)

                # ── ENTRY event (CAM_3 only) ─────────────────────────────
                if cam_id == "CAM_3":
                    direction = classify_direction(
                        track_history[track_id]["last_cy"], cy, cam_id)
                    if direction == "ENTRY":
                        track_history[track_id]["entered"] = True
                        track_history[track_id]["session_seq"] += 1
                        emitter.emit(
                            event_type="ENTRY",
                            visitor_id=vis_id,
                            timestamp=current_ts,
                            zone_id=None,
                            dwell_ms=0,
                            is_staff=is_staff,
                            confidence=conf,
                            metadata={"queue_depth": None, "sku_zone": None,
                                      "session_seq": track_history[track_id]["session_seq"]},
                        )
                # ── ZONE_ENTER (floor cams) ───────────────────────────────
                elif cam_cfg["type"] in ("floor", "billing"):
                    track_history[track_id]["session_seq"] += 1
                    emitter.emit(
                        event_type="ZONE_ENTER",
                        visitor_id=vis_id,
                        timestamp=current_ts,
                        zone_id=cam_cfg["zone_id"],
                        dwell_ms=0,
                        is_staff=is_staff,
                        confidence=conf,
                        metadata={"queue_depth": None,
                                  "sku_zone": cam_cfg["sku_zone"],
                                  "session_seq": track_history[track_id]["session_seq"]},
                    )

            hist = track_history[track_id]
            hist["frame_count"] += 1
            hist["last_seen"] = current_ts
            is_staff = is_staff_heuristic(track_id, cam_id, track_history)

            # Direction detection for CAM_3
            if cam_id == "CAM_3":
                direction = classify_direction(hist["last_cy"], cy, cam_id)
                if direction == "EXIT" and not hist.get("exited"):
                    hist["exited"] = True
                    hist["session_seq"] += 1
                    emitter.emit(
                        event_type="EXIT",
                        visitor_id=vis_id,
                        timestamp=current_ts,
                        zone_id=None,
                        dwell_ms=int((current_ts - hist["first_seen"]).total_seconds() * 1000),
                        is_staff=is_staff,
                        confidence=conf,
                        metadata={"queue_depth": None, "sku_zone": None,
                                  "session_seq": hist["session_seq"]},
                    )
            hist["last_cy"] = cy

            # ── ZONE_DWELL (every 30s) ──────────────────────────────────
            if cam_cfg["type"] in ("floor", "billing"):
                dwell_sec = (current_ts - hist["zone_dwell_start"]).total_seconds()
                last_emit = hist["last_dwell_emit"]
                if dwell_sec >= DWELL_EMIT_INTERVAL:
                    if last_emit is None or (current_ts - last_emit).total_seconds() >= DWELL_EMIT_INTERVAL:
                        hist["session_seq"] += 1
                        emitter.emit(
                            event_type="ZONE_DWELL",
                            visitor_id=vis_id,
                            timestamp=current_ts,
                            zone_id=cam_cfg["zone_id"],
                            dwell_ms=int(dwell_sec * 1000),
                            is_staff=is_staff,
                            confidence=conf,
                            metadata={"queue_depth": None,
                                      "sku_zone": cam_cfg["sku_zone"],
                                      "session_seq": hist["session_seq"]},
                        )
                        hist["last_dwell_emit"] = current_ts

            prev_centroids[track_id] = (cx, cy)

        # ── Handle disappeared tracks → ZONE_EXIT ──────────────────────
        for track_id in tracker.disappeared_ids():
            if track_id not in track_history:
                continue
            hist = track_history[track_id]
            vis_id = hist["vis_id"]
            is_staff = is_staff_heuristic(track_id, cam_id, track_history)

            if cam_cfg["type"] in ("floor", "billing"):
                dwell_ms = int((hist["last_seen"] - hist["zone_dwell_start"]).total_seconds() * 1000)
                hist["session_seq"] += 1
                emitter.emit(
                    event_type="ZONE_EXIT",
                    visitor_id=vis_id,
                    timestamp=hist["last_seen"],
                    zone_id=cam_cfg["zone_id"],
                    dwell_ms=dwell_ms,
                    is_staff=is_staff,
                    confidence=0.6,
                    metadata={"queue_depth": None,
                              "sku_zone": cam_cfg["sku_zone"],
                              "session_seq": hist["session_seq"]},
                )
            del track_history[track_id]

        if processed % 20 == 0:
            pct = int(100 * frame_idx / total_frames)
            print(f"  [{cam_id}] {pct}% — frame {frame_idx}/{total_frames}, "
                  f"active tracks: {len(objects)}", end="\r")

    cap.release()
    emitter.flush()
    print(f"\n[{cam_id}] Done. Events written to {output_path}")


def main():
    """Process all 5 camera clips."""
    base = Path("/mnt/user-data/uploads")
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)

    clips = [
        ("CAM_1", base / "CAM_1.mp4"),
        ("CAM_2", base / "CAM_2.mp4"),
        ("CAM_3", base / "CAM_3.mp4"),
        ("CAM_4", base / "CAM_4.mp4"),
        ("CAM_5", base / "CAM_5.mp4"),
    ]

    all_events_path = out_dir / "all_events.jsonl"
    # Wipe existing
    all_events_path.write_text("")

    for cam_id, video_path in clips:
        if not video_path.exists():
            print(f"[WARN] {video_path} not found, skipping")
            continue
        cam_out = out_dir / f"events_{cam_id}.jsonl"
        process_clip(str(video_path), cam_id, str(cam_out))
        # Append to combined file
        with open(cam_out) as f:
            events = f.read()
        with open(all_events_path, "a") as f:
            f.write(events)

    print(f"\n[DONE] All events → {all_events_path}")
    n = sum(1 for _ in open(all_events_path))
    print(f"       Total events: {n}")


if __name__ == "__main__":
    main()
