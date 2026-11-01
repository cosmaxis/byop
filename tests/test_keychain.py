"""Tests for keychain/env key injection (subprocess mocked)."""

import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core import keychain as kc


def _fake_run(returncode=0, stdout="", stderr=""):
    def _run(cmd, *a, **k):
        class R:
            pass

        r = R()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = stderr
        return r

    return _run


def test_keychain_has_true():
    with mock.patch.object(kc, "_run", _fake_run(0)):
        assert kc.keychain_has("https://x/v1") is True


def test_keychain_has_false():
    # `security` exits 44 ("item not found") when the entry doesn't exist.
    with mock.patch.object(kc, "_run", _fake_run(kc._SECURITY_ITEM_NOT_FOUND)):
        assert kc.keychain_has("https://x/v1") is False


def test_keychain_get_returns_secret():
    with mock.patch.object(kc, "_run", _fake_run(0, "sk-abc\n")):
        assert kc.keychain_get("https://x/v1") == "sk-abc"


def test_keychain_get_returns_none_on_item_not_found():
    with mock.patch.object(kc, "_run", _fake_run(kc._SECURITY_ITEM_NOT_FOUND)):
        assert kc.keychain_get("https://x/v1") is None


def test_keychain_set_calls_add_and_delete():
    calls = []

    def _run(cmd, *a, **k):
        calls.append(cmd)

        class R:
            pass

        r = R()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    with mock.patch.object(kc, "_run", _run):
        kc.keychain_set("https://x/v1", "sk-abc")
    # delete then add must both be issued
    delete = [c for c in calls if c[0:2] == ["security", "delete-internet-password"]]
    add = [c for c in calls if c[0:2] == ["security", "add-internet-password"]]
    assert delete and add
    assert "sk-abc" in add[0]
    assert "https://x/v1" in add[0]


def test_keychain_set_raises_on_failure():
    with mock.patch.object(kc, "_run", _fake_run(1, "", "denied")):
        try:
            kc.keychain_set("https://x/v1", "sk-abc")
            raise AssertionError("expected RuntimeError")
        except RuntimeError:
            pass



def test_find_env_in_profiles(tmp_path, monkeypatch):
    prof = tmp_path / ".zshrc"
    prof.write_text('export MYPROV_API_KEY="sk-x"\n')
    monkeypatch.setattr(kc, "profile_candidates", lambda: [prof])
    assert kc.find_env_in_profiles("MYPROV_API_KEY") == prof


def test_write_env_appends_once(tmp_path, monkeypatch):
    prof = tmp_path / ".zshrc"
    prof.write_text("# header\n")
    monkeypatch.setattr(kc, "profile_candidates", lambda: [prof])
    kc.write_env_to_profile("PROV_API_KEY", "sk-1", profile=prof)
    first = prof.read_text()
    kc.write_env_to_profile("PROV_API_KEY", "sk-1", profile=prof)
    assert prof.read_text() == first


def test_write_env_replaces_existing_value_on_rotation(tmp_path, monkeypatch):
    prof = tmp_path / ".zshrc"
    prof.write_text('# header\nexport PROV_API_KEY="sk-old"\nexport OTHER=1\n')
    monkeypatch.setattr(kc, "profile_candidates", lambda: [prof])
    kc.write_env_to_profile("PROV_API_KEY", "sk-new", profile=prof)
    content = prof.read_text()
    assert "sk-old" not in content
    assert subprocess.run(
        ["/bin/sh", "-c", '. "$1"; printf %s "$PROV_API_KEY"', "sh", str(prof)],
        capture_output=True, text=True, check=True,
    ).stdout == "sk-new"
    assert "export OTHER=1" in content


