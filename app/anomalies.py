"""
anomalies.py — GET /stores/{store_id}/anomalies
Active anomalies: queue spike, conversion drop, dead zone.
"""

import uuid
from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from database import get_db, EventRecord
from models import AnomalyResponse, Anomaly

router = APIRouter()

# Thresholds
QUEUE_SPIKE_THRESHOLD = 5          # >5 people in billing simultaneously
DEAD_ZONE_MINUTES = 30             # no visits in 30 min
CONVERSION_DROP_THRESHOLD = 0.30   # 30% drop vs 7-day avg (we use hourly avg as proxy)


@router.get("/stores/{store_id}/anomalies", response_model=AnomalyResponse)
def get_anomalies(store_id: str, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    anomalies = []

    # ── 1. Queue Spike ──────────────────────────────────────────────────────
    recent_window = now - timedelta(minutes=15)
    billing_joins = db.query(EventRecord).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "BILLING_QUEUE_JOIN",
        EventRecord.timestamp >= recent_window,
    ).all()
    if billing_joins:
        max_queue = max((ev.queue_depth or 0) for ev in billing_joins)
        if max_queue >= QUEUE_SPIKE_THRESHOLD:
            anomalies.append(Anomaly(
                anomaly_id=str(uuid.uuid4()),
                type="BILLING_QUEUE_SPIKE",
                severity="CRITICAL",
                description=f"Queue depth reached {max_queue} in the last 15 minutes.",
                suggested_action="Open additional billing counter or call more staff to billing.",
                detected_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                zone_id="BILLING",
            ))
        elif max_queue >= 3:
            anomalies.append(Anomaly(
                anomaly_id=str(uuid.uuid4()),
                type="BILLING_QUEUE_BUILDUP",
                severity="WARN",
                description=f"Queue depth {max_queue} — building up at billing.",
                suggested_action="Monitor billing counter; consider proactive staff reallocation.",
                detected_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                zone_id="BILLING",
            ))

    # ── 2. Dead Zone (no visits in 30 min) ─────────────────────────────────
    thirty_min_ago = now - timedelta(minutes=DEAD_ZONE_MINUTES)
    customer_zones = ["SKINCARE", "MAKEUP", "CLEAN_BEAUTY", "KOREAN_BEAUTY",
                      "ACCESSORIES", "LIPS_EYES"]
    for zone in customer_zones:
        recent_zone_visit = db.query(EventRecord).filter(
            EventRecord.store_id == store_id,
            EventRecord.zone_id == zone,
            EventRecord.is_staff == False,
            EventRecord.timestamp >= thirty_min_ago,
        ).first()
        if recent_zone_visit is None:
            # Check if there's ever been any data for this zone today
            has_any = db.query(EventRecord).filter(
                EventRecord.store_id == store_id,
                EventRecord.zone_id == zone,
                EventRecord.timestamp >= today_start,
            ).first()
            severity = "WARN" if has_any else "INFO"
            anomalies.append(Anomaly(
                anomaly_id=str(uuid.uuid4()),
                type="DEAD_ZONE",
                severity=severity,
                description=f"No customer visits in zone {zone} for 30+ minutes.",
                suggested_action=f"Check if {zone} display needs restocking or staff attention.",
                detected_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                zone_id=zone,
            ))

    # ── 3. Conversion Drop ──────────────────────────────────────────────────
    # Compare current hour vs today's average conversion
    hour_ago = now - timedelta(hours=1)
    entries_hour = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "ENTRY",
        EventRecord.is_staff == False,
        EventRecord.timestamp >= hour_ago,
    ).scalar() or 0

    billing_hour = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.zone_id == "BILLING",
        EventRecord.is_staff == False,
        EventRecord.timestamp >= hour_ago,
    ).scalar() or 0

    entries_today = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "ENTRY",
        EventRecord.is_staff == False,
        EventRecord.timestamp >= today_start,
    ).scalar() or 0

    billing_today = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.zone_id == "BILLING",
        EventRecord.is_staff == False,
        EventRecord.timestamp >= today_start,
    ).scalar() or 0

    conv_hour  = billing_hour / entries_hour   if entries_hour  > 0 else None
    conv_today = billing_today / entries_today if entries_today > 0 else None

    if conv_hour is not None and conv_today is not None and conv_today > 0:
        drop = (conv_today - conv_hour) / conv_today
        if drop >= CONVERSION_DROP_THRESHOLD:
            anomalies.append(Anomaly(
                anomaly_id=str(uuid.uuid4()),
                type="CONVERSION_DROP",
                severity="WARN",
                description=f"Conversion rate this hour ({conv_hour:.1%}) is {drop:.0%} below today's average ({conv_today:.1%}).",
                suggested_action="Check floor staff engagement; review if any zone is inaccessible.",
                detected_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                zone_id=None,
            ))

    # ── 4. High abandonment ─────────────────────────────────────────────────
    abandons = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "BILLING_QUEUE_ABANDON",
        EventRecord.timestamp >= hour_ago,
    ).scalar() or 0
    if abandons >= 3:
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            type="HIGH_ABANDONMENT",
            severity="WARN",
            description=f"{abandons} customers abandoned the billing queue in the last hour.",
            suggested_action="Reduce billing wait time; consider mobile billing or express lane.",
            detected_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            zone_id="BILLING",
        ))

    return AnomalyResponse(store_id=store_id, anomalies=anomalies)
