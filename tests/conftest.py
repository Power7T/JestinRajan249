"""
Pytest fixtures for HostAI tests.

Uses an in-memory SQLite database (via SQLAlchemy) for speed and isolation.
Each test gets a fresh DB via the `db` fixture.
"""
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Point at SQLite before importing the app so DATABASE_URL is set
os.environ.setdefault("DATABASE_URL",           "sqlite:///:memory:")
os.environ.setdefault("DATABASE_REPLICA_URL",   "")
os.environ.setdefault("SECRET_KEY",             "test-secret-key-not-for-production")
os.environ.setdefault("FIELD_ENCRYPTION_KEY",   "mR1ZucKnK-yZ83VUfz7ziQIkP61-5ZW9Re0MsDzx75o=")
os.environ.setdefault("APP_BASE_URL",           "http://testserver")
os.environ.setdefault("REDIS_URL",              "")   # Redis disabled in tests
os.environ.setdefault("ENVIRONMENT",            "test")
os.environ.setdefault("RUN_EMBEDDED_WORKERS",   "0")
os.environ.setdefault("WORKERS",                "1")  # no multi-worker warning in tests
os.environ.setdefault("STRIPE_WEBHOOK_SECRET",  "")   # not enforced in test mode
os.environ.setdefault("STRIPE_SECRET_KEY",      "")   # not enforced in test mode
os.environ.setdefault("INTERNAL_TOKEN",         "")

from web.db import Base, get_db  # noqa: E402
from web.app import app           # noqa: E402

# ---------------------------------------------------------------------------
# SQLite test engine — StaticPool ensures a single shared in-memory database
# so all sessions (app code + test fixtures) operate on the same data.
# ---------------------------------------------------------------------------
TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=TEST_ENGINE)


def _seed_plan_configs(session) -> None:
    """Insert default PlanConfig rows (idempotent)."""
    from web.models import PlanConfig
    plans = [
        {"plan_key": "starter", "display_name": "Starter", "base_fee_usd": 20.0, "per_unit_fee_usd": 10.0, "min_units": 1, "max_units": 5},
        {"plan_key": "growth",  "display_name": "Growth",  "base_fee_usd": 20.0, "per_unit_fee_usd":  9.0, "min_units": 6, "max_units": 10},
        {"plan_key": "pro",     "display_name": "Pro",     "base_fee_usd": 20.0, "per_unit_fee_usd":  8.0, "min_units": 11, "max_units": 50},
    ]
    for plan_data in plans:
        if not session.query(PlanConfig).filter_by(plan_key=plan_data["plan_key"]).first():
            session.add(PlanConfig(**plan_data))
    session.commit()


@pytest.fixture(scope="session", autouse=True)
def create_tables():
    """Create all tables once per test session and seed initial data."""
    Base.metadata.create_all(bind=TEST_ENGINE)
    seed = TestingSessionLocal()
    try:
        _seed_plan_configs(seed)
    finally:
        seed.close()
    yield
    Base.metadata.drop_all(bind=TEST_ENGINE)


def _truncate_all_tables(engine) -> None:
    """Delete all rows from every table in dependency-safe order."""
    with engine.begin() as conn:
        # Disable FK enforcement in SQLite so we can truncate in any order
        conn.execute(text("PRAGMA foreign_keys = OFF"))
        for table in Base.metadata.sorted_tables:
            conn.execute(table.delete())
        conn.execute(text("PRAGMA foreign_keys = ON"))


@pytest.fixture()
def db():
    """
    Provide a per-test DB session.

    Before yielding: nothing (tables already exist from create_tables).
    After yielding: truncate all rows so the next test starts clean,
    then re-seed the plan configs that are expected to always be present.

    Using StaticPool + per-test cleanup avoids all the nested-transaction
    complexity that SQLAlchemy 2.0 + SQLite makes tricky with rollback-based
    isolation.
    """
    session = TestingSessionLocal()
    yield session
    session.close()
    _truncate_all_tables(TEST_ENGINE)
    seed = TestingSessionLocal()
    try:
        _seed_plan_configs(seed)
    finally:
        seed.close()


@pytest.fixture()
def client(db):
    """FastAPI TestClient with DB dependency overridden to use the test DB."""
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.state.auth_db_session_provider = lambda: db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()
    if hasattr(app.state, "auth_db_session_provider"):
        delattr(app.state, "auth_db_session_provider")
