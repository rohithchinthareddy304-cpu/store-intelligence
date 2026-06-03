# CHOICES.md — Key Architectural Decisions

---

## Decision 1: Detection Model — HOG+SVM over YOLOv8

### Options Considered
| Option | Pros | Cons |
|--------|------|------|
| YOLOv8 (ultralytics) | Best accuracy, active community | ~500MB model download, GPU preferred, `lap` C++ dep |
| RT-DETR | Transformer-based, strong occluded detection | Even heavier, requires CUDA |
| MediaPipe Pose | Person detection built-in | Struggles with top-down CCTV angles |
| HOG + SVM | Zero model download, CPU-native, ships with OpenCV | Lower accuracy (~80% vs 95%) |
| Background subtraction (MOG2) | Ultra-fast, no model needed | Cannot count individuals, fails on static people |

### What AI Suggested
When I asked Claude to rank these for a **retail CCTV scenario with no GPU guarantee**,
it said:

> "YOLOv8 is the standard choice and I'd normally recommend it. However, for a
> containerised deployment where GPU is not guaranteed, HOG is more robust to deploy.
> The accuracy gap narrows considerably in low-density retail environments (1-8 people
> per frame) versus the crowded scenes YOLOv8 is typically benchmarked on."

### What I Chose and Why
**HOG + SVM (cv2.HOGDescriptor)** — because the acceptance gate requirement is
`docker compose up` on any machine. YOLOv8 needs a 500MB+ model file that either must
be checked into the repo or downloaded at runtime (fragile). HOG works identically
whether the reviewer has an NVIDIA GPU or an M1 MacBook.

The upgrade path is clean: `detect_people_hog()` in `detect.py` can be swapped for a
YOLO inference call in one function — the rest of the pipeline is unchanged.

### Acknowledgement of Limitation
HOG struggles with partial occlusion (e.g., customers behind the display stand in
CAM_1). We handle this by emitting low-confidence events rather than suppressing them,
and flagging `confidence < 0.5` in the API response for downstream callers to decide
how to weight them.

---

## Decision 2: Event Schema Design

### The Core Question
Should the schema be minimal (only fields required by the challenge) or rich (include
extra fields like `reentry_of`, `bbox_coords`, `frame_number`)?

### Options Considered
1. **Minimal schema** — exactly the 11 fields in the challenge spec
2. **Extended schema** — add `reentry_of` (links re-entry to original session),
   `bbox_coords` (bounding box for debugging), `frame_number` (for reproducibility)
3. **Flat schema** — move metadata fields to top level for query simplicity

### What AI Suggested
Claude recommended Option 2 (extended schema), specifically the `reentry_of` field:

> "Linking a REENTRY event to the original visitor session makes downstream analytics
> much cleaner — you can reconstruct the full visit timeline without joining on
> visitor_id across time windows."

### What I Chose and Why
**Option 1 — Minimal schema**, exactly matching the challenge spec.

My reasoning against Claude's suggestion:
- The scoring harness validates against the exact schema in the problem statement.
  Extra fields risk breaking the automated correctness tests.
- The `reentry_of` link can be reconstructed at query time: `SELECT * FROM events WHERE
  visitor_id = ? AND event_type IN ('ENTRY', 'REENTRY') ORDER BY timestamp`
- Adding `bbox_coords` would make the JSONL files ~3x larger with no benefit at ingest.

I did add `session_seq` to the metadata block after Claude pointed out it was in the
challenge spec but I had initially omitted it. That was a correct catch.

### Schema Rationale — Event Type Coverage
- `ENTRY` / `EXIT` — from CAM_3 direction detection (Y-centroid motion)
- `ZONE_ENTER` / `ZONE_EXIT` — from CAM_1, CAM_2, CAM_5 track lifecycle
- `ZONE_DWELL` — emitted every 30s of continuous zone presence
- `BILLING_QUEUE_JOIN` — when >1 person in CAM_5 zone simultaneously
- `BILLING_QUEUE_ABANDON` — CAM_5 exit without a POS transaction in 5-min window
- `REENTRY` — same `visitor_id` reappears after a prior `EXIT`

---

## Decision 3: API Architecture — SQLite with Real-Time Queries

### The Core Question
Should metrics endpoints compute in real-time from raw events, or maintain pre-computed
aggregate tables that get updated on ingest?

### Options Considered
| Approach | Read Speed | Write Complexity | Staleness |
|----------|-----------|-----------------|-----------|
| Real-time SQL queries on events table | Slower (full scan) | Simple | Zero |
| Materialised aggregates updated on ingest | Fast (index lookup) | Complex (dual write) | Near-zero |
| Redis cache with TTL | Fast | Moderate | Configurable |
| Pandas in-memory compute on startup | Fastest | Simple | Full re-read on restart |

### What AI Suggested
Claude recommended the materialised aggregates approach:

> "For a production API serving 40 stores, real-time queries over an events table will
> hit scan limits quickly. Pre-computed aggregates (visitor_counts_hourly,
> zone_dwell_aggregates) give you O(1) reads at the cost of slightly more complex
> ingest code."

### What I Chose and Why
**Real-time SQL queries (Option 1)** for the current submission, with a clear upgrade
path to materialised aggregates.

Why I disagreed with Claude here:
- The challenge says "must be production-aware" but also says the API runs against
  a single store's events from a single day. At this scale (< 10,000 events), a
  SQLite index scan takes <10ms — well within any SLA.
- Adding materialised aggregates introduces dual-write complexity and potential
  consistency bugs that are worse in a judged submission than a slightly slower query.
- The real upgrade in production isn't materialised views — it's moving to PostgreSQL
  with proper indexes on `(store_id, timestamp, event_type)`, which the code supports
  via the `DATABASE_URL` env var.

### What Would Change at 40 Stores / Live Traffic
As I stated in the follow-up question template: the first thing that breaks at 40 stores
with real-time events is the `GET /stores/{id}/metrics` query doing a full table scan
on every request. The fix is:
1. Add a composite index on `(store_id, timestamp, event_type, is_staff)`
2. Add hourly aggregation job writing to `metrics_hourly` table
3. `/metrics` reads from aggregates, falling back to raw scan for last 5 minutes

This is documented but not implemented to keep the codebase reviewable.
