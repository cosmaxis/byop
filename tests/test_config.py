"""Unit tests for the provider configuration model and helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core import default_model_capabilities
from byop.core.config import (
    KNOWN_CAPABILITIES,
    KNOWN_REASONING_EFFORTS,
    ModelConfig,
    ProviderConfig,
)


def _provider(**overrides) -> ProviderConfig:
    base = {
        "provider_name": "ExampleProvider",
        "api_url": "https://api.example.com/v1/",
        "api_key": "sk-12345678",
        "models": [ModelConfig(name="hy3")],
    }
    base.update(overrides)
    return ProviderConfig(**base)


def test_model_to_dict_defaults():
    m = ModelConfig(name="hy3")
    d = m.to_dict()
    assert d["name"] == "hy3"
    assert d["max_tokens"] == 1000000
    assert d["max_output_tokens"] == 128000
    assert d["reasoning_effort"] == "medium"
    assert "capabilities" not in d


def test_model_to_dict_includes_extras():
    m = ModelConfig(
        name="hy3",
        display_name="Hy3",
        reasoning_effort="medium",
        capabilities={"tools": True, "images": True},
    )
    d = m.to_dict()
    assert d["display_name"] == "Hy3"
    assert d["reasoning_effort"] == "medium"
    assert d["capabilities"]["images"] is True


def test_normalized_api_url_strips_slash():
    p = _provider(api_url="https://api.example.com/v1/")
    assert p.normalized_api_url() == "https://api.example.com/v1"


def test_env_var_name_basic():
    p = _provider(provider_name="ExampleProvider")
    assert p.env_var_name() == "EXAMPLEPROVIDER_API_KEY"


def test_env_var_name_spaces_and_symbols():
    p = _provider(provider_name="My Provider!")
    assert p.env_var_name() == "MY_PROVIDER_API_KEY"


def test_keychain_server_uses_normalized_url():
    p = _provider(api_url="https://api.example.com/v1/")
    assert p.keychain_server() == "https://api.example.com/v1"


def test_language_models_block():
    p = _provider()
    block = p.language_models_block()
    assert "ExampleProvider" in block
    assert block["ExampleProvider"]["api_url"] == "https://api.example.com/v1"
    assert block["ExampleProvider"]["available_models"][0]["name"] == "hy3"


def test_agent_block_defaults():
    p = _provider()
    agent = p.agent_block()
    assert agent["default_model"] == {"provider": "ExampleProvider", "model": "hy3"}
    assert agent["inline_assistant_model"] == {
        "provider": "ExampleProvider",
        "model": "hy3",
    }
    assert "commit_message_model" not in agent


def test_agent_block_selective():
    p = _provider(
        set_default_agent=False,
        set_inline_assistant=False,
        set_commit_message=True,
        set_thread_summary=True,
    )
    agent = p.agent_block()
    assert "default_model" not in agent
    assert "inline_assistant_model" not in agent
    assert agent["commit_message_model"]["model"] == "hy3"
    assert agent["thread_summary_model"]["model"] == "hy3"


def test_edit_predictions_block_off_by_default():
    assert _provider().edit_predictions_block() is None


def test_edit_predictions_block_on():
    p = _provider(use_edit_predictions=True)
    ep = p.edit_predictions_block()
    assert ep["provider"] == "openai_compatible"
    assert ep["open_ai_compatible_api"]["model"] == "hy3"
    assert ep["open_ai_compatible_api"]["api_url"].endswith("/v1")


def test_validate_ok():
    assert _provider().validate() == []


def test_validate_bad_provider_name():
    errs = _provider(provider_name="bad/name").validate()
    assert any("Provider name" in e for e in errs)


def test_validate_bad_url():
    errs = _provider(api_url="ftp://nope").validate()
    assert any("http" in e for e in errs)


def test_validate_short_key():
    errs = _provider(api_key="short").validate()
    assert any("API key" in e for e in errs)


def test_validate_no_models():
    p = _provider(models=[])
    errs = p.validate()
    assert any("At least one model" in e for e in errs)


def test_validate_bad_reasoning_effort():
    p = _provider(models=[ModelConfig(name="hy3", reasoning_effort="ultra")])
    errs = p.validate()
    assert any("reasoning_effort" in e for e in errs)


def test_validate_unknown_capability():
    p = _provider(
        models=[ModelConfig(name="hy3", capabilities={"bogus": True})]
    )
    errs = p.validate()
    assert any("unknown capability" in e for e in errs)


def test_default_capabilities_subset_of_known():
    caps = default_model_capabilities()
    assert set(caps).issubset(KNOWN_CAPABILITIES)


def test_known_reasoning_efforts_nonempty():
    assert "medium" in KNOWN_REASONING_EFFORTS
    assert len(KNOWN_REASONING_EFFORTS) == 7
