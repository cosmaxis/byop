"""Warp custom endpoints with non-secret TOML and native Keychain storage."""
from __future__ import annotations

import ipaddress
import json
import os
import plistlib
import shutil
import socket
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import tomlkit

from ..config import ProviderConfig
from ..warp_keychain import delete_endpoint_keys, get_endpoint_keys, set_endpoint_keys

DEFAULT_SETTINGS_PATH = Path.home() / ".warp" / "settings.toml"
WARP_APP = Path("/Applications/Warp.app")
WARP_DMG_URL = "https://app.warp.dev/download?package=dmg"
RESTART_NOTICE = (
    "Warp: save active work, fully quit Warp (Cmd-Q), then reopen it. "
    "Opening a new tab does not reload cached API keys. Select your custom model, not Auto."
)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _warp_app_path() -> Path:
    if WARP_APP.exists():
        return WARP_APP
    user_app = Path.home() / "Applications" / "Warp.app"
    return user_app if user_app.exists() else WARP_APP

def validate_warp_endpoint(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Warp endpoints must be public HTTPS URLs without embedded credentials.")
    host = parsed.hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host.lower().rstrip(".") in {"localhost", "localhost.localdomain"}:
            raise ValueError("Warp endpoints must be public; localhost is unsupported.") from None
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            infos = []
        if any(not ipaddress.ip_address(item[4][0]).is_global for item in infos):
            raise ValueError("Warp endpoints must resolve to public addresses.") from None
    else:
        if not address.is_global:
            raise ValueError("Warp endpoints must resolve to public addresses.")


def endpoint_schema_supported() -> bool:
    """Fail closed when the installed app does not describe the expected file format."""
    try:
        schema = json.loads((_warp_app_path() / "Contents/Resources/settings_schema.json").read_text())
        definitions = schema["$defs"]
        endpoint = definitions["CustomEndpointDefinition"]["properties"]
        model = definitions["CustomEndpointModel"]["properties"]
        protocols = definitions["CustomEndpointSchema"]["oneOf"]
        return (
            definitions["CustomEndpointDefinition"]["type"] == "object"
            and definitions["CustomEndpointModel"]["type"] == "object"
            and set(definitions["CustomEndpointDefinition"].get("required", []))
            <= {"name", "base_url", "models", "schema"}
            and set(definitions["CustomEndpointModel"].get("required", []))
            <= {"name", "alias", "config_key"}
            and endpoint["name"]["type"] == "string"
            and endpoint["base_url"]["type"] == "string"
            and endpoint["models"]["type"] == "array"
            and endpoint["models"]["items"]["$ref"] == "#/$defs/CustomEndpointModel"
            and endpoint["schema"]["$ref"] == "#/$defs/CustomEndpointSchema"
            and model["name"]["type"] == "string"
            and model["config_key"]["type"] == "string"
            and "string" in model["alias"]["type"]
            and any(item.get("const") == "openai_chat_completions" for item in protocols)
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _install_via_download(log: Callable[[str], None] = print) -> None:
    """Download the official Warp DMG, mount it read-only, and copy Warp.app."""
    log("Downloading Warp installer...")
    with tempfile.TemporaryDirectory() as tmp:
        dmg = Path(tmp) / "Warp.dmg"
        try:
            urllib.request.urlretrieve(WARP_DMG_URL, dmg)
        except Exception as exc:
            raise RuntimeError(
                f"Warp download failed ({exc}). Install manually from https://www.warp.dev/download"
            ) from exc
        attach = _run(["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", str(dmg)])
        if attach.returncode != 0:
            raise RuntimeError(
                "Failed to mount Warp.dmg:\n" + (attach.stderr or attach.stdout).strip()
            )
        mount_point: str | None = None
        try:
            info = plistlib.loads(attach.stdout.encode("utf-8"))
            for entity in info.get("system-entities", []):
                mp = entity.get("mount-point")
                if isinstance(mp, str) and (Path(mp) / "Warp.app").exists():
                    mount_point = mp
                    break
            if not mount_point:
                raise RuntimeError("Warp.app not found inside downloaded Warp.dmg.")
            dest = (
                WARP_APP
                if os.access(WARP_APP.parent, os.W_OK)
                else Path.home() / "Applications" / "Warp.app"
            )
            dest.parent.mkdir(parents=True, exist_ok=True)
            copy_res = _run(["ditto", str(Path(mount_point) / "Warp.app"), str(dest)])
            if copy_res.returncode != 0:
                raise RuntimeError(
                    "Failed to copy Warp.app:\n" + (copy_res.stderr or copy_res.stdout).strip()
                )
        finally:
            if mount_point:
                _run(["hdiutil", "detach", mount_point, "-quiet"])
    log("Warp installed via direct download.")


class WarpTarget:
    name = "warp"
    display_name = "Warp"

    def __init__(self, settings_path: Path = DEFAULT_SETTINGS_PATH) -> None:
        self.settings_path = Path(settings_path)

    def is_installed(self) -> bool:
        return (
            WARP_APP.exists()
            or (Path.home() / "Applications" / "Warp.app").exists()
            or shutil.which("warp") is not None
        )

    def install(self, log: Callable[[str], None] = print) -> None:
        if self.is_installed():
            return
        if shutil.which("brew") is not None:
            log("Installing Warp via Homebrew...")
            res = _run(["brew", "install", "--cask", "warp"])
            if res.returncode == 0:
                log("Warp installed via Homebrew.")
                return
            detail = (res.stderr or res.stdout).strip()
            log(f"Homebrew install failed ({detail}); trying direct download.")
        _install_via_download(log=log)
    @staticmethod
    def _read(path: Path) -> dict:
        if not path.exists():
            return {}
        return dict(tomlkit.parse(path.read_text(encoding="utf-8")).unwrap())

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        atomic_write(path, tomlkit.dumps(data).encode())

    @staticmethod
    def _manual_values(provider: ProviderConfig) -> str:
        models = ", ".join(model.name for model in provider.models)
        return (f"Provider name: {provider.provider_name}\nEndpoint URL: {provider.normalized_api_url()}\n"
                f"Protocol: openai_chat_completions\nModels: {models}\n"
                "API key: [REDACTED — paste your key into Warp; byop never prints it]")

    def configure(self, provider: ProviderConfig, *, dry_run: bool = False,
                  use_keychain: bool = True, use_env: bool = False,
                  conflict_action: str | None = None,
                  log: Callable[[str], None] = print) -> None:
        errors = provider.validate()
        if errors:
            raise ValueError("\n".join(errors))
        validate_warp_endpoint(provider.api_url)
        if conflict_action == "append":
            raise ValueError("Warp --conflict append is not supported. Use replace or skip.")
        if dry_run:
            log(f"Dry run: would configure Warp at {self.settings_path}; no settings or Keychain writes.")
            log(self._manual_values(provider))
            log(RESTART_NOTICE)
            return
        if not use_keychain or not endpoint_schema_supported():
            log("Warp: manual setup required in Settings > AI > Custom inference endpoint. "
                "Automatic setup requires Keychain and a compatible installed Warp settings schema.")
            log(self._manual_values(provider))
            log(RESTART_NOTICE)
            return

        if self.settings_path.is_symlink():
            raise ValueError("Warp settings are symlink-managed; use manual setup to preserve the link.")
        original = self.settings_path.read_bytes() if self.settings_path.exists() else None
        document = tomlkit.parse(original.decode()) if original is not None else tomlkit.document()
        agents = document.setdefault("agents", {})
        endpoints = agents.setdefault("custom_endpoints", {})
        matches = [key for key, value in endpoints.items() if value.get("name") == provider.provider_name]
        if len(matches) > 1:
            raise ValueError("Multiple Warp endpoints have that provider name; rename them in Warp first.")
        if matches and conflict_action == "skip":
            log(f"Skip: Warp already has {provider.provider_name}; settings and key unchanged.")
            return
        endpoint_id = matches[0] if matches else str(uuid4())
        existing = endpoints.get(endpoint_id, {})
        previous_models = existing.get("models", [])
        incoming_names = [model.name for model in provider.models]
        existing_names = [model["name"] for model in previous_models]
        if len(set(incoming_names)) != len(incoming_names) or len(set(existing_names)) != len(existing_names):
            raise ValueError("Warp model names must be unique; resolve duplicate models before setup.")
        existing_models = {model["name"]: model for model in previous_models}
        models = []
        for model in provider.models:
            previous = existing_models.get(model.name, {})
            models.append({
                "name": model.name,
                "alias": model.display_name or previous.get("alias") or model.name,
                "config_key": previous.get("config_key") or str(uuid4()),
            })
        removed_ids = {
            value.get("config_key") for value in previous_models
            if value.get("config_key")
        } - {model["config_key"] for model in models}
        profiles = agents.get("execution_profiles", {})
        for profile_name, profile in profiles.items():
            for field, value in profile.items():
                replaces_default = (
                    profile_name == "default" and field == "base_model" and provider.set_default_agent
                )
                if isinstance(value, str) and value in removed_ids and not replaces_default:
                    raise ValueError(
                        "A Warp execution profile still uses a model being removed; "
                        "update that profile in Warp or include the model before replacing it."
                    )
        endpoints[endpoint_id] = {
            "name": provider.provider_name,
            "base_url": provider.normalized_api_url(),
            "schema": "openai_chat_completions",
            "models": models,
        }
        if provider.set_default_agent:
            agents.setdefault("execution_profiles", {}).setdefault("default", {})["base_model"] = models[0]["config_key"]
        rendered = tomlkit.dumps(document).encode()
        previous_keys = get_endpoint_keys()
        keys = dict(previous_keys or {})
        keys[endpoint_id] = provider.api_key

        # Preserve the original file before touching either live configuration store.
        if original is not None:
            backup = self.settings_path.with_name(self.settings_path.name + ".bak")
            if not backup.exists():
                atomic_write(backup, original)
        set_endpoint_keys(keys)
        try:
            current = self.settings_path.read_bytes() if self.settings_path.exists() else None
            if current != original:
                raise RuntimeError("Warp settings changed during setup; retry after closing Settings.")
            mode = self.settings_path.stat().st_mode & 0o777 if original is not None else 0o600
            atomic_write(self.settings_path, rendered, mode)
        except Exception:
            if previous_keys is None:
                delete_endpoint_keys()
            else:
                set_endpoint_keys(previous_keys)
            raise
        log(f"Updated Warp settings at {self.settings_path}; API key stored in Warp's Keychain item.")
        log("Warp sends prompts, context, endpoint URL, and API key through Warp's servers.")
        log(RESTART_NOTICE)

    def current_provider_names(self) -> list[str]:
        endpoints = self._read(self.settings_path).get("agents", {}).get("custom_endpoints", {})
        return [value["name"] for value in endpoints.values() if isinstance(value.get("name"), str)]

    def export_config(self) -> dict:
        endpoints = self._read(self.settings_path).get("agents", {}).get("custom_endpoints", {})
        providers = {}
        for value in endpoints.values():
            if not isinstance(value.get("name"), str):
                continue
            providers[value["name"]] = {
                "base_url": value.get("base_url"),
                "schema": value.get("schema", "openai_chat_completions"),
                "models": [{key: model[key] for key in ("name", "alias", "config_key") if key in model}
                           for model in value.get("models", [])],
            }
        return {self.name: {"config_path": str(self.settings_path), "providers": providers}}

    def load_settings(self) -> dict:
        return self._read(self.settings_path)

    def write_settings(self, data: dict) -> None:
        self._write(self.settings_path, data)
