"""End-to-end smoke test for the CLI entry point (targets mocked)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop import cli
from byop.core import keychain as kc
from byop.core import profiles as prof


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
    monkeypatch.setattr(kc, "ensure_key", lambda *args, **kwargs: writes.append(args))
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda **kwargs: (
        other, {"use_keychain": True, "use_env": False}
    ))

    assert cli._profile_edit_interactive() == 2
    assert prof.get_active_profile_name() == "Demo"
    assert writes == []
    assert prof.load_profile("Other").api_url == "https://other.example/v1"

    renamed = ProviderConfig("Renamed", original.api_url, "secret-123", original.models)
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda **kwargs: (
        renamed, {"use_keychain": True, "use_env": False}
    ))
    assert cli._profile_edit_interactive() == 0
    assert prof.get_active_profile_name() == "Renamed"
    assert prof.load_profile("Renamed").provider_name == "Renamed"
    assert not prof.profile_exists("Demo")


def test_interactive_apply_rejects_existing_provider_name(monkeypatch, tmp_path):
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
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda **kwargs: (
        other, {"use_keychain": True, "use_env": False}
    ))
    assert cli.main(["apply", "--skip-install"]) == 2
    assert prof.get_active_profile_name() == "Demo"
    assert prof.load_profile("Other").api_url == "https://other.example/v1"
    assert writes == []


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
    monkeypatch.setattr(kc, "keychain_set", lambda *args: None)
    actions = []

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
    monkeypatch.setattr(cli.prompt, "choose_action", lambda *args, **kwargs: "skip")
    args = cli._make_args_for_apply(prof.load_profile("Demo"), "secret-123", {"use_keychain": True})
    args.skip_install = True
    assert cli._run_apply(args, interactive_session=True) == 0
    assert actions == ["skip"]
