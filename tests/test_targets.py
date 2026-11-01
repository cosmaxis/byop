"""Tests for the multi-target abstraction: Zed, py.dev, and selection."""

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core.config import ModelConfig, ProviderConfig
from byop.core.targets import available_targets, detect_installed
from byop.core.targets.py import PyTarget
from byop.core.targets.zed import ZedTarget


def _provider(**over):
    base = {  # noqa: C408 - deliberate dict() to allow .update(over)
        "provider_name": "ExampleProvider",
        "api_url": "https://api.example.com/v1/",
        "api_key": "sk-example12345678",
        "models": [ModelConfig(name="hy3", max_tokens=250000,
                               max_output_tokens=32000,
                               reasoning_effort="medium")],
    }
    base.update(over)
    return ProviderConfig(**base)


def test_registry_returns_instances():
    targets = available_targets()
    names = {t.name for t in targets}
    assert "zed" in names
    assert "py" in names


def test_detect_installed_filters():
    zed = ZedTarget()
    py = PyTarget()
    with mock.patch.object(zed, "is_installed", return_value=True), \
         mock.patch.object(py, "is_installed", return_value=False):
        found = detect_installed([zed, py])
    assert [t.name for t in found] == ["zed"]


# ----------------------------------------------------------------------
# Zed target
# ----------------------------------------------------------------------
def test_zed_build_fragment():
    frag = ZedTarget().build_fragment(_provider())
    oc = frag["language_models"]["openai_compatible"]["ExampleProvider"]
    assert oc["api_url"] == "https://api.example.com/v1"
    assert frag["agent"]["inline_assistant_model"]["model"] == "hy3"


def test_zed_configure_writes_and_merges(tmp_path):
    settings = tmp_path / "settings.json"
    sett = __import__("byop.core.settings", fromlist=["settings"])
    sett.write_path(settings, {"vim_mode": True})
    target = ZedTarget(settings_path=settings)
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.zed.kc.ensure_key",
                    return_value=["k:x"]) as ek:
        target.configure(_provider(), log=lambda m: None)
    data = sett.load_path(settings)
    assert data["vim_mode"] is True
    assert "ExampleProvider" in data["language_models"]["openai_compatible"]
    ek.assert_called_once()
    assert ek.call_args.kwargs["server"] == "https://api.example.com/v1"


def test_zed_skip_when_same_provider_and_url(tmp_path):
    """Re-run with same name + same api_url + conflict_action='skip' must not write."""
    settings = tmp_path / "settings.json"
    sett = __import__("byop.core.settings", fromlist=["settings"])
    # Pre-populate as if byop had already configured this provider.
    provider = _provider()  # ExampleProvider / https://api.example.com/v1
    target = ZedTarget(settings_path=settings)
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.zed.kc.ensure_key",
                    return_value=["k:x"]):
        target.configure(provider, log=lambda m: None)
    first = settings.read_text()

    # Second invocation with skip; track sett.write_path calls.
    writes = {"count": 0}
    real_write_path = sett.write_path

    def _tracking(path, data):
        writes["count"] += 1
        return real_write_path(path, data)

    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.zed.sett.write_path", _tracking), \
         mock.patch("byop.core.targets.zed.kc.ensure_key",
                    return_value=["k:x"]) as ek:
        target.configure(provider, conflict_action="skip", log=lambda m: None)
    assert writes["count"] == 0, "skip on idempotent re-run must not write settings"
    ek.assert_called_once()  # but keychain is still ensured
    assert settings.read_text() == first  # file contents unchanged


def test_zed_no_skip_when_url_differs(tmp_path):
    """Same name but different api_url must NOT skip — that's a real conflict."""
    settings = tmp_path / "settings.json"
    sett = __import__("byop.core.settings", fromlist=["settings"])
    # Write a stale entry with a different api_url.
    sett.write_path(settings, {
        "language_models": {
            "openai_compatible": {
                "ExampleProvider": {"api_url": "https://other.example.com/v1",
                                    "available_models": []}
            }
        }
    })
    target = ZedTarget(settings_path=settings)
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.zed.kc.ensure_key",
                    return_value=["k:x"]):
        target.configure(_provider(), conflict_action="skip", log=lambda m: None)
    data = sett.load_path(settings)
    # api_url was overwritten because the URLs differ.
    assert data["language_models"]["openai_compatible"]["ExampleProvider"][
        "api_url"] == "https://api.example.com/v1"



def test_zed_dry_run_does_not_write(tmp_path):
    settings = tmp_path / "settings.json"
    target = ZedTarget(settings_path=settings)
    with mock.patch.object(target, "install"):
        target.configure(_provider(), dry_run=True, log=lambda m: None)
    assert not settings.exists()


# ----------------------------------------------------------------------
# py.dev target
# ----------------------------------------------------------------------
def test_py_is_installed_via_cli():
    with mock.patch("byop.core.targets.py.shutil.which", return_value="/opt/homebrew/bin/pi"):
        assert PyTarget().is_installed() is True
    with mock.patch("byop.core.targets.py.shutil.which", return_value=None), \
         mock.patch.object(Path, "exists", return_value=False):
        assert PyTarget().is_installed() is False


