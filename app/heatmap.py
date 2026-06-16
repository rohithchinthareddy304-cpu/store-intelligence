"""
heatmap.py — GET /stores/{store_id}/heatmap
Zone visit frequency + avg dwell, normalised 0–100.
Uses data-relative date window instead of calendar "today".
"""

from datetime import datetime, date
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from database import get_db, EventRecord
from models import HeatmapResponse, HeatmapZone
from metrics import get_active_range

router = APIRouter()

CUSTOMER_ZONES = ["SKINCARE", "MAKEUP", "CLEAN_BEAUTY", "KOREAN_BEAUTY",
                   "ACCESSORIES", "LIPS_EYES", "BILLING"]


@router.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse)
def get_heatmap(store_id: str, db: Session = Depends(get_db)):
    start, end = get_active_range(db, store_id)

    rows = db.query(
        EventRecord.zone_id,
        func.count(distinct(EventRecord.visitor_id)).label("visit_count"),
        func.avg(EventRecord.dwell_ms).label("avg_dwell"),
    ).filter(
        EventRecord.store_id == store_id,
        EventRecord.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
        EventRecord.is_staff == False,
        EventRecord.zone_id.isnot(None),
        EventRecord.timestamp.between(start, end),
    ).group_by(EventRecord.zone_id).all()

    zone_data = {
        r.zone_id: {
            "visit_count": r.visit_count or 0,
            "avg_dwell_ms": float(r.avg_dwell or 0),
        }
        for r in rows
        if r.zone_id in CUSTOMER_ZONES
    }

    for z in CUSTOMER_ZONES:
        if z not in zone_data:
            zone_data[z] = {"visit_count": 0, "avg_dwell_ms": 0.0}

    max_visits = max((v["visit_count"] for v in zone_data.values()), default=1) or 1

    zones = []
    for zone_id, data in zone_data.items():
        count = data["visit_count"]
        score = round(count / max_visits * 100, 1)
        confidence = "HIGH" if count >= 20 else "LOW"
        zones.append(HeatmapZone(
            zone_id=zone_id,
            visit_count=count,
            avg_dwell_ms=round(data["avg_dwell_ms"], 1),
            normalised_score=score,
            data_confidence=confidence,
        ))

    zones.sort(key=lambda x: x.normalised_score, reverse=True)
    return HeatmapResponse(store_id=store_id, zones=zones)
