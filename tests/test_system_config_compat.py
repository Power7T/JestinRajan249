from types import SimpleNamespace

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import web.app as app_mod
from web.models import SystemConfig
from web.system_config_store import load_system_config, save_system_config, system_config_schema_is_behind


def test_load_system_config_handles_missing_backup_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-system-config.db'}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE system_config (
                id VARCHAR(36) PRIMARY KEY,
                openrouter_api_key_enc VARCHAR(255),
                google_maps_api_key_enc VARCHAR(255),
                primary_model VARCHAR(100) NOT NULL,
                routine_model VARCHAR(100) NOT NULL,
                fallback_model VARCHAR(100),
                sentiment_model VARCHAR(100),
                updated_at DATETIME
            )
        """))
        conn.execute(
            text("""
                INSERT INTO system_config (
                    id,
                    openrouter_api_key_enc,
                    google_maps_api_key_enc,
                    primary_model,
                    routine_model,
                    fallback_model,
                    sentiment_model,
                    updated_at
                ) VALUES (
                    :id,
                    :openrouter_api_key_enc,
                    :google_maps_api_key_enc,
                    :primary_model,
                    :routine_model,
                    :fallback_model,
                    :sentiment_model,
                    :updated_at
                )
            """),
            {
                "id": "cfg-1",
                "openrouter_api_key_enc": "enc-key",
                "google_maps_api_key_enc": None,
                "primary_model": "model/primary",
                "routine_model": "model/routine",
                "fallback_model": "model/fallback",
                "sentiment_model": "model/sentiment",
                "updated_at": "2026-04-13 00:00:00",
            },
        )

    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with SessionLocal() as db:
        sys_conf = load_system_config(db)

    assert sys_conf is not None
    assert system_config_schema_is_behind(sys_conf)
    assert sys_conf.primary_model == "model/primary"
    assert sys_conf.routine_model == "model/routine"
    assert sys_conf.fallback_model == "model/fallback"
    assert sys_conf.sentiment_model == "model/sentiment"
    assert sys_conf.primary_backup_model == "anthropic/claude-3.5-sonnet"
    assert sys_conf.routine_backup_model == "anthropic/claude-3.5-haiku"
    assert sys_conf.voice_llm_model == "openai/gpt-4o-mini"
    assert sys_conf.voice_deepgram_model == "nova-2"
    assert sys_conf.voice_elevenlabs_model == "eleven_turbo_v2"


def test_load_system_config_prefers_most_recent_populated_row(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'multi-system-config.db'}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE system_config (
                id VARCHAR(36) PRIMARY KEY,
                openrouter_api_key_enc VARCHAR(255),
                google_maps_api_key_enc VARCHAR(255),
                deepgram_api_key_enc VARCHAR(255),
                elevenlabs_api_key_enc VARCHAR(255),
                cloudflare_account_id VARCHAR(255),
                cloudflare_r2_access_key_enc VARCHAR(255),
                cloudflare_r2_secret_key_enc VARCHAR(255),
                cloudflare_r2_bucket VARCHAR(255),
                primary_model VARCHAR(100),
                routine_model VARCHAR(100),
                primary_backup_model VARCHAR(100),
                routine_backup_model VARCHAR(100),
                fallback_model VARCHAR(100),
                sentiment_model VARCHAR(100),
                voice_llm_model VARCHAR(100),
                voice_llm_backup_model VARCHAR(100),
                voice_llm_emergency_model VARCHAR(100),
                voice_deepgram_model VARCHAR(50),
                voice_llm_max_tokens INTEGER,
                voice_llm_temperature FLOAT,
                voice_elevenlabs_model VARCHAR(50),
                voice_elevenlabs_stability FLOAT,
                voice_elevenlabs_similarity FLOAT,
                voice_elevenlabs_voice_id VARCHAR(64),
                updated_at DATETIME
            )
        """))
        conn.execute(
            text("""
                INSERT INTO system_config (
                    id, elevenlabs_api_key_enc, cloudflare_account_id,
                    voice_elevenlabs_voice_id, updated_at
                ) VALUES (
                    'cfg-old', 'stale-elevenlabs', 'chandango12@gmail.com',
                    'EXAVITQu4vr4xnSDxMaL', '2026-04-13 00:00:00'
                )
            """)
        )
        conn.execute(
            text("""
                INSERT INTO system_config (
                    id, openrouter_api_key_enc, deepgram_api_key_enc, elevenlabs_api_key_enc,
                    cloudflare_account_id, cloudflare_r2_bucket, voice_llm_model,
                    voice_elevenlabs_voice_id, updated_at
                ) VALUES (
                    'cfg-new', 'good-openrouter', 'good-deepgram', 'good-elevenlabs',
                    'real-account-id', 'real-bucket', 'openai/gpt-4o-mini',
                    'VR6AewLTigWG4xSOukaG', '2026-04-14 00:00:00'
                )
            """)
        )

    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with SessionLocal() as db:
        sys_conf = load_system_config(db)

    assert sys_conf is not None
    assert sys_conf.id == "cfg-new"
    assert sys_conf.elevenlabs_api_key_enc == "good-elevenlabs"
    assert sys_conf.cloudflare_account_id == "real-account-id"
    assert sys_conf.voice_elevenlabs_voice_id == "VR6AewLTigWG4xSOukaG"


