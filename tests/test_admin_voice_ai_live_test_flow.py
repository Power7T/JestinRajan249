from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _read_admin_voice_ai_template() -> str:
    return (ROOT / "web" / "templates" / "admin_voice_ai.html").read_text()


def _assert_voice_ai_websocket_start_payload_has_tenant_context(template: str) -> None:
    start_payload = re.compile(
        r"voiceWs\.send\(\s*JSON\.stringify\(\s*\{\s*type:\s*'start',\s*tenant_id:\s*'{{ admin\.id }}'\s*\}\s*\)\s*\);"
    )
    assert start_payload.search(template), "Expected websocket start payload to include tenant_id context"


def _assert_voice_ai_template_exposes_playback_failure_hooks(template: str) -> None:
    required_snippets = [
        'id="voicePlaybackAlert"',
        "function scheduleAudioWatchdog",
        "function showVoicePlaybackIssue",
        "audio.onerror",
        "audio.play().catch",
        "audioContext.decodeAudioData",
    ]
    for snippet in required_snippets:
        assert snippet in template, f"Missing playback failure hook: {snippet}"


def test_admin_voice_ai_live_test_flow_template_contract():
    template = _read_admin_voice_ai_template()
    _assert_voice_ai_websocket_start_payload_has_tenant_context(template)
    _assert_voice_ai_template_exposes_playback_failure_hooks(template)


if __name__ == "__main__":
    test_admin_voice_ai_live_test_flow_template_contract()
