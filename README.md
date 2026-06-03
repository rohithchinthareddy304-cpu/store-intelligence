# Store Intelligence API
### Purplle Tech Challenge 2026 · Round 2
**Store: Brigade Road Bangalore (ST1008)**

---

## What This Builds

A complete pipeline from raw CCTV footage → live retail analytics API:

```
CCTV Clips (5 cameras) → Detection Pipeline → Events (JSONL) → REST API → Live Dashboard
```

**North Star Metric**: Offline Store Conversion Rate = Customers who purchased ÷ Total unique visitors

---

## Setup in 5 Commands

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/store-intelligence && cd store-intelligence

# 2. Start the API
docker compose up --build -d

# 3. Verify it's running
curl http://localhost:8000/health

# 4. Run the detection pipeline against the clips
cd pipeline && python detect.py

# 5. Feed events into the API and launch the dashboard
python pos_correlate.py output/all_events.jsonl && \
python ../dashboard/dashboard.py
```

> **Note**: Place the 5 camera clips (`CAM_1.mp4` through `CAM_5.mp4`) and the POS CSV
> in the `data/` directory before running step 4.

---

## Full Project Structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py          # Main detection + tracking (HOG + centroid tracker)
│   ├── tracker.py         # Re-ID / centroid tracking logic
│   ├── emit.py            # Event schema + JSONL emitter
│   └── pos_correlate.py   # POS transaction correlation → BILLING events
├── app/
│   ├── main.py            # FastAPI entrypoint + request logging middleware
│   ├── database.py        # SQLAlchemy ORM (SQLite/PostgreSQL)
│   ├── models.py          # Pydantic event schema + response models
│   ├── ingestion.py       # POST /events/ingest (idempotent, batch 500)
│   ├── metrics.py         # GET /stores/{id}/metrics
│   ├── funnel.py          # GET /stores/{id}/funnel
│   ├── heatmap.py         # GET /stores/{id}/heatmap
│   ├── anomalies.py       # GET /stores/{id}/anomalies
│   └── health.py          # GET /health
├── dashboard/
│   └── dashboard.py       # Rich terminal live dashboard
├── tests/
│   ├── test_pipeline.py   # Detection pipeline tests (with AI prompt blocks)
│   └── test_metrics.py    # API endpoint tests (with AI prompt blocks)
├── docs/
│   ├── DESIGN.md          # Architecture + AI-assisted decisions
│   └── CHOICES.md         # 3 key decisions with full reasoning
├── store_layout.json      # Zone definitions derived from video analysis
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Running the Detection Pipeline

```bash
cd pipeline

# Process all 5 cameras (outputs to pipeline/output/)
python detect.py

# This produces:
#   output/events_CAM_1.jsonl   ← Skincare zone events
#   output/events_CAM_2.jsonl   ← Makeup zone events
#   output/events_CAM_3.jsonl   ← Entry/Exit events
#   output/events_CAM_4.jsonl   ← Backroom (staff) events
#   output/events_CAM_5.jsonl   ← Billing counter events
#   output/all_events.jsonl     ← Combined (use this for ingest)

# Run POS correlation (adds BILLING_QUEUE_JOIN + BILLING_QUEUE_ABANDON events)
python pos_correlate.py output/all_events.jsonl path/to/pos_transactions.csv
```

---

## Feeding Events into the API

```bash
# Batch ingest all events (chunked at 500/request)
python ingest_events.py output/all_events.jsonl

# Or use curl directly:
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d '{"events": [...]}'
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/events/ingest` | Batch ingest events (max 500, idempotent) |
| GET | `/stores/ST1008/metrics` | Real-time visitor + conversion metrics |
| GET | `/stores/ST1008/funnel` | Entry → Zone → Billing → Purchase funnel |
| GET | `/stores/ST1008/heatmap` | Zone visit frequency, normalised 0–100 |
| GET | `/stores/ST1008/anomalies` | Active anomalies with severity + action |
| GET | `/health` | Service health, feed lag, active cameras |

**Swagger UI**: http://localhost:8000/docs

---

## Running Tests

```bash
# From project root
pip install pytest pytest-asyncio -q
pytest tests/ -v --tb=short

# With coverage
pip install pytest-cov
pytest tests/ --cov=app --cov-report=term-missing
```

---

## Live Dashboard

```bash
# Requires API running on localhost:8000
python dashboard/dashboard.py
```

The terminal dashboard refreshes every 3 seconds and shows:
- Unique visitor count + conversion rate
- Real-time queue depth
- Zone heatmap with dwell times
- Active anomalies with severity
- Feed health status

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./store_intel.db` | SQLite or PostgreSQL connection string |
| `API_PORT` | `8000` | API port |

---

## Data Sources

This system was built and tested against:
- `CAM_1.mp4` — 1080p@30fps, 140s, Skincare/Clean Beauty wall
- `CAM_2.mp4` — 1080p@30fps, 126s, Makeup/Accessories wall  
- `CAM_3.mp4` — 1080p@30fps, 148s, Entry/Exit glass threshold
- `CAM_4.mp4` — 1080p@25fps, 146s, Backroom/stock area
- `CAM_5.mp4` — 1080p@25fps, 139s, Billing counter
- POS CSV: 101 line items, 24 unique orders, 10-Apr-2026, store ST1008

---

## Architecture Notes

See `docs/DESIGN.md` for full architecture breakdown and AI-assisted decisions.
See `docs/CHOICES.md` for the three key decisions: model selection, schema design, API architecture.
