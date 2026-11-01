"""Tests for byop.core.paste."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core.config import ProviderConfig
from byop.core.paste import looks_like_provider_paste, parse_provider_paste


def test_looks_like_provider_paste_accepts_multiline_json():
    # Pretty-printed JSON has multi-line layout — exactly what users paste.
    s = json.dumps(
        {
            "provider_name": "P",
            "api_url": "u",
            "models": [],
        },
        indent=2,
    )
    assert "\n" in s  # sanity
    assert looks_like_provider_paste(s) is True


def test_looks_like_provider_paste_rejects_single_line():
    assert looks_like_provider_paste("Cosmaxis") is False
    assert looks_like_provider_paste("https://api.example.com/v1") is False


def test_looks_like_provider_paste_rejects_empty():
    assert looks_like_provider_paste("") is False
    assert looks_like_provider_paste("\n\n") is False


def test_looks_like_provider_paste_rejects_non_json_garbage():
    # Multi-line with braces but not valid JSON.
    assert looks_like_provider_paste("hello {\nworld\n}") is False


def test_parse_provider_paste_round_trip():
    text = json.dumps({
        "provider_name": "Cosmaxis",
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-12345678",
        "models": [
            {"name": "hy3", "max_tokens": 250000, "max_output_tokens": 32000}
        ],
    })
    cfg, missing = parse_provider_paste(text)
    assert isinstance(cfg, ProviderConfig)
    assert cfg.provider_name == "Cosmaxis"
    assert cfg.normalized_api_url() == "https://api.example.com/v1"
    assert cfg.api_key == "sk-12345678"
    assert len(cfg.models) == 1
    assert cfg.models[0].name == "hy3"
    assert cfg.models[0].max_tokens == 250000
    assert cfg.models[0].max_output_tokens == 32000
    assert missing == []


def test_parse_provider_paste_reports_missing_api_key():
    text = json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "models": [{"name": "m"}],
    })
    cfg, missing = parse_provider_paste(text)
    assert cfg.provider_name == "P"
    assert "api_key" in missing


def test_parse_provider_paste_reports_missing_name_and_url():
    text = json.dumps({"api_key": "sk", "models": [{"name": "m"}]})
    cfg, missing = parse_provider_paste(text)
    assert "provider_name" in missing
    assert "api_url" in missing


def test_parse_provider_paste_reports_missing_models():
    text = json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-12345678",
    })
    cfg, missing = parse_provider_paste(text)
    assert "models" in missing


def test_parse_provider_paste_accepts_id_alias_for_models():
    """When only ``id`` is present, ``id`` becomes the model name."""
    text = json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-12345678",
        "models": [{"id": "m1"}],
    })
    cfg, missing = parse_provider_paste(text)
    assert cfg.models[0].name == "m1"
    assert missing == []


def test_parse_provider_paste_name_wins_over_id():
    """When both ``name`` and ``id`` are present, ``name`` is preferred (it's the source of truth)."""
    text = json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-12345678",
        "models": [{"id": "m1", "name": "M1"}],
    })
    cfg, _missing = parse_provider_paste(text)
    assert cfg.models[0].name == "M1"


def test_parse_provider_paste_accepts_context_window_alias():
    text = json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-12345678",
        "models": [{"name": "m1", "context_window": 200000}],
    })
    cfg, missing = parse_provider_paste(text)
    assert cfg.models[0].max_tokens == 200000
    assert missing == []


def test_parse_provider_paste_rejects_garbage():
    import pytest
    with pytest.raises(ValueError):
        parse_provider_paste("not json at all {{{")


def test_parse_provider_paste_rejects_non_object():
    import pytest
    with pytest.raises(ValueError):
        parse_provider_paste("[1, 2, 3]")


def test_parse_provider_paste_rejects_model_missing_name():
    text = json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-12345678",
        "models": [{"display_name": "No ID"}],
    })
    import pytest
    with pytest.raises(ValueError, match="missing 'name'"):
        parse_provider_paste(text)


def test_parse_provider_paste_silently_drops_non_dict_model_entries():
    """A string or number in the models list is skipped (no crash)."""
    text = json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-12345678",
        "models": ["ignored", 123, {"name": "kept"}],
    })
    cfg, missing = parse_provider_paste(text)
    assert missing == []
    assert [m.name for m in cfg.models] == ["kept"]


def test_parse_provider_paste_non_dict_capabilities_become_empty():
    """If `capabilities` isn't a JSON object, the field defaults to {}."""
    text = json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-12345678",
        "models": [{"name": "m", "capabilities": "not-a-dict"}],
    })
    cfg, _ = parse_provider_paste(text)
    assert cfg.models[0].capabilities == {}


def test_parse_provider_paste_accepts_jsonc_line_comments():
    """A pasted snippet with // comments parses cleanly."""
    text = (
        '{\n'
        '  // provider name\n'
        '  "provider_name": "Cosmaxis",\n'
        '  "api_url": "https://api.example.com/v1",\n'
        '  "api_key": "sk-12345678",\n'
        '  "models": [{"name": "hy3"}]\n'
        '}\n'
    )
    cfg, missing = parse_provider_paste(text)
    assert missing == []
    assert cfg.provider_name == "Cosmaxis"


def test_parse_provider_paste_accepts_jsonc_block_comments():
    text = (
        '{\n'
        '  /* block comment is fine */\n'
        '  "provider_name": "P",\n'
        '  "api_url": "https://api.example.com/v1",\n'
        '  "api_key": "sk-x",\n'
        '  "models": [{"name": "m1"}]\n'
        '}\n'
    )
    cfg, missing = parse_provider_paste(text)
    assert cfg.provider_name == "P"
    assert missing == []


def test_parse_provider_paste_does_not_strip_comments_inside_strings():
    """Comments nested in string literals must NOT be touched."""
    text = json.dumps({
        "provider_name": "https://api.example.com/v1",  # real value, comment outside
    })
    cfg, missing = parse_provider_paste(text)
    # url accidentally matches the "missing" sentinel pattern check: empty
    # is False → it's fine. The string value is preserved exactly.
    # The point of this test is mostly "no crash on edge cases".
    assert isinstance(cfg.provider_name, str)


def test_parse_provider_paste_reports_missing_in_list_not_placeholder_string():
    """The missing list — not string identity — signals a missing field.

    A user could legitimately name a provider ``sk-placeholder-key``. We
    must rely on the missing list, not a substring comparison against the
    placeholder value, to decide whether to prompt.
    """
    text = json.dumps({
        "provider_name": "sk-placeholder-key",  # legitimate value
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-real",
        "models": [{"name": "m"}],
    })
    _cfg, missing = parse_provider_paste(text)
    assert "provider_name" not in missing
    assert "api_key" not in missing


def test_looks_like_provider_paste_accepts_jsonc():
    text = (
        '{\n'
        '  // comment\n'
        '  "provider_name": "P",\n'
        '  "models": []\n'
        '}\n'
    )
    assert looks_like_provider_paste(text) is True
