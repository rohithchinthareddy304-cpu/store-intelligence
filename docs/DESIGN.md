# DESIGN.md — Store Intelligence System
## Purplle Tech Challenge 2026 · Round 2

---

## Overview

This system converts raw CCTV footage from the Purplle Brigade Road Bangalore store
into a live analytics API that answers one core business question:

> **How many customers visited today, and how many bought something?**

The pipeline runs end-to-end: raw MP4 clips → structured events → REST API → live dashboard.

---

## Architecture

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────────┐    ┌──────────────────┐
│  5 CCTV     │───▶│ Detection Layer  │───▶│  Event Stream   │───▶│ Intelligence API │
│  MP4 Clips  │    │ (OpenCV + HOG +  │    │  (JSONL file /  │    │  (FastAPI +      │
│  1080p@30fps│    │  CentroidTracker)│    │  POST /ingest)  │    │  SQLite)         │
└─────────────┘    └──────────────────┘    └─────────────────┘    └──────────────────┘
                                                                           │
                                                                   ┌───────▼──────────┐
                                                                   │  Live Dashboard  │
                                                                   │  (rich terminal) │
                                                                   └──────────────────┘
```

### Camera Mapping (derived from video analysis)

| Camera | Role | Zones Covered |
|--------|------|---------------|
| CAM_1 | Main floor — Skincare wall | SKINCARE, CLEAN_BEAUTY, KOREAN_BEAUTY |
| CAM_2 | Main floor — Makeup wall | MAKEUP, ACCESSORIES, LIPS_EYES |
| CAM_3 | Entry/Exit glass threshold | ENTRY_EXIT |
| CAM_4 | Back room / stock area | BACKROOM (staff only) |
| CAM_5 | Billing counter | BILLING |

### Stage 1 — Detection Layer (`pipeline/detect.py`)

- **Frame extraction**: OpenCV VideoCapture, sampled at 2fps from 30fps source
- **Person detection**: HOG + SVM (`cv2.HOGDescriptor_getDefaultPeopleDetector`)
- **Multi-object tracking**: Custom centroid tracker with Hungarian-style greedy matching
  (see `pipeline/tracker.py`)
- **Zone classification**: Rule-based by camera ID (each camera covers fixed zones)
- **Entry/Exit direction**: Y-centroid motion on CAM_3 — upward movement = entry,
  downward = exit
- **Staff classification**: Anyone detected only in CAM_4 (backroom), or with >600
  continuous frames (5+ min dwell) is flagged `is_staff=true`
- **Re-ID**: SHA3-based visitor token per `{camera_id}_{track_id}` — unique per visit session

### Stage 2 — Event Stream

Events emitted as JSONL, validated against Pydantic schema before write.
POS correlation (`pipeline/pos_correlate.py`) adds `BILLING_QUEUE_JOIN` and
`BILLING_QUEUE_ABANDON` events using a 5-minute time window around each transaction.

### Stage 3 — Intelligence API (`app/`)

- **Framework**: FastAPI with SQLAlchemy ORM
- **Storage**: SQLite (upgradeable to PostgreSQL via `DATABASE_URL` env var)
- **Ingest**: Idempotent by `event_id`, batch up to 500, partial success on malformed rows
- **Metrics**: All computed in real-time from DB queries — no caching
- **Anomalies**: Rule-based thresholds (queue spike >5, dead zone >30min, conversion drop >30%)

### Stage 4 — Live Dashboard (`dashboard/dashboard.py`)

Rich terminal UI polling all API endpoints every 3 seconds.
Shows: visitor count, conversion rate, zone heatmap, funnel, anomalies, health status.

---

## AI-Assisted Decisions

### 1. Tracker Architecture
I asked Claude to compare ByteTrack, DeepSORT, and a custom centroid tracker for a
retail footfall scenario with low-speed pedestrians, no severe occlusion, and no GPU
available in the base environment. The AI recommended ByteTrack for accuracy but noted
the custom centroid approach would be within 5% of ByteTrack accuracy for slow-moving
retail shoppers. I agreed and built the centroid tracker — it removes the `lap` (linear
assignment problem) C++ dependency and works identically in Python-only environments.

**What I overrode**: Claude initially suggested cosine Re-ID on appearance embeddings
(torchreid). I chose trajectory-based Re-ID instead because the faces are blurred (no
appearance features) and the clips are short enough that spatial trajectory is sufficient.

### 2. Staff Detection Strategy
I asked Claude whether a VLM (GPT-4V or Gemini) would be more accurate than rule-based
staff detection. It said yes for uniform recognition, but flagged latency (300-500ms per
frame at VLM API rates vs <5ms rule-based). Given 30fps source footage, VLM was
impractical for frame-level inference. I agreed and used the rule-based approach
(CAM_4-only + long-dwell heuristic), but documented the VLM path as an upgrade.

### 3. Event Schema Design
I used Claude to validate my event schema against the challenge spec. It caught that I
was missing `session_seq` in the metadata block and that `dwell_ms` should be 0 for
instantaneous events (not null). Both changes were correct — I adopted them.
Claude also suggested adding a `reentry_of` field to link re-entry events to the original
session. I considered it but didn't implement it to keep the schema minimal and matching
the challenge spec exactly.

---

## Data Sources Used

- `CAM_1.mp4` — Skincare/Clean Beauty zone, ~140s, 1080p@30fps
- `CAM_3.mp4` — Entry/Exit glass door, ~148s, 1080p@30fps
- `CAM_5.mp4` — Billing counter, ~139s, 1080p@30fps
- `Brigade_Bangalore_10_April_26_*.csv` — 101 POS line items, 24 unique orders,
  10-Apr-2026, 12:15–21:39, store ST1008
- `Brigade_Road_Store_layout.xlsx` — Visual store layout map (image-based, not
  machine-readable; zone definitions derived from video analysis)

---

## Key Design Trade-offs

| Decision | Choice | Alternative | Reason |
|----------|--------|-------------|--------|
| Detection model | HOG+SVM | YOLOv8 | No GPU required; works in any Docker env |
| Storage | SQLite | PostgreSQL | Zero-config; upgrade path via env var |
| Tracking | Centroid + Hungarian | ByteTrack | Pure Python, no C++ deps |
| Staff detection | Rule-based (CAM_4 + dwell) | VLM classification | Latency constraints |
| Dashboard | Rich terminal | React web UI | Simpler to run; no Node.js required |
