"""byop command-line interface.

Subcommand structure (since v2.0.0):

  byop status                   # same as bare `byop` — read-only dashboard
  byop apply [flags]            # sync active profile to selected targets
  byop profile {list,use,new,edit,delete,export}
  byop doctor                   # drift detection
  byop export-config [flags]    # dump current target settings as JSON

Backwards compatibility: the top-level flags (``--provider``, ``--api-url``,
``--model``, ``--target``, ``--config-file``, ``--export-config``, etc.) still
work — they're routed to ``apply`` / ``export-config`` automatically. So
existing scripts and CI pipelines keep running unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

from .core import default_model_capabilities, prompt, wizard
from .core.config import ModelConfig, ProviderConfig
from .core.conflict import resolve_conflict_action, supports_append
from .core.paste import parse_provider_paste


# ===========================================================================
# Argument parser
# ===========================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="byop",
        description="Interactively set up a custom LLM provider for Zed, "
        "py.dev, and other AI coding tools.",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_pkg_version('byop')}",
    )

    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    # ---- status (default) ------------------------------------------------
    status_p = sub.add_parser(
        "status",
        help="Show the current profile + per-target state (read-only).",
        description="Read-only dashboard. Same as running `byop` with no "
        "subcommand. Does not modify any files.",
    )
    _add_omp_path_args(status_p)

    # ---- apply -----------------------------------------------------------
    apply_p = sub.add_parser(
        "apply",
        help="Sync the active profile to one or more targets.",
        description="Apply the active profile to selected targets. The wizard "
        "is shown when no profile exists yet, or when --provider/--api-url/"
        "--api-key/--model are not provided.",
    )
    _add_apply_args(apply_p)

    # ---- profile ---------------------------------------------------------
    prof_p = sub.add_parser(
        "profile",
        help="Manage saved provider profiles.",
        description="List, switch, create, edit, delete, or export saved "
        "provider profiles.",
    )
    prof_sub = prof_p.add_subparsers(dest="profile_command", metavar="CMD")
    prof_sub.add_parser("list", help="List all saved profiles (active first).")
    prof_sub.add_parser("new", help="Create a new profile (interactive).")
    prof_sub.add_parser("edit", help="Edit the active profile (interactive).")
    prof_sub.add_parser("export", help="Print the active profile as JSON.")

    use_p = prof_sub.add_parser("use", help="Switch the active profile.")
    use_p.add_argument("name", help="Profile name to activate.")

    del_p = prof_sub.add_parser("delete", help="Delete a saved profile.")
    del_p.add_argument("name", help="Profile name to delete.")

    # ---- doctor ----------------------------------------------------------
    doctor_p = sub.add_parser(
        "doctor",
        help="Detect drift between the active profile and current targets.",
        description="Compares the active profile against each target's "
        "actual settings.json / models.json and reports drift. Read-only.",
    )
    _add_omp_path_args(doctor_p)

    # ---- export-config ---------------------------------------------------
    exp_p = sub.add_parser(
        "export-config",
        help="Print the current byop-managed configuration for each target.",
    )
    _add_apply_args(exp_p)  # shares --target / --export-provider
    exp_p.add_argument(
        "--export-provider",
        default=None,
        help="Only include this provider name across targets.",
    )

    return p

def _add_omp_path_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--profile",
        dest="omp_profile",
        help="OMP profile name (writes ~/.omp/profiles/NAME/agent/models.yml).",
    )
    p.add_argument(
        "--omp-models-path",
        type=Path,
        help="Explicit OMP models.yml path (overrides --profile).",
    )


def _add_apply_args(p: argparse.ArgumentParser) -> None:
    """Args shared between ``apply`` and ``export-config``."""
    p.add_argument(
        "--settings",
        type=Path,
        default=Path.home() / ".config" / "zed" / "settings.json",
        help="Path to Zed settings.json (default: ~/.config/zed/settings.json)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without modifying anything.",
    )
    p.add_argument(
        "--no-keychain",
        action="store_true",
        help="Do not write the API key to the macOS keychain.",
    )
    p.add_argument(
        "--env-key",
        action="store_true",
        help="Also export the API key as a shell environment variable.",
    )
    p.add_argument(
        "--skip-install",
        action="store_true",
        help="Do not install/upgrade any app (configure settings only).",
    )
    p.add_argument(
        "--target",
        action="append",
        default=[],
        choices=["zed", "py", "omp", "claude", "opencode", "warp"],
        help="Restrict to specific target(s): zed, py, omp, claude, opencode, warp (repeatable). Defaults to all detected/installed apps.",
    )
    p.add_argument(
        "--profile",
        dest="omp_profile",
        help="OMP profile name (writes ~/.omp/profiles/NAME/agent/models.yml).",
    )
    p.add_argument(
        "--omp-models-path",
        type=Path,
        help="Explicit OMP models.yml path (overrides --profile).",
    )
    p.add_argument(
        "--claude-permission-mode",
        choices=["manual", "auto"],
        help="Claude only: manual uses human approvals; auto uses safety classification. Omit to preserve.",
    )
    p.add_argument(
        "--claude-auto-mode-model",
        type=str.strip,
        help="Claude only: explicit safety classifier model (not the main model). Requires Claude 2.1.302+. Does not enable auto mode.",
    )
    p.add_argument(
        "--config-file",
        type=Path,
        help="Path to a JSON file with provider fields (provider_name, "
        "api_url, api_key, models). Overrides --provider/--api-url/...",
    )
    p.add_argument(
        "--conflict",
        choices=["replace", "skip", "append", "prompt"],
        default=None,
        help="What to do when the provider name already exists on a target.",
    )
    # Non-interactive provider options (apply only)
    g = p.add_argument_group("provider (non-interactive mode)")
    g.add_argument("--provider", help="Provider name, e.g. 'MyProvider'")
    g.add_argument("--api-url", help="API base URL, e.g. https://.../v1")
    g.add_argument("--api-key", help="API key for the provider")
    g.add_argument(
        "--model", action="append", default=[],
        help="Model spec (repeatable). First becomes the default. "
        "Format: NAME[:KEY=VAL][:FLAG]... "
        "Bare keys (images, tools, ...) set a capability to True. "
        "Examples: --model any:images, --model command-code:reasoning_effort=max:max_tokens=1000000.",
    )
    g.add_argument("--model-name", help="Single model ID (legacy alias).")
    g.add_argument("--model-display", help="Display name for models.")
    g.add_argument(
        "--reasoning-effort",
        choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"],
    )
    g.add_argument("--max-tokens", type=int, default=128000)
    g.add_argument("--max-output-tokens", type=int, default=32000)
    g.add_argument("--images", action="store_true")
    g.add_argument("--interleaved-reasoning", action="store_true")
    g.add_argument("--max-tokens-parameter", action="store_true")
    g.add_argument("--default-agent", action="store_true", default=True)
    g.add_argument("--no-default-agent", dest="default_agent", action="store_false")
    g.add_argument("--inline-assistant", action="store_true", default=True)
    g.add_argument("--no-inline-assistant", dest="inline_assistant", action="store_false")
    g.add_argument("--commit-message", action="store_true")
    g.add_argument("--thread-summary", action="store_true")
    g.add_argument("--edit-predictions", action="store_true")


# ===========================================================================
# Top-level dispatch
# ===========================================================================
def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (KeyboardInterrupt, EOFError):
        prompt.warn("Cancelled.")
        return 130


def _main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    argv = _inject_default_subcommand(argv)
    args = parser.parse_args(argv)

    # No subcommand: route based on legacy flags vs. fresh user.
    if args.command is None:
        return _route_bare(parser, args)
    return _dispatch(parser, args)


def _inject_default_subcommand(argv: list[str] | None) -> list[str]:
    """Prepend ``apply`` when no subcommand is present.

    Lets the legacy top-level-flag UX (``byop --provider P --api-url ...``)
    keep working without requiring users to type ``byop apply ...``.
    """
    if argv is None:
        argv = []
    if any(t in argv for t in ("-h", "--help", "--version")):
        return list(argv)
    known = {"status", "apply", "profile", "doctor", "export-config"}
    for tok in argv:
        if tok in known or tok == "--":
            return list(argv)
    if not argv:
        return list(argv)
    return ["apply"] + list(argv)


def _dispatch(parser, args) -> int:
    if args.command == "status":
        return _run_status(
            omp_profile=getattr(args, "omp_profile", None),
            omp_models_path=getattr(args, "omp_models_path", None),
        )
    if args.command == "apply":
        return _run_apply(args)
    if args.command == "profile":
        return _run_profile(args)
    if args.command == "doctor":
        return _run_doctor(
            omp_profile=getattr(args, "omp_profile", None),
            omp_models_path=getattr(args, "omp_models_path", None),
        )
    if args.command == "export-config":
        return _run_export(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


def _route_bare(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Decide what to do when `byop` is run with no subcommand.

    Backwards compatibility: if any of the legacy apply flags are set
    (``--provider``, ``--api-url``, ``--api-key``, ``--model``, ``--target``,
    ``--config-file``, ``--export-config``, etc.), route to ``apply``.

    Otherwise: show status dashboard if a profile exists, else launch the
    first-run wizard.
    """
    # When no subcommand was given, the apply-only attributes are missing.
    # Use getattr with defaults — true means "user passed a legacy apply flag".
    legacy_apply_args = (
        getattr(args, "provider", None)
        or getattr(args, "api_url", None)
        or getattr(args, "api_key", None)
        or getattr(args, "model", None)
        or getattr(args, "model_name", None)
        or getattr(args, "target", None)
        or getattr(args, "config_file", None) is not None
        or getattr(args, "export_config", False)
        or getattr(args, "dry_run", False)
        or getattr(args, "skip_install", False)
        or getattr(args, "env_key", False)
        or getattr(args, "no_keychain", False)
        or getattr(args, "conflict", None) is not None
    )
    if legacy_apply_args:
        # Inject 'apply' so _run_apply's parser path runs.
        return _run_apply(args)
    return _run_default_landing()


