from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.models import SystemConfig


def _assert_admin_voice_ai_template_surfaces_playback_failures() -> None:
    template_path = ROOT / "web" / "templates" / "admin_voice_ai.html"
    template = template_path.read_text()

    assert 'id="voicePlaybackAlert"' in template
    assert "function scheduleAudioWatchdog" in template
    assert "function showVoicePlaybackIssue" in template
    assert "tenant_id: '{{ admin.id }}'" in template
    assert "audioContext.decodeAudioData" in template
    assert "audio.play().catch" in template
    assert "audio.onerror" in template


def _assert_system_config_maps_voice_id() -> None:
    assert hasattr(SystemConfig, "voice_elevenlabs_voice_id")


def test_admin_voice_ai_template_surfaces_playback_failures():
    _assert_admin_voice_ai_template_surfaces_playback_failures()
    _assert_system_config_maps_voice_id()


if __name__ == "__main__":
    _assert_admin_voice_ai_template_surfaces_playback_failures()
    _assert_system_config_maps_voice_id()
