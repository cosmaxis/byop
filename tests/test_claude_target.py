"""Tests for the Claude Code target."""

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core.config import ModelConfig, ProviderConfig
from byop.core.targets.claude import ClaudeTarget, anthropic_base_url


# ----------------------------------------------------------------------
# Install/detection (T7)
# ----------------------------------------------------------------------
def _result(returncode=0, stdout="", stderr=""):
    class R:
        pass
    r = R()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def test_install_via_brew_when_present():
    calls = []

    def _run(cmd, *a, **k):
        calls.append(cmd)
        r = _result()
        return r

    with mock.patch(
        "byop.core.targets.claude.shutil.which",
        side_effect=lambda n: "/opt/homebrew/bin/brew" if n == "brew" else None,
    ), mock.patch("byop.core.targets.claude._run", _run):
        ClaudeTarget().install(log=lambda m: None)
    assert any(c[:4] == ["brew", "install", "--cask", "claude-code"] for c in calls)


def test_install_falls_back_to_npm():
    calls = []

    def _run(cmd, *a, **k):
        calls.append(cmd)
        return _result()

    def which(name):
        return "/usr/local/bin/npm" if name == "npm" else None

    with mock.patch("byop.core.targets.claude.shutil.which", which), \
         mock.patch("byop.core.targets.claude._run", _run):
        ClaudeTarget().install(log=lambda m: None)
    assert any("npm" in c and "install" in c and "-g" in c for c in calls)


# ----------------------------------------------------------------------
# build_fragment + configure (T8)
# ----------------------------------------------------------------------
def _provider(**over):
    base = {
        "provider_name": "Cosmaxis",
        "api_url": "https://api.example.com/v1/",
        "api_key": "sk-12345678",
        "models": [
            ModelConfig(name="hy3", max_tokens=250000, max_output_tokens=32000)
        ],
    }
    base.update(over)
    return ProviderConfig(**base)


def test_anthropic_base_url_strips_trailing_v1():
    assert anthropic_base_url("https://api.example.com/v1/") == "https://api.example.com"
    assert anthropic_base_url("https://gw.example.com/proxy/v1") == "https://gw.example.com/proxy"
    assert anthropic_base_url("https://gw.example.com/anthropic") == "https://gw.example.com/anthropic"


def test_build_fragment_shape_with_keychain():
    frag = ClaudeTarget().build_fragment(_provider(), use_keychain=True)
    assert frag["env"]["ANTHROPIC_BASE_URL"] == "https://api.example.com"
    assert frag["env"]["BYOP_PROVIDER"] == "Cosmaxis"
    assert "ANTHROPIC_AUTH_TOKEN" not in frag["env"]
    assert frag["model"] == "hy3"
    assert frag["apiKeyHelper"].startswith("security find-internet-password")
    assert not frag["apiKeyHelper"].startswith("!")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS keychain required")
def test_configure_first_run_stores_key_before_building_fragment(tmp_path):
    """First run (keychain initially empty) must store key first, then write apiKeyHelper."""
    settings = tmp_path / "settings.json"
    target = ClaudeTarget(settings_path=settings)
    stored = {"present": False}

    def fake_ensure(**kwargs):
        stored["present"] = True
        return ["keychain:x"]

    with mock.patch("byop.core.targets.claude.kc.keychain_has", side_effect=lambda *_: stored["present"]), \
         mock.patch("byop.core.targets.claude.kc.ensure_key", side_effect=fake_ensure):
        target.configure(_provider(), log=lambda m: None)
    data = json.loads(settings.read_text())
    assert "sk-12345678" not in settings.read_text()
    assert data["apiKeyHelper"].startswith("security find-internet-password")
    assert data["env"]["ANTHROPIC_BASE_URL"] == "https://api.example.com"
    assert data["env"]["BYOP_PROVIDER"] == "Cosmaxis"


