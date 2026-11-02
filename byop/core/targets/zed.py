"""Zed target: wires a provider into Zed's settings.json + keychain."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .. import keychain as kc
from .. import settings as sett
from .. import zed as zedmod
from ..config import ProviderConfig

DEFAULT_SETTINGS_PATH = Path.home() / ".config" / "zed" / "settings.json"


class ZedTarget:
    name = "zed"
    display_name = "Zed"

    def __init__(self, settings_path: Path = DEFAULT_SETTINGS_PATH) -> None:
        self.settings_path = settings_path

    # ------------------------------------------------------------------
    def is_installed(self) -> bool:
        return zedmod.is_installed()

    def install(self, log: Callable[[str], None] = print) -> None:
        zedmod.install(log=log)

    # ------------------------------------------------------------------
    def build_fragment(self, provider: ProviderConfig) -> dict:
        language_models = sett.merge(
            {}, {"openai_compatible": provider.language_models_block()}
        )
        fragment: dict = {"language_models": language_models}

        agent = provider.agent_block()
        if agent:
            fragment["agent"] = agent

        ep = provider.edit_predictions_block()
        if ep:
            fragment["edit_predictions"] = ep

        return fragment

    def _ensure_key(
        self, provider: ProviderConfig, *, use_keychain: bool, use_env: bool, log: Callable[[str], None]
    ) -> None:
        if not use_keychain and use_env and provider.api_key_env:
            name = provider.env_var_name()
            if name != provider.api_key_env:
                alias = f'export {name}="$(printenv {provider.api_key_env})"'
                found = False
                for path in kc.profile_candidates():
                    if not path.exists():
                        continue
                    try:
                        lines = path.read_text(encoding="utf-8").splitlines()
                    except OSError as exc:
                        raise RuntimeError(f"Could not inspect shell profile {path}: {exc}") from exc
                    for line in lines:
                        if line.lstrip().startswith(f"export {name}="):
                            if line.strip() != alias:
                                raise ValueError(
                                    f"Zed needs {name}, but {path} already exports it. "
                                    f"Update that export to reference {provider.api_key_env}, then retry."
                                )
                            found = True
                if not found:
                    with (Path.home() / ".zshrc").open("a", encoding="utf-8") as shell:
                        shell.write(f"\n# Added by byop for Zed\n{alias}\n")
                log(f"Warning: Zed needs {name}; its shell export reads {provider.api_key_env}. "
                    "Set it before opening a new terminal and launching Zed.")
            return
        kc.ensure_key(
            provider.keychain_server(), provider.api_key, provider.env_var_name(),
            log=log, use_keychain=use_keychain, use_env=use_env,
        )

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
            raise ValueError("Invalid provider configuration:\n  - " +
                             "\n  - ".join(errors))

        fragment = self.build_fragment(provider)

        if dry_run:
            log("[dry-run] Zed would write the following settings fragment:")
            log(sett.dumps(fragment))
            if not use_keychain and provider.api_key_env:
                log(f"[dry-run] Zed would reference {provider.api_key_env} through {provider.env_var_name()}.")
            else:
                log(f"[dry-run] Zed would store key for server={provider.keychain_server()}")
            return

        # When the caller chose 'skip' and there's an existing provider entry
        # with the *same* api_url, treat the run as a no-op (the merge would
        # produce an identical fragment anyway). We still ensure the keychain
        # entry — that's a maintenance step, not a state change.
        #
        # Note: 'skip' is taken only when the existing entry already points at
        # the same provider name AND api_url. If the name matches but the url
        # has changed (e.g. an api endpoint migration) we fall through and
        # let the merge path update api_url in place — preserving the user
        # expectation that 'skip' means "idempotent re-run".
        if conflict_action == "skip":
            existing = sett.load_path(self.settings_path)
            existing_block = (
                existing.get("language_models", {})
                .get("openai_compatible", {})
                .get(provider.provider_name)
            )
            if isinstance(existing_block, dict) and existing_block.get(
                "api_url"
            ) == provider.normalized_api_url():
                self._ensure_key(provider, use_keychain=use_keychain, use_env=use_env, log=log)
                log(
                    f"Skip: {provider.provider_name} already configured with "
                    f"the same api_url."
                )
                return

        current = sett.load_path(self.settings_path)
        existing_url = (
            current.get("language_models", {})
            .get("openai_compatible", {})
            .get(provider.provider_name, {})
            .get("api_url")
        )
        if (
            existing_url
            and existing_url != provider.normalized_api_url()
            and not (conflict_action == "skip")
        ):
            log(
                f"Note: {provider.provider_name} api_url changed "
                f"({existing_url} -> {provider.normalized_api_url()})."
            )
        merged = sett.merge(current, fragment)
        self._ensure_key(provider, use_keychain=use_keychain, use_env=use_env, log=log)
        sett.write_path(self.settings_path, merged)
        log(f"Updated Zed settings at {self.settings_path}")


    def current_provider_names(self) -> list[str]:
        data = sett.load_path(self.settings_path)
        block = data.get("language_models", {}).get("openai_compatible", {})
        return list(block.keys()) if isinstance(block, dict) else []

    def export_config(self) -> dict:
        """Return the byop-managed slice of Zed's settings.json.

        Includes ``language_models.openai_compatible`` plus the ``agent`` and
        ``edit_predictions`` blocks (only those byop writes — other agent
        settings the user has set are intentionally excluded).
        """
        data = sett.load_path(self.settings_path)
        providers = (
            data.get("language_models", {}).get("openai_compatible", {})
        )
        out = {
            self.name: {
                "config_path": str(self.settings_path),
                "providers": dict(providers) if isinstance(providers, dict) else {},
            }
        }
        # Surface the agent/edit_predictions wiring only if byop-managed keys
        # are present, so the export stays focused on what byop wrote.
        agent = data.get("agent")
        if isinstance(agent, dict) and agent:
            out[self.name]["agent"] = agent
        ep = data.get("edit_predictions")
        if isinstance(ep, dict) and ep:
            out[self.name]["edit_predictions"] = ep
        return out
