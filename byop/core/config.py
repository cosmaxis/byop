"""Provider configuration model and validation for byop.

This module defines the :class:`ProviderConfig` dataclass that captures all
information needed to wire a custom OpenAI-compatible LLM provider into Zed,
plus the pure functions that turn it into the relevant fragments of Zed's
``settings.json`` and the environment variable name used for the API key.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

# Capabilities that Zed understands for OpenAI-compatible models.
KNOWN_CAPABILITIES = {
    "tools",
    "images",
    "parallel_tool_calls",
    "prompt_cache_key",
    "chat_completions",
    "interleaved_reasoning",
    "max_tokens_parameter",
}

# Valid reasoning_effort values accepted by Zed.
KNOWN_REASONING_EFFORTS = {
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
}

DEFAULT_MAX_TOKENS = 1000000
DEFAULT_MAX_OUTPUT_TOKENS = 128000
DEFAULT_REASONING_EFFORT = "medium"


@dataclass
class ModelConfig:
    """A single model exposed by a provider."""

    name: str
    display_name: str | None = None
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    reasoning_effort: str | None = DEFAULT_REASONING_EFFORT
    capabilities: Mapping[str, bool | int | str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a Zed ``available_models`` entry."""
        entry: dict = {
            "name": self.name,
            "max_tokens": self.max_tokens,
            "max_output_tokens": self.max_output_tokens,
        }
        if self.display_name:
            entry["display_name"] = self.display_name
        if self.reasoning_effort:
            entry["reasoning_effort"] = self.reasoning_effort
        if self.capabilities:
            entry["capabilities"] = dict(self.capabilities)
        return entry


@dataclass
class ProviderConfig:
    """Complete description of a custom OpenAI-compatible provider."""

    provider_name: str
    api_url: str
    api_key: str
    models: list[ModelConfig] = field(default_factory=list)
    set_inline_assistant: bool = True
    set_default_agent: bool = True
    set_commit_message: bool = False
    set_thread_summary: bool = False
    use_edit_predictions: bool = False
    api_key_env: str | None = None

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self) -> list[str]:
        """Return a list of human-readable validation error messages."""
        errors: list[str] = []

        if not self.provider_name:
            errors.append("Provider name must not be empty.")
        elif not re.match(r"^[A-Za-z0-9 _.\-]+$", self.provider_name):
            errors.append(
                "Provider name may only contain letters, numbers, spaces, "
                "and the characters _ . -"
            )

        if not self.api_url:
            errors.append("API URL must not be empty.")
        else:
            parsed = urlparse(self.api_url)
            if parsed.scheme not in ("http", "https"):
                errors.append("API URL must start with http:// or https://")
            if not parsed.netloc:
                errors.append("API URL is missing a host.")

        if not self.api_key:
            errors.append("API key must not be empty.")
        elif len(self.api_key) < 8:
            errors.append("API key looks too short to be valid.")

        if self.api_key_env is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env):
            errors.append("API key environment variable name is invalid.")

        if not self.models:
            errors.append("At least one model must be configured.")

        for model in self.models:
            if not model.name:
                errors.append("A model name must not be empty.")
            if model.max_tokens <= 0:
                errors.append(f"Model '{model.name}' max_tokens must be > 0.")
            if model.max_output_tokens <= 0:
                errors.append(
                    f"Model '{model.name}' max_output_tokens must be > 0."
                )
            if model.reasoning_effort and (
                model.reasoning_effort not in KNOWN_REASONING_EFFORTS
            ):
                errors.append(
                    f"Model '{model.name}' has invalid reasoning_effort "
                    f"'{model.reasoning_effort}'. Valid: "
                    f"{sorted(KNOWN_REASONING_EFFORTS)}"
                )
            for cap in model.capabilities:
                if cap not in KNOWN_CAPABILITIES:
                    errors.append(
                        f"Model '{model.name}' has unknown capability "
                        f"'{cap}'."
                    )

        return errors

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    def normalized_api_url(self) -> str:
        """Return the API URL without a trailing slash (Zed is picky)."""
        return self.api_url.rstrip("/")

    def env_var_name(self) -> str:
        """Return the Zed-derived API key environment variable name.

        Zed derives the key env var from the provider name as upper snake
        case plus ``_API_KEY`` (e.g. ``My Provider`` -> ``MY_PROVIDER_API_KEY``).
        """
        snake = re.sub(r"[^A-Za-z0-9]+", "_", self.provider_name)
        snake = snake.strip("_").upper()
        return f"{snake}_API_KEY"

    def keychain_server(self) -> str:
        """Return the keychain 'server' field Zed uses for the key."""
        return self.normalized_api_url()

    def language_models_block(self) -> dict:
        """Return the ``language_models.openai_compatible`` entry."""
        return {
            self.provider_name: {
                "api_url": self.normalized_api_url(),
                "available_models": [m.to_dict() for m in self.models],
            }
        }

    def agent_block(self) -> dict:
        """Return the ``agent`` settings fragment for feature models."""
        agent: dict = {}
        primary = self.models[0]
        if self.set_default_agent:
            agent["default_model"] = {
                "provider": self.provider_name,
                "model": primary.name,
            }
        if self.set_inline_assistant:
            agent["inline_assistant_model"] = {
                "provider": self.provider_name,
                "model": primary.name,
            }
        if self.set_commit_message:
            agent["commit_message_model"] = {
                "provider": self.provider_name,
                "model": primary.name,
            }
        if self.set_thread_summary:
            agent["thread_summary_model"] = {
                "provider": self.provider_name,
                "model": primary.name,
            }
        return agent

    def edit_predictions_block(self) -> dict | None:
        """Return the ``edit_predictions`` fragment, if enabled."""
        if not self.use_edit_predictions:
            return None
        primary = self.models[0]
        return {
            "provider": "openai_compatible",
            "open_ai_compatible_api": {
                "api_url": self.normalized_api_url(),
                "model": primary.name,
            },
            "mode": "eager",
            "allow_data_collection": "no",
        }
