"""
main.py — FastAPI entrypoint for Store Intelligence API
"""

import uuid
import time
import logging
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import database as _db_module
from database import Base, get_db
from ingestion import router as ingest_router
from metrics import router as metrics_router
from funnel import router as funnel_router
from heatmap import router as heatmap_router
from anomalies import router as anomaly_router
from health import router as health_router

# ── Structured logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
)
logger = logging.getLogger("store_intel")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB tables on startup."""
    Base.metadata.create_all(bind=_db_module.engine)
    logger.info('"Startup: DB tables created"')
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

# ── Request logging middleware ────────────────────────────────────────────────
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


# ── Routers ───────────────────────────────────────────────────────────────────
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
