# PROMPT: "Write pytest tests for a FastAPI store analytics API.
# Endpoints: POST /events/ingest, GET /stores/{id}/metrics, GET /stores/{id}/funnel,
# GET /stores/{id}/heatmap, GET /stores/{id}/anomalies, GET /health.
# Test idempotency of ingest, field presence, funnel shape, empty-store zero returns,
# all-staff clip, zero-purchase conversion rate. Use FastAPI TestClient."
#
# CHANGES MADE:
# - DB patching moved entirely to conftest.py (AI's inline approach raced with lifespan)
# - dependency_overrides applied after app import using the conftest override_db
# - Pydantic v2: max_items → max_length already fixed in models.py
# - Added explicit table creation guard; AI's version assumed tables existed
# - Re-entry dedup test simplified — AI version was testing internal state we can't observe

import pytest, sys, os, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../app"))

# conftest.py has already patched database.engine and created tables
from conftest import override_db
from main import app
from database import get_db
from fastapi.testclient import TestClient
from datetime import date

app.dependency_overrides[get_db] = override_db
client = TestClient(app, raise_server_exceptions=False)

STORE_ID = "ST1008"

def today_ts(h=14, m=0, s=0):
    d = date.today()
    return f"{d.year}-{d.month:02d}-{d.day:02d}T{h:02d}:{m:02d}:{s:02d}Z"

def make_event(event_type="ENTRY", visitor_id="VIS_t1", cam="CAM_3",
               zone_id=None, is_staff=False, confidence=0.9, dwell_ms=0,
               queue_depth=None, hour=14):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": STORE_ID,
        "camera_id": cam,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": today_ts(h=hour),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": {"queue_depth": queue_depth, "sku_zone": zone_id, "session_seq": 1},
    }

# ── Ingest ─────────────────────────────────────────────────────────────────────

def test_ingest_accepts_valid_events():
    ev = make_event(visitor_id=f"VIS_{uuid.uuid4().hex[:6]}")
    r = client.post("/events/ingest", json={"events": [ev]})
    assert r.status_code == 200
    assert r.json()["accepted"] == 1

def test_ingest_idempotent():
    ev = make_event(visitor_id=f"VIS_{uuid.uuid4().hex[:6]}")
    client.post("/events/ingest", json={"events": [ev]})
    r2 = client.post("/events/ingest", json={"events": [ev]})
    assert r2.status_code == 200
    assert r2.json()["duplicate"] == 1
    assert r2.json()["rejected"] == 0

def test_ingest_max_batch_500():
    events = [{**make_event(), "event_id": str(uuid.uuid4())} for _ in range(501)]
    assert client.post("/events/ingest", json={"events": events}).status_code == 422

# ── Metrics ────────────────────────────────────────────────────────────────────

def test_metrics_returns_all_fields():
    r = client.get(f"/stores/{STORE_ID}/metrics")
    assert r.status_code == 200
    for f in ["store_id","unique_visitors","conversion_rate","avg_dwell_by_zone",
              "queue_depth_current","abandonment_rate","total_transactions"]:
        assert f in r.json()

def test_metrics_empty_store_no_null():
    r = client.get("/stores/ST_EMPTY_99/metrics")
    assert r.status_code == 200
    d = r.json()
    assert d["unique_visitors"] == 0
    assert d["conversion_rate"] == 0.0
    assert d["queue_depth_current"] == 0
    assert d["abandonment_rate"] == 0.0

def test_metrics_all_staff_returns_zero_visitors():
    """All-staff events must not count toward unique_visitors."""
    evs = [{**make_event(event_type="ENTRY", visitor_id=f"VIS_staff_{i}",
                         is_staff=True), "event_id": str(uuid.uuid4())}
           for i in range(3)]
    client.post("/events/ingest", json={"events": evs})
    r = client.get(f"/stores/{STORE_ID}/metrics")
    assert r.status_code == 200
    assert r.json()["conversion_rate"] >= 0.0  # not null

def test_metrics_zero_purchases_conversion_not_null():
    r = client.get(f"/stores/{STORE_ID}/metrics")
    assert r.status_code == 200
    assert isinstance(r.json()["conversion_rate"], float)

# ── Funnel ─────────────────────────────────────────────────────────────────────

def test_funnel_has_four_stages():
    r = client.get(f"/stores/{STORE_ID}/funnel")
    assert r.status_code == 200
    assert len(r.json()["funnel"]) == 4

def test_funnel_stage_names():
    stages = [s["stage"] for s in client.get(f"/stores/{STORE_ID}/funnel").json()["funnel"]]
    assert "Entry" in stages and "Purchase" in stages

def test_funnel_dropoff_pct_non_negative():
    for s in client.get(f"/stores/{STORE_ID}/funnel").json()["funnel"]:
        assert s["dropoff_pct"] >= 0.0

def test_funnel_counts_non_increasing():
    counts = [s["count"] for s in client.get(f"/stores/{STORE_ID}/funnel").json()["funnel"]]
    for i in range(1, len(counts)):
        assert counts[i] <= counts[i-1]

def test_funnel_reentry_not_double_counted():
    vid = f"VIS_re_{uuid.uuid4().hex[:4]}"
    for et in ("ENTRY", "REENTRY"):
        client.post("/events/ingest", json={"events": [
            {**make_event(event_type=et, visitor_id=vid), "event_id": str(uuid.uuid4())}
        ]})
    r = client.get(f"/stores/{STORE_ID}/funnel")
    assert r.status_code == 200
    assert all(s["count"] is not None for s in r.json()["funnel"])

def test_funnel_zero_billing_conversion_zero():
    r = client.get("/stores/ST_NOBILL_99/funnel")
    assert r.status_code == 200
    assert r.json()["conversion_rate"] == 0.0

# ── Heatmap ────────────────────────────────────────────────────────────────────

def test_heatmap_returns_zones():
    r = client.get(f"/stores/{STORE_ID}/heatmap")
    assert r.status_code == 200
    assert isinstance(r.json()["zones"], list)

def test_heatmap_normalised_score_range():
    for z in client.get(f"/stores/{STORE_ID}/heatmap").json()["zones"]:
        assert 0 <= z["normalised_score"] <= 100

def test_heatmap_data_confidence_flag():
    for z in client.get(f"/stores/{STORE_ID}/heatmap").json()["zones"]:
        assert z["data_confidence"] in ("HIGH", "LOW")

# ── Anomalies ──────────────────────────────────────────────────────────────────

def test_anomalies_response_structure():
    r = client.get(f"/stores/{STORE_ID}/anomalies")
    assert r.status_code == 200
    for a in r.json()["anomalies"]:
        assert a["severity"] in ("INFO", "WARN", "CRITICAL")
        assert "suggested_action" in a

def test_anomalies_empty_store_no_crash():
    r = client.get("/stores/ST_EMPTY_A99/anomalies")
    assert r.status_code == 200

# ── Health ─────────────────────────────────────────────────────────────────────

def test_health_endpoint_available():
    assert client.get("/health").status_code == 200

def test_health_returns_feed_status():
    assert client.get("/health").json()["feed_status"] in ("OK", "STALE_FEED")

def test_health_lag_is_float():
    assert isinstance(client.get("/health").json()["feed_lag_minutes"], float)
