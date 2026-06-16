"""
funnel.py — GET /stores/{store_id}/funnel
Session-based conversion funnel using a data-relative date window
(the most recent event's date) instead of calendar "today".
"""

from datetime import datetime, date
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from database import get_db, EventRecord
from models import FunnelResponse, FunnelStep
from metrics import get_active_range

router = APIRouter()


@router.get("/stores/{store_id}/funnel", response_model=FunnelResponse)
def get_funnel(store_id: str, db: Session = Depends(get_db)):
    start, end = get_active_range(db, store_id)

    entries = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "ENTRY",
        EventRecord.is_staff == False,
        EventRecord.timestamp.between(start, end),
    ).scalar() or 0

    if entries == 0:
        entries = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
            EventRecord.store_id == store_id,
            EventRecord.is_staff == False,
            EventRecord.timestamp.between(start, end),
        ).scalar() or 0

    zone_visitors = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
        EventRecord.zone_id.notin_(["ENTRY_EXIT", "BILLING", "BACKROOM"]),
        EventRecord.is_staff == False,
        EventRecord.timestamp.between(start, end),
    ).scalar() or 0

    if zone_visitors == 0 and entries > 0:
        zone_visitors = entries

    billing_visitors = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.zone_id == "BILLING",
        EventRecord.is_staff == False,
        EventRecord.timestamp.between(start, end),
    ).scalar() or 0

    purchased = db.query(func.count(distinct(EventRecord.visitor_id))).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type == "BILLING_QUEUE_JOIN",
        EventRecord.is_staff == False,
        EventRecord.timestamp.between(start, end),
    ).scalar() or 0

    if purchased == 0:
        purchased = billing_visitors

    def dropoff(prev, curr):
        if prev == 0:
            return 0.0
        return round((prev - curr) / prev * 100, 1)

    stages = [
        FunnelStep(stage="Entry",         count=entries,          dropoff_pct=0.0),
        FunnelStep(stage="Zone Visit",    count=zone_visitors,    dropoff_pct=dropoff(entries, zone_visitors)),
        FunnelStep(stage="Billing Queue", count=billing_visitors, dropoff_pct=dropoff(zone_visitors, billing_visitors)),
        FunnelStep(stage="Purchase",      count=purchased,        dropoff_pct=dropoff(billing_visitors, purchased)),
    ]

    conversion_rate = round(purchased / entries, 4) if entries > 0 else 0.0

    return FunnelResponse(
        store_id=store_id,
        funnel=stages,
        conversion_rate=conversion_rate,
    )
