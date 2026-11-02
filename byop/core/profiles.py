"""Persistent profile storage for byop.

A *profile* captures everything needed to wire a provider into a target:

* Provider name, API URL, keychain reference (server = ``api_url``)
* The list of models with their capabilities
* Feature wiring (default agent, inline assistant, commit messages, etc.)

Profiles live as one ``.toml`` file each under ``<config_dir>/profiles/``;
the global ``config.toml`` points at the *active* profile via
``active_profile``. This two-file layout mirrors ``gh``'s split between
``config.yml`` (preferences) and ``hosts.yml`` (auth) — it lets the CLI
re-read the active profile quickly without parsing all saved profiles, and
it makes profile switching atomic (you only rewrite ``config.toml``).

API keys are never stored in these files. The ``api_key_ref`` field points
at where the secret lives (``"keychain"`` or ``"env:VAR_NAME"``); the actual
secret is fetched at apply time. The original ``api_key`` argument to
``save_profile`` is the in-memory value (validated, then discarded).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tomli_w

from .config import ModelConfig, ProviderConfig
from .paths import config_path, ensure_config_dir, profiles_dir

DEFAULT_PROFILE_NAME = "default"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class ProfileError(Exception):
    """Base class for profile persistence errors."""


class ProfileNotFound(ProfileError):
    """Requested profile name doesn't exist on disk."""


class ProfileExists(ProfileError):
    """Attempted to create a profile whose name already exists."""


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------
@dataclass
class Profile:
    """One saved provider configuration.

    Round-trips losslessly through TOML. ``created_at`` / ``updated_at`` are
    ISO-8601 UTC strings, set automatically on save.
    """

    name: str
    provider_name: str
    api_url: str
    api_key_ref: str = "keychain"   # "keychain" or "env:NAME"
    env_var: str | None = None      # populated when api_key_ref == "env:NAME"
    models: list[ModelConfig] = field(default_factory=list)
    set_default_agent: bool = True
    set_inline_assistant: bool = True
    set_commit_message: bool = False
    set_thread_summary: bool = False
    use_edit_predictions: bool = False
    created_at: str = ""
    updated_at: str = ""
    export_env: bool = False        # explicitly selected plaintext shell export

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------
    def to_provider_config(self, api_key: str = "") -> ProviderConfig:
        """Build a :class:`ProviderConfig` from this profile.

        ``api_key`` is passed in (typically read from keychain or env); the
        profile itself does not store the secret.
        """
        return ProviderConfig(
            provider_name=self.provider_name,
            api_url=self.api_url,
            api_key=api_key,
            models=list(self.models),
            set_default_agent=self.set_default_agent,
            set_inline_assistant=self.set_inline_assistant,
            set_commit_message=self.set_commit_message,
            set_thread_summary=self.set_thread_summary,
            use_edit_predictions=self.use_edit_predictions,
            api_key_env=(
                self.env_var or self.api_key_ref.removeprefix("env:")
                if self.api_key_ref.startswith("env:") else None
            ),
        )

    # ------------------------------------------------------------------
    # (De)serialization
    # ------------------------------------------------------------------
    def to_toml(self) -> dict[str, Any]:
        """Return the profile as a TOML-ready dict."""
        out: dict[str, Any] = {
            "name": self.provider_name,
            "api_url": self.api_url,
            "api_key_ref": self.api_key_ref,
            "models": [_model_to_toml(m) for m in self.models],
            "features": {
                "default_agent": self.set_default_agent,
                "inline_assistant": self.set_inline_assistant,
                "commit_message": self.set_commit_message,
                "thread_summary": self.set_thread_summary,
                "edit_predictions": self.use_edit_predictions,
            },
        }
        if self.env_var:
            out["env_var"] = self.env_var
        if self.export_env:
            out["export_env"] = True
        if self.created_at:
            out["created_at"] = self.created_at
        if self.updated_at:
            out["updated_at"] = self.updated_at
        return out

    @classmethod
    def from_toml(cls, profile_name: str, data: dict[str, Any]) -> Profile:
        """Parse a profile dict from TOML."""
        provider_name = data.get("name", profile_name)
        api_url = data.get("api_url", "")
        if not api_url:
            raise ProfileError(
                f"Profile '{profile_name}' is missing api_url."
            )
        api_key_ref = data.get("api_key_ref", "keychain")
        env_var = data.get("env_var")
        if api_key_ref.startswith("env:") and not env_var:
            env_var = api_key_ref[len("env:"):]

        raw_models = data.get("models", [])
        models = [_model_from_toml(m) for m in raw_models if isinstance(m, dict)]

        feats = data.get("features", {}) or {}
        return cls(
            name=profile_name,
            provider_name=provider_name,
            api_url=api_url,
            api_key_ref=api_key_ref,
            env_var=env_var,
            export_env=bool(data.get("export_env", False)),
            models=models,
            set_default_agent=bool(feats.get("default_agent", True)),
            set_inline_assistant=bool(feats.get("inline_assistant", True)),
            set_commit_message=bool(feats.get("commit_message", False)),
            set_thread_summary=bool(feats.get("thread_summary", False)),
            use_edit_predictions=bool(feats.get("edit_predictions", False)),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


def _model_to_toml(model: ModelConfig) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": model.name,
        "context_window": model.max_tokens,
        "max_tokens": model.max_output_tokens,
    }
    if model.display_name:
        entry["name"] = model.display_name
    if model.reasoning_effort:
        entry["reasoning_effort"] = model.reasoning_effort
    if model.capabilities:
        entry["capabilities"] = dict(model.capabilities)
    return entry