def _short_path(path: Path | str) -> str:
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home):] if text.startswith(home + "/") else text


def _target_config_path(target: object) -> str:
    for attr in ("settings_path", "config_path", "models_path"):
        val = getattr(target, attr, None)
        if val is not None:
            return _short_path(val)
    return ""


def _safe_provider_names(target: object) -> list[str]:
    fn = getattr(target, "current_provider_names", None)
    if not callable(fn):
        return []
    try:
        return list(fn())
    except Exception:
        return []


def _profile_subtitle(profile) -> str:
    models = [m.name for m in profile.models]
    model_summary = ", ".join(models[:2]) + (f" +{len(models) - 2}" if len(models) > 2 else "")
    parts = [profile.provider_name, profile.api_url]
    if model_summary:
        parts.append(model_summary)
    return " · ".join(parts)


def _run_default_landing() -> int:
    """Interactive landing menu for tool setup and provider management."""
    from .core import profiles as prof

    shown_subtitle: str | None = None
    while True:
        active_name = prof.get_active_profile_name()
        has_profile = prof.profile_exists(active_name)
        subtitle = _profile_subtitle(prof.load_profile(active_name)) if has_profile else "no provider configured"
        if shown_subtitle is None:
            prompt.intro("byop", subtitle)
            shown_subtitle = subtitle
        elif subtitle != shown_subtitle:
            shown_subtitle = subtitle

        action = prompt.choose(
            "Action",
            [
                "Set up tools",
                "Add provider",
                "Switch provider",
                "Edit provider",
                "Quit",
            ],
        )
        if action == "Quit":
            return 0
        if action == "Add provider":
            _profile_new_interactive(activate=True)
        elif action == "Switch provider":
            names = prof.list_profiles()
            if names:
                profiles = [prof.load_profile(name) for name in names]
                labels = [p.provider_name for p in profiles]
                if len(set(labels)) != len(labels):
                    labels = [f"{p.provider_name} ({p.name})" for p in profiles]
                hints = [p.api_url for p in profiles]
                selected = prompt.choose("Provider", labels, hints=hints)
                chosen = profiles[labels.index(selected)]
                prof.set_active_profile_name(chosen.name)
                prompt.success(f"Active provider · {chosen.provider_name}")
            else:
                prompt.warn("No saved providers yet.")
        elif action in ("Edit provider", "Edit active provider"):
            _profile_edit_interactive()
        elif not has_profile:
            _run_first_run_wizard()
        else:
            profile = prof.load_profile(active_name)
            api_key = _load_profile_api_key(profile)
            args = _make_args_for_apply(
                profile,
                api_key,
                {
                    "use_keychain": profile.api_key_ref == "keychain",
                    "use_env": profile.api_key_ref.startswith("env:"),
                },
            )
            _run_apply(args, interactive_session=True)


