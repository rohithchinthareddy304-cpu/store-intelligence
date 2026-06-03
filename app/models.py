"""
models.py — Pydantic event schema matching challenge specification
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
import uuid

VALID_EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
    "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
}


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 0


class Event(BaseModel):
    event_id:   str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id:   str
    camera_id:  str
    visitor_id: str
    event_type: str
    timestamp:  str
    zone_id:    Optional[str] = None
    dwell_ms:   int = 0
    is_staff:   bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata:   EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v):
        if v not in VALID_EVENT_TYPES:
            raise ValueError(f"Invalid event_type: {v}")
        return v

    @field_validator("dwell_ms")
    @classmethod
    def validate_dwell_ms(cls, v):
        return max(0, v)


class EventBatch(BaseModel):
    events: List[Event] = Field(max_length=500)


class IngestResponse(BaseModel):
    accepted: int
    rejected: int
    duplicate: int
    errors: List[dict] = []


class MetricsResponse(BaseModel):
    store_id: str
    date: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_by_zone: dict
    queue_depth_current: int
    abandonment_rate: float
    total_transactions: int
    data_window_minutes: int


class FunnelStep(BaseModel):
    stage: str
    count: int
    dropoff_pct: float


class FunnelResponse(BaseModel):
    store_id: str
    funnel: List[FunnelStep]
    conversion_rate: float


class HeatmapZone(BaseModel):
    zone_id: str
    visit_count: int
    avg_dwell_ms: float
    normalised_score: float   # 0–100
    data_confidence: str      # HIGH / LOW


class HeatmapResponse(BaseModel):
    store_id: str
    zones: List[HeatmapZone]


class Anomaly(BaseModel):
    anomaly_id: str
    type: str
    severity: str   # INFO / WARN / CRITICAL
    description: str
    suggested_action: str
    detected_at: str
    zone_id: Optional[str] = None


class AnomalyResponse(BaseModel):
    store_id: str
    anomalies: List[Anomaly]


class StoreHealth(BaseModel):
    store_id: str
    status: str
    last_event_timestamp: Optional[str]
    feed_lag_minutes: float
    feed_status: str   # OK / STALE_FEED
    event_count_today: int
    cameras_active: List[str]
