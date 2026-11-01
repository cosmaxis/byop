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


def test_cli_session_loop_quits(monkeypatch, tmp_path):
    """Bare byop with an existing profile enters the session loop and quits."""
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))

    from byop.core import profiles as prof
    from byop.core.config import ModelConfig, ProviderConfig

    provider = ProviderConfig(
        provider_name="T",
        api_url="https://x",
        api_key="sk-tes",
        models=[ModelConfig(name="m", max_tokens=1000, max_output_tokens=100)],
    )
    prof.save_profile(
        prof.profile_from_provider(provider, name=prof.DEFAULT_PROFILE_NAME),
        api_key="sk-tes",
    )

    # Drive the session loop: status prints, then user picks "quit".
    call_count = {"status": 0}

    orig_status = cli._run_status

    def fake_status():
        call_count["status"] += 1
        return orig_status()

    def fake_choose(prompt_text, options, default_index=0):
        return "Quit"

    monkeypatch.setattr(cli, "_run_status", fake_status)
    monkeypatch.setattr(cli.prompt, "choose", fake_choose)

    rc = cli.main([])

    assert rc == 0
    assert call_count["status"] >= 1


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
    monkeypatch.setattr(kc, "keychain_set", lambda s, k, *a: keychain_calls.append((s, k)))

    rc = cli.main(["apply", "--skip-install"])
    assert rc == 0
    assert keychain_calls == [("https://api.example.com/v1", "sk-interactive-123")]
    assert configured == [("sk-interactive-123", "replace")]


def test_first_run_asks_before_setting_up_missing_app(monkeypatch, tmp_path):
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

    def decline(question, default=False):
        calls.append((question, default))
        return False

    monkeypatch.setattr(cli.prompt, "confirm", decline)
    assert cli.main([]) == 0
    assert calls == [("Install and configure Zed?", False)]


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


def test_skip_install_asks_before_configuring_missing_app(monkeypatch):
    import byop.core.targets as tmod

    class MissingTarget:
        name = "zed"
        display_name = "Zed"

        def is_installed(self):
            return False

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [MissingTarget()])
    questions = []
    monkeypatch.setattr(
        cli.prompt, "confirm",
        lambda question, default: questions.append((question, default)) or False,
    )
    args = cli.build_parser().parse_args(["apply", "--skip-install"])
    assert cli._select_targets(args, interactive=True) == []
    assert questions == [("Configure Zed without installing?", False)]


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
    monkeypatch.setattr(cli.prompt, "ask", lambda *args, **kwargs: "new")
    monkeypatch.setattr(cli, "_run_status", lambda: 0)
    actions = iter(["Add provider", "Switch provider", "default", "Quit"])
    def choose(*args, **kwargs):
        action = next(actions)
        if action == "Switch provider":
            assert prof.get_active_profile_name() == "new"
        return action

    monkeypatch.setattr(cli.prompt, "choose", choose)

    assert cli.main([]) == 0
    assert prof.load_profile("new").provider_name == "New"
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
            pass

        def configure(self, provider, **kwargs):
            applied.append(provider.api_key)

    monkeypatch.setattr(tmod, "available_targets", lambda **kwargs: [InstalledTarget()])
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda prefill=None: (
        provider, {"use_keychain": False, "use_env": True}
    ))
    monkeypatch.setattr(kc, "ensure_key", lambda *args, **kwargs: [])
    monkeypatch.setattr(cli.prompt, "ask", lambda *args, **kwargs: "work")
    monkeypatch.setattr(cli.prompt, "confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "_run_status", lambda: 0)
    actions = iter(["Add provider", "Set up tools", "Quit"])
    monkeypatch.setattr(cli.prompt, "choose", lambda *args, **kwargs: next(actions))

    assert cli.main([]) == 0
    assert prof.load_profile("work").api_key_ref == "env:DEMO_API_KEY"
    assert applied == ["secret-123"]
