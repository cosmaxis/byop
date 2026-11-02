"""Codex target: configure a custom OpenAI Responses API provider.

Codex stores user configuration in ``~/.codex/config.toml``. A custom provider
uses the Responses API and keychain command authentication or an environment
reference. Model catalog entries inherit the installed Codex client's bundled
metadata so byop does not replace its system instructions with invented ones.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import tomlkit

from .. import keychain as kc
from ..config import ModelConfig, ProviderConfig
from ..settings import atomic_write

DEFAULT_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
DEFAULT_CATALOG_DIR = Path.home() / ".codex" / "byop-catalogs"

# Available picker levels for models whose reasoning support is configured.
_CATALOG_REASONING_LEVELS = [
    {"effort": "low", "description": "Fast responses with lighter reasoning"},
    {"effort": "medium", "description": "Balances speed and reasoning depth for everyday tasks"},
    {"effort": "high", "description": "Greater reasoning depth for complex problems"},
    {"effort": "xhigh", "description": "Extra high reasoning depth for complex problems"},
    {"effort": "max", "description": "Maximum reasoning depth for the hardest problems"},
    {"effort": "ultra", "description": "Maximum reasoning with automatic task delegation"},
]



def _run(cmd: list[str]) -> subprocess.CompletedProcess:  # pragma: no cover
    return subprocess.run(cmd, capture_output=True, text=True, check=False)



def _codex_cli() -> str | None:
    executable = shutil.which("codex")
    if executable:
        return executable
    for root in (Path.home() / "Applications", Path("/Applications")):
        for app in ("Codex.app", "ChatGPT.app"):
            resources = root / app / "Contents" / "Resources"
            for relative in ("codex-cli/bin/codex", "codex-cli/CodexCLI.app/Contents/MacOS/codex", "codex"):
                candidate = resources / relative
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
    return None


def _active_provider(data: dict) -> str | None:
    name = data.get("model_provider")
    if isinstance(name, str) and name:
        return name
    model = data.get("model")
    return "openai" if isinstance(model, str) and model else None

def _catalog_path_for(provider_name: str, catalog_dir: Path = DEFAULT_CATALOG_DIR) -> Path:
    # Percent encoding is reversible, unlike replacing spaces and dots with underscores.
    return catalog_dir / f"{quote(provider_name, safe='-_.')}.json"


def _catalog_entry(model: ModelConfig, priority: int, template: dict) -> dict:
    entry = dict(template)
    entry["slug"] = model.name
    entry["display_name"] = model.display_name or model.name
    entry["description"] = f"{entry['display_name']} via byop"
    default_effort = model.reasoning_effort or "none"
    levels = list(_CATALOG_REASONING_LEVELS) if default_effort != "none" else []
    if default_effort in {"none", "minimal"}:
        levels.insert(0, {"effort": default_effort, "description": "Configured model default"})
    entry["default_reasoning_level"] = default_effort
    entry["supported_reasoning_levels"] = levels
    entry["priority"] = priority
    entry["context_window"] = model.max_tokens
    entry["max_context_window"] = model.max_tokens
    entry["input_modalities"] = ["text", "image"] if model.capabilities.get("images") else ["text"]
    return entry


class CodexTarget:
    """Configure the Codex desktop app and CLI user configuration."""

    name = "codex"
    display_name = "Codex"

    def __init__(
        self,
        config_path: Path = DEFAULT_CONFIG_PATH,
        catalog_dir: Path | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.catalog_dir = catalog_dir or self.config_path.parent / "byop-catalogs"

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def is_installed(self) -> bool:
        return (
            _codex_cli() is not None
            or Path("/Applications/Codex.app").exists()
            or (Path.home() / "Applications" / "Codex.app").exists()
        )

    def install(self, log: Callable[[str], None] = print) -> None:  # pragma: no cover
        if shutil.which("brew") is not None:
            log("Installing Codex via Homebrew...")
            result = _run(["brew", "install", "--cask", "codex"])
            if result.returncode == 0:
                log("Codex installed via Homebrew.")
                return
            log(f"brew install failed: {result.stderr or result.stdout}")
        log("Install Codex from https://developers.openai.com/codex/ then re-run byop.")
        log("  The desktop app is bundled with ChatGPT; the CLI is `npm i -g @openai/codex` or `brew install codex`.")

    # ------------------------------------------------------------------
    # Fragment builders
    # ------------------------------------------------------------------
    def build_fragment(
        self,
        provider: ProviderConfig,
        *,
        use_keychain: bool = True,
        use_env: bool = False,
    ) -> dict:
        """Return the Codex TOML fragment without exposing the API key."""
        if provider.provider_name.lower() in {"openai", "ollama", "lmstudio"}:
            raise ValueError("Codex reserves the provider IDs openai, ollama, and lmstudio.")
        parsed = urlsplit(provider.api_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Codex API URL must not contain credentials, a query, or a fragment.")
        primary = provider.models[0]
        block: dict = {
            "name": provider.provider_name,
            "base_url": provider.normalized_api_url(),
            "wire_api": "responses",
        }
        if use_keychain:
            block["auth"] = {
                "command": "security",
                "args": [
                    "find-internet-password",
                    "-s",
                    provider.keychain_server(),
                    "-a",
                    "Bearer",
                    "-w",
                ],
                "timeout_ms": 5000,
                "refresh_interval_ms": 300000,
            }
        elif use_env:
            block["env_key"] = provider.api_key_env or provider.env_var_name()
        else:
            raise ValueError("Codex requires either keychain storage or an environment variable.")

        has_reasoning = any(m.reasoning_effort not in (None, "none") for m in provider.models)
        fragment: dict = {
            "model": primary.name,
            "model_provider": provider.provider_name,
            "model_providers": {provider.provider_name: block},
        }
        if has_reasoning or len(provider.models) > 1:
            fragment["model_catalog_json"] = str(
                _catalog_path_for(provider.provider_name, self.catalog_dir)
            )
        else:
            fragment["model_context_window"] = primary.max_tokens
        if primary.reasoning_effort is not None:
            fragment["model_reasoning_effort"] = primary.reasoning_effort

        return fragment

    def build_catalog(self, provider: ProviderConfig) -> dict:
        """Derive catalog entries from the installed client's own bundled metadata."""
        executable = _codex_cli()
        if executable is None:
            raise RuntimeError("Codex CLI is required to build its model catalog; install Codex first.")
        try:
            with tempfile.TemporaryDirectory(prefix="byop-codex-") as temporary:
                env = {**os.environ, "CODEX_HOME": temporary}
                result = subprocess.run(
                    [executable, "debug", "models", "--bundled"],
                    cwd=temporary, env=env, capture_output=True, text=True, timeout=30, check=False,
                )
            if result.returncode:
                raise RuntimeError("Codex could not provide its bundled model catalog; update Codex.")
            bundled = json.loads(result.stdout)["models"]
            template = next(model for model in bundled if isinstance(model, dict))
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, StopIteration) as exc:
            raise RuntimeError("Could not read Codex's bundled model catalog; update Codex.") from exc
        return {"models": [_catalog_entry(model, index, template) for index, model in enumerate(provider.models)]}

    def _load(self) -> dict:
        if not self.config_path.exists():
            return tomlkit.document()
        try:
            return tomlkit.parse(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Could not parse {self.config_path}: {exc}") from exc

    def _write(self, data: dict) -> None:
        # Resolve symlinks so dotfile-managed config links remain intact.
        destination = self.config_path.resolve()
        mode = destination.stat().st_mode & 0o777 if destination.exists() else 0o600
        atomic_write(destination, tomlkit.dumps(data).encode(), mode)

    # ------------------------------------------------------------------
    # Public API
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
            raise ValueError("Invalid provider configuration:\n  - " + "\n  - ".join(errors))
        if conflict_action == "append":
            raise ValueError("Codex supports one active model provider; use replace or skip.")

        fragment = self.build_fragment(provider, use_keychain=use_keychain, use_env=use_env)
        if dry_run:
            log("[dry-run] Codex would merge this config.toml fragment:")
            log(tomlkit.dumps(fragment))
            if "model_catalog_json" in fragment:
                log("[dry-run] Codex would generate a catalog from its bundled model metadata.")
            log("[dry-run] Codex's model catalog has no per-model output-token limit field.")
            return

        original = self.config_path.read_bytes() if self.config_path.exists() else None
        current = self._load()
        existing_provider = _active_provider(current)
        if conflict_action == "skip" and existing_provider:
            log(f"Skip: Codex already uses {existing_provider}.")
            return

        catalog_path = Path(fragment["model_catalog_json"]) if "model_catalog_json" in fragment else None
        catalog = self.build_catalog(provider) if catalog_path else None
        if catalog_path and catalog_path.is_symlink():
            raise ValueError("Codex catalog is symlink-managed; remove the link before setup.")

        providers = current.setdefault("model_providers", tomlkit.table())
        if not isinstance(providers, dict):
            raise ValueError("Codex model_providers must be a TOML table.")
        block = providers.setdefault(provider.provider_name, tomlkit.table())
        if not isinstance(block, dict):
            raise ValueError("Codex provider settings must be a TOML table.")
        for key in ("auth", "env_key", "experimental_bearer_token", "requires_openai_auth"):
            block.pop(key, None)
        block.update(fragment.pop("model_providers")[provider.provider_name])
        if "model_reasoning_effort" not in fragment:
            current.pop("model_reasoning_effort", None)
        if "model_context_window" not in fragment:
            current.pop("model_context_window", None)
        previous_catalog = current.get("model_catalog_json")
        if catalog_path is None and isinstance(previous_catalog, str):
            if Path(previous_catalog).parent == self.catalog_dir:
                current.pop("model_catalog_json", None)
        current.update(fragment)

        if use_keychain or not provider.api_key_env:
            kc.ensure_key(
                server=provider.keychain_server(),
                api_key=provider.api_key,
                env_var=provider.env_var_name(),
                log=log,
                use_keychain=use_keychain,
                use_env=use_env,
            )
        old_catalog = catalog_path.read_bytes() if catalog_path and catalog_path.exists() else None
        old_mode = catalog_path.stat().st_mode & 0o777 if old_catalog is not None and catalog_path else 0o600
        if catalog_path and catalog is not None:
            atomic_write(catalog_path, (json.dumps(catalog, indent=2) + "\n").encode(), old_mode)
        try:
            latest = self.config_path.read_bytes() if self.config_path.exists() else None
            if latest != original:
                raise RuntimeError("Codex settings changed during setup; retry.")
            self._write(current)
        except Exception:
            if catalog_path:
                if old_catalog is None:
                    catalog_path.unlink(missing_ok=True)
                else:
                    atomic_write(catalog_path, old_catalog, old_mode)
            raise
        log(f"Updated Codex config at {self.config_path}")
        log("Warning: Codex's model catalog has no per-model output-token limit field.")

    def current_provider_names(self) -> list[str]:
        name = _active_provider(self._load())
        return [name] if name else []

    def export_config(self) -> dict:
        data = self._load()
        name = data.get("model_provider")
        providers: dict = {}
        all_providers = data.get("model_providers", {})
        if isinstance(name, str) and name and isinstance(all_providers, dict):
            block = all_providers.get(name)
            if isinstance(block, dict):
                safe = {key: block[key] for key in ("name", "wire_api", "env_key") if key in block}
                base_url = block.get("base_url")
                if isinstance(base_url, str):
                    try:
                        parsed = urlsplit(base_url)
                    except ValueError:
                        pass
                    else:
                        safe["base_url"] = urlunsplit(
                            (parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", "")
                        )
                providers[name] = safe
        # Surface catalogue for completeness; not every installation uses it.
        if isinstance(data.get("model_catalog_json"), str):
            providers["_catalog"] = {"path": data["model_catalog_json"]}
        return {
            self.name: {
                "config_path": str(self.config_path),
                "providers": providers,
            }
        }
