"""End-to-end smoke test for the CLI entry point (targets mocked)."""

import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop import cli
from byop.core import keychain as kc
from byop.core import profiles as prof


def test_empty_claude_classifier_rejected_before_profile_or_secret_writes(monkeypatch, tmp_path):
    config = tmp_path / "byop"
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(config))
    assert cli.main(["apply", "--claude-auto-mode-model", "   "]) == 2
    assert not config.exists()


def test_cli_dry_run_non_interactive(monkeypatch):
    calls = {"install": 0, "configure": 0}

    class FakeTarget:
        name = "zed"
        display_name = "Zed"

        def is_installed(self):
            return True

        def install(self, log=print):
            calls["install"] += 1

        def configure(self, provider, **kwargs):
            calls["configure"] += 1
            calls["kwargs"] = kwargs

        def current_provider_names(self):
            return []  # No collision -> per-target _pick returns default policy.

    import byop.core.targets as tmod

    monkeypatch.setattr(tmod, "available_targets", lambda settings_path=None: [FakeTarget()])
    monkeypatch.setattr(tmod, "detect_installed", lambda targets: targets)

    rc = cli.main(
        [
            "--provider", "P",
            "--api-url", "https://api.example.com/v1",
            "--api-key", "sk-x",
            "--model", "m1",
            "--dry-run",
        ]
    )

    assert rc == 0
    assert calls["install"] == 0
    assert calls["configure"] == 1


def test_cli_dry_run_does_not_persist_profile_or_keychain(monkeypatch, tmp_path):
    """Dry-run must not save profile metadata or secrets before target work."""
    import byop.core.targets as tmod

    class FakeTarget:
        name = "zed"
        display_name = "Zed"

        def is_installed(self):
            return True

        def install(self, log=print):
            pass

        def configure(self, provider, **kwargs):
            assert kwargs["dry_run"] is True

        def current_provider_names(self):
            return []

    monkeypatch.setattr(tmod, "available_targets", lambda settings_path=None: [FakeTarget()])
    monkeypatch.setattr(tmod, "detect_installed", lambda targets: targets)
    calls = {"save": 0, "keychain": 0}
    monkeypatch.setattr(prof, "save_profile", lambda *a, **k: calls.__setitem__("save", calls["save"] + 1))
    monkeypatch.setattr(kc, "keychain_set", lambda *a, **k: calls.__setitem__("keychain", calls["keychain"] + 1))

    assert cli.main([
        "--provider", "P", "--api-url", "https://api.example.com/v1",
        "--api-key", "sk-dry-run", "--model", "m1", "--dry-run",
    ]) == 0
    assert calls == {"save": 0, "keychain": 0}


def test_cli_uses_first_model_for_agent_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(kc, "keychain_set", lambda *a, **k: None)
    captured = {}

    class FakeTarget:
        name = "zed"
        display_name = "Zed"

        def is_installed(self):
            return True

        def install(self, log=print):
            pass

        def configure(self, provider, **kwargs):
            captured["provider"] = provider

        def current_provider_names(self):
            return []

    import byop.core.targets as tmod
    monkeypatch.setattr(tmod, "available_targets", lambda settings_path=None: [FakeTarget()])
    monkeypatch.setattr(tmod, "detect_installed", lambda targets: targets)

    assert cli.main([
        "--provider", "P", "--api-url", "https://api.example.com/v1",
        "--api-key", "sk-12345678", "--model", "new-model",
    ]) == 0
    provider = captured["provider"]
    assert provider.set_default_agent is True
    assert provider.set_inline_assistant is True
    assert provider.models[0].name == "new-model"
def test_cli_non_interactive_requires_provider(monkeypatch, capsys):
    rc = cli.main(["--api-url", "https://api.example.com/v1"])
    assert rc == 2


def test_cli_config_file_path(monkeypatch, tmp_path):
    cfg = tmp_path / "provider.json"
    cfg.write_text(json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-x",
        "models": [{"name": "m1"}],
    }))
    called = {"configure": 0, "install": 0}

    class FT:
        name = "zed"
        display_name = "Zed"
        existing = []

        def is_installed(self):
            return True

        def install(self, log=print):
            called["install"] += 1

        def configure(self, provider, **kw):
            called["configure"] += 1

        def current_provider_names(self):
            return list(self.existing)

    import byop.core.targets as tmod

    monkeypatch.setattr(tmod, "available_targets", lambda settings_path=None: [FT()])
    monkeypatch.setattr(tmod, "detect_installed", lambda targets: targets)

    rc = cli.main(["--config-file", str(cfg), "--dry-run"])
    assert rc == 0
    assert called["configure"] == 1


def test_cli_target_choices_include_omp_and_claude():
    """The --target flag must accept the new options without a parse error."""
    from argparse import ArgumentParser

    def _walk_actions(parser: ArgumentParser):
        for action in parser._actions:
            yield action
            # Subparser actions carry a `.choices` mapping of name->subparser.
            sub = getattr(action, "choices", None)
            if isinstance(sub, dict):
                for subparser in sub.values():
                    if isinstance(subparser, ArgumentParser):
                        yield from _walk_actions(subparser)

    parser = cli.build_parser()
    for action in _walk_actions(parser):
        if "--target" in action.option_strings:
            assert "omp" in action.choices
            assert "claude" in action.choices
            assert "opencode" in action.choices
            return
    raise AssertionError("--target action not found")


def test_cli_runs_only_opencode_target(monkeypatch, tmp_path):
    """`--target opencode` must route through OpencodeTarget.configure end-to-end."""
    from byop.core.targets.opencode import OpencodeTarget

    target = OpencodeTarget(config_path=tmp_path / "opencode.json")

    import byop.core.targets as tmod
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path / "byop-cfg"))
    monkeypatch.setattr(kc, "keychain_set", lambda *a, **k: None)
    monkeypatch.setattr(tmod, "available_targets", lambda settings_path=None: [target])
    monkeypatch.setattr(tmod, "detect_installed", lambda targets: targets)
    monkeypatch.setattr("byop.core.targets.opencode.kc.keychain_has", lambda *a, **k: True)
    monkeypatch.setattr("byop.core.targets.opencode.kc.ensure_key", lambda *a, **k: ["k:x"])

    rc = cli.main([
        "--provider", "P",
        "--api-url", "https://api.example.com/v1",
        "--api-key", "sk-12345678",
        "--model", "m1",
        "--target", "opencode",
        "--skip-install",
    ])

    assert rc == 0
    data = json.loads((tmp_path / "opencode.json").read_text())
    assert data["provider"]["P"]["options"]["baseURL"] == "https://api.example.com/v1"


