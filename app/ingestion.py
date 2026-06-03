"""
ingestion.py — POST /events/ingest
  - Idempotent by event_id (safe to call twice with same payload)
  - Accepts batches of up to 500 events
  - Partial success on malformed events
  - Structured error response
"""

import json
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database import get_db, EventRecord
from models import EventBatch, Event, IngestResponse

router = APIRouter()
logger = logging.getLogger("store_intel.ingest")


def event_to_record(ev: Event) -> EventRecord:
    try:
        ts = datetime.strptime(ev.timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        ts = datetime.utcnow()
    return EventRecord(
        event_id=ev.event_id,
        store_id=ev.store_id,
        camera_id=ev.camera_id,
        visitor_id=ev.visitor_id,
        event_type=ev.event_type,
        timestamp=ts,
        zone_id=ev.zone_id,
        dwell_ms=ev.dwell_ms,
        is_staff=ev.is_staff,
        confidence=ev.confidence,
        queue_depth=ev.metadata.queue_depth,
        sku_zone=ev.metadata.sku_zone,
        session_seq=ev.metadata.session_seq,
    )


@router.post("/events/ingest", response_model=IngestResponse)
def ingest_events(batch: EventBatch, request: Request, db: Session = Depends(get_db)):
    accepted = 0
    rejected = 0
    duplicate = 0
    errors = []

    for ev in batch.events:
        try:
            record = event_to_record(ev)
            db.add(record)
            db.flush()  # Detect constraint violations immediately
            accepted += 1
        except IntegrityError:
            db.rollback()
            duplicate += 1
            # Idempotent: duplicate event_id is not an error
        except Exception as exc:
            db.rollback()
            rejected += 1
            errors.append({
                "event_id": ev.event_id if hasattr(ev, "event_id") else "unknown",
                "reason": str(exc)[:200],
            })

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(json.dumps({"msg": "commit_failed", "error": str(exc)}))

    logger.info(json.dumps({
        "endpoint": "/events/ingest",
        "event_count": len(batch.events),
        "accepted": accepted,
        "duplicate": duplicate,
        "rejected": rejected,
    }))

    return IngestResponse(
        accepted=accepted,
        rejected=rejected,
        duplicate=duplicate,
        errors=errors,
    )