def _run_first_run_wizard() -> int:
    """First-run path: collect provider, save active profile, and apply to tools."""
    from .core import profiles as prof

    provider, prefs = wizard.run_wizard()
    profile_name = provider.provider_name
    errors = provider.validate()
    if errors:
        prompt.error("Invalid provider configuration: " + "; ".join(errors))
        return 2
    if prof.profile_exists(profile_name) or _profile_for_provider(profile_name):
        prompt.error(f"Provider {profile_name!r} already exists. Switch to it or edit it.")
        return 2
    api_key = provider.api_key
    from .core import keychain as kc

    use_keychain = prefs["use_keychain"]
    use_env = prefs["use_env"] or not use_keychain
    profile = prof.profile_from_provider(
        provider, name=profile_name,
        api_key_ref="keychain" if use_keychain else f"env:{provider.env_var_name()}",
    )
    kc.ensure_key(
        provider.keychain_server(), api_key, provider.env_var_name(),
        use_keychain=use_keychain, use_env=use_env, log=lambda _: None,
    )
    if not use_keychain:
        os.environ[provider.env_var_name()] = api_key
    prof.save_profile(profile, api_key=api_key)
    prof.set_active_profile_name(profile_name)

    args = _make_args_for_apply(profile, api_key, prefs)
    return _run_apply(args, interactive_session=True)
def _make_args_for_apply(profile, api_key, prefs):
    primary = profile.models[0] if profile.models else None
    args = argparse.Namespace(
        settings=Path.home() / ".config" / "zed" / "settings.json",
        dry_run=False,
        no_keychain=not prefs.get("use_keychain", True),
        env_key=prefs.get("use_env", False),
        skip_install=False,
        target=[],
        omp_profile=None,
        omp_models_path=None,
        config_file=None,
        conflict=None,
        provider=profile.provider_name,
        api_url=profile.api_url,
        api_key=api_key,
        model=[m.name for m in profile.models],
        model_name=None,
        model_display=primary.display_name if primary else None,
        reasoning_effort=primary.reasoning_effort if primary else None,
        max_tokens=primary.max_tokens if primary else 128000,
        max_output_tokens=primary.max_output_tokens if primary else 32000,
        images=bool(primary and primary.capabilities.get("images")),
        interleaved_reasoning=bool(
            primary and primary.capabilities.get("interleaved_reasoning")
        ),
        max_tokens_parameter=bool(
            primary and primary.capabilities.get("max_tokens_parameter")
        ),
        default_agent=profile.set_default_agent,
        inline_assistant=profile.set_inline_assistant,
        commit_message=profile.set_commit_message,
        thread_summary=profile.set_thread_summary,
        edit_predictions=profile.use_edit_predictions,
        export_config=False,
        export_provider=None,
    )
    return args



