"""Tests for the Oh My Pi (omp) target."""

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core.config import ModelConfig, ProviderConfig
from byop.core.targets import available_targets
from byop.core.targets.omp import OmpTarget
from byop.core.targets.py import PyTarget


def _provider(**over):
    base = {
        "provider_name": "Cosmaxis",
        "api_url": "https://api.example.com/v1/",
        "api_key": "sk-12345678",
        "models": [ModelConfig(name="hy3")],
    }
    base.update(over)
    return ProviderConfig(**base)


def test_omp_default_path():
    """OmpTarget defaults to ~/.omp/agent/models.yml, NOT py.dev's models.json."""
    target = OmpTarget()
    assert target.models_path == Path.home() / ".omp" / "agent" / "models.yml"
    # Sanity check it is distinct from PyTarget's default.
    assert target.models_path != PyTarget().models_path
def test_omp_profile_selects_profile_scoped_models_path(tmp_path, monkeypatch):
    """A named OMP profile writes under its isolated agent root."""
    monkeypatch.setattr("byop.core.targets.omp.Path.home", lambda: tmp_path)

    target = OmpTarget(profile="cosmaxis")

    assert target.models_path == (
        tmp_path / ".omp" / "profiles" / "cosmaxis" / "agent" / "models.yml"
    )


def test_omp_explicit_models_path_overrides_profile(tmp_path):
    """An explicit path remains the deterministic escape hatch for callers."""
    models = tmp_path / "custom" / "models.yml"

    target = OmpTarget(profile="cosmaxis", models_path=models)

    assert target.models_path == models

def test_omp_profile_uses_explicit_config_root(tmp_path):
    """Callers can target a non-default OMP config root deterministically."""
    target = OmpTarget(profile="cosmaxis", config_root=tmp_path)

    assert target.models_path == (
        tmp_path / "profiles" / "cosmaxis" / "agent" / "models.yml"
    )


def test_omp_profile_configure_does_not_touch_global_models(tmp_path, monkeypatch):
    """Profile onboarding must not modify the global or another profile root."""
    monkeypatch.setattr("byop.core.targets.omp.Path.home", lambda: tmp_path)
    global_models = tmp_path / ".omp" / "agent" / "models.yml"
    other_models = tmp_path / ".omp" / "profiles" / "other" / "agent" / "models.yml"
    global_models.parent.mkdir(parents=True)
    other_models.parent.mkdir(parents=True)
    global_models.write_text("providers:\n  Global: {}\n")
    other_models.write_text("providers:\n  Other: {}\n")

    target = OmpTarget(profile="cosmaxis")
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.py.kc.ensure_key", return_value=["k:x"]):
        target.configure(_provider(), log=lambda m: None)

    assert "Global" in global_models.read_text()
    assert "Other" in other_models.read_text()
    assert "Cosmaxis:" in target.models_path.read_text()


def test_omp_configure_never_writes_literal_secret_with_keychain(tmp_path):
    """The API key stays out of models.yml when keychain lookup succeeds."""
    models = tmp_path / "models.yml"
    target = OmpTarget(models_path=models)
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.py.kc.ensure_key", return_value=["k:x"]):
        target.configure(_provider(), log=lambda m: None)

    text = models.read_text()
    assert _provider().api_key not in text
    assert "!security find-internet-password" in text


def test_omp_is_py_target_subclass():
    """Subclass so the models.json fragment logic stays DRY."""
    assert issubclass(OmpTarget, PyTarget)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS keychain required")
def test_omp_configure_writes_to_omp_path(tmp_path):
    """omp reads ~/.omp/agent/models.yml (NOT models.json)."""
    models = tmp_path / "models.yml"
    target = OmpTarget(models_path=models)
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.py.kc.ensure_key", return_value=["k:x"]):
        target.configure(_provider(), log=lambda m: None)
    text = models.read_text()
    assert "Cosmaxis:" in text
    # apiKey is the keychain shell-out — not the literal key.
    assert "!security find-internet-password" in text


def test_omp_install_via_brew_when_present():
    calls = []

    def _run(cmd, *a, **k):
        calls.append(cmd)

        class R:
            pass
        r = R()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    with mock.patch(
        "byop.core.targets.omp.shutil.which",
        side_effect=lambda n: "/opt/homebrew/bin/brew" if n == "brew" else None,
    ), mock.patch("byop.core.targets.omp._run", _run):
        OmpTarget().install(log=lambda m: None)
    assert any(c[:3] == ["brew", "install", "can1357/tap/omp"] for c in calls)


def test_omp_registered_with_default_registry():
    """OmpTarget must be in ALL_TARGETS after the registry update."""
    targets = available_targets()
    names = {t.__class__.__name__ for t in targets}
    assert "OmpTarget" in names
    assert "ClaudeTarget" in names
    assert "PyTarget" in names
    assert "ZedTarget" in names


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS keychain required")
def test_omp_warns_when_appending_under_suffix(tmp_path):
    """When omp already has Cosmaxis and --conflict append fires,
    the user must be warned the new entry landed under Cosmaxis_2
    (silent duplicates are a UX trap)."""
    models = tmp_path / "models.yml"
    # Write initial as YAML
    models.write_text(
        "providers:\n  Cosmaxis:\n    baseUrl: https://x/v1\n    api: openai-completions\n"
        "    apiKey: !anything\n    authHeader: true\n    models: []\n"
    )
    target = OmpTarget(models_path=models)
    messages = []
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.py.kc.ensure_key", return_value=["k:x"]):
        target.configure(
            _provider(), conflict_action="append",
            log=lambda m: messages.append(m),
        )
    assert any("Cosmaxis_2" in m for m in messages), (
        f"append must name the suffixed provider; got: {messages}"
    )
    # And there must be a visible "warning" line so silent duplicate
    # accumulation doesn't catch the user off guard.
    assert any(
        m.lower().startswith("warning") or "previous" in m.lower()
        for m in messages
    ), f"append must emit a Warning line; got: {messages}"


def test_omp_detect_installed_uses_PATH_or_dir():
    """is_installed is True when ~/.omp/ exists OR `omp` is on PATH."""
    import shutil

    with mock.patch.object(OmpTarget, "__init__", lambda self, models_path=None: None):
        target = OmpTarget.__new__(OmpTarget)
        target.models_path = Path("/tmp/whatever/models.json")
        with mock.patch.object(Path, "exists", return_value=True):
            assert target.is_installed() is True
        with mock.patch.object(Path, "exists", return_value=False), \
             mock.patch.object(shutil, "which", return_value="/usr/local/bin/omp"):
            assert target.is_installed() is True
        with mock.patch.object(Path, "exists", return_value=False), \
             mock.patch.object(shutil, "which", return_value=None):
            assert target.is_installed() is False


def test_omp_dry_run_emits_yaml_without_leaking_secret(tmp_path):
    models = tmp_path / "models.yml"
    target = OmpTarget(models_path=models)
    logs: list[str] = []
    with mock.patch("byop.core.targets.py.kc.keychain_has") as khas, \
         mock.patch("byop.core.targets.py.kc.ensure_key") as ek:
        target.configure(_provider(), dry_run=True, use_keychain=False, log=logs.append)
    assert not models.exists()
    khas.assert_not_called()
    ek.assert_not_called()
    joined = "\n".join(logs)
    assert "models.yml" in joined
    assert "providers:" in joined
    assert "sk-12345678" not in joined
    assert "[REDACTED]" in joined
