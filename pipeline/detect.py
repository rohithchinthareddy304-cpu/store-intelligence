import cv2
import json
import uuid
import os
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from tracker import CentroidTracker
from emit import EventEmitter

CAMERA_CONFIG = {
    "CAM_1": {"zone_id": "SKINCARE",   "type": "floor",      "sku_zone": "SKINCARE"},
    "CAM_2": {"zone_id": "MAKEUP",     "type": "floor",      "sku_zone": "MAKEUP"},
    "CAM_3": {"zone_id": "ENTRY_EXIT", "type": "entry_exit", "sku_zone": None},
    "CAM_4": {"zone_id": "BACKROOM",   "type": "backroom",   "sku_zone": None},
    "CAM_5": {"zone_id": "BILLING",    "type": "billing",    "sku_zone": None},
}

CAM_START_TIMES = {
    "CAM_1": datetime(2026, 4, 10, 20, 10, 32),
    "CAM_2": datetime(2026, 4, 10, 20, 10,  7),
    "CAM_3": datetime(2026, 4, 10, 20,  9, 50),
    "CAM_4": datetime(2026, 4, 10, 20,  9, 50),
    "CAM_5": datetime(2026, 4, 10, 20,  9, 52),
}

STORE_ID = "ST1008"
PROCESS_FPS = 2
DWELL_EMIT_INTERVAL = 30
MIN_DETECTION_CONFIDENCE = 0.35


def build_detector():
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    return hog


def detect_people_hog(frame, hog):
    h, w = frame.shape[:2]
    scale_w = min(w, 640) / w
    small = cv2.resize(frame, (int(w * scale_w), int(h * scale_w)))
    rects, weights = hog.detectMultiScale(
        small, winStride=(8, 8), padding=(4, 4), scale=1.05
    )
    detections = []
    for i, (rx, ry, rw, rh) in enumerate(rects):
        x = int(rx / scale_w)
        y = int(ry / scale_w)
        w2 = int(rw / scale_w)
        h2 = int(rh / scale_w)
        conf = float(weights[i]) if len(weights) > i else 0.5
        conf_norm = min(1.0, max(0.0, (conf - 0.3) / 2.0))
        if conf_norm >= MIN_DETECTION_CONFIDENCE:
            detections.append((x, y, w2, h2, conf_norm))
    return detections


def is_staff(cam_id, frame_count):
    return cam_id == "CAM_4" or frame_count > 600


def classify_direction(prev_cy, curr_cy, cam_id):
    if cam_id != "CAM_3":
        return "unknown"
    delta = curr_cy - prev_cy
    if delta < -15:
        return "ENTRY"
    elif delta > 15:
        return "EXIT"
    return "unknown"


