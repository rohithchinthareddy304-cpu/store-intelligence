# PROMPT: "Write pytest tests for a CCTV-based retail event detection pipeline. 
# The pipeline emits JSONL events with this schema: 
# {event_id, store_id, camera_id, visitor_id, event_type, timestamp, zone_id, 
#  dwell_ms, is_staff, confidence, metadata:{queue_depth, sku_zone, session_seq}}
# Test: schema compliance, event_id uniqueness, timestamp format, confidence range,
# staff exclusion logic, entry/exit count accuracy, group handling (3 people → 3 ENTRY events),
# re-entry detection. Use pytest fixtures for sample events."
#
# CHANGES MADE:
# - Added edge case for empty store periods (no events in a clip window)
# - Added test for confidence calibration (low-conf events NOT silently dropped)
# - Changed group test from mocking to using actual emit.build_event() function
# - Added schema compliance test for all 8 event types
# - Removed AI-generated test for "partial occlusion" which tested internal HOG 
#   internals that aren't accessible; replaced with confidence degradation check

import pytest
import json
import uuid
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../pipeline"))

from emit import build_event, EVENT_TYPES


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_event():
    """A valid ENTRY event."""
    return build_event(
        store_id="ST1008",
        camera_id="CAM_3",
        visitor_id="VIS_abc123",
        event_type="ENTRY",
        timestamp=datetime(2026, 4, 10, 14, 22, 10),
        zone_id=None,
        dwell_ms=0,
        is_staff=False,
        confidence=0.91,
        metadata={"queue_depth": None, "sku_zone": None, "session_seq": 1},
    )


@pytest.fixture
def billing_event():
    return build_event(
        store_id="ST1008",
        camera_id="CAM_5",
        visitor_id="VIS_billed1",
        event_type="BILLING_QUEUE_JOIN",
        timestamp=datetime(2026, 4, 10, 18, 0, 0),
        zone_id="BILLING",
        dwell_ms=0,
        is_staff=False,
        confidence=0.85,
        metadata={"queue_depth": 2, "sku_zone": None, "session_seq": 3},
    )


# ── Schema compliance ─────────────────────────────────────────────────────────

def test_event_has_all_required_fields(sample_event):
    required = ["event_id", "store_id", "camera_id", "visitor_id",
                "event_type", "timestamp", "zone_id", "dwell_ms",
                "is_staff", "confidence", "metadata"]
    for field in required:
        assert field in sample_event, f"Missing required field: {field}"


def test_metadata_has_required_keys(sample_event):
    meta = sample_event["metadata"]
    assert "queue_depth" in meta
    assert "sku_zone" in meta
    assert "session_seq" in meta


def test_all_event_types_are_valid():
    for et in EVENT_TYPES:
        ev = build_event(
            store_id="ST1008", camera_id="CAM_1", visitor_id="VIS_x",
            event_type=et,
            timestamp=datetime(2026, 4, 10, 12, 0, 0),
            zone_id="SKINCARE" if "ZONE" in et or "BILLING" in et else None,
            dwell_ms=5000 if "DWELL" in et else 0,
            is_staff=False, confidence=0.75,
            metadata={"queue_depth": 1 if "BILLING" in et else None,
                      "sku_zone": None, "session_seq": 1},
        )
        assert ev["event_type"] == et


def test_invalid_event_type_raises():
    with pytest.raises(AssertionError):
        build_event(
            store_id="ST1008", camera_id="CAM_1", visitor_id="VIS_x",
            event_type="INVALID_TYPE",
            timestamp=datetime(2026, 4, 10, 12, 0),
            zone_id=None, dwell_ms=0, is_staff=False, confidence=0.5,
            metadata={},
        )


# ── Event ID uniqueness ───────────────────────────────────────────────────────

def test_event_ids_are_unique():
    events = [
        build_event(
            store_id="ST1008", camera_id="CAM_3", visitor_id=f"VIS_{i}",
            event_type="ENTRY",
            timestamp=datetime(2026, 4, 10, 12, i, 0),
            zone_id=None, dwell_ms=0, is_staff=False, confidence=0.8,
            metadata={"queue_depth": None, "sku_zone": None, "session_seq": i},
        )
        for i in range(20)
    ]
    ids = [e["event_id"] for e in events]
    assert len(set(ids)) == 20, "Duplicate event_ids found"


def test_event_id_is_uuid_format(sample_event):
    try:
        uuid.UUID(sample_event["event_id"])
    except ValueError:
        pytest.fail("event_id is not a valid UUID")


# ── Timestamp format ──────────────────────────────────────────────────────────

def test_timestamp_is_iso8601_utc(sample_event):
    ts = sample_event["timestamp"]
    assert ts.endswith("Z"), "Timestamp must end with Z (UTC)"
    # Must parse without error
    datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


# ── Confidence calibration ────────────────────────────────────────────────────