def test_admin_ai_page_shows_warning_in_compat_mode(client, monkeypatch):
    sys_conf = SystemConfig(
        primary_model="model/primary",
        routine_model="model/routine",
        fallback_model="model/fallback",
        sentiment_model="model/sentiment",
        primary_backup_model="anthropic/claude-3.5-sonnet",
        routine_backup_model="anthropic/claude-3.5-haiku",
        voice_llm_model="openai/gpt-4o-mini",
        voice_llm_backup_model="anthropic/claude-3.5-haiku",
        voice_llm_emergency_model="meta-llama/llama-3.3-70b-instruct",
        voice_deepgram_model="nova-2",
        voice_llm_max_tokens=300,
        voice_llm_temperature=0.7,
        voice_elevenlabs_model="eleven_turbo_v2",
        voice_elevenlabs_stability=0.5,
        voice_elevenlabs_similarity=0.75,
    )
    setattr(sys_conf, "_schema_drift_fallback", True)

    monkeypatch.setattr(
        app_mod,
        "_require_admin",
        lambda request, db: SimpleNamespace(email="admin@example.com"),
    )
    monkeypatch.setattr(app_mod, "load_system_config", lambda db, create_if_missing=False: sys_conf)

    response = client.get("/admin/ai")

    assert response.status_code == 200
    assert b"compatibility mode" in response.content


def test_save_system_config_updates_existing_columns_in_compat_mode(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-system-config-save.db'}")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE system_config (
                id VARCHAR(36) PRIMARY KEY,
                openrouter_api_key_enc VARCHAR(255),
                elevenlabs_api_key_enc VARCHAR(255),
                cloudflare_account_id VARCHAR(255),
                voice_elevenlabs_model VARCHAR(50),
                updated_at DATETIME
            )
        """))
        conn.execute(
            text("""
                INSERT INTO system_config (
                    id, openrouter_api_key_enc, elevenlabs_api_key_enc, cloudflare_account_id, voice_elevenlabs_model, updated_at
                ) VALUES (
                    'cfg-1', 'old-openrouter', 'old-elevenlabs', 'old-account', 'eleven_turbo_v2', '2026-04-13 00:00:00'
                )
            """)
        )

    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with SessionLocal() as db:
        sys_conf = load_system_config(db)
        assert sys_conf is not None
        assert system_config_schema_is_behind(sys_conf)

        sys_conf.openrouter_api_key_enc = "new-openrouter"
        sys_conf.elevenlabs_api_key_enc = "new-elevenlabs"
        sys_conf.cloudflare_account_id = "new-account"
        sys_conf.voice_elevenlabs_voice_id = "VR6AewLTigWG4xSOukaG"

        saved = save_system_config(db, sys_conf)

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT openrouter_api_key_enc, elevenlabs_api_key_enc, cloudflare_account_id, voice_elevenlabs_model
            FROM system_config
            WHERE id = 'cfg-1'
        """)).mappings().one()

    assert saved is not None
    assert row["openrouter_api_key_enc"] == "new-openrouter"
    assert row["elevenlabs_api_key_enc"] == "new-elevenlabs"
    assert row["cloudflare_account_id"] == "new-account"
    assert row["voice_elevenlabs_model"] == "eleven_turbo_v2"