def _load_profile_api_key(profile) -> str:
    if profile.api_key_ref == "keychain":
        from .core import keychain as kc
        return kc.keychain_get(profile.api_url.rstrip("/")) or ""
    return os.environ.get(
        profile.env_var or profile.api_key_ref.removeprefix("env:"), ""
    )


def _profile_for_provider(provider_name: str) -> str | None:
    """Find an existing profile, including profiles created with older names."""
    from .core import profiles as prof

    for name in prof.list_profiles():
        if prof.load_profile(name).provider_name == provider_name:
            return name
    return None

# ===========================================================================
# status / apply / profile / doctor / export-config
# ===========================================================================
def _run_status(
    *, omp_profile: str | None = None, omp_models_path: Path | None = None
) -> int:
    """Print a status dashboard for the active profile + each target."""
    from .core import profiles as prof
    from .core.targets import available_targets

    if not prof.profile_exists(prof.get_active_profile_name()):
        prompt.warn("No active profile. Run `byop` to get started.")
        return 0

    profile = prof.load_profile()
    target_kwargs: dict[str, object] = {"settings_path": Path.home() / ".config" / "zed" / "settings.json"}
    if omp_profile is not None:
        target_kwargs["omp_profile"] = omp_profile
    if omp_models_path is not None:
        target_kwargs["omp_models_path"] = omp_models_path
    targets = available_targets(**target_kwargs)  # type: ignore[arg-type]

    prompt.intro("byop", "status")
    prompt.kv("Provider", profile.provider_name)
    prompt.kv("Endpoint", profile.api_url)
    auth = (
        "macOS Keychain"
        if profile.api_key_ref == "keychain"
        else (profile.env_var or profile.api_key_ref)
    )
    prompt.kv("Auth", auth)
    model_names = [m.name for m in profile.models]
    prompt.kv("Models", ", ".join(model_names) if model_names else "none")
    prompt.spacer()

    unconfigured = 0
    for target in targets:
        names = target.current_provider_names()
        installed = target.is_installed()
        cfg_path = _target_config_path(target)
        if not installed:
            prompt.step(target.display_name, "not installed", status="skip")
        elif profile.provider_name in names:
            detail = f"configured · {cfg_path}" if cfg_path else "configured"
            prompt.step(target.display_name, detail, status="ok")
        else:
            unconfigured += 1
            detail = f"not configured · {cfg_path}" if cfg_path else "not configured"
            prompt.step(target.display_name, detail, status="warn")

    prompt.outro(
        "Run `byop apply` to sync targets"
        if unconfigured
        else "All installed tools configured"
    )
    return 0

