# © 2024 Jestin Rajan. All rights reserved.
"""
Read-only database session — routes SELECT queries to the replica when available.

When DATABASE_REPLICA_URL is set (pointing at a PostgreSQL streaming standby),
all read-heavy endpoints (dashboard, activity log, draft list) use this session
so writes on the primary don't compete with large scans.

Falls back transparently to the primary (get_db) when no replica is configured,
so the app works identically in dev/single-DB environments.

Usage:
    from web.db_read import get_read_db
    def my_route(db: Session = Depends(get_read_db)): ...
"""

import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

log = logging.getLogger(__name__)

_DATABASE_REPLICA_URL = os.getenv("DATABASE_REPLICA_URL", "")

if _DATABASE_REPLICA_URL:
    _replica_engine = create_engine(
        _DATABASE_REPLICA_URL,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_timeout=15,
        pool_recycle=1800,
        pool_pre_ping=True,
    )
    _ReplicaSession = sessionmaker(autocommit=False, autoflush=False, bind=_replica_engine)
    log.info("Read replica configured: %s", _DATABASE_REPLICA_URL.split("@")[-1])
else:
    # No replica — fall back to primary (imported lazily to avoid circular import)
    _replica_engine = None
    _ReplicaSession = None


def get_read_db():
    """
    FastAPI dependency: yields a read-only DB session.
    Uses the replica if DATABASE_REPLICA_URL is set, otherwise the primary.
    """
    if _ReplicaSession is not None:
        db = _ReplicaSession()
    else:
        from web.db import SessionLocal
        db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
