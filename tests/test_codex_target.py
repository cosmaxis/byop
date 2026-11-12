"""Tests for the Codex target."""

import json
import subprocess
import tomllib
from pathlib import Path
from unittest import mock

import pytest

from byop.core.config import ModelConfig, ProviderConfig
from byop.core.targets.codex import CodexTarget


@pytest.fixture(autouse=True)
def isolated_codex_catalog(monkeypatch):
    """Use a deterministic bundled catalog without invoking Codex or touching user files."""
    bundled = {"models": [{
        "slug": "built-in", "display_name": "Built-in",
        "model_messages": {"instructions_template": "bundled instructions"},
    }]}
    monkeypatch.setattr("byop.core.targets.codex.shutil.which", lambda name: "/fake/codex")
    monkeypatch.setattr(
        "byop.core.targets.codex.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(bundled), ""),
    )


def _provider(**over):
    base = {
        "provider_name": "HyberOrbit",
        "api_url": "https://api.example.com/v1/",
        "api_key": "sk-12345678",
        "models": [ModelConfig(name="hy3", reasoning_effort="high")],
    }
    base.update(over)
    return ProviderConfig(**base)


def test_codex_default_path():
    assert CodexTarget().config_path == Path.home() / ".codex" / "config.toml"


def test_codex_settings_directory_alone_does_not_imply_installation(tmp_path, monkeypatch):
    monkeypatch.setattr("byop.core.targets.codex._codex_cli", lambda: None)
    with mock.patch.object(Path, "exists", return_value=False):
        assert CodexTarget(tmp_path / "config.toml").is_installed() is False


def test_codex_uses_desktop_bundled_cli_when_not_on_path(tmp_path, monkeypatch):
    binary = tmp_path / "Applications" / "Codex.app" / "Contents" / "Resources" / "codex-cli" / "bin" / "codex"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr("byop.core.targets.codex.Path.home", lambda: tmp_path)
    monkeypatch.setattr("byop.core.targets.codex.shutil.which", lambda _: None)
    target = CodexTarget(tmp_path / "config.toml")
    assert target.is_installed()
    assert target.build_catalog(_provider())["models"][0]["slug"] == "hy3"


def test_codex_build_fragment_uses_responses_and_env_key():
    fragment = CodexTarget().build_fragment(_provider(), use_keychain=False, use_env=True)
    assert fragment["model"] == "hy3"
    block = fragment["model_providers"]["HyberOrbit"]
    assert block["base_url"] == "https://api.example.com/v1"
    assert block["wire_api"] == "responses"
    assert block["env_key"] == "HYBERORBIT_API_KEY"
    assert _provider().api_key not in str(fragment)


def test_codex_build_fragment_uses_command_auth_without_secret():
    fragment = CodexTarget().build_fragment(_provider())
    block = fragment["model_providers"]["HyberOrbit"]
    assert block["auth"]["command"] == "security"
    assert block["auth"]["args"][-1] == "-w"
    assert "env_key" not in block
    assert "sk-12345678" not in str(fragment)


def test_codex_build_fragment_rejects_missing_credentials():
    with pytest.raises(ValueError, match="either keychain storage"):
        CodexTarget().build_fragment(_provider(), use_keychain=False, use_env=False)


@pytest.mark.parametrize("url", [
    "https://user:password@example.com/v1",
    "https://example.com/v1?api_key=secret-query",
    "https://example.com/v1#secret",
])
def test_codex_rejects_credential_bearing_urls_without_writes(tmp_path, url):
    cfg = tmp_path / "config.toml"
    with mock.patch("byop.core.targets.codex.kc.ensure_key") as ensure_key, \
         pytest.raises(ValueError, match="Codex API URL"):
        CodexTarget(cfg).configure(_provider(api_url=url), log=lambda _: None)
    assert not cfg.exists()
    ensure_key.assert_not_called()


