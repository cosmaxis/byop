"""OpenCode target: wires a custom OpenAI-compatible provider into OpenCode.

OpenCode (https://opencode.ai) stores its configuration at
``~/.config/opencode/opencode.json`` (or ``opencode.jsonc``). Providers live
under the top-level ``provider`` key, keyed by a user-chosen name.

OpenCode supports ``{env:VAR}`` and ``{file:path}`` variable substitution in
``opencode.json`` — NOT ``!command`` shell execution. To keep plaintext secrets
out of ``opencode.json``, we embed ``{env:<PROVIDER>_API_KEY}`` and export that
variable dynamically from the macOS login keychain in the user's shell profile.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from .. import keychain as kc
from .. import settings as sett
from ..config import ModelConfig, ProviderConfig

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "opencode" / "opencode.json"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


class OpencodeTarget:
    """Configures OpenCode with a custom OpenAI-compatible provider."""

    name = "opencode"
    display_name = "OpenCode"

    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.config_path = config_path

    # ------------------------------------------------------------------
    def is_installed(self) -> bool:
        return self.config_path.parent.exists() or shutil.which("opencode") is not None

    def install(self, log: Callable[[str], None] = print) -> None:
        if shutil.which("brew") is not None:
            log("Installing OpenCode via Homebrew...")
            res = _run(["brew", "install", "--cask", "opencode"])
            if res.returncode == 0:
                log("OpenCode installed via Homebrew.")
                return
            log(
                f"brew install failed: {res.stderr or res.stdout}; "
                f"falling back to the published installer."
            )
        log("Install OpenCode with one of:")
        log("  brew install --cask opencode")
        log("  curl -fsSL https://opencode.ai/install | sh")
        log("then re-run byop.")

    # ------------------------------------------------------------------
    def _api_key_ref(
        self,
        provider: ProviderConfig,
        *,
        use_keychain: bool | None = None,
        use_env: bool = False,
        redact: bool = False,
    ) -> str:
        """Return the ``apiKey`` value to embed in ``opencode.json``.

        OpenCode interpolates ``{env:VAR_NAME}`` at runtime. Use that reference
        whenever Keychain (backed by a dynamic shell export) or ``use_env`` is
        active; fall back to literal key (or ``[REDACTED]`` in dry-run) only
        when neither is available.
        """
        env_ref = f"{{env:{provider.env_var_name()}}}"
        if use_keychain is True or use_env:
            return env_ref
        if use_keychain is None and kc.keychain_has(provider.keychain_server()):
            return env_ref
        return "[REDACTED]" if redact else provider.api_key

    def _model_entry(self, model: ModelConfig) -> dict:
        entry: dict = {
            "name": model.display_name or model.name,
            "tool_call": True,
            "temperature": True,
            "attachment": model.capabilities.get("images", False),
        }
        if model.reasoning_effort and model.reasoning_effort != "none":
            entry["reasoning"] = True
        if model.capabilities.get("interleaved_reasoning"):
            entry["interleaved"] = {"field": "reasoning_content"}
        return entry

    def build_fragment(
        self,
        provider: ProviderConfig,
        *,
        use_keychain: bool | None = None,
        use_env: bool = False,
        redact: bool = False,
    ) -> dict:
        provider_block: dict = {
            "name": provider.provider_name,
            "npm": "@ai-sdk/openai-compatible",
            "options": {
                "baseURL": provider.normalized_api_url(),
                "apiKey": self._api_key_ref(
                    provider,
                    use_keychain=use_keychain,
                    use_env=use_env,
                    redact=redact,
                ),
            },
            "models": {
                m.name: self._model_entry(m) for m in provider.models
            },
        }
        return {"provider": {provider.provider_name: provider_block}}

    # ------------------------------------------------------------------
    def configure(
        self,
        provider: ProviderConfig,
        *,
        dry_run: bool = False,
        use_keychain: bool = True,
        use_env: bool = False,
        conflict_action: str | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        errors = provider.validate()
        if errors:
            raise ValueError(
                "Invalid provider configuration:\n  - "
                + "\n  - ".join(errors)
            )

        if dry_run:
            fragment = self.build_fragment(
                provider,
                use_keychain=use_keychain,
                use_env=use_env,
                redact=True,
            )
            log("[dry-run] OpenCode would write the following opencode.json fragment:")
            log(json.dumps(fragment, indent=2))
            return

        existing = self._load()
        existing_providers = existing.get("provider") or {}
        name_to_write = provider.provider_name
        if provider.provider_name in existing_providers:
            if conflict_action == "skip":
                log(
                    f"Skip: OpenCode already has {provider.provider_name}; "
                    f"leaving {self.config_path} untouched."
                )
                if use_keychain or use_env:
                    kc.ensure_key(
                        server=provider.keychain_server(),
                        api_key=provider.api_key,
                        env_var=provider.env_var_name(),
                        log=log,
                        use_keychain=use_keychain,
                        use_env=use_env,
                        keychain_env=use_keychain,
                    )
                return
            if conflict_action == "append":
                i = 2
                while f"{provider.provider_name}_{i}" in existing_providers:
                    i += 1
                name_to_write = f"{provider.provider_name}_{i}"
                log(
                    f"Append: OpenCode already has {provider.provider_name}; "
                    f"writing under {name_to_write}."
                )

        if use_keychain or use_env:
            kc.ensure_key(
                server=provider.keychain_server(),
                api_key=provider.api_key,
                env_var=provider.env_var_name(),
                log=log,
                use_keychain=use_keychain,
                use_env=use_env,
                keychain_env=use_keychain,
            )

        fragment = self.build_fragment(
            provider,
            use_keychain=False if not use_keychain else None,
            use_env=use_env,
        )
        new_block = fragment["provider"][provider.provider_name]

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        current = self._load()
        providers = current.setdefault("provider", {})
        providers[name_to_write] = new_block
        self._write(current)
        log(f"Updated OpenCode config at {self.config_path}")
        if new_block["options"]["apiKey"].startswith("{env:"):
            log(
                f"API key referenced via {new_block['options']['apiKey']} "
                f"(open a new terminal or source your shell profile before launching OpenCode)."
            )
        else:
            log(
                "Warning: --no-keychain was set without env export, so the API "
                "key is embedded in opencode.json in plaintext."
            )

    # ------------------------------------------------------------------
    def _load(self) -> dict:
        if not self.config_path.exists():
            return {}
        try:
            return sett.load_path(self.config_path)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Could not parse {self.config_path}: {exc}"
            ) from exc

    def _write(self, data: dict) -> None:
        self.config_path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )

    def current_provider_names(self) -> list[str]:
        data = self._load()
        block = data.get("provider", {})
        return list(block.keys()) if isinstance(block, dict) else []

    def export_config(self) -> dict:
        """Return the byop-managed slice of opencode.json (top-level ``provider``)."""
        data = self._load()
        providers = data.get("provider", {})
        return {
            self.name: {
                "config_path": str(self.config_path),
                "providers": dict(providers) if isinstance(providers, dict) else {},
            }
        }
