"""Scoped OMP models, native settings, and MCP configuration."""

import json
from pathlib import Path
from unittest import mock

import pytest

from byop import cli
from byop.core.config import ModelConfig, ProviderConfig
from byop.core.targets.omp import OmpTarget


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path / "byop"))
    for name in ("OMP_PROFILE", "PI_PROFILE", "PI_CONFIG_DIR", "PI_CODING_AGENT_DIR"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _source(home):
    root = home / ".omp"
    global_agent = root / "agent"
    global_agent.mkdir(parents=True)
    (global_agent / "config.yml").write_text("theme:\n  dark: true\neditor:\n  tabSize: 2\n")
    (global_agent / "mcp.json").write_text(json.dumps({
        "mcpServers": {"shared": {"command": "echo", "args": ["never-run"], "env": {"TOKEN": "secret-mcp-value"}}},
        "disabledServers": ["global-only"],
    }))
    return root


def _provider():
    return ProviderConfig("Demo", "https://example.test/v1", "not-a-real-secret", [ModelConfig("model")], api_key_env="BYOP_TEST_KEY")


def test_scoped_sync_preserves_destination_and_unselected(home):
    root = _source(home)
    selected = root / "profiles" / "one" / "agent"
    other = root / "profiles" / "two" / "agent"
    selected.mkdir(parents=True)
    other.mkdir(parents=True)
    (selected / "config.yml").write_text("# local\ntheme:\n  dark: false\n  localOption: keep\n")
    (selected / "mcp.json").write_text(json.dumps({
        "mcpServers": {"shared": {"command": "old"}, "local": {"command": "own"}},
        "enabledServers": ["local"],
    }))
    (other / "mcp.json").write_text('{"mcpServers":{"other":{"command":"own"}}}')
    before_other = (other / "mcp.json").read_bytes()
    before_global = (root / "agent" / "mcp.json").read_bytes()
    target = OmpTarget(profile="one", config_root=root)
    logs = []
    with mock.patch("byop.core.targets.py.kc.ensure_key"):
        target.configure(_provider(), use_keychain=False, log=logs.append)
    settings = (selected / "config.yml").read_text()
    assert settings.startswith("# local\ntheme:\n  dark: true\n")
    assert "editor:\n  tabSize: 2" in settings
    assert "  localOption: keep\n" in settings
    mcp = json.loads((selected / "mcp.json").read_text())
    assert mcp["mcpServers"]["shared"] == {"command": "echo", "args": ["never-run"], "env": {"TOKEN": "secret-mcp-value"}}
    assert mcp["mcpServers"]["local"] == {"command": "own"}
    assert mcp["disabledServers"] == ["global-only"]
    assert mcp["enabledServers"] == ["local"]
    assert (selected / "mcp.json").stat().st_mode & 0o777 == 0o600
    assert any("/mcp reload" in line for line in logs)
    assert "secret-mcp-value" not in "\n".join(logs)
    assert (other / "mcp.json").read_bytes() == before_other
    assert (root / "agent" / "mcp.json").read_bytes() == before_global
    prior = ((selected / "config.yml").read_bytes(), (selected / "mcp.json").read_bytes())
    logs.clear()
    with mock.patch("byop.core.targets.py.kc.ensure_key"):
        target.configure(_provider(), use_keychain=False, log=logs.append)
    assert prior == ((selected / "config.yml").read_bytes(), (selected / "mcp.json").read_bytes())
    assert not any("/mcp reload" in line for line in logs)
    assert target.sync_state() == ([], [])


def test_dry_run_and_invalid_mcp_do_not_write(home):
    root = _source(home)
    target = OmpTarget(profile="one", config_root=root)
    logs = []
    target.configure(_provider(), dry_run=True, use_keychain=False, log=logs.append)
    assert not target.agent_dir.exists()
    assert str(target.mcp_path) in "\n".join(logs)
    target.agent_dir.mkdir(parents=True)
    target.mcp_path.write_text("not json")
    with pytest.raises(ValueError):
        with mock.patch("byop.core.targets.py.kc.ensure_key"):
            target.configure(_provider(), use_keychain=False)
    assert not target.models_path.exists()


def test_cli_multi_scope_export_and_diagnostics(home, monkeypatch, capsys):
    root = _source(home)
    for name in ("one", "two", "untouched"):
        (root / "profiles" / name / "agent").mkdir(parents=True)
    monkeypatch.setattr(OmpTarget, "is_installed", lambda self: True)
    monkeypatch.setattr("byop.core.targets.py.kc.ensure_key", lambda **kwargs: None)
    options = ["--target", "omp", "--target", "omp", "--profile", "one", "--profile", "two"]
    base = ["--provider", "Demo", "--api-url", "https://example.test/v1", "--api-key", "not-a-real-secret", "--model", "model", "--no-keychain", "--skip-install"]
    assert cli.main(["apply", *options, *base, "--dry-run"]) == 0
    dry_output = capsys.readouterr().out
    paths = [f"~/.omp/profiles/{name}/agent/models.yml" for name in ("one", "two")]
    assert all(path in dry_output for path in paths)
    assert not (root / "profiles" / "one" / "agent" / "mcp.json").exists()
    assert not (home / "byop").exists()
    assert cli.main(["apply", *options, *base]) == 0
    output = capsys.readouterr().out
    assert all(path in output for path in paths)
    assert "not-a-real-secret" not in output
    assert "secret-mcp-value" not in output
    assert not (root / "profiles" / "untouched" / "agent" / "mcp.json").exists()
    from byop.core import targets as registry
    available = registry.available_targets
    monkeypatch.setattr(registry, "available_targets", lambda **kwargs: [
        target for target in available(**kwargs) if target.name == "omp"
    ])
    assert cli.main(["status", "--profile", "one", "--profile", "two"]) == 0
    status = capsys.readouterr().out
    assert all(path in status for path in paths)
    assert cli.main(["doctor", "--profile", "one", "--profile", "two"]) == 0
    assert paths[1] in capsys.readouterr().out
    assert cli.main(["export-config", *options]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert set(exported["omp_profiles"]) == {"one", "two"}
    assert "not-a-real-secret" not in json.dumps(exported)
    assert cli.main(["export-config", "--target", "omp", "--all-profiles"]) == 0
    assert set(json.loads(capsys.readouterr().out)["omp_profiles"]) == {"default", "one", "two", "untouched"}
    (root / "profiles" / "one" / "agent" / "mcp.json").unlink()
    assert cli.main(["status", "--profile", "one"]) == 0
    status = capsys.readouterr().out
    assert paths[0] in status
    assert "secret-mcp-value" not in status
    assert cli.main(["doctor", "--profile", "one"]) == 1
    doctor = capsys.readouterr().out
    assert paths[0] in doctor
    assert "secret-mcp-value" not in doctor
    mcp_path = root / "profiles" / "one" / "agent" / "mcp.json"
    mcp_path.write_text("invalid JSON")
    assert cli.main(["doctor", "--profile", "one"]) == 1
    assert str(mcp_path) in capsys.readouterr().out


def test_all_profiles_includes_global_despite_agent_override(home, monkeypatch):
    from byop.core.targets import available_targets

    root = _source(home)
    (root / "profiles" / "one").mkdir(parents=True)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(home / "other-agent"))
    scope = cli._omp_target_kwargs(None, True, None)
    targets = [target for target in available_targets(**scope) if target.name == "omp"]
    assert [target.models_path for target in targets] == [
        root / "agent" / "models.yml",
        root / "profiles" / "one" / "agent" / "models.yml",
    ]


def test_invalid_scope_rejected_before_profile_write(home):
    assert cli.main(["apply", "--profile", "one", "--all-profiles", "--provider", "Demo"]) == 2
    assert cli.main(["apply", "--profile", "../escape", "--provider", "Demo"]) == 2
    assert cli.main(["apply", "--profile", " ", "--provider", "Demo"]) == 2
    assert cli.main(["apply", "--profile", "one", "--profile", "two", "--omp-models-path", str(home / "custom"), "--provider", "Demo"]) == 2
    assert not (home / "byop").exists()


def test_explicit_model_path_does_not_sync_inherited_or_explicit_profile(home, monkeypatch):
    root = _source(home)
    monkeypatch.setenv("OMP_PROFILE", "one")
    monkeypatch.setattr("byop.core.targets.py.kc.ensure_key", lambda **kwargs: None)
    custom = home / "custom" / "models.yml"
    assert cli.main([
        "apply", "--target", "omp", "--profile", "one", "--omp-models-path", str(custom),
        "--provider", "Demo", "--api-url", "https://example.test/v1",
        "--api-key", "not-a-real-secret", "--model", "model", "--no-keychain", "--skip-install",
    ]) == 0
    assert custom.exists()
    assert not (root / "profiles").exists()
    assert not (custom.parent / "config.yml").exists()
    assert not (custom.parent / "mcp.json").exists()