def process_clip(video_path, cam_id, output_path):
    cam_cfg = CAMERA_CONFIG[cam_id]
    start_ts = CAM_START_TIMES[cam_id]

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[WARN] {video_path} not found, skipping")
        return

    src_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_skip = max(1, int(src_fps / PROCESS_FPS))
    print(f"[{cam_id}] {video_path}: {total_frames} frames @ {src_fps:.1f}fps")

    hog = build_detector()
    tracker = CentroidTracker(max_disappeared=PROCESS_FPS * 5)
    emitter = EventEmitter(output_path, STORE_ID, cam_id)

    frame_idx = 0
    track_history = {}

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1
        if frame_idx % frame_skip != 0:
            continue

        elapsed_sec = frame_idx / src_fps
        current_ts = start_ts + timedelta(seconds=elapsed_sec)

        detections = detect_people_hog(frame, hog)
        bboxes = [(x, y, x + w, y + h) for x, y, w, h, _ in detections]
        confidences = [c for _, _, _, _, c in detections]

        objects = tracker.update(bboxes)

        for track_id, (cx, cy) in objects.items():
            vis_id = "VIS_" + hashlib.md5(
                (cam_id + "_" + str(track_id)).encode()
            ).hexdigest()[:6]
            conf = confidences[0] if confidences else 0.5

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
                staff = is_staff(cam_id, 0)
                if cam_id == "CAM_3":
                    track_history[track_id]["entered"] = True
                    track_history[track_id]["session_seq"] += 1
                    emitter.emit(
                        "ENTRY", vis_id, current_ts, None, 0, staff, conf,
                        {"queue_depth": None, "sku_zone": None,
                         "session_seq": track_history[track_id]["session_seq"]}
                    )
                elif cam_cfg["type"] in ("floor", "billing"):
                    track_history[track_id]["session_seq"] += 1
                    emitter.emit(
                        "ZONE_ENTER", vis_id, current_ts,
                        cam_cfg["zone_id"], 0, staff, conf,
                        {"queue_depth": None, "sku_zone": cam_cfg["sku_zone"],
                         "session_seq": track_history[track_id]["session_seq"]}
                    )

            hist = track_history[track_id]
            hist["frame_count"] += 1
            hist["last_seen"] = current_ts
            staff = is_staff(cam_id, hist["frame_count"])

            if cam_id == "CAM_3":
                direction = classify_direction(hist["last_cy"], cy, cam_id)
                if direction == "EXIT" and not hist.get("exited"):
                    hist["exited"] = True
                    hist["session_seq"] += 1
                    dwell_ms = int(
                        (current_ts - hist["first_seen"]).total_seconds() * 1000
                    )
                    emitter.emit(
                        "EXIT", vis_id, current_ts, None, dwell_ms, staff, conf,
                        {"queue_depth": None, "sku_zone": None,
                         "session_seq": hist["session_seq"]}
                    )
            hist["last_cy"] = cy

            if cam_cfg["type"] in ("floor", "billing"):
                dwell_sec = (current_ts - hist["zone_dwell_start"]).total_seconds()
                last_emit = hist["last_dwell_emit"]
                if dwell_sec >= DWELL_EMIT_INTERVAL:
                    if last_emit is None or (
                        current_ts - last_emit
                    ).total_seconds() >= DWELL_EMIT_INTERVAL:
                        hist["session_seq"] += 1
                        emitter.emit(
                            "ZONE_DWELL", vis_id, current_ts,
                            cam_cfg["zone_id"], int(dwell_sec * 1000),
                            staff, conf,
                            {"queue_depth": None, "sku_zone": cam_cfg["sku_zone"],
                             "session_seq": hist["session_seq"]}
                        )
                        hist["last_dwell_emit"] = current_ts

        for track_id in tracker.disappeared_ids():
            if track_id not in track_history:
                continue
            hist = track_history[track_id]
            staff = is_staff(cam_id, hist["frame_count"])
            if cam_cfg["type"] in ("floor", "billing"):
                dwell_ms = int(
                    (hist["last_seen"] - hist["zone_dwell_start"]).total_seconds() * 1000
                )
                hist["session_seq"] += 1
                emitter.emit(
                    "ZONE_EXIT", hist["vis_id"], hist["last_seen"],
                    cam_cfg["zone_id"], dwell_ms, staff, 0.6,
                    {"queue_depth": None, "sku_zone": cam_cfg["sku_zone"],
                     "session_seq": hist["session_seq"]}
                )
            del track_history[track_id]

        if frame_idx % 60 == 0:
            pct = int(100 * frame_idx / total_frames)
            print(
                f"  [{cam_id}] {pct}% done — frame {frame_idx}/{total_frames}",
                end="\r"
            )

    cap.release()
    emitter.flush()
    print(f"\n[{cam_id}] Done.")


def main():
    base = Path(".")
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)

    clips = [
        ("CAM_1", "CAM 1 - zone.mp4"),
        ("CAM_2", "CAM 2 - zone.mp4"),
        ("CAM_3", "CAM 3 - entry.mp4"),
        ("CAM_4", "CAM 4 - billing.mp4"),
        ("CAM_5", "CAM 5 - billing.mp4"),
    ]

    all_events_path = out_dir / "all_events.jsonl"
    all_events_path.write_text("")

    for cam_id, filename in clips:
        video_path = base / filename
        if not video_path.exists():
            print(f"[WARN] {filename} not found, skipping")
            continue
        cam_out = out_dir / f"events_{cam_id}.jsonl"
        process_clip(str(video_path), cam_id, str(cam_out))
        with open(cam_out) as f:
            events = f.read()
        with open(all_events_path, "a") as f:
            f.write(events)

    print(f"\n[DONE] All events written to {all_events_path}")
    total = sum(1 for _ in open(all_events_path))
    print(f"       Total events: {total}")


if __name__ == "__main__":
    main()