from types import SimpleNamespace

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import web.app as app_mod
from web.models import SystemConfig
from web.system_config_store import load_system_config, system_config_schema_is_behind


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
