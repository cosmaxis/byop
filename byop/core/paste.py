"""Detect and parse pasted provider JSON.

Used by the interactive wizard: when the first prompt is a multi-line blob
that looks like JSON, parse it into a :class:`ProviderConfig` instead of
asking each field one by one.

We accept strict JSON *and* JSONC (with ``//`` line comments and ``/* ... */``
block comments) so users can copy a fragment out of an existing
``settings.json`` without manually stripping comments first.
"""

from __future__ import annotations

import json
from typing import Any

from .config import DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_MAX_TOKENS, ModelConfig, ProviderConfig


def _strip_jsonc_comments(text: str) -> str:
    """Remove C-style comments while respecting quoted strings.

    Matches the same minimal grammar used elsewhere in byop:
      ``// line comments`` and ``/* block comments */``, both stripped
      only when not inside a string literal. Trailing commas are not
      handled here — those would need a full JSONC parser and aren't
      common in pasted snippets.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    quote = ""
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                in_str = False
            i += 1
            continue
        if c in ('"', "'"):
            in_str = True
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2  # skip the closing */
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _safe_loads(text: str) -> Any:
    """Load JSON, transparently stripping JSONC comments first."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_strip_jsonc_comments(text))


def looks_like_provider_paste(text: str) -> bool:
    """True if ``text`` looks like a JSON/JSONC provider object rather than a plain field."""
    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return False
    try:
        parsed = _safe_loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(parsed, dict)

def _coerce_model(raw: dict[str, Any]) -> ModelConfig:
    """Normalize canonical, Pi, and OpenCode model limit fields."""
    name = raw.get("id") or raw.get("name")
    if not name:
        raise ValueError("model entry missing 'name' (or 'id')")

    raw_limits = raw.get("limit")
    limits: dict[str, Any] = raw_limits if isinstance(raw_limits, dict) else {}
    context = raw.get("context_window", raw.get("contextWindow", limits.get("context")))
    max_tokens: Any = context if context is not None else raw.get("max_tokens", DEFAULT_MAX_TOKENS)
    output_default = raw.get("max_tokens", DEFAULT_MAX_OUTPUT_TOKENS) if context is not None else DEFAULT_MAX_OUTPUT_TOKENS
    max_output_tokens: Any = raw.get(
        "max_output_tokens",
        raw.get("max_tokens_out", raw.get("maxTokens", limits.get("output", output_default))),
    )
    display = raw.get("display_name") or (raw.get("name") if raw.get("id") else None)
    reasoning = raw.get("reasoning_effort")
    caps_raw = raw.get("capabilities")
    capabilities: dict[str, Any] = dict(caps_raw) if isinstance(caps_raw, dict) else {}
    if isinstance(raw.get("input"), list):
        capabilities.setdefault("images", "image" in raw["input"])
    if isinstance(raw.get("attachment"), bool):
        capabilities.setdefault("images", raw["attachment"])
    if isinstance(raw.get("tool_call"), bool):
        capabilities.setdefault("tools", raw["tool_call"])
    try:
        max_tokens = int(max_tokens)
        max_output_tokens = int(max_output_tokens)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Model context and output limits must be integers.") from None
    return ModelConfig(
        name=name,
        display_name=display,
        max_tokens=max_tokens,
        max_output_tokens=max_output_tokens,
        reasoning_effort=reasoning,
        capabilities=capabilities,
    )


# Field names the user must supply before we can actually configure anything.
REQUIRED_KEYS = ("provider_name", "api_url", "api_key", "models")

# Sentinel object used to mark fields that were absent from the paste. We
# deliberately do NOT bake placeholder strings into the returned ProviderConfig
# — anything caller-side can substring-match against would also be a real
# value a user could legitimately paste (the prior "sk-placeholder-key"
# approach was vulnerable to that).
_MISSING = object()


def _missing_str() -> str:
    """Distinct, unguessable placeholder string used only when no other value
    is available. Callers must check the ``missing`` list — never compare
    against the literal text — before considering a field "present".
    """
    return "byop:missing-field"


def parse_provider_paste(text: str) -> tuple[ProviderConfig, list[str]]:
    """Parse a pasted JSON provider description.

    Returns ``(ProviderConfig, missing_fields)``. The config has placeholder
    values for any missing field so callers can identify which fields to
    prompt for. ``missing_fields`` is the authoritative "what's missing"
    list — the placeholder string is only there so the dataclass is still
    constructible.

    JSONC (with ``//`` and ``/* */`` comments) is accepted so users can paste
    a fragment verbatim from an existing settings.json file.
    """
    try:
        data = _safe_loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Could not parse pasted JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Pasted content must be a JSON object.")

    missing: list[str] = []
    provider_name = data.get("provider_name") or data.get("name")
    if not provider_name:
        missing.append("provider_name")

    api_url = data.get("api_url") or data.get("apiUrl") or data.get("base_url") or data.get("baseUrl")
    if not api_url:
        missing.append("api_url")

    api_key = data.get("api_key") or data.get("apiKey")
    if not api_key:
        missing.append("api_key")

    raw_models = data.get("models")
    models: list[ModelConfig]
    if raw_models is None or raw_models == [] or raw_models == {}:
        missing.append("models")
        models = [ModelConfig(name=_missing_str())]
    elif isinstance(raw_models, list):
        models = []
        for model in raw_models:
            if isinstance(model, str) and model.strip():
                models.append(ModelConfig(name=model.strip()))
            elif isinstance(model, dict):
                models.append(_coerce_model(model))
            else:
                raise ValueError("Each model must be an ID string or an object.")
    elif isinstance(raw_models, dict):
        models = []
        for model_id, details in raw_models.items():
            if not isinstance(details, dict):
                raise ValueError("Each model in the models object must be an object.")
            model = {**details, "id": model_id}
            if "display_name" not in model and details.get("name"):
                model["display_name"] = details["name"]
            models.append(_coerce_model(model))
    else:
        raise ValueError("Models must be an array or an object keyed by model ID.")

    cfg = ProviderConfig(
        provider_name=provider_name or _missing_str(),
        api_url=api_url or _missing_str(),
        api_key=api_key or _missing_str(),
        models=models,
    )
    return cfg, missing


__all__ = ["looks_like_provider_paste", "parse_provider_paste"]