def test_codex_configure_merges_and_preserves_other_values(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "old"\n[features]\njs_repl = false\n', encoding="utf-8")
    target = CodexTarget(path)
    with mock.patch("byop.core.targets.codex.kc.ensure_key"):
        target.configure(_provider(), use_keychain=False, use_env=True, log=lambda _: None)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["model"] == "hy3"
    assert data["model_provider"] == "HyberOrbit"
    assert data["features"]["js_repl"] is False


def test_codex_skip_does_not_write(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model_provider = "HyberOrbit"\n', encoding="utf-8")
    target = CodexTarget(path)
    with mock.patch.object(Path, "write_text") as write:
        target.configure(_provider(), use_keychain=False, use_env=True,
                         conflict_action="skip", log=lambda _: None)
    write.assert_not_called()


def test_codex_rejects_append(tmp_path):
    with pytest.raises(ValueError, match="one active model provider"):
        CodexTarget(tmp_path / "config.toml").configure(
            _provider(), use_keychain=False, use_env=True,
            conflict_action="append", log=lambda _: None
        )


def test_codex_current_provider_names(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model_provider = "HyberOrbit"\n', encoding="utf-8")
    assert CodexTarget(path).current_provider_names() == ["HyberOrbit"]


def test_codex_implicit_openai_model_is_an_active_provider(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5"\n')
    target = CodexTarget(path)
    original = path.read_bytes()
    assert target.current_provider_names() == ["openai"]
    with mock.patch("byop.core.targets.codex.kc.ensure_key") as ensure_key:
        target.configure(_provider(), conflict_action="skip", log=lambda _: None)
    assert path.read_bytes() == original
    ensure_key.assert_not_called()


def test_codex_build_fragment_includes_reasoning_and_catalog(tmp_path):
    # Provider with reasoning should expose thinking-effort picker via catalogue.
    target = CodexTarget(tmp_path / "config.toml", catalog_dir=tmp_path / "catalogs")
    frag = target.build_fragment(_provider(), use_keychain=False, use_env=True)
    assert frag["model_reasoning_effort"] == "high"
    assert "model_supports_reasoning_summaries" not in frag
    assert frag["model_catalog_json"].endswith("HyberOrbit.json")
    assert "model_providers" in frag


@pytest.mark.parametrize(("effort", "expected"), [(None, None), ("none", "none")])
def test_codex_single_model_preserves_explicit_reasoning_off(tmp_path, effort, expected):
    path = tmp_path / "config.toml"
    path.write_text('model_reasoning_effort = "high"\n')
    provider = _provider(models=[ModelConfig(
        name="plain", max_tokens=128000, max_output_tokens=32000, reasoning_effort=effort,
    )])
    with mock.patch("byop.core.targets.codex.kc.ensure_key"):
        CodexTarget(path).configure(provider, use_keychain=False, use_env=True, log=lambda _: None)
    assert tomllib.loads(path.read_text()).get("model_reasoning_effort") == expected

    assert tomllib.loads(path.read_text())["model_context_window"] == 128000


def test_codex_single_and_multiple_models_have_distinct_contexts(tmp_path):
    target = CodexTarget(tmp_path / "config.toml")
    single = _provider(models=[
        ModelConfig(name="solo", max_tokens=64000, max_output_tokens=4096, reasoning_effort=None)
    ])
    assert target.build_fragment(single)["model_context_window"] == 64000
    with mock.patch("byop.core.targets.codex.kc.ensure_key"):
        target.configure(single, use_keychain=False, use_env=True, log=lambda _: None)
    assert tomllib.loads(target.config_path.read_text())["model_context_window"] == 64000
    assert "model_catalog_json" not in tomllib.loads(target.config_path.read_text())
    multi = _provider(models=[
        ModelConfig(name="small", max_tokens=64000, max_output_tokens=4096),
        ModelConfig(name="large", max_tokens=256000, max_output_tokens=16384),
    ])
    models = target.build_catalog(multi)["models"]
    assert [(m["context_window"], m["max_context_window"]) for m in models] == [
        (64000, 64000), (256000, 256000)
    ]
    assert all("max_output_tokens" not in model for model in models)
    with mock.patch("byop.core.targets.codex.kc.ensure_key"):
        target.configure(multi, use_keychain=False, use_env=True, log=lambda _: None)
    settings = tomllib.loads(target.config_path.read_text())
    assert "model_context_window" not in settings
    catalog_path = Path(settings["model_catalog_json"])
    assert [m["context_window"] for m in json.loads(catalog_path.read_text())["models"]] == [
        64000, 256000
    ]
    with mock.patch("byop.core.targets.codex.kc.ensure_key"):
        target.configure(single, use_keychain=False, use_env=True, log=lambda _: None)
    settings = tomllib.loads(target.config_path.read_text())
    assert settings["model_context_window"] == 64000
    assert "model_catalog_json" not in settings

def test_codex_build_catalog_has_all_models(tmp_path):
    prov = _provider(models=[
        ModelConfig(name="lite", reasoning_effort="low"),
        ModelConfig(name="pro", reasoning_effort="medium"),
        ModelConfig(name="max", reasoning_effort="high"),
    ])
    target = CodexTarget(tmp_path / "config.toml", catalog_dir=tmp_path / "catalogs")
    cat = target.build_catalog(prov)
    slugs = [m["slug"] for m in cat["models"]]
    assert slugs == ["lite", "pro", "max"]
    for m in cat["models"]:
        assert "supported_reasoning_levels" in m
        assert {lvl["effort"] for lvl in m["supported_reasoning_levels"]} >= {"low", "medium", "high", "xhigh"}


def test_codex_catalog_retains_configured_minimal_and_disabled_effort(tmp_path):
    provider = _provider(models=[
        ModelConfig(name="lite", reasoning_effort="minimal"),
        ModelConfig(name="plain", reasoning_effort="none"),
    ])
    catalog = CodexTarget(tmp_path / "config.toml").build_catalog(provider)
    lite, plain = catalog["models"]
    assert lite["default_reasoning_level"] == "minimal"
    assert "minimal" in {level["effort"] for level in lite["supported_reasoning_levels"]}
    assert plain["default_reasoning_level"] == "none"
    assert {level["effort"] for level in plain["supported_reasoning_levels"]} == {"none"}


def test_codex_configure_writes_catalog_and_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cat_dir = tmp_path / "catalogs"
    target = CodexTarget(cfg, catalog_dir=cat_dir)
    prov = _provider(models=[
        ModelConfig(name="lite", reasoning_effort="low"),
        ModelConfig(name="pro", reasoning_effort="medium"),
    ])
    with mock.patch("byop.core.targets.codex.kc.ensure_key"):
        target.configure(prov, use_keychain=False, use_env=True, log=lambda _: None)
    # Config written
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["model"] == "lite"
    assert data["model_provider"] == "HyberOrbit"
    assert data["model_catalog_json"].endswith("HyberOrbit.json")
    # Catalogue written
    cat_path = cat_dir / "HyberOrbit.json"
    assert cat_path.exists()
    cat = json.loads(cat_path.read_text(encoding="utf-8"))
    assert len(cat["models"]) == 2


def test_codex_configure_deep_merges_preserves_other_providers(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'model = "old"\n[model_providers.Other]\nname = "Other"\nbase_url = "https://other.example.com/v1"\n',
        encoding="utf-8",
    )
    target = CodexTarget(cfg, catalog_dir=tmp_path / "catalogs")
    with mock.patch("byop.core.targets.codex.kc.ensure_key"):
        target.configure(_provider(), use_keychain=False, use_env=True, log=lambda _: None)
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert "Other" in data["model_providers"]
    assert "HyberOrbit" in data["model_providers"]


def test_codex_dry_run_does_not_write(tmp_path):
    cfg = tmp_path / "config.toml"
    cat_dir = tmp_path / "catalogs"
    target = CodexTarget(cfg, catalog_dir=cat_dir)
    with mock.patch.object(Path, "write_text") as wt:
        target.configure(_provider(), dry_run=True, use_keychain=False, use_env=True, log=lambda _: None)
    wt.assert_not_called()
    assert not (cat_dir / "HyberOrbit.json").exists()


def test_codex_preserves_comments_permissions_and_symlink(tmp_path):
    destination = tmp_path / "dotfiles.toml"
    destination.write_text('# keep this comment\n[features]\njs_repl = false\n')
    destination.chmod(0o600)
    link = tmp_path / "config.toml"
    link.symlink_to(destination)
    with mock.patch("byop.core.targets.codex.kc.ensure_key"):
        CodexTarget(link).configure(_provider(), use_keychain=False, use_env=True, log=lambda _: None)
    assert link.is_symlink()
    assert "# keep this comment" in destination.read_text()
    assert destination.stat().st_mode & 0o777 == 0o600
    assert tomllib.loads(destination.read_text())["features"]["js_repl"] is False


def test_codex_switches_auth_without_leaking_existing_secrets(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'model_provider = "HyberOrbit"\n'
        '[model_providers.HyberOrbit]\n'
        'base_url = "https://user:password@example.com/v1?token=secret-query"\n'
        'experimental_bearer_token = "secret-token"\n'
        'http_headers = { Authorization = "secret-header" }\n'
    )
    target = CodexTarget(cfg)
    exported = json.dumps(target.export_config())
    for secret in ("password", "secret-query", "secret-token", "secret-header"):
        assert secret not in exported

    with mock.patch("byop.core.targets.codex.kc.ensure_key"):
        target.configure(_provider(), use_keychain=True, log=lambda _: None)
        target.configure(_provider(), use_keychain=False, use_env=True, log=lambda _: None)
    data = tomllib.loads(cfg.read_text())
    block = data["model_providers"]["HyberOrbit"]
    assert block["env_key"] == "HYBERORBIT_API_KEY"
    assert "auth" not in block and "experimental_bearer_token" not in block
    assert block["http_headers"]["Authorization"] == "secret-header"


def test_codex_skip_preserves_active_provider_and_credentials(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('model_provider = "Other"\nmodel = "old"\n')
    original = cfg.read_bytes()
    with mock.patch("byop.core.targets.codex.kc.ensure_key") as ensure_key:
        CodexTarget(cfg).configure(_provider(), conflict_action="skip", log=lambda _: None)
    assert cfg.read_bytes() == original
    ensure_key.assert_not_called()


def test_codex_reverts_catalog_when_config_write_fails(tmp_path):
    cfg = tmp_path / "config.toml"
    target = CodexTarget(cfg)
    catalog = target.catalog_dir / "HyberOrbit.json"
    catalog.parent.mkdir()
    catalog.write_text("previous catalog")
    with mock.patch("byop.core.targets.codex.kc.ensure_key"), \
         mock.patch.object(target, "_write", side_effect=OSError("disk full")), \
         pytest.raises(OSError, match="disk full"):
        target.configure(_provider(), use_keychain=False, use_env=True, log=lambda _: None)
    assert catalog.read_text() == "previous catalog"
    assert not cfg.exists()


def test_codex_switch_to_plain_model_clears_managed_reasoning(tmp_path):
    cfg = tmp_path / "config.toml"
    target = CodexTarget(cfg)
    plain = _provider(models=[ModelConfig(name="plain", reasoning_effort=None)])
    with mock.patch("byop.core.targets.codex.kc.ensure_key"):
        target.configure(_provider(), use_keychain=False, use_env=True, log=lambda _: None)
        target.configure(plain, use_keychain=False, use_env=True, log=lambda _: None)
    data = tomllib.loads(cfg.read_text())
    assert data["model"] == "plain"
    assert "model_reasoning_effort" not in data
    assert "model_catalog_json" not in data
