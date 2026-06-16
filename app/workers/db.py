"""
app/workers/db.py
──────────────────
Synchronous SQLAlchemy engine + session factory for Celery tasks.

Celery workers are synchronous processes, so they cannot share the API's async
engine/connection pool.  Previously each task built its own engine with
`create_engine(...)` and disposed it per call — wasteful (a fresh pool, TCP
handshake, and teardown on every task).

This module creates a *single* module-level engine per worker process and a
reusable session factory.  Use the `worker_session()` context manager:

    from app.workers.db import worker_session

    with worker_session() as session:
        session.add(row)
        # commit handled by the context manager
"""

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# One engine per worker process. A small pool is plenty for the worker's
# concurrency; pool_pre_ping guards against connections dropped while idle.
engine = create_engine(
    settings.SYNC_DATABASE_URL,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=1800,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


@contextmanager
def worker_session():
    """Yield a Session; commit on success, roll back on error, always close."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
