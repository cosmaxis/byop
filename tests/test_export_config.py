"""Tests for the ``--export-config`` feature on each target.

Verifies that ``target.export_config()`` returns the expected snapshot shape
for each target, and that the CLI glue passes ``--export-provider`` correctly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core.config import ModelConfig, ProviderConfig
from byop.core.targets.omp import OmpTarget
from byop.core.targets.opencode import OpencodeTarget
from byop.core.targets.py import PyTarget
from byop.core.targets.zed import ZedTarget


def _provider() -> ProviderConfig:
    return ProviderConfig(
        provider_name="ExampleProvider",
        api_url="https://api.example.com/v1",
        api_key="sk-example12345678",
        models=[
            ModelConfig(
                name="hy3",
                max_tokens=128000,
                max_output_tokens=32000,
                reasoning_effort="high",
            ),
        ],
        set_default_agent=True,
        set_inline_assistant=True,
    )


# ---------------------------------------------------------------------------
# Per-target shape
# ---------------------------------------------------------------------------
def test_zed_export_config_includes_provider_block(tmp_path):
    settings = tmp_path / "settings.json"
    target = ZedTarget(settings_path=settings)
    # No file yet -> empty snapshot but with the path so users can see it.
    snap = target.export_config()
    assert target.name in snap
    assert snap[target.name]["config_path"] == str(settings)
    assert snap[target.name]["providers"] == {}

    # After configuring, the snapshot should contain the provider block.
    with mock.patch("byop.core.keychain.ensure_key", return_value=["env:x"]):
        target.configure(_provider(), use_keychain=False, use_env=True)
    snap = target.export_config()
    block = snap[target.name]["providers"]["ExampleProvider"]
    assert block["api_url"] == "https://api.example.com/v1"
    assert block["available_models"][0]["name"] == "hy3"
    # byop-managed agent block must be surfaced.
    assert "agent" in snap[target.name]


def test_py_export_config_shape(tmp_path):
    models = tmp_path / "models.json"
    target = PyTarget(models_path=models)
    with mock.patch("byop.core.keychain.ensure_key", return_value=["env:x"]):
        target.configure(_provider(), use_keychain=False, use_env=True)
    snap = target.export_config()
    assert snap[target.name]["config_path"] == str(models)
    block = snap[target.name]["providers"]["ExampleProvider"]
    assert block["baseUrl"] == "https://api.example.com/v1"
    assert block["api"] == "openai-completions"
    # Either a literal key (no keychain) or a keychain shell-out ref. We
    # don't assert a specific value because the user's real keychain may
    # already contain an entry for this server.
    assert block["apiKey"] == "sk-example12345678" or block["apiKey"].startswith("!security ")


def test_opencode_export_config_shape(tmp_path):
    cfg = tmp_path / "opencode.json"
    target = OpencodeTarget(config_path=cfg)
    with mock.patch("byop.core.keychain.ensure_key", return_value=["env:x"]):
        target.configure(_provider(), use_keychain=False, use_env=True)
    snap = target.export_config()
    assert snap[target.name]["config_path"] == str(cfg)
    block = snap[target.name]["providers"]["ExampleProvider"]
    assert block["npm"] == "@ai-sdk/openai-compatible"
    assert block["options"]["baseURL"] == "https://api.example.com/v1"


def test_omp_export_config_inherits_py(tmp_path):
    """OmpTarget subclasses PyTarget — same models.json shape."""
    models = tmp_path / "models.json"
    target = OmpTarget(models_path=models)
    with mock.patch("byop.core.keychain.ensure_key", return_value=["env:x"]):
        target.configure(_provider(), use_keychain=False, use_env=True)
    snap = target.export_config()
    assert snap[target.name]["config_path"] == str(models)
    assert "ExampleProvider" in snap[target.name]["providers"]


# ---------------------------------------------------------------------------
# Missing-file safety
# ---------------------------------------------------------------------------
def test_export_config_returns_empty_when_no_file(tmp_path):
    """All targets must handle a missing config file without raising."""
    for cls, path_attr, path_value in [
        (ZedTarget, "settings_path", tmp_path / "zed.json"),
        (PyTarget, "models_path", tmp_path / "models.json"),
        (OpencodeTarget, "config_path", tmp_path / "oc.json"),
    ]:
        t = cls(**{path_attr: path_value})
        snap = t.export_config()
        assert snap[t.name]["providers"] == {}
        assert snap[t.name]["config_path"] == str(path_value)


# ---------------------------------------------------------------------------
# CLI glue: --export-config + --export-provider filtering
# ---------------------------------------------------------------------------
def test_cli_export_config_emits_json_for_each_target(capsys):
    """The CLI prints valid JSON to stdout and exits 0."""
    from byop.cli import build_parser

    parser = build_parser()
    # Top-level --export-config now lives under the export-config subparser.
    args = parser.parse_args(["export-config"])
    # Avoid touching the user's real files: monkeypatch the registry to a
    # single in-memory target pointed at a temp file.
    import byop.core.targets as tmod
    from byop.core.targets.zed import ZedTarget

    settings = Path("/tmp/__byop_test_export_does_not_exist.json")
    if settings.exists():
        settings.unlink()

    target = ZedTarget(settings_path=settings)

    with mock.patch.object(
        tmod, "available_targets", return_value=[target]
    ), mock.patch.object(target, "export_config", return_value={
        target.name: {
            "config_path": str(settings),
            "providers": {"P1": {"api_url": "x"}},
        }
    }) as ec:
        from byop.cli import _run_export

        rc = _run_export(args)

    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert target.name in parsed
    assert parsed[target.name]["providers"] == {"P1": {"api_url": "x"}}
    ec.assert_called_once()


def test_cli_export_config_filters_by_provider():
    """``--export-provider`` drops targets whose providers don't match."""
    from byop.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["export-config", "--export-provider", "Wanted"])

    import byop.core.targets as tmod
    from byop.core.targets.zed import ZedTarget

    target = ZedTarget(settings_path=Path("/tmp/__nope.json"))
    snap = {
        target.name: {
            "config_path": "/tmp/__nope.json",
            "providers": {"Other": {"api_url": "x"}},
        }
    }
    with mock.patch.object(
        tmod, "available_targets", return_value=[target]
    ), mock.patch.object(target, "export_config", return_value=snap):
        from byop.cli import _run_export

        rc = _run_export(args)

    assert rc == 0
    # Filtered out -> target omitted from output entirely.
