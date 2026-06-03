"""
database.py — SQLAlchemy setup with SQLite (upgradeable to PostgreSQL)
"""

import os
from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./store_intel.db")

# SQLite needs check_same_thread=False for FastAPI
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class EventRecord(Base):
    __tablename__ = "events"

    event_id    = Column(String, primary_key=True, index=True)
    store_id    = Column(String, index=True)
    camera_id   = Column(String)
    visitor_id  = Column(String, index=True)
    event_type  = Column(String, index=True)
    timestamp   = Column(DateTime, index=True)
    zone_id     = Column(String, nullable=True)
    dwell_ms    = Column(Integer, default=0)
    is_staff    = Column(Boolean, default=False)
    confidence  = Column(Float, default=1.0)
    queue_depth = Column(Integer, nullable=True)
    sku_zone    = Column(String, nullable=True)
    session_seq = Column(Integer, default=0)
    ingested_at = Column(DateTime, default=datetime.utcnow)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