def test_cli_conflict_flag_passed_to_configure(monkeypatch, tmp_path):
    """--conflict skip must be threaded into ZedTarget.configure(...)."""
    cfg = tmp_path / "provider.json"
    cfg.write_text(json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-x",
        "models": [{"name": "m1"}],
    }))
    captured = {}

    class FT:
        name = "zed"
        display_name = "Zed"
        existing = ["P"]

        def is_installed(self):
            return True

        def install(self, log=print):
            pass

        def configure(self, provider, **kw):
            captured["conflict_action"] = kw.get("conflict_action")

        def current_provider_names(self):
            return list(self.existing)

    import byop.core.targets as tmod

    monkeypatch.setattr(tmod, "available_targets", lambda settings_path=None: [FT()])
    monkeypatch.setattr(tmod, "detect_installed", lambda targets: targets)

    rc = cli.main([
        "--config-file", str(cfg),
        "--conflict", "skip",
        "--dry-run",
    ])
    assert rc == 0
    assert captured["conflict_action"] == "skip"


def test_cli_config_file_missing_key_rejected(monkeypatch, tmp_path):
    cfg = tmp_path / "provider.json"
    cfg.write_text(json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "models": [{"name": "m1"}],
    }))
    rc = cli.main(["--config-file", str(cfg), "--target", "zed"])
    # api_key missing -> CLI exits 2.
    assert rc == 2


def test_cli_conflict_prompt_in_non_interactive_rejected(monkeypatch, tmp_path, capsys):
    """--conflict prompt is meaningless without a TTY and must exit 2."""
    cfg = tmp_path / "provider.json"
    cfg.write_text(json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-x",
        "models": [{"name": "m1"}],
    }))

    class FT:
        name = "zed"
        display_name = "Zed"

        def is_installed(self):
            return True

        def install(self, log=print):
            pass

        def configure(self, provider, **kw):
            pass

        def current_provider_names(self):
            return []

    import byop.core.targets as tmod

    monkeypatch.setattr(tmod, "available_targets", lambda settings_path=None: [FT()])
    monkeypatch.setattr(tmod, "detect_installed", lambda targets: targets)

    rc = cli.main(["--config-file", str(cfg), "--conflict", "prompt", "--dry-run"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "--conflict prompt requires an interactive TTY" in captured.err


def test_cli_conflict_append_against_single_provider_rejected(monkeypatch, tmp_path, capsys):
    """--conflict append must refuse zed/claude (single-provider targets)."""
    cfg = tmp_path / "provider.json"
    cfg.write_text(json.dumps({
        "provider_name": "P",
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-x",
        "models": [{"name": "m1"}],
    }))

    class FT:
        name = "zed"
        display_name = "Zed"
        existing = ["P"]

        def is_installed(self):
            return True

        def install(self, log=print):
            pass

        def configure(self, provider, **kw):
            pass

        def current_provider_names(self):
            return list(self.existing)

    import byop.core.targets as tmod

    monkeypatch.setattr(tmod, "available_targets", lambda settings_path=None: [FT()])
    monkeypatch.setattr(tmod, "detect_installed", lambda targets: targets)

    rc = cli.main([
        "--config-file", str(cfg),
        "--conflict", "append",
        "--target", "zed",
        "--dry-run",
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert "append is not supported for" in captured.err


def test_cli_ctrl_c_exits_cleanly(monkeypatch, capsys, tmp_path):
    """Ctrl+C during the interactive wizard prints cancellation, not a traceback."""
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))

    def raise_keyboard_interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.prompt, "choose", raise_keyboard_interrupt)

    rc = cli.main([])

    assert rc == 130
    assert "Cancelled." in capsys.readouterr().out


def test_cli_ctrl_d_exits_cleanly(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))

    def end_input(*args, **kwargs):
        raise EOFError

    monkeypatch.setattr(cli.prompt, "choose", end_input)
    assert cli.main([]) == 130
    captured = capsys.readouterr()
    assert "Cancelled." in captured.out
    assert "Traceback" not in captured.err


def test_cli_menu_shows_active_provider_without_verbose_dashboard(monkeypatch, tmp_path, capsys):
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://example.com", "secret-123", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(provider, name="Demo"))
    prof.set_active_profile_name("Demo")
    monkeypatch.setattr(cli.prompt, "choose", lambda *args, **kwargs: "Quit")
    assert cli.main([]) == 0
    output = capsys.readouterr().out
    assert "Demo" in output
    assert "Next steps" not in output


def test_status_shows_each_models_context_and_output_without_secrets(monkeypatch, tmp_path, capsys):
    import byop.core.targets as tmod
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://example.com/v1", "secret-123", [
        ModelConfig("large", max_tokens=750000, max_output_tokens=32000),
        ModelConfig("small", max_tokens=128000, max_output_tokens=8000),
    ])
    prof.save_profile(prof.profile_from_provider(provider, name="Demo"))
    prof.set_active_profile_name("Demo")
    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [])
    assert cli._run_status() == 0
    output = capsys.readouterr().out
    assert all(item in output for item in ("large", "750000", "32000", "small", "128000", "8000"))
    assert "secret-123" not in output