def test_py_build_fragment_shape():
    provider = _provider(models=[
        ModelConfig(name="hy3", max_tokens=250000, max_output_tokens=32000,
                    reasoning_effort="medium",
                    capabilities={"max_tokens_parameter": True})
    ])
    frag = PyTarget().build_fragment(provider)
    prov = frag["providers"]["ExampleProvider"]
    assert prov["baseUrl"] == "https://api.example.com/v1"
    assert prov["api"] == "openai-completions"
    assert prov["authHeader"] is True
    model = prov["models"][0]
    assert model["id"] == "hy3"
    assert model["reasoning"] is True
    # reasoning_effort 'medium' maps to high in thinkingLevelMap
    assert prov["compat"]["supportsReasoningEffort"] is True
    assert prov["compat"]["maxTokensField"] == "max_tokens"


def test_py_api_key_ref_uses_keychain_command_when_present():
    provider = _provider()
    with mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True):
        ref = PyTarget()._api_key_ref(provider)
    assert ref.startswith("!security find-internet-password")
    assert provider.keychain_server() in ref


def test_py_api_key_ref_falls_back_to_literal_without_keychain():
    provider = _provider()
    with mock.patch("byop.core.targets.py.kc.keychain_has", return_value=False):
        ref = PyTarget()._api_key_ref(provider)
    assert ref == provider.api_key


def test_py_build_fragment_uses_keychain_command():
    provider = _provider()
    with mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True):
        frag = PyTarget().build_fragment(provider)
    assert frag["providers"]["ExampleProvider"]["apiKey"].startswith("!security")


def test_py_configure_writes_models_json(tmp_path):
    models = tmp_path / "models.json"
    target = PyTarget(models_path=models)
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.py.kc.ensure_key", return_value=["k"]):
        target.configure(_provider(), log=lambda m: None)
    data = json.loads(models.read_text())
    assert "ExampleProvider" in data["providers"]
    assert data["providers"]["ExampleProvider"]["apiKey"].startswith("!security")


def test_py_configure_merges_existing_providers(tmp_path):
    models = tmp_path / "models.json"
    models.write_text(json.dumps({
        "providers": {"Other": {"baseUrl": "x", "api": "openai-completions"}}
    }))
    target = PyTarget(models_path=models)
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.py.kc.ensure_key", return_value=["k"]):
        target.configure(_provider(), log=lambda m: None)
    data = json.loads(models.read_text())
    assert "ExampleProvider" in data["providers"]
    assert "Other" in data["providers"]


def test_py_dry_run_does_not_write_or_leak_secret(tmp_path):
    models = tmp_path / "models.json"
    target = PyTarget(models_path=models)
    logs: list[str] = []
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.py.kc.keychain_has") as khas, \
         mock.patch("byop.core.targets.py.kc.ensure_key") as ek:
        target.configure(_provider(), dry_run=True, use_keychain=False, log=logs.append)
        target.configure(_provider(), dry_run=True, use_keychain=True, log=logs.append)
    assert not models.exists()
    khas.assert_not_called()
    ek.assert_not_called()
    joined = "\n".join(logs)
    assert "sk-example12345678" not in joined
    assert "[REDACTED]" in joined
    assert "!security find-internet-password" in joined

def test_py_append_uses_numeric_suffix(tmp_path):
    """On collision, append under ProviderName_2 (skipping already-taken suffixes)."""
    import json as _json
    models = tmp_path / "models.json"
    models.write_text(_json.dumps({
        "providers": {"ExampleProvider": {"baseUrl": "x", "api": "openai-completions"}}
    }))
    target = PyTarget(models_path=models)
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.py.kc.ensure_key", return_value=["k"]):
        target.configure(_provider(), conflict_action="append", log=lambda m: None)
    data = _json.loads(models.read_text())
    assert "ExampleProvider" in data["providers"]
    assert "ExampleProvider_2" in data["providers"]
    # The appended entry has the freshly configured api_url, not the stale one.
    assert data["providers"]["ExampleProvider_2"]["baseUrl"] == \
        "https://api.example.com/v1"


def test_py_append_picks_next_available_suffix(tmp_path):
    """If _2 is also taken, jump to _3."""
    import json as _json
    models = tmp_path / "models.json"
    models.write_text(_json.dumps({
        "providers": {
            "ExampleProvider": {"baseUrl": "x"},
            "ExampleProvider_2": {"baseUrl": "x"},
        }
    }))
    target = PyTarget(models_path=models)
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.py.kc.ensure_key", return_value=["k"]):
        target.configure(_provider(), conflict_action="append", log=lambda m: None)
    data = _json.loads(models.read_text())
    assert "ExampleProvider_3" in data["providers"]


def test_py_skip_when_provider_exists(tmp_path):
    """conflict_action='skip' must not rewrite an existing provider entry."""
    import json as _json
    models = tmp_path / "models.json"
    sentinel = {"baseUrl": "x", "api": "openai-completions"}
    models.write_text(_json.dumps({"providers": {"ExampleProvider": sentinel}}))
    target = PyTarget(models_path=models)
    writes = {"count": 0}
    real_write = target._write

    def tracking(data):
        writes["count"] += 1
        return real_write(data)

    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.py.kc.ensure_key", return_value=["k"]), \
         mock.patch.object(target, "_write", tracking):
        target.configure(_provider(), conflict_action="skip", log=lambda m: None)
    assert writes["count"] == 0
    data = _json.loads(models.read_text())
    # Original block untouched.
    assert data["providers"]["ExampleProvider"] == sentinel
