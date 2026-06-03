"""
metrics.py — GET /stores/{store_id}/metrics
Real-time store metrics computed from ingested events.
"""

from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from database import get_db, EventRecord
from models import MetricsResponse

router = APIRouter()


def get_today_range():
    today = date.today()
    start = datetime(today.year, today.month, today.day, 0, 0, 0)
    end = datetime(today.year, today.month, today.day, 23, 59, 59)
    return start, end


@router.get("/stores/{store_id}/metrics", response_model=MetricsResponse)
def get_metrics(store_id: str, db: Session = Depends(get_db)):
    start, end = get_today_range()

    # Total unique customer visitors today (exclude staff)
    unique_visitors = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "ENTRY",
        EventRecord.is_staff == False,
        EventRecord.timestamp.between(start, end),
    ).scalar() or 0

    # Visitors who reached billing (proxy for intent to purchase)
    billing_visitors = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.zone_id == "BILLING",
        EventRecord.is_staff == False,
        EventRecord.timestamp.between(start, end),
    ).scalar() or 0

    # Conversion = billing visitors / unique visitors (simplified)
    # Full conversion uses POS correlation; this is the session-based proxy
    conversion_rate = round(billing_visitors / unique_visitors, 4) if unique_visitors > 0 else 0.0

    # Avg dwell per zone (ZONE_DWELL events)
    dwell_rows = db.query(
        EventRecord.zone_id,
        func.avg(EventRecord.dwell_ms).label("avg_dwell"),
    ).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "ZONE_DWELL",
        EventRecord.is_staff == False,
        EventRecord.timestamp.between(start, end),
        EventRecord.zone_id.isnot(None),
    ).group_by(EventRecord.zone_id).all()
    avg_dwell_by_zone = {row.zone_id: round(row.avg_dwell or 0, 1) for row in dwell_rows}

    # Current queue depth (people in BILLING right now = ZONE_ENTER without ZONE_EXIT in last 10min)
    ten_min_ago = datetime.utcnow() - timedelta(minutes=10)
    billing_entered = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.zone_id == "BILLING",
        EventRecord.event_type == "ZONE_ENTER",
        EventRecord.is_staff == False,
        EventRecord.timestamp >= ten_min_ago,
    ).scalar() or 0
    billing_exited = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.zone_id == "BILLING",
        EventRecord.event_type.in_(["ZONE_EXIT", "EXIT"]),
        EventRecord.is_staff == False,
        EventRecord.timestamp >= ten_min_ago,
    ).scalar() or 0
    queue_depth = max(0, billing_entered - billing_exited)

    # Abandonment rate
    abandoned = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "BILLING_QUEUE_ABANDON",
        EventRecord.is_staff == False,
        EventRecord.timestamp.between(start, end),
    ).scalar() or 0
    abandonment_rate = round(abandoned / billing_visitors, 4) if billing_visitors > 0 else 0.0

    # Total transactions (unique BILLING_QUEUE_JOIN or from POS correlation)
    total_transactions = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "BILLING_QUEUE_JOIN",
        EventRecord.timestamp.between(start, end),
    ).scalar() or billing_visitors  # fallback

    return MetricsResponse(
        store_id=store_id,
        date=str(date.today()),
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_by_zone=avg_dwell_by_zone,
        queue_depth_current=queue_depth,
        abandonment_rate=abandonment_rate,
        total_transactions=total_transactions,
        data_window_minutes=int((end - start).total_seconds() / 60),
    )
