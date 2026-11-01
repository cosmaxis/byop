"""Tests for the interactive progressive-disclosure wizard."""

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core import wizard
from byop.core.config import ModelConfig, ProviderConfig


def _patch_wizard(
    ask_seq,
    *,
    secret="sk-test123",
    confirm_seq=(False,),
    choose_seq=("macOS Keychain",),
    ask_int_seq=(),
    select_many_seq=(),
):
    return (
        mock.patch.object(wizard.prompt, "ask", side_effect=list(ask_seq)),
        mock.patch.object(wizard.prompt, "ask_secret", return_value=secret),
        mock.patch.object(wizard.prompt, "confirm", side_effect=list(confirm_seq)),
        mock.patch.object(wizard.prompt, "choose", side_effect=list(choose_seq)),
        mock.patch.object(wizard.prompt, "ask_int", side_effect=list(ask_int_seq)),
        mock.patch.object(wizard.prompt, "select_many", side_effect=list(select_many_seq)),
    )


def test_run_wizard_builds_provider_and_prefs():
    patches = _patch_wizard(
        ["MyProvider", "https://api.example.com/v1", "my-model"],
        confirm_seq=[False],
        choose_seq=["macOS Keychain"],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        provider, prefs = wizard.run_wizard()

    assert provider.provider_name == "MyProvider"
    assert provider.normalized_api_url() == "https://api.example.com/v1"
    assert provider.api_key == "sk-test123"
    assert [m.name for m in provider.models] == ["my-model"]
    assert provider.set_default_agent is True
    assert provider.set_inline_assistant is True
    assert provider.set_commit_message is False
    assert provider.set_thread_summary is False
    assert provider.use_edit_predictions is False
    assert prefs == {"use_keychain": True, "use_env": False}


def test_run_wizard_comma_separated_models_deduplicates_preserving_order():
    patches = _patch_wizard(
        ["MyProvider", "https://api.example.com/v1", "m1, m2, m1"],
        confirm_seq=[False],
        choose_seq=["macOS Keychain"],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        provider, _ = wizard.run_wizard()

    assert [m.name for m in provider.models] == ["m1", "m2"]


def test_run_wizard_advanced_model_customization():
    patches = _patch_wizard(
        ["MyProvider", "https://api.example.com/v1", "pro", "Pro Model"],
        confirm_seq=[True],
        choose_seq=["high", "Keychain + shell env"],
        ask_int_seq=[256000, 64000],
        select_many_seq=[[0, 2]],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        provider, prefs = wizard.run_wizard()

    model = provider.models[0]
    assert model.name == "pro"
    assert model.display_name == "Pro Model"
    assert model.max_tokens == 256000
    assert model.max_output_tokens == 64000
    assert model.reasoning_effort == "high"
    assert model.capabilities["images"] is True
    assert model.capabilities["interleaved_reasoning"] is False
    assert model.capabilities["max_tokens_parameter"] is True
    assert prefs == {"use_keychain": True, "use_env": True}


def test_run_wizard_keychain_opt_out():
    patches = _patch_wizard(
        ["MyProvider", "https://api.example.com/v1", "my-model"],
        confirm_seq=[False],
        choose_seq=["Shell env only (plaintext)"],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        _, prefs = wizard.run_wizard()

    assert prefs == {"use_keychain": False, "use_env": True}


def test_run_wizard_accepts_full_paste_on_first_prompt():
    paste = json.dumps(
        {
            "provider_name": "Cosmaxis",
            "api_url": "https://api.example.com/v1",
            "api_key": "sk-12345678",
            "models": [{"name": "hy3"}],
        },
        indent=2,
    )
    patches = _patch_wizard([paste], confirm_seq=[], choose_seq=["macOS Keychain"])
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        provider, prefs = wizard.run_wizard()

    assert provider.provider_name == "Cosmaxis"
    assert provider.api_url == "https://api.example.com/v1"
    assert provider.api_key == "sk-12345678"
    assert [m.name for m in provider.models] == ["hy3"]
    assert prefs == {"use_keychain": True, "use_env": False}


def test_run_wizard_falls_back_to_secret_prompt_when_paste_missing_key():
    paste = json.dumps(
        {
            "provider_name": "Cosmaxis",
            "api_url": "https://api.example.com/v1",
            "models": [{"name": "hy3"}],
        }
    )
    patches = _patch_wizard(
        [paste],
        secret="sk-filled-in",
        confirm_seq=[],
        choose_seq=["macOS Keychain"],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        provider, _ = wizard.run_wizard()
    assert provider.api_key == "sk-filled-in"


def test_run_wizard_saved_prefill_empty_enter_keeps_stored_key_and_model_metadata():
    prefill = ProviderConfig(
        provider_name="Cosmaxis",
        api_url="https://api.example.com/v1",
        api_key="sk-stored-keychain",
        models=[ModelConfig(name="hy3", max_tokens=200000)],
        set_commit_message=True,
        use_edit_predictions=True,
    )
    patches = _patch_wizard(
        ["Cosmaxis", "https://api.example.com/v1", "hy3"],
        secret="",
        confirm_seq=[False],
        choose_seq=["macOS Keychain"],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        provider, _ = wizard.run_wizard(prefill=prefill)
    assert provider.api_key == "sk-stored-keychain"
    assert provider.models[0].max_tokens == 200000
    assert provider.set_commit_message is True
    assert provider.use_edit_predictions is True


def test_configure_zed_features_uses_single_checklist():
    base = ProviderConfig("Demo", "https://api.example.com/v1", "sk-123", [ModelConfig("m")])
    with mock.patch.object(wizard.prompt, "select_many", return_value=[1, 2, 4]) as sm:
        updated = wizard.configure_zed_features(base)
    assert sm.call_args.kwargs["defaults"] == [0, 1]
    assert updated.set_default_agent is False
    assert updated.set_inline_assistant is True
    assert updated.set_commit_message is True
    assert updated.set_thread_summary is False
    assert updated.use_edit_predictions is True