def test_cli_interactive_apply_without_collision_succeeds_and_stores_key(monkeypatch, tmp_path):
    """Interactive `byop apply` against an unconfigured target must not hit AssertionError
    and must persist the wizard-collected key to Keychain."""
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    from byop.core.config import ModelConfig, ProviderConfig

    collected = ProviderConfig(
        provider_name="Cosmaxis",
        api_url="https://api.example.com/v1",
        api_key="sk-interactive-123",
        models=[ModelConfig(name="hy3")],
    )
    keychain_calls = []
    configured = []

    class FakeTarget:
        name = "zed"
        display_name = "Zed"

        def is_installed(self):
            return True

        def install(self, log=print):
            pass

        def configure(self, provider, **kwargs):
            configured.append((provider.api_key, kwargs["conflict_action"]))

        def current_provider_names(self):
            return []  # No collision!

    import byop.core.targets as tmod
    monkeypatch.setattr(tmod, "available_targets", lambda **kw: [FakeTarget()])
    monkeypatch.setattr(tmod, "detect_installed", lambda targets: targets)
    monkeypatch.setattr(
        cli.wizard,
        "run_wizard",
        lambda prefill=None: (collected, {"use_keychain": True, "use_env": False}),
    )
    monkeypatch.setattr(cli.prompt, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(cli.prompt, "select_many", lambda *args, **kwargs: [0])
    monkeypatch.setattr(kc, "keychain_set", lambda s, k, *a: keychain_calls.append((s, k)))

    rc = cli.main(["apply", "--skip-install"])
    assert rc == 0
    assert keychain_calls == [("https://api.example.com/v1", "sk-interactive-123")]
    assert configured == [("sk-interactive-123", "replace")]


def test_first_run_leaves_unselected_missing_app_untouched(monkeypatch, tmp_path):
    import byop.core.targets as tmod
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://api.example.com/v1", "secret-123", [ModelConfig("m")])
    calls = []

    class MissingTarget:
        name = "zed"
        display_name = "Zed"

        def is_installed(self):
            return False

        def install(self, log=print):
            calls.append("install")

        def configure(self, provider, **kwargs):
            calls.append("configure")

        def current_provider_names(self):
            return []

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [MissingTarget()])
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda: (
        provider, {"use_keychain": True, "use_env": False}
    ))
    monkeypatch.setattr(kc, "ensure_key", lambda *args, **kwargs: [])
    monkeypatch.setattr(kc, "keychain_set", lambda *args: None)
    monkeypatch.setattr(cli, "_run_status", lambda: 0)
    actions = iter(["Set up tools", "Quit"])
    monkeypatch.setattr(cli.prompt, "choose", lambda *args, **kwargs: next(actions))

    choices = []
    hints_seen = []
    monkeypatch.setattr(
        cli.prompt, "select_many",
        lambda question, options, **kw: (choices.extend(options), hints_seen.extend(kw.get("hints", ()))) and [],
    )
    assert cli.main([]) == 0
    assert choices == ["Zed"]
    assert hints_seen == ["not installed"]
    assert calls == []
    assert prof.get_active_profile_name() == "Demo"
    assert prof.list_profiles() == ["Demo"]


def test_noninteractive_without_installed_targets_does_not_install_everything(monkeypatch):
    import byop.core.targets as tmod

    class MissingTarget:
        name = "zed"
        display_name = "Zed"

        def is_installed(self):
            return False

        def install(self, **kwargs):
            raise AssertionError("must not install an unselected app")

        def configure(self, *args, **kwargs):
            raise AssertionError("must not configure an unselected app")

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [MissingTarget()])
    assert cli.main([
        "apply", "--provider", "Demo", "--api-url", "https://api.example.com/v1",
        "--api-key", "secret-123", "--model", "m", "--dry-run",
    ]) == 0


def test_skip_install_allows_selecting_missing_app(monkeypatch):
    import byop.core.targets as tmod

    class MissingTarget:
        name = "zed"
        display_name = "Zed"

        def is_installed(self):
            return False

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [MissingTarget()])
    choices = []
    hints_seen = []
    monkeypatch.setattr(
        cli.prompt, "select_many",
        lambda question, options, **kw: (choices.extend(options), hints_seen.extend(kw.get("hints", ()))) and [0],
    )
    args = cli.build_parser().parse_args(["apply", "--skip-install"])
    assert [target.name for target in cli._select_targets(args, interactive=True)] == ["zed"]
    assert choices == ["Zed"]
    assert hints_seen == ["not installed"]

def test_interactive_menu_adds_and_switches_provider(monkeypatch, tmp_path):
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    original = ProviderConfig("Original", "https://old.example/v1", "old-secret", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(original, name="default"))
    added = ProviderConfig("New", "https://new.example/v1", "new-secret", [ModelConfig("m")])
    monkeypatch.setattr(kc, "keychain_get", lambda *_: "old-secret")
    monkeypatch.setattr(kc, "ensure_key", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda prefill=None: (
        added, {"use_keychain": True, "use_env": False}
    ))
    monkeypatch.setattr(cli, "_run_status", lambda: 0)
    actions = iter(["Add provider", "Switch provider", "Original", "Quit"])
    def choose(*args, **kwargs):
        action = next(actions)
        if action == "Switch provider":
            assert prof.get_active_profile_name() == "New"
        return action

    monkeypatch.setattr(cli.prompt, "choose", choose)

    assert cli.main([]) == 0
    assert prof.load_profile("New").provider_name == "New"
    assert prof.get_active_profile_name() == "default"


def test_missing_target_install_failure_does_not_write_configuration(monkeypatch, tmp_path):
    import byop.core.targets as tmod

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(kc, "keychain_set", lambda *args: None)
    calls = []

    class MissingTarget:
        name = "zed"
        display_name = "Zed"

        def is_installed(self):
            return False

        def current_provider_names(self):
            return []

        def install(self, log=print):
            calls.append("install")

        def configure(self, *args, **kwargs):
            calls.append("configure")

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [MissingTarget()])
    assert cli.main([
        "apply", "--provider", "Demo", "--api-url", "https://api.example.com/v1",
        "--api-key", "secret-123", "--model", "m", "--target", "zed",
    ]) == 1
    assert calls == ["install"]


