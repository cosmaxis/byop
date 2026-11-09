"""Tests for the OpenCode target."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core.config import ModelConfig, ProviderConfig
from byop.core.targets.opencode import DEFAULT_CONFIG_PATH, OpencodeTarget


def _provider(**over):
    base = {
        "provider_name": "Cosmaxis",
        "api_url": "https://api.example.com/v1/",
        "api_key": "sk-12345678",
        "models": [
            ModelConfig(
                name="hy3",
                display_name="Cosmaxis 3",
                max_tokens=250000,
                max_output_tokens=32000,
                reasoning_effort="medium",
                capabilities={"images": True, "interleaved_reasoning": True},
            )
        ],
    }
    base.update(over)
    return ProviderConfig(**base)


def test_opencode_default_path():
    target = OpencodeTarget()
    assert target.config_path == Path.home() / ".config" / "opencode" / "opencode.json"


def test_opencode_build_fragment_uses_env_ref_with_keychain():
    frag = OpencodeTarget().build_fragment(_provider(), use_keychain=True)
    block = frag["provider"]["Cosmaxis"]
    assert block["npm"] == "@ai-sdk/openai-compatible"
    assert block["options"]["baseURL"] == "https://api.example.com/v1"
    assert block["options"]["apiKey"] == "{env:COSMAXIS_API_KEY}"
    model = block["models"]["hy3"]
    assert model["name"] == "Cosmaxis 3"
    assert model["reasoning"] is True
    assert model["interleaved"] == {"field": "reasoning_content"}
    assert model["limit"] == {"context": 250000, "output": 32000}


def test_opencode_keeps_different_limits_per_model():
    provider = _provider(models=[
        ModelConfig(name="small", max_tokens=64000, max_output_tokens=4096),
        ModelConfig(name="large", max_tokens=256000, max_output_tokens=16384),
    ])
    models = OpencodeTarget().build_fragment(provider, use_keychain=True)["provider"]["Cosmaxis"]["models"]
    assert models["small"]["limit"] == {"context": 64000, "output": 4096}
    assert models["large"]["limit"] == {"context": 256000, "output": 16384}


def test_opencode_updates_model_limits_without_losing_unmanaged_metadata(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text(json.dumps({"provider": {"Cosmaxis": {
        "options": {"headers": {"X-Team": "keep"}},
        "models": {
            "pro": {"cost": {"input": 2, "output": 3}, "temperature": False, "interleaved": {"field": "reasoning_content"}},
            "removed": {"name": "Removed"},
        },
    }}}))
    provider = _provider(models=[ModelConfig(
        name="pro", max_tokens=500000, max_output_tokens=16000, reasoning_effort="none",
        capabilities={"tools": False, "interleaved_reasoning": False},
    )])
    OpencodeTarget(cfg).configure(provider, use_keychain=False, use_env=False, log=lambda _: None)
    block = json.loads(cfg.read_text())["provider"]["Cosmaxis"]
    assert block["options"]["headers"] == {"X-Team": "keep"}
    assert set(block["models"]) == {"pro"}
    model = block["models"]["pro"]
    assert model["cost"] == {"input": 2, "output": 3}
    assert model["limit"] == {"context": 500000, "output": 16000}
    assert model["temperature"] is False
    assert model["tool_call"] is False
    assert model["reasoning"] is False and model["interleaved"] is False


def test_opencode_falls_back_to_literal_key_only_when_no_keychain_and_no_env():
    target = OpencodeTarget()
    with mock.patch("byop.core.targets.opencode.kc.keychain_has", return_value=False):
        frag = target.build_fragment(_provider(), use_env=False)
    assert frag["provider"]["Cosmaxis"]["options"]["apiKey"] == "sk-12345678"


def test_opencode_dry_run_redacts_secret_and_skips_keychain(tmp_path):
    cfg = tmp_path / "opencode.json"
    target = OpencodeTarget(config_path=cfg)
    logs: list[str] = []
    with mock.patch("byop.core.targets.opencode.kc.keychain_has") as khas, \
         mock.patch("byop.core.targets.opencode.kc.ensure_key") as ek:
        target.configure(
            _provider(), dry_run=True, use_keychain=False, use_env=False, log=logs.append
        )
    assert not cfg.exists()
    khas.assert_not_called()
    ek.assert_not_called()
    joined = "\n".join(logs)
    assert "sk-12345678" not in joined
    assert "[REDACTED]" in joined


def test_opencode_loads_jsonc_with_comments_and_trailing_commas(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text(
        '// user comment\n{\n  "provider": {\n    "Existing": {"name": "E"},\n  },\n}\n',
        encoding="utf-8",
    )
    target = OpencodeTarget(config_path=cfg)
    assert target.current_provider_names() == ["Existing"]
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS keychain required")
def test_opencode_configure_writes_merges_preserves_other_providers(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps(
            {
                "provider": {"OtherOne": {"name": "x", "npm": "y"}},
                "permission": {"bash": "ask"},
            }
        ),
        encoding="utf-8",
    )
    target = OpencodeTarget(config_path=cfg)
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.opencode.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.opencode.kc.ensure_key", return_value=["k:x"]) as ek:
        target.configure(_provider(), log=lambda m: None)
    ek.assert_called_once()
    assert ek.call_args.kwargs["keychain_env"] is True
    data = json.loads(cfg.read_text())
    assert data["provider"]["Cosmaxis"]["options"]["apiKey"] == "{env:COSMAXIS_API_KEY}"
    assert data["provider"]["OtherOne"]["name"] == "x"
    assert data["permission"]["bash"] == "ask"

@pytest.mark.skipif(sys.platform != "darwin", reason="macOS keychain required")
def test_opencode_skip_when_existing_provider(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text(
        json.dumps({"provider": {"Cosmaxis": {"name": "old"}}}), encoding="utf-8"
    )
    target = OpencodeTarget(config_path=cfg)
    writes = {"count": 0}
    real = cfg.write_text

    def _track(*a, **k):
        writes["count"] += 1
        return real(*a, **k)

    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.opencode.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.opencode.kc.ensure_key", return_value=["k:x"]), \
         mock.patch.object(Path, "write_text", _track):
        target.configure(_provider(), conflict_action="skip", log=lambda m: None)
    assert writes["count"] == 0
    data = json.loads(cfg.read_text())
    # Untouched on skip.
    assert data["provider"]["Cosmaxis"]["name"] == "old"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS keychain required")
def test_opencode_append_when_existing_provider(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text(
        json.dumps({"provider": {"Cosmaxis": {"name": "old"}}}), encoding="utf-8"
    )
    target = OpencodeTarget(config_path=cfg)
    with mock.patch.object(target, "install"), \
         mock.patch("byop.core.targets.opencode.kc.keychain_has", return_value=True), \
         mock.patch("byop.core.targets.opencode.kc.ensure_key", return_value=["k:x"]):
        target.configure(_provider(), conflict_action="append", log=lambda m: None)
    data = json.loads(cfg.read_text())
    assert "Cosmaxis" in data["provider"]
    assert "Cosmaxis_2" in data["provider"]


def test_opencode_current_provider_names(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text(
        json.dumps({"provider": {"Cosmaxis": {}, "OtherOne": {}}}),
        encoding="utf-8",
    )
    target = OpencodeTarget(config_path=cfg)
    assert sorted(target.current_provider_names()) == ["Cosmaxis", "OtherOne"]


def test_opencode_registered_in_default_registry():
    from byop.core.targets import available_targets

    names = {t.name for t in available_targets()}
    assert "opencode" in names
    assert DEFAULT_CONFIG_PATH.name == "opencode.json"


@pytest.mark.parametrize(("installed", "mode"), [
    ("cli", "interactive"), ("desktop", "interactive"),
    ("cli", "scripted"), ("desktop", "scripted"),
    ("cli", "skip"), ("desktop", "skip"),
    ("none", "interactive"), ("both", "interactive"),
])
def test_setup_installs_only_missing_opencode_companion(tmp_path, monkeypatch, installed, mode):
    from byop import cli
    from byop.core import keychain as kc
    from byop.core import targets

    app = tmp_path / "OpenCode.app"
    if installed in {"desktop", "both"}:
        app.mkdir()
    state = {"cli": installed in {"cli", "both"}}
    commands = []
    prompts = []
    hints = []
    target = OpencodeTarget(tmp_path / "opencode.json")
    monkeypatch.setattr("byop.core.targets.opencode.OPENCODE_APP", app)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path / "byop"))
    monkeypatch.setattr(targets, "available_targets", lambda **kwargs: [target])
    monkeypatch.setattr(kc, "keychain_set", lambda *args, **kwargs: None)
    monkeypatch.setattr(kc, "ensure_key", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/opt/homebrew/bin/brew" if name == "brew" else (
            "/opt/homebrew/bin/opencode" if name == "opencode" and state["cli"] else None
        ),
    )

    def install(command):
        commands.append(command)
        if command == ["brew", "install", "opencode"]:
            state["cli"] = True
        elif command == ["brew", "install", "--cask", "opencode-desktop"]:
            app.mkdir()
        else:
            raise AssertionError(f"Unexpected installation: {command}")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("byop.core.targets.opencode._run", install)
    monkeypatch.setattr(
        cli.prompt, "select_many",
        lambda title, labels, **kwargs: hints.extend(kwargs["hints"]) or [0],
    )
    monkeypatch.setattr(
        cli.prompt, "confirm",
        lambda question, default=False: prompts.append(question) or True,
    )
    argv = [
        "apply", "--provider", "Cosmaxis", "--api-url", "https://api.example.com/v1",
        "--api-key", "sk-12345678", "--model", "hy3",
    ]
    if mode == "skip":
        argv.append("--skip-install")
    args = cli.build_parser().parse_args(argv)
    assert cli._run_apply(args, interactive_session=mode == "interactive") == 0

    expected = {
        "cli": [["brew", "install", "--cask", "opencode-desktop"]],
        "desktop": [["brew", "install", "opencode"]],
        "none": [["brew", "install", "opencode"], ["brew", "install", "--cask", "opencode-desktop"]],
        "both": [],
    }[installed]
    assert commands == ([] if mode == "skip" else expected)
    assert target.is_installed() is (mode != "skip")
    assert json.loads(target.config_path.read_text())["provider"]["Cosmaxis"]["models"]["hy3"]
    hint = "not installed" if installed == "none" else "installed" if installed == "both" else "partially installed"
    assert hints == ([hint] if mode == "interactive" else [])
    assert len(prompts) == (1 if mode == "interactive" and installed != "both" else 0)


def test_status_and_doctor_report_partial_opencode_installation(tmp_path, monkeypatch):
    from byop import cli
    from byop.core import profiles, targets

    target = OpencodeTarget(tmp_path / "opencode.json")
    target.config_path.write_text(json.dumps({"provider": {"Cosmaxis": {}}}))
    monkeypatch.setattr("byop.core.targets.opencode.OPENCODE_APP", tmp_path / "OpenCode.app")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path / "byop"))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/opencode" if name == "opencode" else None)
    monkeypatch.setattr(targets, "available_targets", lambda **kwargs: [target])
    profile = profiles.profile_from_provider(_provider(), name="Cosmaxis")
    profiles.save_profile(profile)
    profiles.set_active_profile_name("Cosmaxis")
    messages = []
    monkeypatch.setattr(
        cli.prompt, "step",
        lambda name, detail, status: messages.append((name, detail, status)),
    )

    assert cli._run_status() == 0
    assert any("partially installed" in detail and status == "warn" for _, detail, status in messages)
    messages.clear()
    assert cli._run_doctor() == 1
    assert any("partially installed" in detail and status == "warn" for _, detail, status in messages)


def test_opencode_keychain_profiles_remain_isolated(tmp_path, monkeypatch):
    target = OpencodeTarget(tmp_path / "opencode.json")
    monkeypatch.setattr("byop.core.targets.opencode.kc.ensure_key", lambda **kwargs: [])
    monkeypatch.setattr("byop.core.targets.opencode.kc.keychain_has", lambda server: False)

    target.configure(_provider(), use_keychain=True, log=lambda _: None)
    config = json.loads(target.config_path.read_text())
    assert config["provider"]["Cosmaxis"]["options"]["apiKey"] == "{env:COSMAXIS_API_KEY}"
    assert "sk-12345678" not in target.config_path.read_text()

    other = _provider(api_url="https://other.example.com/v1")
    target.configure(other, use_keychain=True, conflict_action="append", log=lambda _: None)
    mapping_path = tmp_path / "plugins" / "byop-keychain.json"
    assert json.loads(mapping_path.read_text()) == {
        "Cosmaxis": "https://api.example.com/v1",
        "Cosmaxis_2": "https://other.example.com/v1",
    }

    target.configure(_provider(), use_keychain=False, use_env=True, log=lambda _: None)
    assert json.loads(mapping_path.read_text()) == {"Cosmaxis_2": "https://other.example.com/v1"}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed to exercise the OpenCode plugin")
def test_opencode_keychain_plugin_rejects_changed_endpoints(tmp_path):
    source = Path(__file__).resolve().parents[1] / "byop/core/targets/opencode-keychain.js"
    plugin = tmp_path / "byop-keychain.mjs"
    plugin.write_bytes(source.read_bytes())
    (tmp_path / "byop-keychain.json").write_text(
        json.dumps({"Cosmaxis": "https://api.example.com/v1"})
    )
    script = """
