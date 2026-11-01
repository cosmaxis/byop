"""Claude Code target: wires an Anthropic-compatible gateway into ~/.claude/settings.json."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from .. import keychain as kc
from ..config import ProviderConfig

DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Legacy keys written by older byop versions that Claude Code ignores.
_LEGACY_KEYS = ("provider", "apiBaseUrl", "apiKey", "models")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def anthropic_base_url(api_url: str) -> str:
    """Normalize a provider URL for ``ANTHROPIC_BASE_URL``.

    Claude Code posts to ``$ANTHROPIC_BASE_URL/v1/messages``, so a trailing
    ``/v1`` segment common to OpenAI-compatible URLs must be stripped to avoid
    hitting ``/v1/v1/messages``.
    """
    normalized = api_url.rstrip("/")
    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/")
    if path == "/v1" or path.endswith("/v1"):
        path = path[:-3]
    return urlunparse(parsed._replace(path=path))


class ClaudeTarget:
    name = "claude"
    display_name = "Claude Code"

    def __init__(self, settings_path: Path = DEFAULT_SETTINGS_PATH) -> None:
        self.settings_path = settings_path

    # ------------------------------------------------------------------
    # Install / detection
    # ------------------------------------------------------------------
    def is_installed(self) -> bool:
        return shutil.which("claude") is not None

    def install(self, log: Callable[[str], None] = print) -> None:
        if shutil.which("brew") is not None:
            log("Installing Claude Code via Homebrew...")
            res = _run(["brew", "install", "--cask", "claude-code"])
            if res.returncode == 0:
                log("Claude Code installed via Homebrew.")
                return
            log(f"brew install failed: {res.stderr or res.stdout}; trying npm.")

        if shutil.which("npm") is not None:
            log("Installing Claude Code via npm...")
            res = _run(["npm", "install", "-g", "@anthropic-ai/claude-code"])
            if res.returncode == 0:
                log("Claude Code installed via npm.")
                return
            log(f"npm install failed: {res.stderr or res.stdout}; falling back to curl.")

        log("Install Claude Code with one of:")
        log("  brew install --cask claude-code")
        log("  npm install -g @anthropic-ai/claude-code")
        log("  curl -fsSL https://claude.ai/install.sh | sh")

    # ------------------------------------------------------------------
    # Provider fragment + keychain-aware apiKeyHelper
    # ------------------------------------------------------------------
    def _api_key_helper(
        self,
        provider: ProviderConfig,
        *,
        use_keychain: bool | None = None,
    ) -> str | None:
        """Return ``apiKeyHelper`` shell command when Keychain holds the key."""
        server = provider.keychain_server()
        if use_keychain is True:
            return kc.security_command(server)
        if use_keychain is None and kc.keychain_has(server):
            return kc.security_command(server)
        return None

    def build_fragment(
        self,
        provider: ProviderConfig,
        *,
        use_keychain: bool | None = None,
        redact: bool = False,
    ) -> dict:
        primary = provider.models[0]
        env: dict[str, str] = {
            "ANTHROPIC_BASE_URL": anthropic_base_url(provider.normalized_api_url()),
            "BYOP_PROVIDER": provider.provider_name,
            "ANTHROPIC_CUSTOM_MODEL_OPTION": primary.name,
            "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME": primary.display_name or primary.name,
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(primary.max_tokens),
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(primary.max_output_tokens),
        }
        fragment: dict = {
            "env": env,
            "model": primary.name,
        }
        helper = self._api_key_helper(provider, use_keychain=use_keychain)
        if helper is not None:
            fragment["apiKeyHelper"] = helper
        else:
            env["ANTHROPIC_AUTH_TOKEN"] = "[REDACTED]" if redact else provider.api_key
        return fragment

    # ------------------------------------------------------------------
    # Configure
    # ------------------------------------------------------------------
    def _load(self) -> dict:
        if not self.settings_path.exists():
            return {}
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Could not parse {self.settings_path}: {exc}"
            ) from exc

    def _write(self, data: dict) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _extract_provider_name(data: dict) -> str | None:
        env = data.get("env")
        if isinstance(env, dict):
            name = env.get("BYOP_PROVIDER")
            if isinstance(name, str) and name:
                return name
        legacy = data.get("provider")
        if isinstance(legacy, str) and legacy:
            return legacy
        return None

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
                provider, use_keychain=use_keychain, redact=True
            )
            log(
                "[dry-run] Claude Code would write the following settings "
                "fragment:"
            )
            log(json.dumps(fragment, indent=2))
            return

        target_base_url = anthropic_base_url(provider.normalized_api_url())
        if conflict_action == "skip":
            existing = self._load()
            raw_env = existing.get("env")
            existing_env = raw_env if isinstance(raw_env, dict) else {}
            same = (
                self._extract_provider_name(existing) == provider.provider_name
                and existing_env.get("ANTHROPIC_BASE_URL") == target_base_url
            )
            if same:
                if use_keychain or use_env:
                    kc.ensure_key(
                        server=provider.keychain_server(),
                        api_key=provider.api_key,
                        env_var=provider.env_var_name(),
                        log=log,
                        use_keychain=use_keychain,
                        use_env=use_env,
                    )
                log(
                    f"Skip: Claude Code already has {provider.provider_name} "
                    f"with the same ANTHROPIC_BASE_URL."
                )
                return

        # Store key BEFORE build_fragment so Keychain detection sees fresh entries.
        if use_keychain or use_env:
            kc.ensure_key(
                server=provider.keychain_server(),
                api_key=provider.api_key,
                env_var=provider.env_var_name(),
                log=log,
                use_keychain=use_keychain,
                use_env=use_env,
            )
        fragment = self.build_fragment(
            provider, use_keychain=False if not use_keychain else None
        )

        current = self._load()
        merged = {k: v for k, v in current.items() if k not in _LEGACY_KEYS}
        current_env = current.get("env")
        merged_env = dict(current_env) if isinstance(current_env, dict) else {}
        merged_env.pop("ANTHROPIC_API_KEY", None)
        if "apiKeyHelper" in fragment:
            merged_env.pop("ANTHROPIC_AUTH_TOKEN", None)
            merged["apiKeyHelper"] = fragment["apiKeyHelper"]
        else:
            merged.pop("apiKeyHelper", None)
        merged_env.update(fragment["env"])
        merged["env"] = merged_env
        merged["model"] = fragment["model"]

        self._write(merged)
        log(f"Updated Claude Code settings at {self.settings_path}")
        log(
            "Note: Claude Code uses the Anthropic Messages API "
            f"({target_base_url}/v1/messages); ensure your gateway exposes it."
        )
        if "apiKeyHelper" not in fragment:
            log(
                "Warning: --no-keychain was set, so ANTHROPIC_AUTH_TOKEN is "
                "stored in settings.json in plaintext. Prefer removing "
                "--no-keychain to use apiKeyHelper with the macOS keychain."
            )

    def current_provider_names(self) -> list[str]:
        name = self._extract_provider_name(self._load())
        return [name] if name else []

    def export_config(self) -> dict:
        """Return the byop-managed slice of Claude Code's ``settings.json``."""
        data = self._load()
        providers: dict = {}
        name = self._extract_provider_name(data)
        if name:
            raw_env = data.get("env")
            env = raw_env if isinstance(raw_env, dict) else {}
            entry: dict = {
                "provider": name,
                "anthropicBaseUrl": env.get("ANTHROPIC_BASE_URL") or data.get("apiBaseUrl"),
                "model": data.get("model"),
            }
            if "apiKeyHelper" in data:
                entry["apiKeyHelper"] = data["apiKeyHelper"]
            elif env.get("ANTHROPIC_AUTH_TOKEN"):
                entry["authToken"] = "[REDACTED]"
            providers[name] = entry
        return {
            self.name: {
                "config_path": str(self.settings_path),
                "providers": providers,
            }
        }