def _run_apply(args: argparse.Namespace, *, interactive_session: bool = False) -> int:
    """Sync a provider to selected targets — the renamed default behavior."""
    # Backwards-compat: ``byop --export-config`` (no subcommand) used to be a
    # top-level flag. The apply subparser doesn't declare it; default it.
    if getattr(args, "export_config", False):
        return _run_export(args)
    if getattr(args, "claude_auto_mode_model", None) == "":
        prompt.error("--claude-auto-mode-model must not be empty.")
        return 2

    # Resolve provider either from --config-file, from CLI flags, from the
    # active profile (in that order), or fall through to the wizard.
    non_interactive = False
    provider: ProviderConfig | None = None

    if args.config_file is not None:
        try:
            raw = args.config_file.read_text(encoding="utf-8")
        except OSError as exc:
            prompt.error(f"Could not read --config-file: {exc}")
            return 2
        try:
            provider, missing = parse_provider_paste(raw)
        except ValueError as exc:
            prompt.error(str(exc))
            return 2
        if missing:
            prompt.error(
                "--config-file is missing required fields: "
                + ", ".join(missing)
            )
            return 2
        non_interactive = True
        use_keychain = not args.no_keychain
        use_env = args.env_key
        api_key = provider.api_key
    else:
        non_interactive_args = any(
            [args.provider, args.api_url, args.api_key, args.model, args.model_name]
        )
        if non_interactive_args:
            if not (args.provider and args.api_url and args.api_key):
                prompt.error(
                    "Non-interactive mode requires --provider, --api-url and "
                    "--api-key."
                )
                return 2
            provider = _provider_from_args(args)
            use_keychain = not args.no_keychain
            use_env = args.env_key
            api_key = provider.api_key
            non_interactive = True
        else:
            # Interactive path. Prefer loading the active profile to pre-fill
            # so the user doesn't re-enter provider/api_url/models.
            from .core import profiles as prof

            prefill: ProviderConfig | None = None
            if prof.profile_exists(prof.get_active_profile_name()):
                saved = prof.load_profile()
                prefill = saved.to_provider_config(
                    api_key=_load_profile_api_key(saved)
                )

            collected, prefs = wizard.run_wizard(prefill=prefill)
            provider = collected
            api_key = provider.api_key
            use_keychain = prefs["use_keychain"] and not args.no_keychain
            use_env = prefs["use_env"] or args.env_key
    if interactive_session:
        non_interactive = False
    if not use_keychain and not use_env:
        use_env = True


    # Dry-run is read-only: do not persist profile metadata or secrets.
    if not args.dry_run:
        from .core import profiles as prof

        active_name = prof.get_active_profile_name()
        existing = prof.load_profile(active_name) if prof.profile_exists(active_name) else None
        profile_name = (
            active_name if existing and existing.provider_name == provider.provider_name
            else _profile_for_provider(provider.provider_name) or provider.provider_name
        )
        destination = prof.load_profile(profile_name) if prof.profile_exists(profile_name) else None
        if destination and destination.provider_name != provider.provider_name:
            prompt.error(f"Profile {profile_name!r} already belongs to another provider.")
            return 2
        if not non_interactive and profile_name != active_name and prof.profile_exists(profile_name):
            prompt.error(f"Provider {profile_name!r} already exists. Switch to it or choose another name.")
            return 2
    # ---- Select targets --------------------------------------------------
    targets = _select_targets(
        args,
        interactive=not non_interactive,
        active_provider=provider.provider_name,
    )
    if not targets:
        return 0

    if non_interactive and args.conflict == "prompt":
        prompt.error(
            "--conflict prompt requires an interactive TTY. "
            "Pass --conflict replace|skip|append for non-interactive runs."
        )
        return 2
    if not args.dry_run:
        profile = prof.profile_from_provider(
            provider, name=profile_name,
            api_key_ref="keychain" if use_keychain else f"env:{provider.env_var_name()}",
        )
        if prof.profile_exists(profile_name):
            prof.save_profile(profile, allow_overwrite=True, api_key=api_key)
        else:
            prof.save_profile(profile, api_key=api_key)
        if profile_name != active_name:
            prof.set_active_profile_name(profile_name)
        if use_keychain and api_key:
            from .core import keychain as kc
            kc.keychain_set(provider.keychain_server(), api_key)
    cli_flag = args.conflict if args.conflict and args.conflict != "prompt" else None

    def _pick(target) -> str:
        if cli_flag == "append" and not supports_append(target.name):
            raise _ConflictRejected(
                f"--conflict append is not supported for {target.display_name} "
                f"(only py/omp/opencode accept multi-provider entries). "
                f"Use --conflict replace or --conflict skip."
            )
        existing_names = target.current_provider_names()
        has_collision = (
            bool(existing_names) and provider.provider_name in existing_names
        )
        decided = resolve_conflict_action(
            target.name,
            has_collision=has_collision,
            interactive=not non_interactive,
            conflict_flag=cli_flag,
        )
        if decided is not None:
            return decided.value
        options = ["replace", "skip"]
        if supports_append(target.name):
            options.append("append")
        return prompt.choose_action(target.display_name, options, default="replace")

    install_approved = True
    if not args.skip_install and not args.dry_run and not non_interactive:
        missing_targets = [t for t in targets if not t.is_installed()]
        if missing_targets:
            missing_names = ", ".join(t.display_name for t in missing_targets)
            question = (
                f"Install {missing_names} automatically?"
                if len(missing_targets) == 1
                else f"Install missing tools ({missing_names}) automatically?"
            )
            install_approved = prompt.confirm(question, default=True)

    success = True
    configured_count = 0
    configured_warp = False
    exported_shell_env = False

    for target in targets:
        try:
            conflict_action = _pick(target)
            was_installed = target.is_installed()
            if not was_installed and not args.skip_install and not args.dry_run:
                if not install_approved:
                    prompt.step(target.display_name, "skipped (not installed)", status="skip")
                    continue
                install_logs: list[str] = []
                with prompt.spinner(f"Installing {target.display_name}…"):
                    target.install(log=install_logs.append)
                if not target.is_installed():
                    reason = install_logs[-1] if install_logs else "not installed"
                    prompt.step(target.display_name, f"install failed · {reason}", status="error")
                    success = False
                    continue

            target_provider = provider
            if target.name == "zed" and not non_interactive and conflict_action != "skip":
                target_provider = wizard.configure_zed_features(provider)

            if args.dry_run:
                target.configure(
                    target_provider,
                    dry_run=True,
                    use_keychain=use_keychain,
                    use_env=use_env,
                    conflict_action=conflict_action,
                    log=prompt.info,
                )
            else:
                cfg_logs: list[str] = []
                target.configure(
                    target_provider,
                    dry_run=False,
                    use_keychain=use_keychain,
                    use_env=use_env,
                    conflict_action=conflict_action,
                    log=cfg_logs.append,
                )
                if any(line.startswith("Skip:") for line in cfg_logs):
                    prompt.step(target.display_name, "unchanged", status="skip")
                elif any("manual setup required" in line.lower() for line in cfg_logs):
                    prompt.step(target.display_name, "manual setup required", status="warn")
                    for line in cfg_logs:
                        prompt.info(line)
                else:
                    cfg_path = _target_config_path(target)
                    parts = []
                    if not was_installed and target.is_installed():
                        parts.append("installed")
                    if cfg_path:
                        parts.append(cfg_path)
                    prompt.step(target.display_name, " · ".join(parts) or "configured", status="ok")
                    configured_count += 1
                    if target.name == "warp":
                        configured_warp = True
                    if target.name == "opencode" and use_keychain:
                        exported_shell_env = True

            if target_provider is not provider and not args.dry_run:
                saved_profile = prof.load_profile()
                saved_profile.set_default_agent = target_provider.set_default_agent
                saved_profile.set_inline_assistant = target_provider.set_inline_assistant
                saved_profile.set_commit_message = target_provider.set_commit_message
                saved_profile.set_thread_summary = target_provider.set_thread_summary
                saved_profile.use_edit_predictions = target_provider.use_edit_predictions
                prof.save_profile(saved_profile, allow_overwrite=True)
        except ValueError as exc:
            prompt.error(f"{target.display_name}: {exc}")
            success = False
        except RuntimeError as exc:
            prompt.error(f"{target.display_name}: {exc}")
            success = False
        except _ConflictRejected as exc:
            prompt.error(str(exc))
            return 2

    if args.dry_run:
        prompt.outro("Dry run complete · no changes made")
        return 0 if success else 1

    if configured_warp:
        prompt.warn(
            "Warp: Cmd-Q and reopen to reload cached API keys (select custom model, not Auto)"
        )
    if use_env or exported_shell_env:
        prompt.warn("Shell profile updated · run `source ~/.zshrc` or open a new terminal")

    if success:
        if configured_count:
            noun = "tool" if configured_count == 1 else "tools"
            prompt.outro(f"Configured {configured_count} {noun} for {provider.provider_name}")
        else:
            prompt.outro("Nothing changed")
    return 0 if success else 1


