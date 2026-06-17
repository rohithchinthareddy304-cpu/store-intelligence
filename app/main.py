"""
main.py — FastAPI entrypoint for Store Intelligence API

CHANGE: Added auto-seed on startup. Render's free tier filesystem is
ephemeral — every redeploy wipes the SQLite file. Instead of relying on
manual re-ingestion after each deploy, the app now loads events directly
from a bundled JSONL file (events_seed.jsonl) into the database on
every startup. This guarantees the dashboard always has data without
any manual step, even right after a fresh deploy.
"""

import uuid
import time
import logging
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import database as _db_module
from database import Base, get_db, EventRecord
from ingestion import router as ingest_router
from metrics import router as metrics_router
from funnel import router as funnel_router
from heatmap import router as heatmap_router
from anomalies import router as anomaly_router
from health import router as health_router

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
)
logger = logging.getLogger("store_intel")

SEED_FILE = Path(__file__).parent / "events_seed.jsonl"


def seed_events_if_empty():
    """On startup, if the events table is empty, load events from the
    bundled seed file. This makes the API self-healing after every
    Render redeploy without needing a manual re-ingest step."""
    db = _db_module.SessionLocal()
    try:
        existing_count = db.query(EventRecord).count()
        if existing_count > 0:
            logger.info(f'"Seed skipped: {existing_count} events already present"')
            return

        if not SEED_FILE.exists():
            logger.info('"Seed skipped: no events_seed.jsonl found"')
            return

        loaded = 0
        with open(SEED_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    ts = datetime.strptime(ev["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
                    record = EventRecord(
                        event_id=ev["event_id"],
                        store_id=ev["store_id"],
                        camera_id=ev["camera_id"],
                        visitor_id=ev["visitor_id"],
                        event_type=ev["event_type"],
                        timestamp=ts,
                        zone_id=ev.get("zone_id"),
                        dwell_ms=ev.get("dwell_ms", 0),
                        is_staff=ev.get("is_staff", False),
                        confidence=ev.get("confidence", 1.0),
                        queue_depth=ev.get("metadata", {}).get("queue_depth"),
                        sku_zone=ev.get("metadata", {}).get("sku_zone"),
                        session_seq=ev.get("metadata", {}).get("session_seq", 0),
                    )
                    db.add(record)
                    loaded += 1
                except Exception as exc:
                    logger.warning(f'"Skipped malformed seed line: {str(exc)[:100]}"')
                    continue
        db.commit()
        logger.info(f'"Seed complete: {loaded} events loaded from events_seed.jsonl"')
    except Exception as exc:
        db.rollback()
        logger.error(f'"Seed failed: {str(exc)[:200]}"')
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB tables and auto-seed on startup."""
    Base.metadata.create_all(bind=_db_module.engine)
    logger.info('"Startup: DB tables created"')
    seed_events_if_empty()
    yield
    logger.info('"Shutdown"')


app = FastAPI(
    title="Store Intelligence API",
    description="Purplle Brigade Bangalore — retail analytics from CCTV",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    trace_id = str(uuid.uuid4())[:8]
    request.state.trace_id = trace_id
    start = time.time()

    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error(json.dumps({
            "trace_id": trace_id,
            "endpoint": str(request.url.path),
            "error": str(exc),
        }))
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "trace_id": trace_id}
        )

    latency_ms = int((time.time() - start) * 1000)
    store_id = request.path_params.get("store_id", "-")
    logger.info(json.dumps({
        "trace_id": trace_id,
        "store_id": store_id,
        "endpoint": str(request.url.path),
        "method": request.method,
        "latency_ms": latency_ms,
        "status_code": response.status_code,
    }))
    response.headers["X-Trace-Id"] = trace_id
    return response


app.include_router(ingest_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(heatmap_router)
app.include_router(anomaly_router)
app.include_router(health_router)


@app.get("/")
def root():
    return {"service": "Store Intelligence API", "status": "ok",
            "store": "ST1008 — Brigade Bangalore"}