def test_interactive_acceptance_installs_then_configures_missing_app(monkeypatch, tmp_path):
    import byop.core.targets as tmod
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://api.example.com/v1", "secret-123", [ModelConfig("m")])
    calls = []

    class MissingTarget:
        name = "zed"
        display_name = "Zed"
        installed = False

        def is_installed(self):
            return self.installed

        def current_provider_names(self):
            return []

        def install(self, log=print):
            calls.append("install")
            self.installed = True

        def configure(self, provider, **kwargs):
            calls.append("configure")

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [MissingTarget()])
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda: (
        provider, {"use_keychain": True, "use_env": False}
    ))
    monkeypatch.setattr(kc, "ensure_key", lambda *args, **kwargs: [])
    monkeypatch.setattr(kc, "keychain_set", lambda *args: None)
    monkeypatch.setattr(cli, "_run_status", lambda: 0)
    actions = iter(["Set up tools", "Quit"])
    monkeypatch.setattr(cli.prompt, "choose", lambda *args, **kwargs: next(actions))
    monkeypatch.setattr(cli.prompt, "confirm", lambda question, default=False: True)
    monkeypatch.setattr(cli.prompt, "select_many", lambda *args, **kwargs: [0])

    assert cli.main([]) == 0
    assert calls == ["install", "configure"]


def test_added_env_provider_can_be_used_in_same_session(monkeypatch, tmp_path):
    import byop.core.targets as tmod
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("DEMO_API_KEY", raising=False)
    provider = ProviderConfig("Demo", "https://api.example.com/v1", "secret-123", [ModelConfig("m")])
    applied = []

    class InstalledTarget:
        name = "zed"
        display_name = "Zed"

        def is_installed(self):
            return True

        def current_provider_names(self):
            return []

        def install(self, log=print):
            raise AssertionError("installed tools must not be upgraded automatically")

        def configure(self, provider, **kwargs):
            applied.append(provider.api_key)

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [InstalledTarget()])
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda prefill=None: (
        provider, {"use_keychain": False, "use_env": True}
    ))
    monkeypatch.setattr(kc, "ensure_key", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli.prompt, "confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli.prompt, "select_many", lambda *args, **kwargs: [0])
    monkeypatch.setattr(cli, "_run_status", lambda: 0)
    actions = iter(["Add provider", "Set up tools", "Quit"])
    monkeypatch.setattr(cli.prompt, "choose", lambda *args, **kwargs: next(actions))

    assert cli.main([]) == 0
    assert prof.load_profile("Demo").api_key_ref == "env:DEMO_API_KEY"
    assert applied == ["secret-123"]


def test_adding_existing_provider_does_not_overwrite_or_store_key(monkeypatch, tmp_path):
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://api.example.com/v1", "secret-123", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(provider, name="default"))
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda: (
        provider, {"use_keychain": True, "use_env": False}
    ))
    monkeypatch.setattr(kc, "ensure_key", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("must not overwrite existing credentials")
    ))
    assert cli._profile_new_interactive(activate=True) == 2
    assert prof.load_profile("default").provider_name == "Demo"
    assert not prof.profile_exists("Demo")


def test_zed_questions_only_appear_during_zed_setup(monkeypatch, tmp_path):
    import byop.core.targets as tmod
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://api.example.com/v1", "secret-123", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(provider, name="Demo"))
    prof.set_active_profile_name("Demo")
    monkeypatch.setattr(kc, "keychain_get", lambda *_: "secret-123")
    monkeypatch.setattr(kc, "keychain_set", lambda *args: None)
    configured = {}

    class Target:
        def __init__(self, name):
            self.name = name
            self.display_name = name

        def is_installed(self):
            return True

        def current_provider_names(self):
            return []

        def configure(self, provider, **kwargs):
            configured[self.name] = provider

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [Target("zed"), Target("py")])
    select_calls = []

    def select_many(title, options, **kw):
        select_calls.append(title)
        if title == "Tools":
            return [0, 1]
        # Zed roles checklist: pick only "Git commit messages" (index 2).
        return [2]

    monkeypatch.setattr(cli.prompt, "select_many", select_many)
    args = cli._make_args_for_apply(prof.load_profile("Demo"), "secret-123", {"use_keychain": True})
    args.skip_install = True
    assert cli._run_apply(args, interactive_session=True) == 0
    assert select_calls == ["Tools", "Zed roles"]
    assert configured["zed"].set_default_agent is False
    assert configured["zed"].set_commit_message is True
    assert configured["py"].set_default_agent is True
    assert prof.load_profile("Demo").set_commit_message is True

def test_edit_provider_renames_profile_without_overwriting_another(monkeypatch, tmp_path):
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    original = ProviderConfig("Demo", "https://api.example.com/v1", "secret-123", [ModelConfig("m")])
    other = ProviderConfig("Other", "https://other.example/v1", "secret-456", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(original, name="Demo"))
    prof.save_profile(prof.profile_from_provider(other, name="Other"))
    prof.set_active_profile_name("Demo")
    monkeypatch.setattr(kc, "keychain_get", lambda *_: "secret-123")
    writes = []
    monkeypatch.setattr(kc, "keychain_set", lambda *args: writes.append(args))
    monkeypatch.setattr(cli.wizard, "edit_provider", lambda provider, storage: (other, storage))

    assert cli._profile_edit_interactive() == 2
    assert prof.get_active_profile_name() == "Demo"
    assert writes == []
    assert prof.load_profile("Other").api_url == "https://other.example/v1"

    renamed = ProviderConfig("Renamed", original.api_url, "", original.models)
    monkeypatch.setattr(cli.wizard, "edit_provider", lambda provider, storage: (
        renamed, storage
    ))
    assert cli._profile_edit_interactive() == 0
    assert prof.get_active_profile_name() == "Renamed"
    assert prof.load_profile("Renamed").provider_name == "Renamed"
    assert not prof.profile_exists("Demo")
    assert writes == []


