"""Interactive wizard that collects provider info with progressive disclosure."""

from __future__ import annotations

from dataclasses import replace

from . import default_model_capabilities, prompt
from .config import ModelConfig, ProviderConfig
from .paste import looks_like_provider_paste, parse_provider_paste

_REASONING_OPTIONS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]
_CAPABILITY_OPTIONS = [
    ("Image input", "images"),
    ("Interleaved reasoning", "interleaved_reasoning"),
    ("Legacy max_tokens field", "max_tokens_parameter"),
]
_KEY_STORAGE_OPTIONS = [
    "macOS Keychain",
    "Keychain + shell env",
    "Shell env only (plaintext)",
]
_ZED_ROLES = [
    ("Default agent", "set_default_agent"),
    ("Inline assistant", "set_inline_assistant"),
    ("Git commit messages", "set_commit_message"),
    ("Thread summaries", "set_thread_summary"),
    ("Edit predictions", "use_edit_predictions"),
]


def _customize_model(model: ModelConfig) -> ModelConfig:
    prefix = f"{model.name} · "
    display = prompt.ask(
        f"{prefix}Display name",
        default=model.display_name or "",
        allow_empty=True,
        hint="optional",
    )
    max_tokens = prompt.ask_int(f"{prefix}Context window", default=model.max_tokens)
    max_out = prompt.ask_int(f"{prefix}Max output tokens", default=model.max_output_tokens)
    cur_reasoning = model.reasoning_effort or "off"
    r_idx = _REASONING_OPTIONS.index(cur_reasoning) if cur_reasoning in _REASONING_OPTIONS else 0
    chosen_reasoning = prompt.choose(
        f"{prefix}Reasoning effort",
        _REASONING_OPTIONS,
        default_index=r_idx,
    )
    reasoning_value = None if chosen_reasoning == "off" else chosen_reasoning

    caps = {**default_model_capabilities(), **model.capabilities}
    cap_defaults = [i for i, (_, key) in enumerate(_CAPABILITY_OPTIONS) if caps.get(key)]
    chosen_caps = set(
        prompt.select_many(
            f"{prefix}Capabilities",
            [label for label, _ in _CAPABILITY_OPTIONS],
            defaults=cap_defaults,
        )
    )
    for i, (_, key) in enumerate(_CAPABILITY_OPTIONS):
        caps[key] = i in chosen_caps

    return ModelConfig(
        name=model.name,
        display_name=display or None,
        max_tokens=max_tokens,
        max_output_tokens=max_out,
        reasoning_effort=reasoning_value,
        capabilities=caps,
    )


def _ask_models(existing: list[ModelConfig] | None = None) -> list[ModelConfig]:
    existing_by_name = {m.name: m for m in (existing or [])}
    default_str = ", ".join(existing_by_name.keys())
    while True:
        raw = prompt.ask(
            "Models",
            default=default_str,
            hint="comma-separated, first is default",
        )
        names = list(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
        if names:
            break
        prompt.warn("Enter at least one model ID.")

    models = [
        existing_by_name[name]
        if name in existing_by_name
        else ModelConfig(name=name, capabilities=default_model_capabilities())
        for name in names
    ]
    if prompt.confirm("Advanced model options?", default=False):
        models = [_customize_model(m) for m in models]
    return models


def run_wizard(prefill: ProviderConfig | None = None) -> tuple[ProviderConfig, dict]:
    """Collect provider configuration interactively with minimal prompts."""
    pasted_prefill: ProviderConfig | None = None
    missing: list[str] = []

    default_name = prefill.provider_name if prefill and prefill.provider_name else ""
    first = prompt.ask(
        "Provider name",
        default=default_name,
        hint="" if default_name else "or paste JSON",
        allow_paste=True,
    )
    if first.lstrip().startswith("{"):
        if looks_like_provider_paste(first):
            try:
                pasted_prefill, missing = parse_provider_paste(first)
                prefill = pasted_prefill
            except ValueError:
                prompt.warn("Invalid provider JSON — entering fields manually.")
                first = ""
        else:
            prompt.warn("Could not parse pasted JSON — entering fields manually.")
            first = ""
    if pasted_prefill is not None:
        provider_name = (
            pasted_prefill.provider_name
            if "provider_name" not in missing
            else prompt.ask("Provider name")
        )
        api_url = (
            pasted_prefill.api_url
            if "api_url" not in missing
            else prompt.ask("API URL", hint="https://api.example.com/v1")
        )
        api_key = (
            pasted_prefill.api_key
            if "api_key" not in missing
            else prompt.ask_secret("API key")
        )
        models = (
            list(pasted_prefill.models)
            if "models" not in missing and pasted_prefill.models
            else _ask_models()
        )
    else:
        provider_name = first or prompt.ask("Provider name", default=default_name)
        default_url = prefill.api_url if prefill else ""
        api_url = prompt.ask(
            "API URL",
            default=default_url,
            hint="" if default_url else "https://api.example.com/v1",
        )
        if prefill is not None and prefill.api_key:
            api_key = (
                prompt.ask_secret(
                    "API key",
                    hint="Enter to keep stored key",
                    allow_empty=True,
                )
                or prefill.api_key
            )
        else:
            api_key = prompt.ask_secret("API key")
        models = _ask_models(prefill.models if prefill else None)

    storage = prompt.choose("Key storage", _KEY_STORAGE_OPTIONS)
    use_keychain = storage != "Shell env only (plaintext)"
    use_env = storage != "macOS Keychain"

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
    """Ask about Zed-specific roles in one multi-select checklist."""
    defaults = [i for i, (_, attr) in enumerate(_ZED_ROLES) if getattr(provider, attr)]
    chosen = set(
        prompt.select_many(
            "Zed roles",
            [label for label, _ in _ZED_ROLES],
            defaults=defaults,
        )
    )
    return replace(
        provider,
        set_default_agent=0 in chosen,
        set_inline_assistant=1 in chosen,
        set_commit_message=2 in chosen,
        set_thread_summary=3 in chosen,
        use_edit_predictions=4 in chosen,
    )