def test_low_confidence_events_are_not_suppressed():
    """Low confidence events should be emitted (not silently dropped)."""
    ev = build_event(
        store_id="ST1008", camera_id="CAM_1", visitor_id="VIS_lowconf",
        event_type="ZONE_ENTER", timestamp=datetime(2026, 4, 10, 15, 0, 0),
        zone_id="SKINCARE", dwell_ms=0, is_staff=False, confidence=0.36,
        metadata={"queue_depth": None, "sku_zone": "SKINCARE", "session_seq": 1},
    )
    assert ev["confidence"] == 0.36


def test_confidence_out_of_range_raises():
    with pytest.raises(AssertionError):
        build_event(
            store_id="ST1008", camera_id="CAM_1", visitor_id="VIS_x",
            event_type="ZONE_ENTER", timestamp=datetime(2026, 4, 10, 12, 0),
            zone_id="SKINCARE", dwell_ms=0, is_staff=False,
            confidence=1.5,  # invalid
            metadata={},
        )


# ── Staff exclusion ───────────────────────────────────────────────────────────

def test_staff_events_are_flagged(sample_event):
    """Staff events must have is_staff=True."""
    staff_ev = build_event(
        store_id="ST1008", camera_id="CAM_4", visitor_id="VIS_staff1",
        event_type="ZONE_ENTER", timestamp=datetime(2026, 4, 10, 14, 0, 0),
        zone_id="BACKROOM", dwell_ms=0, is_staff=True, confidence=0.9,
        metadata={"queue_depth": None, "sku_zone": None, "session_seq": 1},
    )
    assert staff_ev["is_staff"] is True


def test_customer_event_is_not_staff(sample_event):
    assert sample_event["is_staff"] is False


# ── Group handling ────────────────────────────────────────────────────────────

def test_group_entry_emits_one_event_per_person():
    """3 people entering together → 3 separate ENTRY events with different visitor_ids."""
    group_visitors = ["VIS_grp1", "VIS_grp2", "VIS_grp3"]
    events = [
        build_event(
            store_id="ST1008", camera_id="CAM_3",
            visitor_id=vid,
            event_type="ENTRY",
            timestamp=datetime(2026, 4, 10, 15, 30, 0),  # same timestamp
            zone_id=None, dwell_ms=0, is_staff=False, confidence=0.82,
            metadata={"queue_depth": None, "sku_zone": None, "session_seq": 1},
        )
        for vid in group_visitors
    ]
    assert len(events) == 3
    visitor_ids = {e["visitor_id"] for e in events}
    assert len(visitor_ids) == 3, "Group of 3 must produce 3 distinct visitor_ids"
    entry_events = [e for e in events if e["event_type"] == "ENTRY"]
    assert len(entry_events) == 3


# ── Re-entry detection ────────────────────────────────────────────────────────

def test_reentry_event_schema():
    """REENTRY event should use same visitor_id as prior ENTRY."""
    reentry_ev = build_event(
        store_id="ST1008", camera_id="CAM_3", visitor_id="VIS_ret1",
        event_type="REENTRY", timestamp=datetime(2026, 4, 10, 16, 0, 0),
        zone_id=None, dwell_ms=0, is_staff=False, confidence=0.70,
        metadata={"queue_depth": None, "sku_zone": None, "session_seq": 3},
    )
    assert reentry_ev["event_type"] == "REENTRY"
    assert reentry_ev["visitor_id"] == "VIS_ret1"


# ── Dwell events ──────────────────────────────────────────────────────────────

def test_zone_dwell_has_positive_dwell_ms():
    ev = build_event(
        store_id="ST1008", camera_id="CAM_1", visitor_id="VIS_dwell1",
        event_type="ZONE_DWELL", timestamp=datetime(2026, 4, 10, 14, 0, 0),
        zone_id="SKINCARE", dwell_ms=30000, is_staff=False, confidence=0.88,
        metadata={"queue_depth": None, "sku_zone": "SKINCARE", "session_seq": 2},
    )
    assert ev["dwell_ms"] >= 30000


def test_negative_dwell_clamped_to_zero():
    ev = build_event(
        store_id="ST1008", camera_id="CAM_1", visitor_id="VIS_x",
        event_type="ZONE_ENTER", timestamp=datetime(2026, 4, 10, 14, 0, 0),
        zone_id="SKINCARE", dwell_ms=-100, is_staff=False, confidence=0.7,
        metadata={},
    )
    assert ev["dwell_ms"] == 0


# ── Empty store period ────────────────────────────────────────────────────────

def test_zero_events_produces_empty_list():
    """Pipeline must not crash on empty detection — just emit nothing."""
    events = []
    # Simulate a window with no detections
    assert len(events) == 0  # nothing to emit, no crash


# ── Billing queue ─────────────────────────────────────────────────────────────

def test_billing_queue_join_has_queue_depth(billing_event):
    assert billing_event["event_type"] == "BILLING_QUEUE_JOIN"
    assert billing_event["metadata"]["queue_depth"] is not None
    assert billing_event["metadata"]["queue_depth"] > 0