def test_apply_uses_saved_profile_without_repeating_wizard(monkeypatch, tmp_path):
    import byop.core.targets as tmod
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CUSTOM_SECRET", "secret-123")
    provider = ProviderConfig("Demo", "https://api.example.com/v1", "secret-123", [
        ModelConfig("pro", max_tokens=750000, max_output_tokens=24000),
        ModelConfig("lite", max_tokens=128000, max_output_tokens=8000),
    ])
    prof.save_profile(prof.profile_from_provider(provider, name="Demo", api_key_ref="env:CUSTOM_SECRET"))
    prof.set_active_profile_name("Demo")
    configured = []

    class Target:
        name = "claude"
        display_name = "Claude Code"

        def is_installed(self):
            return True

        def current_provider_names(self):
            return []

        def configure(self, value, **kwargs):
            configured.append((value, kwargs))

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [Target()])
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("saved profile must not reopen the wizard")
    ))
    assert cli.main(["apply", "--target", "claude", "--dry-run", "--skip-install"]) == 0
    assert configured[0][0].models == provider.models
    assert configured[0][1]["dry_run"] is True


def test_scripted_apply_does_not_overwrite_unrelated_legacy_profile(monkeypatch, tmp_path):
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    other = ProviderConfig("Other", "https://other.example/v1", "secret-456", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(other, name="Demo"))
    writes = []
    monkeypatch.setattr(kc, "keychain_set", lambda *args: writes.append(args))
    assert cli.main([
        "apply", "--provider", "Demo", "--api-url", "https://api.example.com/v1",
        "--api-key", "secret-123", "--model", "m", "--skip-install",
    ]) == 2
    assert prof.load_profile("Demo").provider_name == "Other"
    assert writes == []


def test_checklist_applies_only_selected_tools_and_installs_only_missing(monkeypatch, tmp_path):
    import byop.core.targets as tmod
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://example.com/v1", "secret-123", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(provider, name="Demo"))
    prof.set_active_profile_name("Demo")
    monkeypatch.setattr(kc, "keychain_set", lambda *args: None)
    events = []

    class Target:
        def __init__(self, name, installed):
            self.name = self.display_name = name
            self.installed = installed

        def is_installed(self):
            return self.installed

        def current_provider_names(self):
            return []

        def install(self, log=print):
            events.append(("install", self.name))
            self.installed = True

        def configure(self, provider, **kwargs):
            events.append(("configure", self.name))

    targets = [Target("py", True), Target("omp", False), Target("opencode", True)]
    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: targets)
    choices = []
    hints_seen = []
    confirm_questions = []
    monkeypatch.setattr(
        cli.prompt, "select_many",
        lambda title, options, **kw: (choices.extend(options), hints_seen.extend(kw.get("hints", ()))) and [0, 1],
    )
    monkeypatch.setattr(
        cli.prompt, "confirm",
        lambda question, default=False: confirm_questions.append((question, default)) or True,
    )
    args = cli._make_args_for_apply(prof.load_profile("Demo"), "secret-123", {"use_keychain": True})
    assert cli._run_apply(args, interactive_session=True) == 0
    assert choices == ["py", "omp", "opencode"]
    assert hints_seen == ["installed", "not installed", "installed"]
    assert confirm_questions == [("Install omp automatically?", True)]
    assert events == [("configure", "py"), ("install", "omp"), ("configure", "omp")]


def test_declining_auto_install_skips_missing_tool_and_configures_installed(monkeypatch, tmp_path):
    import byop.core.targets as tmod
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://example.com/v1", "secret-123", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(provider, name="Demo"))
    prof.set_active_profile_name("Demo")
    monkeypatch.setattr(kc, "keychain_set", lambda *args: None)
    events = []

    class Target:
        def __init__(self, name, installed):
            self.name = self.display_name = name
            self.installed = installed

        def is_installed(self):
            return self.installed

        def current_provider_names(self):
            return []

        def install(self, log=print):
            events.append(("install", self.name))

        def configure(self, provider, **kwargs):
            events.append(("configure", self.name))

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [Target("claude", True), Target("Warp", False)])
    monkeypatch.setattr(cli.prompt, "select_many", lambda *args, **kwargs: [0, 1])
    monkeypatch.setattr(cli.prompt, "confirm", lambda question, default=False: False)
    args = cli._make_args_for_apply(prof.load_profile("Demo"), "secret-123", {"use_keychain": True})
    assert cli._run_apply(args, interactive_session=True) == 0
    assert events == [("configure", "claude")]

def test_cancelled_checklist_does_not_change_credentials_or_profile(monkeypatch, tmp_path, capsys):
    import byop.core.targets as tmod
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://example.com/v1", "secret-123", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(provider, name="Demo"))
    prof.set_active_profile_name("Demo")
    before = (tmp_path / "profiles" / "Demo.toml").read_bytes()
    monkeypatch.setattr(kc, "keychain_get", lambda *_: "secret-123")
    writes = []
    monkeypatch.setattr(kc, "keychain_set", lambda *args: writes.append(args))

    class Target:
        name = "py"
        display_name = "Pi"

        def is_installed(self):
            return False

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [Target()])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda **kwargs: (
        provider, {"use_keychain": True, "use_env": False}
    ))

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.prompt, "select_many", cancel)
    assert cli.main(["apply"]) == 130
    assert "Cancelled." in capsys.readouterr().out
    assert (tmp_path / "profiles" / "Demo.toml").read_bytes() == before
    assert writes == []


def test_codex_prompts_before_replacing_different_active_provider(monkeypatch, tmp_path):
    import byop.core.targets as tmod
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://example.com/v1", "secret-123", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(provider, name="Demo"))
    prof.set_active_profile_name("Demo")
    key_writes = []
    monkeypatch.setattr(kc, "keychain_set", lambda *args: key_writes.append(args))
    actions = []
    questions = []
    original_profile = (tmp_path / "profiles" / "Demo.toml").read_bytes()

    class Target:
        name = "codex"
        display_name = "Codex"

        def is_installed(self):
            return True

        def current_provider_names(self):
            return ["Other"]

        def configure(self, provider, **kwargs):
            actions.append(kwargs["conflict_action"])

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [Target()])
    monkeypatch.setattr(cli.prompt, "select_many", lambda *args, **kwargs: [0])
    monkeypatch.setattr(cli.prompt, "choose_action", lambda *args, **kwargs: questions.append(args) or "skip")
    args = cli._make_args_for_apply(prof.load_profile("Demo"), "secret-123", {"use_keychain": True})
    args.skip_install = True
    assert cli._run_apply(args, interactive_session=True) == 0
    assert questions
    assert actions == []
    assert key_writes == []
    assert (tmp_path / "profiles" / "Demo.toml").read_bytes() == original_profile


