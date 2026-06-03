"""
health.py — GET /health
Service status, last event timestamp per store, STALE_FEED warning if >10 min lag.
"""

from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from database import get_db, EventRecord
from models import StoreHealth

router = APIRouter()
STALE_FEED_MINUTES = 10


@router.get("/health", response_model=StoreHealth)
def health(db: Session = Depends(get_db)):
    store_id = "ST1008"

    try:
        last_ev = db.query(EventRecord).filter(
            EventRecord.store_id == store_id
        ).order_by(EventRecord.timestamp.desc()).first()

        cameras = db.query(distinct(EventRecord.camera_id)).filter(
            EventRecord.store_id == store_id
        ).all()
        cameras_active = [c[0] for c in cameras]

        event_count = db.query(func.count(EventRecord.event_id)).filter(
            EventRecord.store_id == store_id
        ).scalar() or 0

        now = datetime.utcnow()
        if last_ev:
            lag_minutes = (now - last_ev.timestamp).total_seconds() / 60
            feed_status = "STALE_FEED" if lag_minutes > STALE_FEED_MINUTES else "OK"
            last_ts = last_ev.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            lag_minutes = 9999.0
            feed_status = "STALE_FEED"
            last_ts = None

        return StoreHealth(
            store_id=store_id,
            status="ok",
            last_event_timestamp=last_ts,
            feed_lag_minutes=round(lag_minutes, 1),
            feed_status=feed_status,
            event_count_today=event_count,
            cameras_active=cameras_active,
        )
    except Exception as exc:
        return StoreHealth(
            store_id=store_id,
            status="degraded",
            last_event_timestamp=None,
            feed_lag_minutes=9999.0,
            feed_status="STALE_FEED",
            event_count_today=0,
            cameras_active=[],
        )
