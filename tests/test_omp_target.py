"""Tests for the Oh My Pi (omp) target."""

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core.config import ModelConfig, ProviderConfig
from byop.core.targets import available_targets
from byop.core.targets.omp import OmpTarget
from byop.core.targets.py import PyTarget


@pytest.fixture(autouse=True)
def clear_omp_environment(monkeypatch):
    for name in ("OMP_PROFILE", "PI_PROFILE", "PI_CONFIG_DIR", "PI_CODING_AGENT_DIR"):
        monkeypatch.delenv(name, raising=False)


def _provider(**over):
    base = {
        "provider_name": "Cosmaxis",
        "api_url": "https://api.example.com/v1/",
        "api_key": "sk-12345678",
        "models": [ModelConfig(name="hy3")],
    }
    base.update(over)
    return ProviderConfig(**base)


def test_omp_default_path(tmp_path, monkeypatch):
    monkeypatch.setattr("byop.core.targets.omp.Path.home", lambda: tmp_path)
    target = OmpTarget()
    assert target.models_path == tmp_path / ".omp" / "agent" / "models.yml"
    assert target.models_path != PyTarget().models_path


@pytest.mark.parametrize(("variables", "expected"), [
    ({"PI_PROFILE": "work"}, ".omp/profiles/work/agent/models.yml"),
    ({"OMP_PROFILE": "chosen", "PI_PROFILE": "legacy"}, ".omp/profiles/chosen/agent/models.yml"),
    ({"OMP_PROFILE": "", "PI_PROFILE": "legacy"}, ".omp/agent/models.yml"),
    ({"PI_CONFIG_DIR": ".custom", "PI_PROFILE": "work"}, ".custom/profiles/work/agent/models.yml"),
    ({"PI_PROFILE": "default"}, ".omp/agent/models.yml"),
])
def test_omp_uses_active_profile_and_config_root(tmp_path, monkeypatch, variables, expected):
    monkeypatch.setattr("byop.core.targets.omp.Path.home", lambda: tmp_path)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    assert OmpTarget().models_path == tmp_path / expected


def test_omp_default_agent_override_does_not_override_named_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("byop.core.targets.omp.Path.home", lambda: tmp_path)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "agent-override"))
    assert OmpTarget().models_path == tmp_path / "agent-override" / "models.yml"
    monkeypatch.setenv("PI_PROFILE", "work")
    assert OmpTarget().models_path == tmp_path / ".omp" / "profiles" / "work" / "agent" / "models.yml"
    assert OmpTarget(profile="default").models_path == tmp_path / "agent-override" / "models.yml"


@pytest.mark.parametrize("filename", ["models.yaml", "models.json"])
def test_omp_reuses_existing_lower_priority_model_file(tmp_path, monkeypatch, filename):
    monkeypatch.setattr("byop.core.targets.omp.Path.home", lambda: tmp_path)
    agent = tmp_path / ".omp" / "agent"
    agent.mkdir(parents=True)
    source = agent / filename
    source.write_text(
        '{"providers":{"Other":{"baseUrl":"https://other.example.com/v1","auth":"none"}}}'
        if filename.endswith(".json") else
        "providers:\n  Other:\n    baseUrl: https://other.example.com/v1\n    auth: none\n"
    )
    target = OmpTarget()
    assert target.models_path == source
    with mock.patch("byop.core.targets.py.kc.ensure_key"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True):
        target.configure(_provider(), log=lambda _: None)
    assert set(target._load()["providers"]) == {"Other", "Cosmaxis"}
    assert not (agent / "models.yml").exists()

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


def test_omp_updates_each_model_limit_without_rewriting_other_providers(tmp_path):
    models = tmp_path / "models.yml"
    untouched = (
        "# user's preface\nproviders:\n  Other:\n    # keep this\n"
        "    baseUrl: https://other.example/v1\n    models: []\n"
    )
    models.write_text(untouched)
    target = OmpTarget(models_path=models)
    provider = _provider(models=[
        ModelConfig(name="small", max_tokens=64000, max_output_tokens=4096),
        ModelConfig(name="large", max_tokens=256000, max_output_tokens=16384),
    ])
    with mock.patch("byop.core.targets.py.kc.ensure_key"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True):
        target.configure(provider, log=lambda _: None)
    text = models.read_text()
    assert text.startswith(untouched)
    configured = target._load()["providers"]["Cosmaxis"]["models"]
    assert [(m["contextWindow"], m["maxTokens"]) for m in configured] == [
        (64000, 4096), (256000, 16384)
    ]
    assert provider.api_key not in text