def test_saved_profile_preserves_model_limits_when_setting_up_claude(monkeypatch, tmp_path, capsys):
    import byop.core.targets as tmod
    from byop.core.config import ModelConfig, ProviderConfig
    from byop.core.targets.claude import ClaudeTarget

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path / "byop"))
    provider = ProviderConfig(
        "Demo", "https://example.com/v1", "secret-123",
        [ModelConfig("pro", max_tokens=1000000, max_output_tokens=16000),
         ModelConfig("lite", max_tokens=128000, max_output_tokens=8000)],
    )
    prof.save_profile(prof.profile_from_provider(provider, name="Demo"))
    prof.set_active_profile_name("Demo")
    settings = tmp_path / "claude" / "settings.json"
    target = ClaudeTarget(settings)
    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [target])
    monkeypatch.setattr(cli.prompt, "select_many", lambda *args, **kwargs: [0])
    monkeypatch.setattr(kc, "keychain_set", lambda *args: None)
    monkeypatch.setattr(kc, "keychain_has", lambda *args: True)
    monkeypatch.setattr(kc, "ensure_key", lambda *args, **kwargs: [])
    args = cli._make_args_for_apply(prof.load_profile("Demo"), "secret-123", {"use_keychain": True})
    args.skip_install = True

    assert cli._run_apply(args, interactive_session=True) == 0
    env = json.loads(settings.read_text())["env"]
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "1000000"
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "16000"
    assert prof.load_profile("Demo").models[0].max_tokens == 1000000
    output = capsys.readouterr().out
    assert "session-wide" in output
    assert "secret-123" not in output


def test_cli_model_spec_global_precedence_aliases_and_capabilities():
    args = cli.build_parser().parse_args([
        "apply", "--provider", "Demo", "--api-url", "https://example.com/v1",
        "--api-key", "secret-123", "--max-tokens", "1000000",
        "--max-output-tokens", "16000", "--images",
        "--model", "base",
        "--model", "special:contextWindow=750000:max_output_tokens=25000:"
                   "parallel_tool_calls:tools=false:prompt_cache_key:chat_completions=false",
        "--model", "legacy:context_window=600000:display=Legacy:reasoning_effort=high:"
                   "interleaved_reasoning:max_tokens_parameter:images=false",
    ])
    models = cli._provider_from_args(args).models
    assert [(m.name, m.max_tokens, m.max_output_tokens) for m in models] == [
        ("base", 1000000, 16000), ("special", 750000, 25000), ("legacy", 600000, 16000)
    ]
    assert models[0].capabilities["images"] is True
    assert models[1].capabilities["images"] is True
    assert models[1].capabilities["parallel_tool_calls"] is True
    assert models[1].capabilities["tools"] is False
    assert models[1].capabilities["prompt_cache_key"] is True
    assert models[1].capabilities["chat_completions"] is False
    assert models[2].display_name == "Legacy"
    assert models[2].reasoning_effort == "high"
    assert models[2].capabilities["images"] is False
    assert models[2].capabilities["interleaved_reasoning"] is True
    assert models[2].capabilities["max_tokens_parameter"] is True


def test_profile_editor_metadata_save_and_cancel_never_touch_credentials(monkeypatch, tmp_path):
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig(
        "Demo", "https://example.com/v1", "secret-123",
        [ModelConfig("first", display_name="First", max_tokens=600000,
                     max_output_tokens=24000, capabilities={"images": True}),
         ModelConfig("second", reasoning_effort="high", capabilities={"tools": False})],
    )
    profile = prof.profile_from_provider(provider, name="Demo", api_key_ref="env:CUSTOM_SECRET")
    prof.save_profile(profile)
    prof.set_active_profile_name("Demo")
    before = (tmp_path / "profiles" / "Demo.toml").read_bytes()
    monkeypatch.setattr(cli, "_load_profile_api_key", lambda *_: (_ for _ in ()).throw(
        AssertionError("metadata editing must not fetch secrets")
    ))
    monkeypatch.setattr(kc, "keychain_set", lambda *a: (_ for _ in ()).throw(
        AssertionError("metadata editing must not touch keychain")
    ))
    monkeypatch.setattr(kc, "write_env_to_profile", lambda *a: (_ for _ in ()).throw(
        AssertionError("metadata editing must not touch shell profile")
    ))
    monkeypatch.setattr(cli.wizard, "edit_provider", lambda p, storage: None)
    assert cli._profile_edit_interactive() == 0
    assert (tmp_path / "profiles" / "Demo.toml").read_bytes() == before
    monkeypatch.setattr(cli.wizard, "edit_provider", lambda p, storage: (p, storage))
    assert cli._profile_edit_interactive() == 0
    assert (tmp_path / "profiles" / "Demo.toml").read_bytes() == before

    def edit(p, storage):
        assert storage == "Shell env only (plaintext)"
        assert p.api_key == ""
        p.models.insert(0, p.models.pop(1))
        p.models[0] = ModelConfig(
            "second-new", reasoning_effort="high", max_tokens=750000,
            capabilities={"tools": False, "prompt_cache_key": True},
        )
        return p, storage

    monkeypatch.setattr(cli.wizard, "edit_provider", edit)
    assert cli._profile_edit_interactive() == 0
    saved = prof.load_profile("Demo")
    assert saved.api_key_ref == "env:CUSTOM_SECRET"
    assert saved.env_var == "CUSTOM_SECRET"
    assert [m.name for m in saved.models] == ["second-new", "first"]
    assert saved.models[0].max_tokens == 750000
    assert saved.models[0].capabilities["prompt_cache_key"] is True
    assert saved.models[1] == provider.models[0]


