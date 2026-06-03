"""
conftest.py — DB setup for tests.
Uses a single SQLite connection shared across engine + sessions,
so in-memory tables are visible to all test code.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../app"))

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
import database

# Single in-memory DB with shared cache so all connections see the same tables
_engine = create_engine(
    "sqlite:///file:testdb?mode=memory&cache=shared&uri=true",
    connect_args={"check_same_thread": False},
)
_Session = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

# Patch database module BEFORE any app code runs
database.engine = _engine
database.SessionLocal = _Session
database.Base.metadata.create_all(bind=_engine)

def override_db():
    db = _Session()
    try:
        yield db
    finally:
        db.close()

database.get_db = override_db