def test_omp_replaces_one_provider_and_keeps_user_model_fields(tmp_path):
    models = tmp_path / "models.yml"
    models.write_text(
        "providers:\n  Cosmaxis:\n    baseUrl: https://old.example/v1\n"
        "    api: openai-completions\n    apiKey: old-ref\n    models:\n"
        "      - id: small\n        contextWindow: 32000\n        maxTokens: 2048\n"
        "        headers:\n          X-Model: special\n"
        "  Other:\n    # untouched provider comment\n    models: []\n"
    )
    target = OmpTarget(models_path=models)
    with mock.patch("byop.core.targets.py.kc.ensure_key"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True):
        target.configure(_provider(models=[
            ModelConfig(name="small", max_tokens=96000, max_output_tokens=8192),
            ModelConfig(name="large", max_tokens=192000, max_output_tokens=12000),
        ]), log=lambda _: None)
    text = models.read_text()
    assert text.endswith("  Other:\n    # untouched provider comment\n    models: []\n")
    small, large = target._load()["providers"]["Cosmaxis"]["models"]
    assert small["headers"] == {"X-Model": "special"}
    assert (small["contextWindow"], small["maxTokens"]) == (96000, 8192)
    assert (large["contextWindow"], large["maxTokens"]) == (192000, 12000)

def test_omp_quoted_provider_name_retains_metadata_and_honors_skip(tmp_path):
    path = tmp_path / "models.yml"
    path.write_text(
        "providers:\n  'Demo Provider':\n    baseUrl: https://old.example/v1\n"
        "    api: openai-completions\n    apiKey: old-ref\n"
        "    headers:\n      X-Team: keep\n    models:\n"
        "      - id: small\n        contextWindow: 32000\n        maxTokens: 2048\n"
        "        headers:\n          X-Model: special\n"
    )
    target = OmpTarget(path)
    provider = _provider(provider_name="Demo Provider", models=[
        ModelConfig(name="small", max_tokens=96000, max_output_tokens=8192, reasoning_effort="none"),
    ])
    original = path.read_bytes()
    with mock.patch("byop.core.targets.py.kc.ensure_key"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True):
        target.configure(provider, conflict_action="skip", log=lambda _: None)
        assert path.read_bytes() == original
        target.configure(provider, conflict_action="replace", log=lambda _: None)
    block = target._load()["providers"]["Demo Provider"]
    assert block["headers"] == {"X-Team": "keep"}
    assert block["models"][0]["headers"] == {"X-Model": "special"}
    assert block["models"][0]["reasoning"] is False
    assert block["models"][0]["contextWindow"] == 96000

def test_omp_can_reapply_after_reasoning_disabled_with_empty_maps(tmp_path):
    path = tmp_path / "models.yml"
    path.write_text(
        "providers:\n  Cosmaxis:\n    baseUrl: https://api.example.com/v1\n"
        "    api: openai-completions\n    apiKey: CUSTOM_SECRET\n"
        "    headers: {}\n    compat:\n      supportsReasoningEffort: true\n"
        "    models:\n      - id: small\n        reasoning: true\n"
    )
    target = OmpTarget(path)
    provider = _provider(api_key_env="CUSTOM_SECRET", models=[
        ModelConfig(name="small", reasoning_effort="none")
    ])
    target.configure(provider, use_keychain=False, use_env=True, log=lambda _: None)
    target.configure(provider, use_keychain=False, use_env=True, log=lambda _: None)
    block = target._load()["providers"]["Cosmaxis"]
    assert block["headers"] == {}
    assert "compat" not in block
    assert block["models"][0]["reasoning"] is False