def _run_profile(args: argparse.Namespace) -> int:
    """Dispatch `byop profile ...` subcommands."""
    from .core import profiles as prof

    cmd = args.profile_command
    if cmd == "list":
        names = prof.list_profiles()
        active = prof.get_active_profile_name()
        if not names:
            prompt.info("No saved profiles. Run `byop profile new`.")
            return 0
        prompt.intro("byop", "profiles")
        for name in names:
            p = prof.load_profile(name)
            models = ", ".join(m.name for m in p.models[:2])
            tag = "active" if name == active else ""
            detail = " · ".join(part for part in (p.api_url, models, tag) if part)
            prompt.step(p.provider_name, detail, status="ok" if name == active else "skip")
        prompt.outro(f"{len(names)} saved profile{'s' if len(names) != 1 else ''}")
        return 0
    if cmd == "use":
        if not prof.profile_exists(args.name):
            prompt.error(f"Profile {args.name!r} does not exist.")
            return 2
        prof.set_active_profile_name(args.name)
        prompt.success(f"Active profile: {args.name}")
        return 0

    if cmd == "new":
        return _profile_new_interactive()

    if cmd == "edit":
        return _profile_edit_interactive()

    if cmd == "delete":
        try:
            prof.delete_profile(args.name)
        except prof.ProfileNotFound as exc:
            prompt.error(str(exc))
            return 2
        prompt.success(f"Deleted profile {args.name!r}.")
        return 0

    if cmd == "export":
        try:
            p = prof.load_profile()
        except prof.ProfileNotFound as exc:
            prompt.error(str(exc))
            return 2
        sys.stdout.write(json.dumps(p.to_toml(), indent=2) + "\n")
        return 0

    prompt.error(
        "`byop profile` needs a subcommand: list | new | edit | use | delete | export"
    )
    return 2


def _profile_new_interactive(*, activate: bool = False) -> int:
    """Walk the user through creating a new saved profile."""
    from .core import profiles as prof

    collected, prefs = wizard.run_wizard()
    name = collected.provider_name
    errors = collected.validate()
    if errors:
        prompt.error("Invalid provider configuration: " + "; ".join(errors))
        return 2
    if prof.profile_exists(name) or _profile_for_provider(name):
        prompt.error(f"Provider {name!r} already exists. Switch to it or edit it.")
        return 2
    from .core import keychain as kc

    use_keychain = prefs["use_keychain"]
    use_env = prefs["use_env"] or not use_keychain
    profile = prof.profile_from_provider(
        collected, name=name,
        api_key_ref="keychain" if use_keychain else f"env:{collected.env_var_name()}",
    )
    kc.ensure_key(
        collected.keychain_server(), collected.api_key, collected.env_var_name(),
        use_keychain=use_keychain, use_env=use_env, log=lambda _: None,
    )
    if not use_keychain:
        os.environ[collected.env_var_name()] = collected.api_key
    prof.save_profile(profile, api_key=collected.api_key)
    if activate:
        prof.set_active_profile_name(name)
        prompt.success(f"Active provider · {name}")
    else:
        prompt.success(f"Saved provider · {name}")
    return 0


def _profile_edit_interactive() -> int:
    """Re-run the wizard pre-filled with the active profile."""
    from .core import profiles as prof

    name = prof.get_active_profile_name()
    if not prof.profile_exists(name):
        prompt.error("No active profile to edit. Run `byop profile new`.")
        return 2
    saved = prof.load_profile(name)
    collected, prefs = wizard.run_wizard(
        prefill=saved.to_provider_config(api_key=_load_profile_api_key(saved))
    )
    errors = collected.validate()
    if errors:
        prompt.error("Invalid provider configuration: " + "; ".join(errors))
        return 2
    new_name = collected.provider_name if collected.provider_name != saved.provider_name else name
    if new_name != name and (prof.profile_exists(new_name) or _profile_for_provider(new_name)):
        prompt.error(f"Provider {new_name!r} already exists. Choose a different name.")
        return 2
    from .core import keychain as kc

    use_keychain = prefs["use_keychain"]
    use_env = prefs["use_env"] or not use_keychain
    profile = prof.profile_from_provider(
        collected, name=new_name,
        api_key_ref="keychain" if use_keychain else f"env:{collected.env_var_name()}",
    )
    kc.ensure_key(
        collected.keychain_server(), collected.api_key, collected.env_var_name(),
        use_keychain=use_keychain, use_env=use_env, log=lambda _: None,
    )
    if not use_keychain:
        os.environ[collected.env_var_name()] = collected.api_key
    prof.save_profile(profile, allow_overwrite=new_name == name, api_key=collected.api_key)
    if new_name != name:
        prof.set_active_profile_name(new_name)
        prof.delete_profile(name)
    prompt.success(f"Updated provider · {new_name}")
    return 0


