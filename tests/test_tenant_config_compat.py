from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from web.tenant_config_store import load_tenant_config, tenant_config_schema_is_behind
from web.voice_analytics import get_voice_analytics


def test_load_tenant_config_handles_missing_voice_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-tenant-config.db'}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE tenant_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id VARCHAR(36) UNIQUE NOT NULL,
                timezone VARCHAR(64) DEFAULT 'UTC',
                data_retention_days INTEGER DEFAULT 30,
                subscription_plan VARCHAR(32) DEFAULT 'starter',
                subscription_status VARCHAR(32) DEFAULT 'requires_upgrade'
            )
        """))
        conn.execute(
            text("""
                INSERT INTO tenant_configs (
                    tenant_id,
                    timezone,
                    data_retention_days,
                    subscription_plan,
                    subscription_status
                ) VALUES (
                    :tenant_id,
                    :timezone,
                    :data_retention_days,
                    :subscription_plan,
                    :subscription_status
                )
            """),
            {
                "tenant_id": "tenant-1",
                "timezone": "UTC",
                "data_retention_days": 30,
                "subscription_plan": "growth",
                "subscription_status": "active",
            },
        )
        conn.execute(text("""
            CREATE TABLE voice_calls (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36) NOT NULL,
                created_at DATETIME NOT NULL,
                status VARCHAR(32),
                duration_seconds INTEGER,
                sentiment VARCHAR(16)
            )
        """))
        conn.execute(text("""
            CREATE TABLE voice_knowledge_gaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id VARCHAR(36) NOT NULL,
                created_at DATETIME NOT NULL,
                resolved BOOLEAN DEFAULT 0,
                guest_room VARCHAR(64)
            )
        """))

    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with SessionLocal() as db:
        cfg = load_tenant_config(db, "tenant-1")
        analytics = get_voice_analytics(db, "tenant-1", days=30)

    assert cfg is not None
    assert tenant_config_schema_is_behind(cfg)
    assert cfg.subscription_plan == "growth"
    assert cfg.voice_llm_model == "openai/gpt-4o-mini"
    assert cfg.voice_elevenlabs_model == "eleven_turbo_v2"
    assert analytics["plan"] == "growth"
    assert analytics["total_calls"] == 0
    assert analytics["knowledge_gaps"]["total"] == 0


def test_create_missing_tenant_config_handles_legacy_schema(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-tenant-config-create.db'}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE tenant_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id VARCHAR(36) UNIQUE NOT NULL,
                timezone VARCHAR(64) DEFAULT 'UTC',
                data_retention_days INTEGER DEFAULT 30,
                subscription_plan VARCHAR(32) DEFAULT 'starter',
                subscription_status VARCHAR(32) DEFAULT 'requires_upgrade'
            )
        """))

    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with SessionLocal() as db:
        cfg = load_tenant_config(db, "tenant-create", create_if_missing=True)
        row = db.execute(
            text("SELECT tenant_id, subscription_plan, subscription_status FROM tenant_configs WHERE tenant_id = :tenant_id"),
            {"tenant_id": "tenant-create"},
        ).mappings().first()

    assert cfg is not None
    assert tenant_config_schema_is_behind(cfg)
    assert row is not None
    assert row["tenant_id"] == "tenant-create"
    assert row["subscription_plan"] == "starter"
    assert row["subscription_status"] == "requires_upgrade"