def test_configure_preserves_env_and_cleans_legacy_keys_and_mode_switches(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "provider": "OldLegacy",
        "apiBaseUrl": "https://old/v1",
        "apiKey": "!security ...",
        "models": [{"id": "old"}],
        "permissions": {"allow": ["Bash(pytest)"]},
        "env": {
            "ANTHROPIC_API_KEY": "sk-stale-api-key",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
    }))
    target = ClaudeTarget(settings_path=settings)

    # --no-keychain writes ANTHROPIC_AUTH_TOKEN and drops legacy keys.
    with mock.patch("byop.core.targets.claude.kc.ensure_key", return_value=["env:x"]):
        target.configure(_provider(), use_keychain=False, use_env=True, log=lambda m: None)
    data = json.loads(settings.read_text())
    for legacy in ("provider", "apiBaseUrl", "apiKey", "models"):
        assert legacy not in data
    assert "apiKeyHelper" not in data
    assert "ANTHROPIC_API_KEY" not in data["env"]
    assert data["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-12345678"
    assert data["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert data["permissions"] == {"allow": ["Bash(pytest)"]}

    # Switching back to Keychain removes ANTHROPIC_AUTH_TOKEN so it cannot shadow apiKeyHelper.
    with mock.patch("byop.core.targets.claude.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.claude.kc.ensure_key", return_value=["keychain:x"]):
        target.configure(_provider(), use_keychain=True, log=lambda m: None)
    data2 = json.loads(settings.read_text())
    assert "ANTHROPIC_API_KEY" not in data2["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in data2["env"]
    assert data2["apiKeyHelper"].startswith("security find-internet-password")
    assert data2["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_configure_dry_run_never_leaks_secret_or_touches_keychain(tmp_path):
    settings = tmp_path / "settings.json"
    target = ClaudeTarget(settings_path=settings)
    logs: list[str] = []
    with mock.patch("byop.core.targets.claude.kc.keychain_has") as khas, \
         mock.patch("byop.core.targets.claude.kc.ensure_key") as ek:
        target.configure(_provider(), dry_run=True, use_keychain=False, log=logs.append)
        target.configure(_provider(), dry_run=True, use_keychain=True, log=logs.append)
    assert not settings.exists()
    khas.assert_not_called()
    ek.assert_not_called()
    joined = "\n".join(logs)
    assert "sk-12345678" not in joined
    assert "[REDACTED]" in joined


def test_configure_skip_when_settings_already_match(tmp_path):
    """Idempotent re-run with conflict_action='skip' and matching settings = no-op."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "apiKeyHelper": "security find-internet-password -s https://api.example.com/v1 -a Bearer -w",
        "env": {
            "ANTHROPIC_BASE_URL": "https://api.example.com",
            "BYOP_PROVIDER": "Cosmaxis",
        },
        "model": "hy3",
    }))
    target = ClaudeTarget(settings_path=settings)
    writes = {"count": 0}
    real_write = target._write

    def tracking(data):
        writes["count"] += 1
        return real_write(data)

    with mock.patch("byop.core.targets.claude.kc.ensure_key", return_value=["k"]) as ek, \
         mock.patch.object(target, "_write", tracking):
        target.configure(_provider(), conflict_action="skip", log=lambda m: None)
    assert writes["count"] == 0
    ek.assert_called_once()


def test_current_provider_names_and_export_redaction(tmp_path):
    settings = tmp_path / "settings.json"
    target = ClaudeTarget(settings_path=settings)
    assert target.current_provider_names() == []

    settings.write_text(json.dumps({
        "env": {
            "ANTHROPIC_BASE_URL": "https://api.example.com",
            "ANTHROPIC_AUTH_TOKEN": "sk-secret",
            "BYOP_PROVIDER": "Cosmaxis",
        },
        "model": "hy3",
    }))
    assert target.current_provider_names() == ["Cosmaxis"]
    exported = target.export_config()
    assert "sk-secret" not in json.dumps(exported)
    assert exported["claude"]["providers"]["Cosmaxis"]["authToken"] == "[REDACTED]"
