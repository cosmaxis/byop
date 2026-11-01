"""Tests for byop.core.profiles — TOML persistence + active-profile pointer."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core import paths as pmod
from byop.core import profiles as prof
from byop.core.config import ModelConfig


@pytest.fixture
def isolated_config(monkeypatch):
    """Redirect BYOP_CONFIG_DIR to a fresh tempdir for the duration of one test."""
    tmp = tempfile.mkdtemp(prefix="byop-test-")
    monkeypatch.setenv("BYOP_CONFIG_DIR", tmp)
    yield Path(tmp)


def _sample_profile(name: str = "default") -> prof.Profile:
    return prof.Profile(
        name=name,
        provider_name="ExampleProvider",
        api_url="https://api.example.com/v1",
        api_key_ref="keychain",
        env_var="EXAMPLEPROVIDER_API_KEY",
        models=[
            ModelConfig(
                name="m1",
                display_name="Model One",
                max_tokens=200000,
                max_output_tokens=32000,
                reasoning_effort="medium",
                capabilities={"tools": True, "images": True},
            ),
        ],
        set_default_agent=True,
        set_inline_assistant=True,
        set_commit_message=False,
        set_thread_summary=False,
        use_edit_predictions=True,
    )


# ---------------------------------------------------------------------------
# Config dir resolution
# ---------------------------------------------------------------------------
def test_config_dir_respects_BYOP_CONFIG_DIR(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("BYOP_CONFIG_DIR", tmp)
    assert pmod.config_dir() == Path(tmp)
    assert pmod.config_path() == Path(tmp) / "config.toml"
    assert pmod.profiles_dir() == Path(tmp) / "profiles"


def test_ensure_config_dir_creates_profiles_subdir(isolated_config):
    target = pmod.ensure_config_dir()
    assert target == isolated_config
    assert (isolated_config / "profiles").is_dir()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------
def test_save_and_load_round_trip(isolated_config):
    p = _sample_profile()
    prof.save_profile(p)
    loaded = prof.load_profile()
    assert loaded.name == p.name
    assert loaded.provider_name == p.provider_name
    assert loaded.api_url == p.api_url
    assert loaded.api_key_ref == "keychain"
    assert loaded.env_var == p.env_var
    assert loaded.set_default_agent is True
    assert loaded.use_edit_predictions is True
    assert len(loaded.models) == 1
    m = loaded.models[0]
    assert m.name == "m1"
    assert m.display_name == "Model One"
    assert m.max_tokens == 200000
    assert m.reasoning_effort == "medium"
    assert m.capabilities.get("images") is True
    # Timestamps stamped on first save.
    assert loaded.created_at
    assert loaded.updated_at


def test_save_overwrite_guard(isolated_config):
    p = _sample_profile()
    prof.save_profile(p)
    with pytest.raises(prof.ProfileExists):
        prof.save_profile(p)
    # allow_overwrite=True bypasses.
    prof.save_profile(p, allow_overwrite=True)


def test_load_missing_raises(isolated_config):
    with pytest.raises(prof.ProfileNotFound):
        prof.load_profile("nope")


def test_delete_profile(isolated_config):
    p = _sample_profile()
    prof.save_profile(p)
    prof.delete_profile("default")
    assert not prof.profile_exists("default")
    with pytest.raises(prof.ProfileNotFound):
        prof.delete_profile("default")


# ---------------------------------------------------------------------------
# Active-profile pointer
# ---------------------------------------------------------------------------
def test_default_active_profile(isolated_config):
    assert prof.get_active_profile_name() == "default"


def test_set_and_get_active_profile(isolated_config):
    prof.set_active_profile_name("work")
    assert prof.get_active_profile_name() == "work"


def test_list_profiles_active_first(isolated_config):
    prof.save_profile(_sample_profile("default"))
    prof.save_profile(_sample_profile("work"))
    prof.save_profile(_sample_profile("personal"))
    prof.set_active_profile_name("personal")
    assert prof.list_profiles() == ["personal", "default", "work"]


# ---------------------------------------------------------------------------
# profile_from_provider
# ---------------------------------------------------------------------------
def test_profile_from_provider_round_trip(isolated_config):
    from byop.core.config import ProviderConfig

    provider = ProviderConfig(
        provider_name="Cosmaxis",
        api_url="https://api.cosmaxis.com/v1/",
        api_key="sk-not-stored",
        models=[
            ModelConfig(name="command-code", max_tokens=1_000_000,
                        max_output_tokens=500_000, reasoning_effort="max"),
        ],
        set_default_agent=True,
        set_inline_assistant=False,
        set_commit_message=True,
    )
    profile = prof.profile_from_provider(provider, name="work")
    # The api_key is not stored on the Profile.
    assert profile.api_key_ref == "keychain"
    # URL is normalized (trailing slash stripped).
    assert profile.api_url == "https://api.cosmaxis.com/v1"
    assert profile.set_commit_message is True
    assert profile.set_inline_assistant is False


def test_profile_from_provider_env_ref():
    from byop.core.config import ProviderConfig

    provider = ProviderConfig(
        provider_name="Env",
        api_url="https://api.example.com/v1",
        api_key="x",
        models=[ModelConfig(name="m")],
    )
    p = prof.profile_from_provider(provider, api_key_ref="env:MY_KEY")
    assert p.api_key_ref == "env:MY_KEY"
    assert p.env_var == "MY_KEY"


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "../etc", "foo/bar", "foo$bar"])
def test_invalid_profile_name_rejected(bad):
    with pytest.raises(prof.ProfileError):
        prof._validate_profile_name(bad)
