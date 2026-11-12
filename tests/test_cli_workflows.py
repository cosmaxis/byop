import json
from dataclasses import replace

import pytest

from byop import cli
from byop.core import keychain as kc
from byop.core import profiles as prof
from byop.core import targets
from byop.core.config import ModelConfig, ProviderConfig
from byop.core.targets.py import PyTarget


@pytest.mark.parametrize("configured", [False, True])
def test_status_and_doctor_report_missing_provider_without_writing(
    monkeypatch, tmp_path, capsys, configured,
):
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path / "byop"))
    provider = ProviderConfig("Demo", "https://example.com/v1", "secret-123", [ModelConfig("m")])
    prof.save_profile(prof.profile_from_provider(provider, name="default"))
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"providers": {"Demo" if configured else "Other": {}}}))
    target = PyTarget(models_path=path)
    monkeypatch.setattr(target, "is_installed", lambda: True)
    monkeypatch.setattr(targets, "available_targets", lambda **kwargs: [target])
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    assert cli.main(["status"]) == 0
    status = capsys.readouterr().out
    assert str(path) in status
    assert ("not configured" in status) is not configured
    assert cli.main(["doctor"]) == (0 if configured else 1)
    diagnosis = capsys.readouterr().out
    assert ("missing Demo" in diagnosis) is not configured
    assert "secret" not in status + diagnosis
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_profile_cli_lifecycle_preserves_secret_reference_and_rejects_unknown_switch(
    monkeypatch, tmp_path, capsys,
):
    monkeypatch.setenv("BYOP_CONFIG_DIR", str(tmp_path))
    provider = ProviderConfig("Demo", "https://example.com/v1", "secret-123", [ModelConfig("m")])
    monkeypatch.setattr(cli.wizard, "run_wizard", lambda: (
        provider, {"use_keychain": True, "use_env": False},
    ))
    monkeypatch.setattr(kc, "ensure_key", lambda *args, **kwargs: [])

    assert cli.main(["profile", "new"]) == 0
    assert prof.load_profile("Demo").api_key_ref == "keychain"
    assert cli.main(["profile", "use", "Demo"]) == 0
    assert cli.main(["profile", "use", "missing"]) == 2
    assert prof.get_active_profile_name() == "Demo"
    assert cli.main(["profile", "list"]) == 0
    assert "Demo" in capsys.readouterr().out

    def reject_secret_access(*args, **kwargs):
        raise AssertionError("metadata editing must not access credentials")

    monkeypatch.setattr(cli, "_load_profile_api_key", reject_secret_access)
    monkeypatch.setattr(kc, "keychain_set", reject_secret_access)
    monkeypatch.setattr(cli.wizard, "edit_provider", lambda config, storage: (
        replace(config, models=[ModelConfig("updated", max_tokens=256000)]), storage,
    ))
    assert cli.main(["profile", "edit"]) == 0
    saved = prof.load_profile()
    assert saved.models[0].name == "updated"
    assert saved.models[0].max_tokens == 256000
    assert saved.api_key_ref == "keychain"
    capsys.readouterr()
    assert cli.main(["profile", "export"]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["models"][0]["id"] == "updated"
    assert "api_key" not in exported
    assert provider.api_key not in json.dumps(exported)

    assert cli.main(["profile", "delete", "Demo"]) == 0
    assert not prof.profile_exists("Demo")
    assert prof.get_active_profile_name() == "default"
    assert cli.main(["profile", "delete", "Demo"]) == 2