def test_omp_preserves_typed_looking_strings_on_reapply(tmp_path):
    path = tmp_path / "models.yml"
    path.write_text(
        "providers:\n  'true':\n    baseUrl: https://api.example.com/v1\n"
        "    api: openai-completions\n    apiKey: CUSTOM_KEY\n"
        "    headers:\n      X-Bool: 'true'\n      X-Null: 'null'\n"
        "      X-Number: '1.0'\n      X-Legacy: 'on'\n      X-Inf: 'Infinity'\n"
        "      X-NaN: 'nan'\n      X-Date: '2026-01-01'\n"
        "    models:\n      - id: '123'\n        name: 'yes'\n"
        "        headers:\n          X-Model: 'off'\n"
    )
    target = OmpTarget(path)
    provider = _provider(
        provider_name="true", api_key_env="CUSTOM_KEY",
        models=[ModelConfig(name="123", display_name="yes")],
    )
    expected_headers = {
        "X-Bool": "true", "X-Null": "null", "X-Number": "1.0", "X-Legacy": "on",
        "X-Inf": "Infinity", "X-NaN": "nan", "X-Date": "2026-01-01",
    }
    for _ in range(2):
        target.configure(provider, use_keychain=False, use_env=True, log=lambda _: None)
        block = target._load()["providers"]["true"]
        assert block["headers"] == expected_headers
        assert block["models"][0]["id"] == "123"
        assert block["models"][0]["name"] == "yes"
        assert block["models"][0]["headers"] == {"X-Model": "off"}


@pytest.mark.parametrize(("target_type", "filename"), [
    (PyTarget, "models.json"),
    (OmpTarget, "models.yml"),
])
def test_pi_and_omp_isolate_mixed_model_token_fields(tmp_path, target_type, filename):
    target = target_type(tmp_path / filename)
    legacy = ModelConfig(name="legacy", reasoning_effort=None, capabilities={"max_tokens_parameter": True})
    modern = ModelConfig(name="modern", reasoning_effort=None, capabilities={"max_tokens_parameter": True})
    provider = _provider(api_key_env="CUSTOM_KEY", models=[legacy, modern])
    target.configure(provider, use_keychain=False, use_env=True, log=lambda _: None)
    previous = target._load()
    block = previous["providers"]["Cosmaxis"]
    block["compat"] = {"maxTokensField": "max_tokens", "supportsStore": False}
    block["models"][1]["compat"]["supportsStore"] = False
    target._write(previous)

    provider.models[1] = ModelConfig(name="modern", reasoning_effort=None, capabilities={"max_tokens_parameter": False})
    for _ in range(2):
        target.configure(provider, use_keychain=False, use_env=True, log=lambda _: None)
        block = target._load()["providers"]["Cosmaxis"]
        models = {model["id"]: model for model in block["models"]}
        assert block["compat"] == {"supportsStore": False}
        assert models["legacy"]["compat"]["maxTokensField"] == "max_tokens"
        assert models["modern"]["compat"] == {"supportsStore": False}




@pytest.mark.parametrize("indent", [1, 2])
def test_omp_adds_provider_after_file_without_final_newline(tmp_path, indent):
    path = tmp_path / "models.yml"
    path.write_text("providers:\n" + " " * indent + "Other:\n" + " " * (indent + 2) + "models: []")
    target = OmpTarget(path)
    with mock.patch("byop.core.targets.py.kc.ensure_key"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True):
        provider = _provider(models=[ModelConfig(name="hy3", max_tokens=128000, max_output_tokens=32000)])
        target.configure(provider, log=lambda _: None)
    providers = target._load()["providers"]
    assert set(providers) == {"Other", "Cosmaxis"}
    assert providers["Cosmaxis"]["models"][0]["contextWindow"] == 128000


def test_omp_preserves_unrelated_legacy_json_data(tmp_path):
    path = tmp_path / "models.yml"
    path.write_text(json.dumps({
        "providers": {"Other": {"models": []}},
        "user_setting": {"keep": True},
    }))
    target = OmpTarget(path)
    with mock.patch("byop.core.targets.py.kc.ensure_key"), \
         mock.patch("byop.core.targets.py.kc.keychain_has", return_value=True):
        target.configure(_provider(), log=lambda _: None)
    data = json.loads(path.read_text())
    assert data["user_setting"] == {"keep": True}
    assert set(data["providers"]) == {"Other", "Cosmaxis"}


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


def test_omp_config_directory_does_not_mean_installed(tmp_path):
    target = OmpTarget(models_path=tmp_path / "agent" / "models.yml")
    target.models_path.parent.mkdir()
    with mock.patch("byop.core.targets.omp.shutil.which", return_value=None):
        assert target.is_installed() is False
    with mock.patch("byop.core.targets.omp.shutil.which", return_value="/usr/local/bin/omp"):
        assert target.is_installed() is True


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
