"""
metrics.py — GET /stores/{store_id}/metrics
Real-time store metrics computed from ingested events.

FIX: Instead of filtering by calendar "today" (which breaks when events
are from a different date than the server's current date), we use a
window relative to the most recent event in the data. This way the
dashboard shows real numbers regardless of when the video was recorded
vs when it's being viewed.
"""

from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from database import get_db, EventRecord
from models import MetricsResponse

router = APIRouter()


def get_active_range(db: Session, store_id: str):
    """
    Returns (start, end) datetime range to use for 'today' style queries.
    Uses the most recent event's date for that store instead of the
    server's wall-clock date, so historical/demo data still produces
    real numbers.
    """
    latest = db.query(func.max(EventRecord.timestamp)).filter(
        EventRecord.store_id == store_id
    ).scalar()

    if latest is None:
        # No data at all — fall back to calendar today (will return zeros)
        today = date.today()
        start = datetime(today.year, today.month, today.day)
        end = datetime(today.year, today.month, today.day, 23, 59, 59)
        return start, end

    start = datetime(latest.year, latest.month, latest.day, 0, 0, 0)
    end = datetime(latest.year, latest.month, latest.day, 23, 59, 59)
    return start, end


@router.get("/stores/{store_id}/metrics", response_model=MetricsResponse)
def get_metrics(store_id: str, db: Session = Depends(get_db)):
    start, end = get_active_range(db, store_id)

    unique_visitors = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "ENTRY",
        EventRecord.is_staff == False,
        EventRecord.timestamp.between(start, end),
    ).scalar() or 0

    billing_visitors = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.zone_id == "BILLING",
        EventRecord.is_staff == False,
        EventRecord.timestamp.between(start, end),
    ).scalar() or 0

    conversion_rate = round(billing_visitors / unique_visitors, 4) if unique_visitors > 0 else 0.0

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

    # Queue depth: use the same data-relative window's last 10 minutes of activity
    latest_ts = db.query(func.max(EventRecord.timestamp)).filter(
        EventRecord.store_id == store_id
    ).scalar() or datetime.utcnow()
    ten_min_before_latest = latest_ts - timedelta(minutes=10)

    billing_entered = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.zone_id == "BILLING",
        EventRecord.event_type == "ZONE_ENTER",
        EventRecord.is_staff == False,
        EventRecord.timestamp >= ten_min_before_latest,
    ).scalar() or 0
    billing_exited = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.zone_id == "BILLING",
        EventRecord.event_type.in_(["ZONE_EXIT", "EXIT"]),
        EventRecord.is_staff == False,
        EventRecord.timestamp >= ten_min_before_latest,
    ).scalar() or 0
    queue_depth = max(0, billing_entered - billing_exited)

    abandoned = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "BILLING_QUEUE_ABANDON",
        EventRecord.is_staff == False,
        EventRecord.timestamp.between(start, end),
    ).scalar() or 0
    abandonment_rate = round(abandoned / billing_visitors, 4) if billing_visitors > 0 else 0.0

    total_transactions = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "BILLING_QUEUE_JOIN",
        EventRecord.timestamp.between(start, end),
    ).scalar() or billing_visitors

    return MetricsResponse(
        store_id=store_id,
        date=str(start.date()),
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_by_zone=avg_dwell_by_zone,
        queue_depth_current=queue_depth,
        abandonment_rate=abandonment_rate,
        total_transactions=total_transactions,
        data_window_minutes=int((end - start).total_seconds() / 60),
    )
