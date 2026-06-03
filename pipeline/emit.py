"""
emit.py — Event schema definition and JSONL emitter

The schema matches the challenge specification exactly.
All events are written as newline-delimited JSON (JSONL).
"""

import json
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path


EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
    "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
}


def build_event(
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: datetime,
    zone_id: Optional[str],
    dwell_ms: int,
    is_staff: bool,
    confidence: float,
    metadata: Dict[str, Any],
) -> dict:
    """Construct a validated event dict matching the challenge schema."""
    assert event_type in EVENT_TYPES, f"Unknown event type: {event_type}"
    assert 0.0 <= confidence <= 1.0, f"Confidence out of range: {confidence}"
    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id,
        "camera_id":  camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp":  timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id":    zone_id,
        "dwell_ms":   max(0, dwell_ms),
        "is_staff":   is_staff,
        "confidence": round(confidence, 4),
        "metadata": {
            "queue_depth": metadata.get("queue_depth"),
            "sku_zone":    metadata.get("sku_zone"),
            "session_seq": metadata.get("session_seq", 0),
        },
    }


class EventEmitter:
    """Writes events to a JSONL file, buffered."""

    def __init__(self, output_path: str, store_id: str, camera_id: str,
                 buffer_size: int = 50):
        self.output_path = output_path
        self.store_id = store_id
        self.camera_id = camera_id
        self.buffer_size = buffer_size
        self._buffer = []
        # Ensure file exists / cleared
        Path(output_path).write_text("")

    def emit(self, event_type: str, visitor_id: str, timestamp: datetime,
             zone_id: Optional[str], dwell_ms: int, is_staff: bool,
             confidence: float, metadata: dict):
        event = build_event(
            store_id=self.store_id,
            camera_id=self.camera_id,
            visitor_id=visitor_id,
            event_type=event_type,
            timestamp=timestamp,
            zone_id=zone_id,
            dwell_ms=dwell_ms,
            is_staff=is_staff,
            confidence=confidence,
            metadata=metadata,
        )
        self._buffer.append(event)
        if len(self._buffer) >= self.buffer_size:
            self.flush()

    def flush(self):
        if not self._buffer:
            return
        with open(self.output_path, "a") as f:
            for ev in self._buffer:
                f.write(json.dumps(ev) + "\n")
        self._buffer.clear()