def test_profile_editor_keychain_url_migration_and_env_rename(monkeypatch, tmp_path):
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://old.example/v1", "secret-123", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(provider, name="Demo"))
    prof.set_active_profile_name("Demo")
    reads, writes, env_writes = [], [], []
    monkeypatch.setattr(kc, "keychain_get", lambda server: reads.append(server) or "secret-123")
    monkeypatch.setattr(kc, "keychain_set", lambda *a: writes.append(a))
    monkeypatch.setattr(kc, "write_env_to_profile", lambda *a: env_writes.append(a))
    monkeypatch.setattr(cli.wizard, "edit_provider", lambda p, storage: (
        replace(p, api_url="https://new.example/v1"), storage
    ))
    assert cli._profile_edit_interactive() == 0
    assert reads == ["https://old.example/v1"]
    assert writes == [("https://new.example/v1", "secret-123")]
    assert env_writes == []
    assert prof.load_profile("Demo").api_url == "https://new.example/v1"

    env = prof.load_profile("Demo")
    env.api_key_ref = "env:CUSTOM_SECRET"
    env.env_var = "CUSTOM_SECRET"
    prof.save_profile(env, allow_overwrite=True)
    monkeypatch.delenv("CUSTOM_SECRET", raising=False)
    monkeypatch.setattr(cli.wizard, "edit_provider", lambda p, storage: (
        replace(p, provider_name="Renamed"), storage
    ))
    assert cli._profile_edit_interactive() == 0
    assert writes == [("https://new.example/v1", "secret-123")]
    assert env_writes == []
    assert prof.load_profile("Renamed").api_key_ref == "env:CUSTOM_SECRET"
    assert not prof.profile_exists("Demo")


def test_saved_profile_apply_preserves_all_models_and_custom_env_reference(monkeypatch, tmp_path):
    import byop.core.targets as targets
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig(
        "Demo", "https://example.com/v1", "secret-123",
        [ModelConfig("primary", max_tokens=900000, max_output_tokens=30000,
                     capabilities={"tools": True, "parallel_tool_calls": True}),
         ModelConfig("secondary", display_name="Backup", max_tokens=500000,
                     max_output_tokens=8000, reasoning_effort="none",
                     capabilities={"chat_completions": False, "images": True})],
    )
    profile = prof.profile_from_provider(provider, name="Demo", api_key_ref="env:CUSTOM_SECRET")
    prof.save_profile(profile)
    prof.set_active_profile_name("Demo")
    configured = []

    class Target:
        name = "py"
        display_name = "Pi"

        def is_installed(self):
            return True

        def current_provider_names(self):
            return []

        def configure(self, applied, **kwargs):
            configured.append(applied)

    monkeypatch.setattr(targets, "available_targets", lambda **kw: [Target()])
    args = cli._make_args_for_apply(profile, "secret-123", {
        "use_keychain": False, "use_env": True,
    })
    args.target = ["py"]
    args.skip_install = True
    assert cli._run_apply(args, interactive_session=True) == 0
    assert configured[0].models == provider.models
    stored = prof.load_profile("Demo")
    assert stored.models == provider.models
    assert stored.api_key_ref == "env:CUSTOM_SECRET"
    assert stored.env_var == "CUSTOM_SECRET"


def test_editor_api_key_only_and_storage_switch_write_only_destination(monkeypatch, tmp_path):
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://example.com/v1", "secret-123", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(provider, name="Demo"))
    prof.set_active_profile_name("Demo")
    reads, writes, exports = [], [], []
    secret = {"key": "secret-123"}
    monkeypatch.setattr(kc, "keychain_get", lambda server: reads.append(server) or secret["key"])

    def store(server, key):
        writes.append((server, key))
        secret["key"] = key

    monkeypatch.setattr(kc, "keychain_set", store)
    monkeypatch.setattr(kc, "write_env_to_profile", lambda *args: exports.append(args))
    choices = iter(["API key", "Save"])
    monkeypatch.setattr(cli.prompt, "choose", lambda *a, **kw: next(choices))
    monkeypatch.setattr(cli.prompt, "ask_secret", lambda *a, **kw: "new-secret-456")
    assert cli._profile_edit_interactive() == 0
    assert writes == [("https://example.com/v1", "new-secret-456")]
    assert exports == [] and reads == []

    monkeypatch.setattr(cli.wizard, "edit_provider", lambda p, storage: (
        p, "Shell env only (plaintext)"
    ))
    assert cli._profile_edit_interactive() == 0
    assert reads == ["https://example.com/v1"]
    assert exports == [("DEMO_API_KEY", "new-secret-456")]
    assert prof.load_profile("Demo").api_key_ref == "env:DEMO_API_KEY"
    assert writes == [("https://example.com/v1", "new-secret-456")]


def test_editor_rejects_overwriting_shared_keychain_url(monkeypatch, tmp_path):
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    for name in ("Demo", "Other"):
        provider = ProviderConfig(name, "https://shared.example/v1", "secret-123", [ModelConfig("m")])
        prof.save_profile(prof.profile_from_provider(provider, name=name))
    prof.set_active_profile_name("Demo")
    monkeypatch.setattr(cli.wizard, "edit_provider", lambda p, storage: (
        replace(p, api_key="replacement-456"), storage
    ))
    monkeypatch.setattr(kc, "keychain_set", lambda *a: (_ for _ in ()).throw(
        AssertionError("shared credential must not change")
    ))
    assert cli._profile_edit_interactive() == 2
    assert prof.load_profile("Demo").api_key_ref == "keychain"


def test_combined_storage_survives_metadata_edit_and_remains_available_for_apply(monkeypatch, tmp_path):
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://example.com/v1", "secret-123", [ModelConfig("m")])
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda: (
        provider, {"use_keychain": True, "use_env": True}
    ))
    monkeypatch.setattr(kc, "ensure_key", lambda *a, **kw: [])
    assert cli._profile_new_interactive(activate=True) == 0
    assert prof.load_profile("Demo").export_env is True

    def edit(p, storage):
        assert storage == "Keychain + shell env (plaintext)"
        return replace(p, models=[replace(p.models[0], max_tokens=500000)]), storage

    monkeypatch.setattr(cli.wizard, "edit_provider", edit)
    monkeypatch.setattr(cli, "_load_profile_api_key", lambda *_: (_ for _ in ()).throw(
        AssertionError("metadata-only editing must not read the Keychain")
    ))
    monkeypatch.setattr(kc, "keychain_set", lambda *a: (_ for _ in ()).throw(
        AssertionError("metadata-only editing must not change the Keychain")
    ))
    monkeypatch.setattr(kc, "write_env_to_profile", lambda *a: (_ for _ in ()).throw(
        AssertionError("metadata-only editing must not change the shell profile")
    ))
    assert cli._profile_edit_interactive() == 0
    saved = prof.load_profile("Demo")
    assert saved.export_env is True and saved.models[0].max_tokens == 500000

    monkeypatch.setattr(cli, "_load_profile_api_key", lambda *_: "secret-123")
    choices = iter(["Set up tools", "Quit"])
    monkeypatch.setattr(cli.prompt, "choose", lambda *a, **kw: next(choices))
    applied = []
    monkeypatch.setattr(cli, "_run_apply", lambda args, **kw: applied.append(args.env_key) or 0)
    assert cli.main([]) == 0
    assert applied == [True]