def _run_doctor(
    *, omp_profile: str | None = None, omp_models_path: Path | None = None
) -> int:
    """Detect drift between the active profile and each target's actual config."""
    from .core import profiles as prof
    from .core.targets import available_targets

    if not prof.profile_exists(prof.get_active_profile_name()):
        prompt.warn("No active profile to check against.")
        return 0
    saved = prof.load_profile()
    target_kwargs: dict[str, object] = {"settings_path": Path.home() / ".config" / "zed" / "settings.json"}
    if omp_profile is not None:
        target_kwargs["omp_profile"] = omp_profile
    if omp_models_path is not None:
        target_kwargs["omp_models_path"] = omp_models_path
    targets = available_targets(**target_kwargs)  # type: ignore[arg-type]

    prompt.intro("byop", f"doctor · {saved.provider_name}")
    issues = 0
    checked = 0
    for target in targets:
        if not target.is_installed():
            continue
        checked += 1
        names = target.current_provider_names()
        if saved.provider_name not in names:
            prompt.step(
                target.display_name,
                f"missing {saved.provider_name} → byop apply --target {target.name}",
                status="warn",
            )
            issues += 1
        else:
            prompt.step(target.display_name, "in sync", status="ok")
    if issues == 0:
        prompt.outro("No drift detected" if checked else "No installed tools detected")
        return 0
    prompt.warn(f"{issues} target{'s' if issues != 1 else ''} out of sync")
    return 1


def _run_export(args: argparse.Namespace) -> int:
    """Print the byop-managed slice of every target's settings as JSON."""
    from .core.targets import available_targets

    target_kwargs: dict[str, object] = {"settings_path": args.settings}
    if getattr(args, "omp_profile", None) is not None:
        target_kwargs["omp_profile"] = args.omp_profile
    if getattr(args, "omp_models_path", None) is not None:
        target_kwargs["omp_models_path"] = args.omp_models_path
    all_targets = available_targets(**target_kwargs)  # type: ignore[arg-type]
    by_name = {t.name: t for t in all_targets}

    if args.target:
        chosen = [by_name[n] for n in args.target if n in by_name]
    else:
        chosen = list(all_targets)

    out: dict = {}
    for target in chosen:
        try:
            snapshot = target.export_config()
        except RuntimeError as exc:
            prompt.error(f"{target.display_name}: {exc}")
            return 1
        if args.export_provider:
            entry = snapshot.get(target.name, {})
            providers = entry.get("providers", {}) or {}
            filtered = {
                k: v for k, v in providers.items() if k == args.export_provider
            }
            if filtered:
                entry = {**entry, "providers": filtered}
                snapshot = {target.name: entry}
            else:
                continue
        out.update(snapshot)

    sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


# ===========================================================================
# Helpers (shared between subcommands)
# ===========================================================================
class _ConflictRejected(Exception):
    """Raised when a --conflict value is incompatible with a target."""


def _build_model(name: str, display: str | None, reasoning: str | None,
                 max_tokens: int, max_out: int, images: bool,
                 interleaved: bool, max_param: bool) -> ModelConfig:
    caps = default_model_capabilities()
    caps["images"] = images
    caps["interleaved_reasoning"] = interleaved
    caps["max_tokens_parameter"] = max_param
    return ModelConfig(
        name=name,
        display_name=display,
        max_tokens=max_tokens,
        max_output_tokens=max_out,
        reasoning_effort=reasoning,
        capabilities=caps,
    )


# Per-model spec keys: bare-key (set True) or key=value (set value).
_MODEL_SPEC_BOOL_KEYS = {"images", "interleaved_reasoning",
                         "max_tokens_parameter", "tools", "parallel_tool_calls",
                         "prompt_cache_key", "chat_completions"}
_MODEL_SPEC_INT_KEYS = {"max_tokens", "max_output_tokens",
                        "context_window", "contextWindow"}
_MODEL_SPEC_STR_KEYS = {"display", "display_name", "reasoning_effort"}