def test_shell_export_does_not_execute_key_contents(tmp_path, monkeypatch):
    prof = tmp_path / ".zshrc"
    marker = tmp_path / "injected"
    key = f"sk-'$(printf injected > {marker})'\""
    monkeypatch.setattr(kc, "profile_candidates", lambda: [prof])
    kc.write_env_to_profile("PROV_API_KEY", key, profile=prof)
    result = subprocess.run(
        ["/bin/sh", "-c", '. "$1"; printf %s "$PROV_API_KEY"', "sh", str(prof)],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout == key
    assert not marker.exists()


def test_newline_key_is_rejected_before_keychain_or_profile_writes():
    with mock.patch.object(kc, "keychain_set") as store, \
         mock.patch.object(kc, "write_env_to_profile") as export:
        try:
            kc.ensure_key("https://example.com", "bad\nkey", "PROV_API_KEY",
                          use_keychain=True, use_env=True)
        except ValueError:
            pass
        else:
            raise AssertionError("newline-containing key must be rejected")
    store.assert_not_called()
    export.assert_not_called()


def test_ensure_key_keychain_env_writes_no_plaintext(tmp_path, monkeypatch):
    prof = tmp_path / ".zshrc"
    prof.write_text("# header\n")
    monkeypatch.setattr(kc, "profile_candidates", lambda: [prof])
    monkeypatch.setattr(kc, "Path", mock.Mock(home=lambda: tmp_path))
    with mock.patch.object(kc, "keychain_set") as kset:
        notes = kc.ensure_key(
            "https://api.example.com/v1",
            "sk-super-secret",
            "PROV_API_KEY",
            use_keychain=True,
            use_env=False,
            keychain_env=True,
            log=lambda _: None,
        )
    kset.assert_called_once_with("https://api.example.com/v1", "sk-super-secret")
    assert any(n.startswith("keychain_env:") for n in notes)
    content = prof.read_text()
    assert "sk-super-secret" not in content
    assert "security find-internet-password" in content

def test_ensure_key_keychain_only(tmp_path, monkeypatch):
    monkeypatch.setattr(kc, "LOGIN_KEYCHAIN", str(tmp_path / "keychain"))
    stored = {}

    def _run(cmd, *a, **k):
        if cmd[0:2] == ["security", "add-internet-password"]:
            stored["server"] = cmd[cmd.index("-s") + 1]
            stored["key"] = cmd[cmd.index("-w") + 1]

        class R:
            pass

        r = R()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    with mock.patch.object(kc, "_run", _run):
        notes = kc.ensure_key("https://x/v1", "sk-1", "PROV_API_KEY")
    assert "keychain:https://x/v1" in notes
    assert stored["key"] == "sk-1"


def test_keychain_has_raises_on_unexpected_exit_code():
    """Anything other than 0 or 44 ('not found') should surface stderr."""

    def _run(cmd, *a, **k):
        class R:
            pass

        r = R()
        r.returncode = 50  # e.g. keychain locked
        r.stdout = ""
        r.stderr = "User interaction is not allowed."
        return r

    import pytest

    with mock.patch.object(kc, "_run", _run), \
         pytest.raises(RuntimeError, match="Keychain lookup failed"):
        kc.keychain_has("https://api.example.com/v1")


def test_keychain_has_returns_false_on_item_not_found():
    def _run(cmd, *a, **k):
        class R:
            pass

        r = R()
        r.returncode = kc._SECURITY_ITEM_NOT_FOUND
        r.stderr = ""
        return r

    with mock.patch.object(kc, "_run", _run):
        assert kc.keychain_has("https://api.example.com/v1") is False


def test_keychain_set_swallows_missing_entry_on_pretend_delete():
    """A missing prior entry is benign — delete returns 44, set still proceeds."""
    seen_delete = {"called": False}

    def _run(cmd, *a, **k):
        class R:
            pass

        r = R()
        r.stdout = ""
        r.stderr = ""
        if cmd[1] == "delete-internet-password":
            seen_delete["called"] = True
            r.returncode = kc._SECURITY_ITEM_NOT_FOUND
        else:
            r.returncode = 0
        return r

    import tempfile

    with tempfile.TemporaryDirectory() as td, \
         mock.patch.object(kc, "_run", _run), \
         mock.patch.object(kc, "LOGIN_KEYCHAIN", f"{td}/kc.db"):
        kc.keychain_set("https://api.example.com/v1", "sk-x")
    assert seen_delete["called"] is True


def test_keychain_delete_raises_on_unexpected_exit_code():
    """A locked-keychain error during delete should not be silent."""
    import pytest

    def _run(cmd, *a, **k):
        class R:
            pass

        r = R()
        r.returncode = 50
        r.stderr = "Keychain locked."
        return r

    with mock.patch.object(kc, "_run", _run), \
         pytest.raises(RuntimeError, match="Failed to remove keychain entry"):
        kc.keychain_delete("https://api.example.com/v1")
