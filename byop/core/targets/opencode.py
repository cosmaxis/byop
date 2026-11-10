"""OpenCode target: wires a custom OpenAI-compatible provider into OpenCode.

OpenCode stores global settings in ``~/.config/opencode/opencode.json``.
Legacy providers use ``provider``; native V2 providers use ``providers``.

OpenCode supports ``{env:VAR}`` and ``{file:path}`` substitution, but not
shell commands. A local OpenCode plugin also reads Keychain at request time
so Desktop does not depend on inheriting the user's shell environment.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from .. import keychain as kc
from .. import settings as sett
from ..config import ModelConfig, ProviderConfig

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "opencode" / "opencode.json"
OPENCODE_APP = Path("/Applications/OpenCode.app")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


class OpencodeTarget:
    """Configures OpenCode with a custom OpenAI-compatible provider."""

    name = "opencode"
    display_name = "OpenCode (CLI + Desktop)"

    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.config_path = config_path

    # ------------------------------------------------------------------
    def _desktop_installed(self) -> bool:
        return OPENCODE_APP.exists() or (Path.home() / "Applications" / "OpenCode.app").exists()

    def is_present(self) -> bool:
        return shutil.which("opencode") is not None or self._desktop_installed()

    def is_installed(self) -> bool:
        return shutil.which("opencode") is not None and self._desktop_installed()

    def install(self, log: Callable[[str], None] = print) -> None:
        commands = []
        if shutil.which("opencode") is None:
            commands.append(["brew", "install", "opencode"])
        if not self._desktop_installed():
            commands.append(["brew", "install", "--cask", "opencode-desktop"])
        if not commands:
            log("OpenCode CLI and Desktop are already installed.")
            return
        if shutil.which("brew") is None:
            log("Install the missing OpenCode components with Homebrew:")
            for command in commands:
                log(f"  {' '.join(command)}")
            log("then re-run byop.")
            return
        for command in commands:
            log(f"Installing {' '.join(command[2:])}...")
            result = _run(command)
            if result.returncode != 0:
                raise RuntimeError(
                    f"{' '.join(command)} failed: {result.stderr or result.stdout}"
                )
        log("OpenCode CLI and Desktop installed.")

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
        env_name = provider.api_key_env if use_env and not use_keychain else None
        env_ref = f"{{env:{env_name or provider.env_var_name()}}}"
        if use_keychain is True or use_env:
            return env_ref
        if use_keychain is None and kc.keychain_has(provider.keychain_server()):
            return env_ref
        return "[REDACTED]" if redact else provider.api_key

    def _model_entry(self, model: ModelConfig) -> dict:
        entry: dict = {
            "name": model.display_name or model.name,
            "limit": {
                "context": model.max_tokens,
                "output": model.max_output_tokens,
            },
            "tool_call": model.capabilities.get("tools", True),
            "temperature": True,
            "attachment": model.capabilities.get("images", False),
        }
        entry["reasoning"] = bool(
            (model.reasoning_effort and model.reasoning_effort != "none")
            or model.capabilities.get("interleaved_reasoning")
        )
        entry["interleaved"] = (
            {"field": "reasoning_content"} if model.capabilities.get("interleaved_reasoning") else False
        )
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

    @staticmethod
    def _provider_maps(data: dict) -> tuple[dict, dict]:
        if not isinstance(data, dict):
            raise ValueError("OpenCode configuration must be an object.")
        legacy = data.get("provider")
        native = data.get("providers")
        legacy = {} if legacy is None else legacy
        native = {} if native is None else native
        if not isinstance(legacy, dict) or not isinstance(native, dict):
            raise ValueError("OpenCode provider and providers entries must be objects.")
        return legacy, native

    @staticmethod
    def _remove_authorization(data: dict) -> None:
        headers = data.get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if key.lower() == "authorization":
                    del headers[key]

    def _native_provider(self, legacy: dict, previous: dict) -> dict:
        native = copy.deepcopy(previous)
        settings = native.get("settings")
        if settings is None:
            settings = {}
        if not isinstance(settings, dict):
            raise ValueError("OpenCode V2 provider settings must be an object.")
        settings.update(legacy["options"])
        self._remove_authorization(settings)
        self._remove_authorization(native)
        provider_compatibility = native.get("compatibility")
        if isinstance(provider_compatibility, dict):
            provider_compatibility.pop("reasoningField", None)
        previous_models = native.get("models") or {}
        if not isinstance(previous_models, dict):
            raise ValueError("OpenCode V2 provider models must be an object.")
        models = {}
        for name, model in legacy["models"].items():
            existing = previous_models.get(name) or {}
            if not isinstance(existing, dict):
                raise ValueError(f"OpenCode V2 model {name!r} must be an object.")
            entry = copy.deepcopy(existing)
            entry.pop("package", None)
            variants = entry.get("variants")
            if variants is not None and not isinstance(variants, list):
                raise ValueError(f"OpenCode V2 model {name!r} variants must be an array.")
            for override in [entry, *(variants or [])]:
                if not isinstance(override, dict):
                    raise ValueError(f"OpenCode V2 model {name!r} has an invalid variant.")
                options = override.get("settings")
                if isinstance(options, dict):
                    options.pop("baseURL", None)
                    options.pop("apiKey", None)
                    self._remove_authorization(options)
                self._remove_authorization(override)
            capabilities = entry.get("capabilities")
            if capabilities is None:
                capabilities = {}
            if not isinstance(capabilities, dict):
                raise ValueError(f"OpenCode V2 model {name!r} capabilities must be an object.")
            inputs = capabilities.get("input")
            inputs = inputs if isinstance(inputs, list) else []
            capabilities["input"] = ["text"] + [item for item in inputs if item not in ("text", "image")]
            if model["attachment"]:
                capabilities["input"].append("image")
            capabilities["tools"] = model["tool_call"]
            capabilities.setdefault("temperature", model["temperature"])
            reasoning = capabilities.get("reasoning")
            reasoning = reasoning if isinstance(reasoning, dict) and reasoning.get("supported") is True else {}
            capabilities["reasoning"] = {**reasoning, "supported": True} if model["reasoning"] else {"supported": False}
            compatibility = entry.get("compatibility")
            if compatibility is None:
                compatibility = {}
            if not isinstance(compatibility, dict):
                raise ValueError(f"OpenCode V2 model {name!r} compatibility must be an object.")
            if model["interleaved"]:
                compatibility["reasoningField"] = model["interleaved"]["field"]
            else:
                compatibility.pop("reasoningField", None)
            entry.update({
                "name": model["name"],
                "modelID": name,
                "type": "chat",
                "disabled": False,
                "limit": sett.merge(entry.get("limit", {}) if isinstance(entry.get("limit"), dict) else {}, model["limit"]),
                "capabilities": capabilities,
                "compatibility": compatibility,
            })
            models[name] = entry
        native.update({
            "name": legacy["name"],
            "package": "aisdk:@ai-sdk/openai-compatible",
            "settings": settings,
            "models": models,
        })
        return native

    def _configure_keychain_plugin(self, name: str, server: str | None) -> None:
        plugins = self.config_path.parent / "plugins"
        mapping_path = plugins / "byop-keychain.json"
        if server is None and not mapping_path.exists():
            return
        try:
            mapping = json.loads(mapping_path.read_text(encoding="utf-8")) if mapping_path.exists() else {}
            if not isinstance(mapping, dict) or not all(isinstance(value, str) for value in mapping.values()):
                raise ValueError("expected provider names mapped to Keychain servers")
            if server is None:
                mapping.pop(name, None)
            else:
                mapping[name] = server
            plugins.mkdir(parents=True, exist_ok=True)
            if server is not None:
                source = Path(__file__).with_name("opencode-keychain.js").read_bytes()
                sett.atomic_write(plugins / "byop-keychain.js", source)
            sett.atomic_write(mapping_path, (json.dumps(mapping, indent=2) + "\n").encode())
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Could not configure OpenCode Keychain integration: {exc}") from exc

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

        existing = self._load()
        legacy, native = self._provider_maps(existing)
        existing_names = legacy.keys() | native.keys()
        name_to_write = provider.provider_name
        if provider.provider_name in existing_names:
            if conflict_action == "skip":
                log(
                    f"Skip: OpenCode already has {provider.provider_name}; "
                    f"leaving {self.config_path} untouched."
                )
                if not dry_run and (use_keychain or (use_env and not provider.api_key_env)):
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
                while f"{provider.provider_name}_{i}" in existing_names:
                    i += 1
                name_to_write = f"{provider.provider_name}_{i}"
                log(
                    f"Append: OpenCode already has {provider.provider_name}; "
                    f"writing under {name_to_write}."
                )

        fragment = self.build_fragment(
            provider,
            use_keychain=use_keychain,
            use_env=use_env,
            redact=dry_run,
        )
        new_block = fragment["provider"].pop(provider.provider_name)
        fragment["provider"][name_to_write] = new_block
        if name_to_write in native:
            if not isinstance(native[name_to_write], dict):
                raise ValueError(f"OpenCode V2 provider {name_to_write!r} must be an object.")
            self._native_provider(new_block, native[name_to_write])
            if dry_run:
                fragment["providers"] = {name_to_write: self._native_provider(new_block, {})}
        if dry_run:
            log("[dry-run] OpenCode would write the following opencode.json fragment:")
            log(json.dumps(fragment, indent=2))
            return

        if use_keychain or (use_env and not provider.api_key_env):
            kc.ensure_key(
                server=provider.keychain_server(),
                api_key=provider.api_key,
                env_var=provider.env_var_name(),
                log=log,
                use_keychain=use_keychain,
                use_env=use_env,
                keychain_env=use_keychain,
            )

        self._configure_keychain_plugin(
            name_to_write, provider.keychain_server() if use_keychain else None
        )

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        current = self._load()
        providers, current_native = self._provider_maps(current)
        current["provider"] = providers
        if name_to_write in current_native:
            if not isinstance(current_native[name_to_write], dict):
                raise ValueError(f"OpenCode V2 provider {name_to_write!r} must be an object.")
            current_native[name_to_write] = self._native_provider(new_block, current_native[name_to_write])
        previous = providers.get(name_to_write)
        if isinstance(previous, dict):
            previous_models = previous.get("models")
            if not isinstance(previous_models, dict):
                previous_models = {}
            merged = sett.merge(previous, new_block)
            merged["models"] = {
                model_id: sett.merge(
                    previous_models.get(model_id, {})
                    if isinstance(previous_models.get(model_id), dict) else {},
                    {
                        key: value for key, value in model.items()
                        if key != "temperature"
                        or not isinstance(previous_models.get(model_id), dict)
                        or key not in previous_models[model_id]
                    },
                )
                for model_id, model in new_block["models"].items()
            }
            providers[name_to_write] = merged
        else:
            providers[name_to_write] = new_block
        if name_to_write in current_native:
            active = providers[name_to_write]
            self._remove_authorization(active.get("options", {}))
            for model_id, model in active["models"].items():
                if "id" in model:
                    model["id"] = model_id
                model.pop("provider", None)
                overrides = [model]
                variants = model.get("variants")
                if isinstance(variants, dict):
                    overrides.extend(item for item in variants.values() if isinstance(item, dict))
                for override in overrides:
                    if override is not model:
                        override.pop("baseURL", None)
                        override.pop("apiKey", None)
                    self._remove_authorization(override)
                    options = override.get("options")
                    if isinstance(options, dict):
                        options.pop("baseURL", None)
                        options.pop("apiKey", None)
                        self._remove_authorization(options)
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
        legacy, native = self._provider_maps(self._load())
        return list(dict.fromkeys([*legacy, *native]))

    def export_config(self) -> dict:
        """Return the effective providers from the OpenCode configuration."""
        legacy, native = self._provider_maps(self._load())
        providers = {**legacy, **native}
        return {
            self.name: {
                "config_path": str(self.config_path),
                "providers": dict(providers) if isinstance(providers, dict) else {},
            }
        }
