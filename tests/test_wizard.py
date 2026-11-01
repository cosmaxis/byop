"""Smoke test for the interactive wizard path (run_wizard).

The wizard is the default mode users hit; this drives it end-to-end with the
prompt layer mocked out so we exercise ProviderConfig assembly without a TTY.
"""

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core import wizard


def _patch_wizard(ask_seq, ask_int_seq, confirm_seq, secret="sk-test123"):
    return (
        mock.patch.object(wizard.prompt, "ask", side_effect=ask_seq),
        mock.patch.object(wizard.prompt, "ask_secret", return_value=secret),
        mock.patch.object(wizard.prompt, "ask_int", side_effect=ask_int_seq),
        mock.patch.object(wizard.prompt, "confirm", side_effect=confirm_seq),
    )


def test_run_wizard_builds_provider_and_prefs():
    ask = [
        "",                          # paste prompt (blank -> per-field)
        "MyProvider",                # provider name
        "https://api.example.com/v1",  # api base url
        "my-model",                  # model id
        "",                          # display name (blank)
        "",                          # reasoning effort (blank)
    ]
    ask_int = [128000, 32000]
    confirm = [
        False,  # supports images
        False,  # interleaved reasoning
        False,  # max_tokens parameter
        False,  # add another model? -> no
        True,   # default agent
        True,   # inline assistant
        False,  # commit message
        False,  # thread summary
        False,  # edit predictions
        True,   # use keychain
        False,  # use env
    ]
    p_ask, p_secret, p_int, p_confirm = _patch_wizard(ask, ask_int, confirm)
    with p_ask, p_secret, p_int, p_confirm:
        provider, prefs = wizard.run_wizard()

    assert provider.provider_name == "MyProvider"
    assert provider.normalized_api_url() == "https://api.example.com/v1"
    assert provider.api_key == "sk-test123"
    assert len(provider.models) == 1
    assert provider.models[0].name == "my-model"
    assert provider.set_default_agent is True
    assert provider.set_inline_assistant is True
    assert provider.set_commit_message is False
    assert provider.set_thread_summary is False
    assert provider.use_edit_predictions is False
    assert prefs == {"use_keychain": True, "use_env": False}


def test_run_wizard_multiple_models():
    ask = [
        "",                          # paste prompt (blank)
        "MyProvider",
        "https://api.example.com/v1",
        "m1", "", "",   # model 1: id, display, reasoning
        "m2", "", "",   # model 2: id, display, reasoning
    ]
    ask_int = [128000, 32000, 200000, 64000]
    confirm = [
        False, False, False, True,    # model 1 caps + add another? yes
        False, False, False, False,   # model 2 caps + add another? no
        True, True, False, False, False, True, False,  # run_wizard prefs
    ]
    p_ask, p_secret, p_int, p_confirm = _patch_wizard(ask, ask_int, confirm)
    with p_ask, p_secret, p_int, p_confirm:
        provider, _ = wizard.run_wizard()

    assert [m.name for m in provider.models] == ["m1", "m2"]


def test_run_wizard_keychain_opt_out():
    ask = [
        "",                          # paste prompt
        "MyProvider",
        "https://api.example.com/v1",
        "my-model", "", "",
    ]
    ask_int = [128000, 32000]
    confirm = [
        False, False, False, False,
        True, True, False, False, False,
        False,  # use keychain? no
        True,   # use env? (default flips to True when keychain off)
    ]
    p_ask, p_secret, p_int, p_confirm = _patch_wizard(ask, ask_int, confirm)
    with p_ask, p_secret, p_int, p_confirm:
        _, prefs = wizard.run_wizard()

    assert prefs == {"use_keychain": False, "use_env": True}


# ----------------------------------------------------------------------
# Paste-path coverage
# ----------------------------------------------------------------------
def test_run_wizard_accepts_full_paste():
    """Full JSON paste should skip all field prompts."""
    paste = json.dumps(
        {
            "provider_name": "Cosmaxis",
            "api_url": "https://api.example.com/v1",
            "api_key": "sk-12345678",
            "models": [{"name": "hy3"}],
        },
        indent=2,
    )
    ask = [paste]  # only the paste prompt is asked
    ask_int = []
    confirm = [
        True, True, False, False, False, True, False,
        # default_agent, inline_assistant, commit_message, thread_summary,
        # edit_predictions, use_keychain, use_env
    ]
    p_ask, p_secret, p_int, p_confirm = _patch_wizard(ask, ask_int, confirm)
    with p_ask, p_secret, p_int, p_confirm:
        provider, prefs = wizard.run_wizard()

    assert provider.provider_name == "Cosmaxis"
    assert provider.api_url == "https://api.example.com/v1"
    assert provider.api_key == "sk-12345678"
    assert [m.name for m in provider.models] == ["hy3"]
    assert prefs["use_keychain"] is True
    assert prefs["use_env"] is False


def test_run_wizard_falls_back_to_secret_prompt_when_paste_missing_key():
    """Partial paste (no api_key) must still ask for the key via ask_secret."""
    paste = json.dumps(
        {
            "provider_name": "Cosmaxis",
            "api_url": "https://api.example.com/v1",
            "models": [{"name": "hy3"}],
        },
        indent=2,
    )
    ask = [paste]
    ask_int = []
    confirm = [
        True, True, False, False, False, True, False,
    ]
    p_ask, p_secret, p_int, p_confirm = _patch_wizard(
        ask, ask_int, confirm, secret="sk-filled-in"
    )
    with p_ask, p_secret, p_int, p_confirm:
        provider, _ = wizard.run_wizard()
    assert provider.api_key == "sk-filled-in"


def test_run_wizard_saved_prefill_empty_enter_keeps_stored_key():
    """Pressing Enter on API key during saved-profile edit/apply keeps prefill.api_key."""
    from byop.core.config import ModelConfig, ProviderConfig

    prefill = ProviderConfig(
        provider_name="Cosmaxis",
        api_url="https://api.example.com/v1",
        api_key="sk-stored-keychain",
        models=[ModelConfig(name="hy3")],
    )
    ask = [
        "",  # paste prompt
        "Cosmaxis",
        "https://api.example.com/v1",
    ]
    confirm = [
        False,  # reconfigure models? -> no
        True, True, False, False, False, True, False,
    ]
    p_ask, p_secret, p_int, p_confirm = _patch_wizard(
        ask, [], confirm, secret=""
    )
    with p_ask, p_secret, p_int, p_confirm:
        provider, _ = wizard.run_wizard(prefill=prefill)
    assert provider.api_key == "sk-stored-keychain"