def test_saved_external_env_is_used_without_persisting_secret(monkeypatch, tmp_path):
    import subprocess
    import tomllib

    import byop.core.targets as targets
    from byop.core.config import ModelConfig, ProviderConfig
    from byop.core.targets.claude import ClaudeTarget
    from byop.core.targets.codex import CodexTarget
    from byop.core.targets.omp import OmpTarget
    from byop.core.targets.opencode import OpencodeTarget
    from byop.core.targets.py import PyTarget
    from byop.core.targets.zed import ZedTarget

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(home / "byop"))
    monkeypatch.setenv("CUSTOM_SECRET", "dummy-external-secret")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    provider = ProviderConfig("Demo", "https://example.com/v1", "dummy-external-secret", [
        ModelConfig("m", max_tokens=240000, max_output_tokens=16000)
    ])
    profile = prof.profile_from_provider(provider, name="Demo", api_key_ref="env:CUSTOM_SECRET")
    prof.save_profile(profile)
    prof.set_active_profile_name("Demo")
    pi, omp = PyTarget(home / "pi.json"), OmpTarget(home / "omp.yml")
    claude, opencode = ClaudeTarget(home / "claude.json"), OpencodeTarget(home / "opencode.json")
    codex, zed = CodexTarget(home / "codex.toml"), ZedTarget(home / "zed.json")
    monkeypatch.setattr(targets, "available_targets", lambda **kw: [pi, omp, claude, opencode, codex, zed])
    monkeypatch.setattr(kc, "ensure_key", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("existing external secret must not be stored")
    ))
    args = ["apply", "--skip-install", "--conflict", "replace"]
    for name in ("py", "omp", "claude", "opencode", "codex", "zed"):
        args.extend(["--target", name])
    assert cli.main(args) == 0
    assert json.loads(pi.models_path.read_text())["providers"]["Demo"]["apiKey"] == "$CUSTOM_SECRET"
    assert omp._load()["providers"]["Demo"]["apiKey"] == "CUSTOM_SECRET"
    assert json.loads(claude.settings_path.read_text())["apiKeyHelper"] == "printenv CUSTOM_SECRET"
    assert json.loads(opencode.config_path.read_text())["provider"]["Demo"]["options"]["apiKey"] == "{env:CUSTOM_SECRET}"
    with codex.config_path.open("rb") as f:
        assert tomllib.load(f)["model_providers"]["Demo"]["env_key"] == "CUSTOM_SECRET"
    shell = home / ".zshrc"
    assert "dummy-external-secret" not in shell.read_text()
    env = {"CUSTOM_SECRET": "dummy-external-secret", "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        ["/bin/sh", "-c", '. "$1"; printf %s "$DEMO_API_KEY"', "sh", str(shell)],
        env=env, capture_output=True, text=True, check=True,
    )
    assert result.stdout == "dummy-external-secret"
    for path in (pi.models_path, omp.models_path, claude.settings_path, opencode.config_path,
                 codex.config_path, zed.settings_path):
        assert "dummy-external-secret" not in path.read_text()
    assert prof.load_profile("Demo").api_key_ref == "env:CUSTOM_SECRET"


def test_shared_keychain_url_rejects_different_key_before_writes(monkeypatch, tmp_path):
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    existing = ProviderConfig("Existing", "https://shared.example/v1", "old-secret-123", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(existing, name="Existing"))
    prof.set_active_profile_name("Existing")
    other = replace(existing, provider_name="Other", api_key="new-secret-456")
    monkeypatch.setattr(kc, "keychain_get", lambda *_: "old-secret-123")
    monkeypatch.setattr(kc, "ensure_key", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("shared credential must not change")
    ))
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda: (
        other, {"use_keychain": True, "use_env": False}
    ))
    assert cli._profile_new_interactive() == 2
    assert cli._run_first_run_wizard() == 2
    assert cli.main([
        "apply", "--provider", "Other", "--api-url", other.api_url, "--api-key", other.api_key,
        "--model", "m", "--target", "py", "--skip-install", "--conflict", "replace",
    ]) == 2
    assert not prof.profile_exists("Other")
    assert prof.load_profile("Existing").api_url == existing.api_url


def test_combined_storage_rejects_invalid_key_before_updating_keychain(monkeypatch, tmp_path):
    from byop.core.config import ModelConfig, ProviderConfig

    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://example.com/v1", "original-key", [ModelConfig("m")])
    profile = prof.profile_from_provider(provider, name="Demo")
    profile.export_env = True
    prof.save_profile(profile)
    prof.set_active_profile_name("Demo")
    before = (tmp_path / "profiles" / "Demo.toml").read_bytes()
    monkeypatch.setattr(cli.wizard, "edit_provider", lambda p, storage: (
        replace(p, api_key="invalid\nnew-key"), storage
    ))
    monkeypatch.setattr(kc, "keychain_set", lambda *a: (_ for _ in ()).throw(
        AssertionError("invalid key must not change Keychain")
    ))
    monkeypatch.setattr(kc, "write_env_to_profile", lambda *a: (_ for _ in ()).throw(
        AssertionError("invalid key must not change shell profile")
    ))
    assert cli._profile_edit_interactive() == 2
    assert (tmp_path / "profiles" / "Demo.toml").read_bytes() == before
