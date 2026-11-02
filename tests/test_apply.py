"""Integration tests for the apply orchestration with a mocked environment."""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core import apply as applymod
from byop.core import keychain as kc
from byop.core import settings as sett
from byop.core.config import ModelConfig, ProviderConfig


def _provider() -> ProviderConfig:
    return ProviderConfig(
        provider_name="ExampleProvider",
        api_url="https://api.example.com/v1/",
        api_key="sk-example12345678",
        models=[ModelConfig(name="hy3", max_tokens=250000,
                            max_output_tokens=32000)],
        set_default_agent=True,
        set_inline_assistant=True,
    )


def _mock_all():
    patches = []
    p_install = mock.patch("byop.core.targets.zed.zedmod.install")
    p_keychain = mock.patch.object(
        kc, "ensure_key", return_value=["keychain:x"]
    )
    patches.append(p_install)
    patches.append(p_keychain)
    started = {
        "install": p_install.start(),
        "keychain": p_keychain.start(),
        "_patches": patches,
    }
    return started


def _stop_all(mocks):
    for p in mocks.get("_patches", []):
        p.stop()


def test_apply_writes_expected_settings(tmp_path):
    settings = tmp_path / "settings.json"
    # Pre-existing unrelated setting to ensure we merge, not overwrite.
    sett.write_path(settings, {"vim_mode": True})
    mocks = _mock_all()
    try:
        applymod.apply_provider(
            _provider(),
            settings_path=settings,
            log=lambda m: None,
        )
    finally:
        _stop_all(mocks)

    reloaded = sett.load_path(settings)
    assert reloaded["vim_mode"] is True
    oc = reloaded["language_models"]["openai_compatible"]["ExampleProvider"]
    assert oc["api_url"] == "https://api.example.com/v1"
    assert oc["available_models"][0]["name"] == "hy3"
    assert reloaded["agent"]["inline_assistant_model"] == {
        "provider": "ExampleProvider",
        "model": "hy3",
    }
    assert "default_model" in reloaded["agent"]


def test_apply_invalid_provider_raises(tmp_path):
    p = _provider()
    p.api_key = "short"
    mocks = _mock_all()
    try:
        try:
            applymod.apply_provider(
                p, settings_path=tmp_path / "settings.json", log=lambda m: None
            )
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
    finally:
        _stop_all(mocks)


def test_apply_dry_run_does_not_write(tmp_path):
    settings = tmp_path / "settings.json"
    mocks = _mock_all()
    try:
        applymod.apply_provider(
            _provider(),
            settings_path=settings,
            log=lambda m: None,
            dry_run=True,
        )
    finally:
        _stop_all(mocks)
    assert not settings.exists()
    # ensure no keychain write happened
    mocks["keychain"].assert_not_called()


def test_current_provider_names(tmp_path):
    settings = tmp_path / "settings.json"
    sett.write_path(settings, {
        "language_models": {
            "openai_compatible": {
                "ExampleProvider": {"api_url": "x", "available_models": []}
            }
        }
    })
    names = applymod.current_provider_names(settings)
    assert names == ["ExampleProvider"]
