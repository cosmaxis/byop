"""Interactive wizard that collects provider info and builds a ProviderConfig."""

from __future__ import annotations

from dataclasses import replace

from . import default_model_capabilities, prompt
from .config import ModelConfig, ProviderConfig
from .paste import REQUIRED_KEYS, looks_like_provider_paste, parse_provider_paste


def _ask_models(provider_display: str) -> list[ModelConfig]:
    models: list[ModelConfig] = []
    prompt.info("Models (first is default)")
    while True:
        name = prompt.ask("Model ID")
        if not name:
            if models:
                break
            prompt.warn("At least one model is required.")
            continue

        display = prompt.ask(
            "Display name (optional)", default="", allow_empty=True
        )
        max_tokens = prompt.ask_int("Context window", default=128000)
        max_out = prompt.ask_int(
            "Max output tokens", default=32000
        )
        reasoning: str = prompt.ask(
            "Reasoning effort (none/minimal/low/medium/high/xhigh/max, blank=off)",
            default="",
            allow_empty=True,
        )
        reasoning_value: str | None = reasoning or None
        caps = default_model_capabilities()
        if prompt.confirm("Supports images?", default=False):
            caps["images"] = True
        if prompt.confirm("Streams thinking as reasoning_content?",
                          default=False):
            caps["interleaved_reasoning"] = True
        if prompt.confirm("Requires max_tokens instead of max_completion_tokens?",
                          default=False):
            caps["max_tokens_parameter"] = True

        models.append(
            ModelConfig(
                name=name,
                display_name=display or None,
                max_tokens=max_tokens,
                max_output_tokens=max_out,
                reasoning_effort=reasoning_value,
                capabilities=caps,
            )
        )
        if not prompt.confirm("Add another model?", default=False):
            break
    return models


def run_wizard(prefill: ProviderConfig | None = None) -> tuple[ProviderConfig, dict]:
    """Drive the interactive prompts.

    When ``prefill`` is supplied (typically from a saved profile), the wizard
    pre-fills provider/api_url/models and only prompts for fields the user
    wants to override. Returns the configured :class:`ProviderConfig` and a
    dict with the chosen key-storage preferences (``use_keychain`` /
    ``use_env``).
    """

    prompt.info("Provider")

    # First prompt: paste JSON or press Enter to fill in fields. We try to
    # detect a paste heuristically (multi-line, parseable JSON), and then only
    # ask for fields the paste didn't supply.
    pasted_prefill: ProviderConfig | None = None
    missing: list[str] = []
    pasted = prompt.ask(
        "Paste provider JSON or press Enter",
        default="",
        allow_empty=True,
    )
    if pasted and looks_like_provider_paste(pasted):
        try:
            pasted_prefill, missing = parse_provider_paste(pasted)
        except ValueError as exc:
            prompt.warn(f"Could not parse pasted JSON ({exc}) — "
                        f"falling back to per-field prompts.")
            pasted_prefill = None
            missing = list(REQUIRED_KEYS)

    # Determine which fields are "known" from a paste. A paste that supplies
    # all fields short-circuits the per-field prompts entirely.
    if pasted_prefill is not None:
        prefill = pasted_prefill

    def _have(field: str) -> bool:
        if pasted_prefill is not None:
            return field not in missing
        return prefill is not None
    def _present(field: str) -> bool:
        """True when prefill has a non-empty value for this field."""
        if not _have(field) or prefill is None:
            return False
        return bool(getattr(prefill, field, None))

    if prefill is not None and _present("provider_name"):
        if pasted_prefill is not None:
            # Complete paste: use values directly, no re-confirmation.
            provider_name = prefill.provider_name
        else:
            # Saved-profile prefill: confirm so the user can override.
            provider_name = prompt.ask(
                "Provider name (Enter to keep)",
                default=prefill.provider_name,
            )
    else:
        provider_name = prompt.ask("Provider name (e.g. 'Cosmaxis')")

    if prefill is not None and _present("api_url"):
        if pasted_prefill is not None:
            api_url = prefill.api_url
        else:
            api_url = prompt.ask(
                "API base URL (Enter to keep)", default=prefill.api_url
            )
    else:
        api_url = prompt.ask(
            "API URL (e.g. https://api.example.com/v1)"
        )

    # api_key: only ask when missing. For a complete paste the user already
    # typed it once; for a saved profile we treat the stored secret as
    # authoritative (the user can rotate via `byop profile edit`).
    if prefill is not None and _present("api_key") and pasted_prefill is not None:
        api_key = prefill.api_key
    elif prefill is not None and _present("api_key"):
        # Saved profile — re-prompt without echoing; empty input keeps stored secret.
        prompt.info("  API key: (loaded — press Enter to keep)")
        api_key = prompt.ask_secret("API key") or prefill.api_key
    else:
        api_key = prompt.ask_secret("API key")

    if prefill is not None and _present("models") and prefill.models:
        if pasted_prefill is not None:
            models = list(prefill.models)
        else:
            prompt.info(
                f"  Models: {', '.join(m.name for m in prefill.models)}"
            )
            if prompt.confirm("Reconfigure models?", default=False):
                models = _ask_models(provider_name)
            else:
                models = list(prefill.models)
    else:
        models = _ask_models(provider_name)


    prompt.info("Key storage")
    use_keychain = prompt.confirm(
        "Store key in macOS Keychain?", default=True
    )
    use_env = prompt.confirm(
        "Also export key in shell profile (plaintext)?",
        default=not use_keychain,
    )

    provider = ProviderConfig(
        provider_name=provider_name,
        api_url=api_url,
        api_key=api_key,
        models=models,
        set_default_agent=prefill.set_default_agent if prefill else True,
        set_inline_assistant=prefill.set_inline_assistant if prefill else True,
        set_commit_message=prefill.set_commit_message if prefill else False,
        set_thread_summary=prefill.set_thread_summary if prefill else False,
        use_edit_predictions=prefill.use_edit_predictions if prefill else False,
    )
    return provider, {"use_keychain": use_keychain, "use_env": use_env}


def configure_zed_features(provider: ProviderConfig) -> ProviderConfig:
    """Ask about Zed-specific features only when setting up Zed."""
    prompt.info("Zed settings")
    return replace(
        provider,
        set_default_agent=prompt.confirm(
            "Set as the default Agent model?", default=provider.set_default_agent
        ),
        set_inline_assistant=prompt.confirm(
            "Set as the Inline Assistant model?", default=provider.set_inline_assistant
        ),
        set_commit_message=prompt.confirm(
            "Use for Git commit messages?", default=provider.set_commit_message
        ),
        set_thread_summary=prompt.confirm(
            "Use for thread summaries?", default=provider.set_thread_summary
        ),
        use_edit_predictions=prompt.confirm(
            "Enable Edit Predictions (autocomplete) with this provider?",
            default=provider.use_edit_predictions,
        ),
    )
