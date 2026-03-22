"""
Pytest fixtures for HostAI tests.

Uses an in-memory SQLite database (via SQLAlchemy) for speed and isolation.
Each test gets a fresh DB via the `db` fixture.
"""
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
# SQLite test engine (each test session gets a fresh in-memory DB)
# ---------------------------------------------------------------------------
TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=TEST_ENGINE)


@pytest.fixture(scope="session", autouse=True)
def create_tables():
    """Create all tables once per test session."""
    Base.metadata.create_all(bind=TEST_ENGINE)
    yield
    Base.metadata.drop_all(bind=TEST_ENGINE)


@pytest.fixture()
def db():
    """Provide a fresh DB session per test, rolled back after each test."""
    connection = TEST_ENGINE.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


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
