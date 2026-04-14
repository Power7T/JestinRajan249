from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "web" / "app.py"
MIGRATION_PATH = ROOT / "web" / "alembic" / "versions" / "20260414_0200_add_system_config_voice_id.py"


def _read(path: Path) -> str:
    return path.read_text()


def _function_source(source: str, name: str) -> str:
    pattern = re.compile(rf"(?ms)^def {name}\(.*?(?=^def |^async def |^@app\\.|\\Z)")
    match = pattern.search(source)
    if match:
        return match.group(0)
    pattern = re.compile(rf"(?ms)^async def {name}\(.*?(?=^def |^async def |^@app\\.|\\Z)")
    match = pattern.search(source)
    assert match, f"Could not find function {name}"
    return match.group(0)


def test_voice_admin_routes_use_schema_compat_loaders() -> None:
    app_source = _read(APP_PATH)

    system_config_routes = [
        "admin_voice_ai",
        "admin_voice_ai_save",
        "host_voice_chat_page",
        "host_voice_chat_message",
        "voice_ai_voice_message",
        "websocket_voice_ai_live",
        "admin_voice_ai_backend",
        "admin_test_voice_ai_connection",
        "admin_voice_ai_status",
    ]
    for route_name in system_config_routes:
        route_source = _function_source(app_source, route_name)
        assert "load_system_config(" in route_source, f"{route_name} should use load_system_config()"
        assert "db.query(SystemConfig).first()" not in route_source, f"{route_name} should not raw-query SystemConfig"

    tenant_config_routes = [
        "host_voice_chat_page",
        "host_voice_chat_message",
        "voice_ai_voice_message",
        "voice_ai_synthesize",
        "websocket_voice_ai_live",
    ]
    for route_name in tenant_config_routes:
        route_source = _function_source(app_source, route_name)
        assert "load_tenant_config(" in route_source, f"{route_name} should use load_tenant_config()"
        assert "db.query(TenantConfig)" not in route_source, f"{route_name} should not raw-query TenantConfig"


def test_admin_live_voice_prefers_system_voice_selection() -> None:
    route_source = _function_source(_read(APP_PATH), "websocket_voice_ai_live")
    system_index = route_source.index('getattr(sys_conf, "voice_elevenlabs_voice_id", None)')
    tenant_index = route_source.index("tenant_config_obj.voice_elevenlabs_voice_id")
    assert system_index < tenant_index, "Admin live voice test should prefer the system-selected voice ID"
    assert 'No authenticated tenant found' in route_source


def test_system_config_voice_id_migration_exists() -> None:
    migration_source = _read(MIGRATION_PATH)
    assert "system_config" in migration_source
    assert "voice_elevenlabs_voice_id" in migration_source


if __name__ == "__main__":
    test_voice_admin_routes_use_schema_compat_loaders()
    test_admin_live_voice_prefers_system_voice_selection()
    test_system_config_voice_id_migration_exists()