const plugin = (await import(process.argv[1])).default;
const headers = {};
const hooks = await plugin.server();
try {
  await hooks["chat.headers"](
    {model: {providerID: "Cosmaxis"}, provider: {options: {baseURL: "https://evil.example/v1"}}},
    {headers},
  );
  throw new Error("legacy hook accepted a changed endpoint");
} catch (error) {
  if (!error.message.includes("endpoint changed")) throw error;
}
let requestHook;
await plugin.setup({session: {hook: async (_, callback) => { requestHook = callback; }}});
for (const url of ["https://evil.example/v1/chat/completions", "https://api.example.com/v1-evil"]) {
  const request = new Request(url);
  try {
    requestHook({model: {providerID: "Cosmaxis"}, request});
    throw new Error("desktop hook accepted a changed endpoint");
  } catch (error) {
    if (!error.message.includes("endpoint changed")) throw error;
    if (request.headers.has("authorization")) throw new Error("credential leaked");
  }
}
if (Object.hasOwn(headers, "Authorization")) throw new Error("credential leaked");
"""
    subprocess.run(["node", "--input-type=module", "-e", script, plugin.as_uri()], check=True)


def test_opencode_reports_desktop_install_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("byop.core.targets.opencode.OPENCODE_APP", tmp_path / "OpenCode.app")
    monkeypatch.setattr(
        "byop.core.targets.opencode.shutil.which",
        lambda name: "/opt/homebrew/bin/" + name if name in {"brew", "opencode"} else None,
    )
    monkeypatch.setattr(
        "byop.core.targets.opencode._run",
        lambda _: mock.Mock(returncode=1, stderr="desktop installation failed", stdout=""),
    )
    with pytest.raises(RuntimeError, match="desktop installation failed"):
        OpencodeTarget().install(log=lambda _: None)
