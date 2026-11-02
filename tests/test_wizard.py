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
        choose_seq=["high", "Keychain + shell env (plaintext)"],
        ask_int_seq=[256000, 64000],
        select_many_seq=[[0], [1, 3]],
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


def test_edit_provider_targets_only_selected_field_and_shows_non_secret_values():
    original = ProviderConfig(
        "Demo", "https://example.com/v1", "",
        [ModelConfig("old", display_name="Old", max_tokens=500000, max_output_tokens=12000,
                     reasoning_effort="high", capabilities={"tools": True, "images": True})],
    )
    with (
        mock.patch.object(wizard.prompt, "choose", side_effect=["View details", "API URL", "Save"]) as choose,
        mock.patch.object(wizard.prompt, "ask", return_value="https://new.example/v1") as ask,
        mock.patch.object(wizard.prompt, "ask_secret") as secret,
        mock.patch.object(wizard.prompt, "step") as step,
        mock.patch.object(wizard.prompt, "info") as info,
    ):
        result = wizard.edit_provider(original, storage="macOS Keychain")
    assert result is not None
    updated, storage = result
    assert storage == "macOS Keychain"
    assert updated.api_url == "https://new.example/v1"
    assert updated.models == original.models
    assert original.api_url == "https://example.com/v1"
    assert ask.call_count == 1
    secret.assert_not_called()
    assert choose.call_count == 3
    summary = step.call_args.args[1]
    assert "context 500000" in summary and "output 12000" in summary
    capabilities = info.call_args.args[0]
    for key in wizard.KNOWN_CAPABILITIES:
        assert key + "=" in capabilities


def test_edit_models_add_selectively_customize_default_and_remove():
    first = ModelConfig("first", max_tokens=300000, capabilities={"tools": False, "images": True})
    second = ModelConfig("second", display_name="Second", max_output_tokens=9000,
                         reasoning_effort="none", capabilities={"prompt_cache_key": True})
    choices = iter([
        "Models", "Add model IDs", "Edit model", "third",
        "Model ID", "Context window", "Max output tokens", "Display name",
        "Reasoning effort", "high", "Capabilities", "Done",
        "Set default model", "renamed", "Remove model", "second",
        "Remove model", "first", "Remove model", "fourth",
        "Remove model", "Done", "Save",
    ])
    asks = iter(["third, fourth, third", "renamed", "Renamed"])
    with (
        mock.patch.object(wizard.prompt, "choose", side_effect=lambda *a, **k: next(choices)),
        mock.patch.object(wizard.prompt, "ask", side_effect=lambda *a, **k: next(asks)),
        mock.patch.object(wizard.prompt, "ask_int", side_effect=[800000, 24000]),
        mock.patch.object(wizard.prompt, "select_many", return_value=[1, 6]) as caps,
        mock.patch.object(wizard.prompt, "warn") as warn,
    ):
        result = wizard.edit_provider(
            ProviderConfig("Demo", "https://example.com/v1", "", [first, second]),
            storage="macOS Keychain",
        )
    assert result is not None
    models = result[0].models
    assert [m.name for m in models] == ["renamed"]
    assert models[0].display_name == "Renamed"
    assert (models[0].max_tokens, models[0].max_output_tokens) == (800000, 24000)
    assert models[0].reasoning_effort == "high"
    assert models[0].capabilities["images"] is True
    assert models[0].capabilities["tools"] is True
    assert set(caps.call_args.args[1]) == {
        key.replace("_", " ").capitalize() for key in wizard.KNOWN_CAPABILITIES
    }
    assert first.max_tokens == 300000
    assert second.reasoning_effort == "none"
    warn.assert_called_once()


def test_edit_models_cannot_remove_last_and_cancel_keeps_original():
    original = ProviderConfig("Demo", "https://example.com/v1", "", [ModelConfig("one")])
    with (
        mock.patch.object(wizard.prompt, "choose", side_effect=[
            "Models", "Remove model", "Done", "Provider name", "Cancel"
        ]),
        mock.patch.object(wizard.prompt, "ask", return_value="Changed"),
        mock.patch.object(wizard.prompt, "warn") as warn,
    ):
        assert wizard.edit_provider(original, storage="macOS Keychain") is None
    assert original.provider_name == "Demo"
    assert original.models[0].name == "one"
    warn.assert_called_once()


def test_add_wizard_advanced_customizes_only_selected_model():
    patches = _patch_wizard(
        ["Demo", "https://example.com/v1", "first, second", "Second"],
        confirm_seq=[True],
        choose_seq=["high", "macOS Keychain"],
        ask_int_seq=[600000, 12000],
        select_many_seq=[[1], [1, 6]],
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        provider, _ = wizard.run_wizard()
    assert provider.models[0].display_name is None
    assert provider.models[1].display_name == "Second"
    assert provider.models[1].max_tokens == 600000


def test_opening_capabilities_without_changes_preserves_unspecified_values():
    model = ModelConfig("plain", capabilities={})
    with (
        mock.patch.object(wizard.prompt, "choose", side_effect=["Capabilities", "Done"]),
        mock.patch.object(wizard.prompt, "select_many", side_effect=lambda *a, **kw: kw["defaults"]),
    ):
        updated = wizard._edit_model(model, set())
    assert updated.capabilities == {}


def test_editor_retries_invalid_context_without_discarding_changes():
    original = ProviderConfig("Demo", "https://example.com/v1", "", [ModelConfig("m")])
    choices = ["Models", "Edit model", "m", "Context window", "Done", "Done",
               "Save", "Models", "Edit model", "m", "Context window", "Done", "Done", "Save"]
    with (
        mock.patch.object(wizard.prompt, "choose", side_effect=choices),
        mock.patch.object(wizard.prompt, "ask_int", side_effect=[0, 256000]),
        mock.patch.object(wizard.prompt, "warn") as warn,
    ):
        result = wizard.edit_provider(original, storage="macOS Keychain")
    assert result is not None
    assert result[0].models[0].max_tokens == 256000
    assert warn.called