def _model_from_toml(data: dict[str, Any]) -> ModelConfig:
    return ModelConfig(
        name=str(data.get("id") or data.get("name") or ""),
        display_name=data.get("name") if "name" in data and data.get("id") else None,
        max_tokens=int(data.get("context_window", 128000)),
        max_output_tokens=int(data.get("max_tokens", 32000)),
        reasoning_effort=data.get("reasoning_effort"),
        capabilities=dict(data.get("capabilities") or {}),
    )


# ---------------------------------------------------------------------------
# Global config.toml (settings + active profile pointer)
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_profile_name(name: str) -> str:
    if not name or not re.match(r"^[A-Za-z0-9 _.\-]+$", name):
        raise ProfileError(
            f"Invalid profile name {name!r}. "
            "Use letters, numbers, spaces, _, ., -."
        )
    return name


def _read_global_config() -> dict[str, Any]:
    p = config_path()
    if not p.exists():
        return {"version": 1, "active_profile": DEFAULT_PROFILE_NAME}
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"Could not read {p}: {exc}") from exc


def _write_global_config(data: dict[str, Any]) -> None:
    ensure_config_dir()
    config_path().write_text(tomli_w.dumps(data), encoding="utf-8")


def get_active_profile_name() -> str:
    """Return the name of the currently active profile (default: ``default``)."""
    return str(_read_global_config().get("active_profile", DEFAULT_PROFILE_NAME))


def set_active_profile_name(name: str) -> None:
    """Set the active profile pointer in ``config.toml``."""
    _validate_profile_name(name)
    cfg = _read_global_config()
    cfg["active_profile"] = name
    _write_global_config(cfg)


# ---------------------------------------------------------------------------
# Per-profile CRUD
# ---------------------------------------------------------------------------
def _profile_path(name: str) -> Path:
    _validate_profile_name(name)
    return profiles_dir() / f"{name}.toml"


def profile_exists(name: str) -> bool:
    return _profile_path(name).exists()


def list_profiles() -> list[str]:
    """Return profile names sorted alphabetically (active profile first)."""
    if not profiles_dir().exists():
        return []
    active = get_active_profile_name()
    names = sorted(p.stem for p in profiles_dir().glob("*.toml"))
    if active in names:
        names.remove(active)
        names.insert(0, active)
    return names


def load_profile(name: str | None = None) -> Profile:
    """Load a profile by name (or the active one when ``name`` is ``None``)."""
    if name is None:
        name = get_active_profile_name()
    path = _profile_path(name)
    if not path.exists():
        raise ProfileNotFound(
            f"Profile {name!r} not found at {path}. "
            f"Run `byop profile new {name}` to create it."
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"Could not read {path}: {exc}") from exc
    return Profile.from_toml(name, data)


def save_profile(
    profile: Profile,
    *,
    allow_overwrite: bool = False,
    api_key: str = "",
) -> Profile:
    """Persist a profile to disk.

    ``api_key`` is **not** stored — the profile's ``api_key_ref`` field tells
    callers where to fetch the secret at apply time. The argument exists so
    the wizard can pass the user-typed secret in once and have it both
    written to the keychain (via :func:`apply_provider`) and referenced from
    the profile via ``"keychain"``.

    Raises :class:`ProfileExists` if the profile already exists and
    ``allow_overwrite`` is False. Stamps ``created_at`` on first save and
    ``updated_at`` on every save.
    """
    path = _profile_path(profile.name)
    if path.exists() and not allow_overwrite:
        raise ProfileExists(
            f"Profile {profile.name!r} already exists. "
            f"Use `byop profile edit {profile.name}` or pass overwrite=True."
        )
    now = _now()
    if not profile.created_at:
        profile.created_at = now
    profile.updated_at = now
    ensure_config_dir()
    path.write_text(tomli_w.dumps(profile.to_toml()), encoding="utf-8")
    return profile


def delete_profile(name: str) -> None:
    """Delete a profile file. Does NOT touch any target's settings."""
    path = _profile_path(name)
    if not path.exists():
        raise ProfileNotFound(f"Profile {name!r} not found.")
    path.unlink()
    # If we deleted the active profile, reset to default.
    if get_active_profile_name() == name:
        try:
            set_active_profile_name(DEFAULT_PROFILE_NAME)
        except ProfileError:
            pass


# ---------------------------------------------------------------------------
# Convenience: build a Profile from a ProviderConfig
# ---------------------------------------------------------------------------
def profile_from_provider(
    provider: ProviderConfig,
    *,
    name: str = DEFAULT_PROFILE_NAME,
    api_key_ref: str = "keychain",
) -> Profile:
    """Build a Profile from an in-memory ProviderConfig.

    Used by the wizard: after collecting provider info, the wizard builds a
    ProviderConfig and the CLI persists it as a Profile.
    """
    _validate_profile_name(name)
    env_var = None
    if api_key_ref.startswith("env:"):
        env_var = api_key_ref[len("env:"):]
    return Profile(
        name=name,
        provider_name=provider.provider_name,
        api_url=provider.normalized_api_url(),
        api_key_ref=api_key_ref,
        env_var=env_var or provider.env_var_name(),
        models=list(provider.models),
        set_default_agent=provider.set_default_agent,
        set_inline_assistant=provider.set_inline_assistant,
        set_commit_message=provider.set_commit_message,
        set_thread_summary=provider.set_thread_summary,
        use_edit_predictions=provider.use_edit_predictions,
    )


__all__ = [
    "DEFAULT_PROFILE_NAME",
    "Profile",
    "ProfileError",
    "ProfileNotFound",
    "ProfileExists",
    "delete_profile",
    "get_active_profile_name",
    "list_profiles",
    "load_profile",
    "profile_exists",
    "profile_from_provider",
    "save_profile",
    "set_active_profile_name",
]