def parse_model_specs(specs: list[str]) -> list[dict]:
    """Parse `--model NAME[:KEY=VAL][:FLAG]...` into per-model dicts.

    Each spec is a colon-separated list. The first segment is the model id.
    Later segments are capability overrides; a bare key sets a bool to True,
    ``key=value`` sets the named field. ``name`` may not contain ``:``.

    Returns a list of dicts with the model fields consumed by _provider_from_args.
    Raises ValueError on unknown keys or invalid reasoning_effort values.
    """
    from .core.config import DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_MAX_TOKENS, KNOWN_REASONING_EFFORTS

    out: list[dict] = []
    for spec in specs:
        if not spec:
            raise ValueError("Empty --model spec.")
        parts = spec.split(":")
        name = parts[0].strip()
        if not name:
            raise ValueError(f"Empty model name in --model {spec!r}.")
        entry = {
            "name": name,
            "images": False,
            "interleaved_reasoning": False,
            "max_tokens_parameter": False,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "reasoning_effort": None,
            "display_name": None,
        }
        for part in parts[1:]:
            if not part:
                raise ValueError(f"Empty segment in --model {spec!r}.")
            if "=" in part:
                k, _, v = part.partition("=")
                k = k.strip()
                v = v.strip()
                if k in _MODEL_SPEC_BOOL_KEYS:
                    if v.lower() in ("true", "1", "yes"):
                        entry[k] = True
                    elif v.lower() in ("false", "0", "no"):
                        entry[k] = False
                    else:
                        raise ValueError(
                            f"--model {spec!r}: {k} expects boolean, got {v!r}."
                        )
                elif k in _MODEL_SPEC_INT_KEYS:
                    try:
                        entry[k] = int(v)
                    except ValueError as exc:
                        raise ValueError(
                            f"--model {spec!r}: {k} expects integer, got {v!r}."
                        ) from exc
                elif k in _MODEL_SPEC_STR_KEYS:
                    if k == "display":
                        entry["display_name"] = v
                    elif k == "reasoning_effort":
                        if v not in KNOWN_REASONING_EFFORTS:
                            raise ValueError(
                                f"--model {spec!r}: invalid reasoning_effort "
                                f"{v!r}; valid: {sorted(KNOWN_REASONING_EFFORTS)}"
                            )
                        entry[k] = v
                    else:
                        entry[k] = v
                else:
                    raise ValueError(
                        f"--model {spec!r}: unknown model capability {k!r}. "
                        f"Known: {sorted(_MODEL_SPEC_BOOL_KEYS | _MODEL_SPEC_INT_KEYS | _MODEL_SPEC_STR_KEYS)}"
                    )
            else:
                key = part.strip()
                if key not in _MODEL_SPEC_BOOL_KEYS:
                    raise ValueError(
                        f"--model {spec!r}: unknown model capability {key!r}. "
                        f"Bare keys must be boolean flags."
                    )
                entry[key] = True
        # Mirror display_name -> display kwarg expected by _build_model.
        out.append(entry)
    return out


def _provider_from_args(args: argparse.Namespace) -> ProviderConfig:
    models: list[ModelConfig] = []
    specs = parse_model_specs(args.model or [])
    for s in specs:
        models.append(_build_model(
            name=s["name"],
            display=s.get("display_name") or args.model_display,
            reasoning=s.get("reasoning_effort") or args.reasoning_effort,
            max_tokens=s.get("max_tokens", args.max_tokens),
            max_out=s.get("max_output_tokens", args.max_output_tokens),
            images=s.get("images", args.images),
            interleaved=s.get("interleaved_reasoning", args.interleaved_reasoning),
            max_param=s.get("max_tokens_parameter", args.max_tokens_parameter),
        ))
    if not models and args.model_name:
        models.append(_build_model(
            name=args.model_name,
            display=args.model_display,
            reasoning=args.reasoning_effort,
            max_tokens=args.max_tokens,
            max_out=args.max_output_tokens,
            images=args.images,
            interleaved=args.interleaved_reasoning,
            max_param=args.max_tokens_parameter,
        ))
    if not models:
        raise SystemExit(
            "No model specified. Use --model NAME[:KEY=VAL]... (repeatable) "
            "or --model-name."
        )
    return ProviderConfig(
        provider_name=args.provider,
        api_url=args.api_url,
        api_key=args.api_key,
        models=models,
        set_default_agent=args.default_agent,
        set_inline_assistant=args.inline_assistant,
        set_commit_message=args.commit_message,
        set_thread_summary=args.thread_summary,
        use_edit_predictions=args.edit_predictions,
    )


def _select_targets(
    args,
    *,
    interactive: bool,
    active_provider: str | None = None,
) -> list:
    from .core.targets import available_targets, detect_installed

    target_kwargs = {"settings_path": args.settings}
    if getattr(args, "omp_profile", None) is not None:
        target_kwargs["omp_profile"] = args.omp_profile
    if getattr(args, "omp_models_path", None) is not None:
        target_kwargs["omp_models_path"] = args.omp_models_path
    for option in ("claude_permission_mode", "claude_auto_mode_model"):
        if getattr(args, option, None) is not None:
            target_kwargs[option] = getattr(args, option)
    all_targets = available_targets(**target_kwargs)
    by_name = {t.name: t for t in all_targets}

    if args.target:
        return [by_name[n] for n in args.target if n in by_name]

    installed = detect_installed(all_targets)
    installed_names = {t.name for t in installed}

    if not interactive:
        return installed

    labels = [target.display_name for target in all_targets]
    hints = []
    for target in all_targets:
        if target.name not in installed_names:
            hints.append("not installed")
        elif active_provider and active_provider in _safe_provider_names(target):
            hints.append("configured")
        else:
            hints.append("installed")

    title = "Tools to preview" if args.dry_run else "Tools"
    indexes = prompt.select_many(title, labels, hints=hints)
    return [all_targets[index] for index in indexes]

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
