"""Tests for byop.core.prompt — primarily the ESC-byte sanitizer."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core import prompt


def test_strip_escape_chars_removes_esc_bytes():
    """Bare ESC bytes (\x1b) — what the terminal delivers on the ESC key —
    must be stripped. This is the regression we hit when the user accidentally
    hits ESC during the JSON-paste prompt and the prompt echoed ^[^[^[^[^[.
    """
    assert prompt._strip_escape_chars("hello") == "hello"
    assert prompt._strip_escape_chars("\x1b\x1b\x1b\x1b\x1b") == ""
    assert prompt._strip_escape_chars("a\x1bb\x1bc") == "abc"
    # Caret-notation rendering shouldn't appear because we strip at byte
    # level, but the ANSI sequences CSI A/B/C/D (arrow keys) also start with
    # ESC and are equally meaningless as wizard answers.
    assert prompt._strip_escape_chars("\x1b[A\x1b[B") == "[A[B"


def test_strip_escape_chars_does_not_touch_legitimate_text():
    """Letters, digits, slashes, braces — all preserved."""
    payload = '{"name": "Cosmaxis", "url": "https://api.example.com/v1"}'
    assert prompt._strip_escape_chars(payload) == payload


def test_arrow_menu_selects_option_and_restores_terminal():
    import os
    import pty
    import select
    import subprocess
    import termios
    import time

    master, slave = pty.openpty()
    before = termios.tcgetattr(slave)
    env = {**os.environ, "TERM": "xterm"}
    child = subprocess.Popen(
        [
            sys.executable, "-c",
            'from byop.core.prompt import choose; print("selected=" + choose("Pick", ["A", "B"]))',
        ],
        stdin=slave, stdout=slave, stderr=slave, env=env,
    )
    output = b""
    try:
        deadline = time.monotonic() + 10
        while b"Enter to select" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                output += os.read(master, 4096)
        assert b"Enter to select" in output
        os.write(master, b"\x1b[B\r")
        deadline = time.monotonic() + 10
        while child.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                output += os.read(master, 4096)
        child.wait(timeout=1)
        while select.select([master], [], [], 0)[0]:
            output += os.read(master, 4096)
        assert child.returncode == 0
        assert b"selected=B" in output
        after = termios.tcgetattr(slave)
        flags = termios.ECHO | termios.ICANON | termios.ISIG
        assert after[3] & flags == before[3] & flags
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(master)
        os.close(slave)


def test_non_tty_selection_retries_invalid_input(monkeypatch, capsys):
    answers = iter(["9", "2"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert prompt.choose("Pick", ["A", "B"]) == "B"
    assert "Choose one of the displayed numbers" in capsys.readouterr().out


def test_checklist_keyboard_toggles_multiple_and_restores_terminal():
    import os
    import pty
    import select
    import subprocess
    import termios
    import time

    master, slave = pty.openpty()
    before = termios.tcgetattr(slave)
    env = {**os.environ, "TERM": "xterm"}
    env.pop("BYOP_PLAIN", None)
    child = subprocess.Popen(
        [
            sys.executable, "-c",
            'from byop.core.prompt import select_many; print("selected=" + str(select_many("Tools", ["A", "B", "C"])))',
        ],
        stdin=slave, stdout=slave, stderr=slave, env=env,
    )
    output = b""
    try:
        deadline = time.monotonic() + 10
        while b"Esc cancel" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                output += os.read(master, 4096)
        assert b"Esc cancel" in output
        os.write(master, b" \x1b[B \x1b[B \x1b[A \r")
        deadline = time.monotonic() + 10
        while child.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                output += os.read(master, 4096)
        child.wait(timeout=1)
        while select.select([master], [], [], 0)[0]:
            output += os.read(master, 4096)
        assert child.returncode == 0
        assert b"selected=[0, 2]" in output
        after = termios.tcgetattr(slave)
        flags = termios.ECHO | termios.ICANON | termios.ISIG
        assert after[3] & flags == before[3] & flags
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(master)
        os.close(slave)


def test_checklist_plain_input_validates_and_preserves_order(monkeypatch, capsys):
    answers = iter(["1,9", "3,1,3"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert prompt.select_many("Tools", ["A", "B", "C"]) == [0, 2]
    output = capsys.readouterr().out
    assert "Enter displayed numbers" in output
    assert "\x1b" not in output


def test_checklist_plain_enter_selects_nothing(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert prompt.select_many("Tools", ["A", "B"]) == []


def test_narrow_terminal_uses_complete_plain_checklist():
    import fcntl
    import os
    import pty
    import select
    import struct
    import subprocess
    import termios
    import time

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 18, 0, 0))
    child = subprocess.Popen(
        [
            sys.executable, "-c",
            'from byop.core.prompt import select_many; print("selected=" + str(select_many("Tools", ["A", "Long option (not installed)"])))',
        ],
        stdin=slave, stdout=slave, stderr=slave, env={**os.environ, "TERM": "xterm"},
    )
    output = b""
    try:
        deadline = time.monotonic() + 10
        while b"Enter for none" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                output += os.read(master, 4096)
        assert b"Long option (not installed)" in output
        assert b"\x1b[" not in output
        os.write(master, b"2\n")
        deadline = time.monotonic() + 10
        while child.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                output += os.read(master, 4096)
        child.wait(timeout=1)
        while select.select([master], [], [], 0)[0]:
            output += os.read(master, 4096)
        assert child.returncode == 0
        assert b"selected=[1]" in output
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(master)
        os.close(slave)


def test_40_column_terminal_with_short_options_falls_back_when_footer_would_wrap():
    """Short option labels ('Zed', 'Warp') at 40 cols must still fall back because the footer is >40 cols."""
    import fcntl
    import os
    import pty
    import select
    import struct
    import subprocess
    import termios
    import time

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 40, 0, 0))
    env = {**os.environ, "TERM": "xterm-256color"}
    env.pop("BYOP_PLAIN", None)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            'from byop.core.prompt import select_many; print("selected=" + str(select_many("Tools", ["Zed", "Warp"])))',
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
    )
    output = b""
    try:
        deadline = time.monotonic() + 10
        while b"): " not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                output += os.read(master, 4096)
        assert b"1. Zed" in output
        assert b"2. Warp" in output
        assert b"\x1b[" not in output
        os.write(master, b"1,2\n")
        deadline = time.monotonic() + 10
        while child.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                output += os.read(master, 4096)
        child.wait(timeout=1)
        while select.select([master], [], [], 0)[0]:
            output += os.read(master, 4096)
        assert child.returncode == 0
        assert b"selected=[0, 1]" in output
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(master)
        os.close(slave)


def test_checklist_select_all_hotkey():
    import os
    import pty
    import select
    import subprocess
    import time

    master, slave = pty.openpty()
    env = {**os.environ, "TERM": "xterm"}
    env.pop("BYOP_PLAIN", None)
    child = subprocess.Popen(
        [
            sys.executable, "-c",
            'from byop.core.prompt import select_many; print("selected=" + str(select_many("Tools", ["A", "B", "C"])))',
        ],
        stdin=slave, stdout=slave, stderr=slave, env=env,
    )
    output = b""
    try:
        deadline = time.monotonic() + 10
        while b"Esc cancel" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                output += os.read(master, 4096)
        os.write(master, b"a\r")
        deadline = time.monotonic() + 10
        while child.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                output += os.read(master, 4096)
        child.wait(timeout=1)
        while select.select([master], [], [], 0)[0]:
            output += os.read(master, 4096)
        assert child.returncode == 0
        assert b"selected=[0, 1, 2]" in output
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(master)
        os.close(slave)


def test_pty_paste_never_echoes_or_collapses_sentinel_api_key():
    """Single-line, multi-line, and bracketed JSON pastes must never echo or print api_key."""
    import json
    import os
    import pty
    import select
    import subprocess
    import termios
    import time

    sentinel = "sk-pty-sentinel-secret-987654321"
    payloads = [
        # 1. Single-line JSON paste with trailing Enter
        (
            json.dumps({
                "provider_name": "Cosmaxis",
                "api_url": "https://api.example.com/v1",
                "api_key": sentinel,
                "models": [{"name": "pro"}],
            })
            + "\r"
        ).encode(),
        # 2. Multi-line pretty JSON paste without trailing Enter
        json.dumps(
            {
                "provider_name": "Cosmaxis",
                "api_url": "https://api.example.com/v1",
                "api_key": sentinel,
                "models": [{"name": "pro"}],
            },
            indent=2,
        ).encode(),
        # 3. Bracketed paste sequence wrapping single-line JSON
        (
            "\x1b[200~"
            + json.dumps({
                "provider_name": "Cosmaxis",
                "api_url": "https://api.example.com/v1",
                "api_key": sentinel,
                "models": [{"name": "pro"}],
            })
            + "\x1b[201~\r"
        ).encode(),
    ]

    for payload in payloads:
        master, slave = pty.openpty()
        before = termios.tcgetattr(slave)
        env = {**os.environ, "TERM": "xterm-256color"}
        env.pop("BYOP_PLAIN", None)
        env.pop("NO_COLOR", None)
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "from byop.core import wizard\n"
                    "p, prefs = wizard.run_wizard()\n"
                    "print('PARSED=' + p.provider_name + ':' + str(len(p.api_key)))\n"
                ),
            ],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
        )
        output = b""
        try:
            deadline = time.monotonic() + 10
            while b"Provider name" not in output and time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    output += os.read(master, 4096)
            os.write(master, payload)

            while b"Key storage" not in output and time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    output += os.read(master, 4096)
            os.write(master, b"\r")  # Confirm default Keychain storage

            while child.poll() is None and time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    output += os.read(master, 4096)
            child.wait(timeout=2)
            while select.select([master], [], [], 0)[0]:
                output += os.read(master, 4096)

            assert child.returncode == 0
            assert sentinel.encode() not in output
            assert b"Cosmaxis (pasted JSON" in output
            assert f"PARSED=Cosmaxis:{len(sentinel)}".encode() in output

            after = termios.tcgetattr(slave)
            flags = termios.ECHO | termios.ICANON | termios.ISIG
            assert after[3] & flags == before[3] & flags
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            os.close(master)
            os.close(slave)


def test_pty_malformed_json_paste_does_not_leak_secret_or_become_provider_name():
    import os
    import pty
    import select
    import subprocess
    import time

    sentinel = "sk-malformed-sentinel-987654"
    master, slave = pty.openpty()
    env = {**os.environ, "TERM": "xterm-256color"}
    env.pop("BYOP_PLAIN", None)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "from byop.core import wizard\n"
                "p, _ = wizard.run_wizard()\n"
                "print('NAME=' + p.provider_name)\n"
            ),
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
    )
    output = b""
    try:
        deadline = time.monotonic() + 10
        while b"Provider name" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                output += os.read(master, 4096)
        # Trailing comma makes JSON invalid, but braces are balanced.
        os.write(master, f'{{"provider_name":"Bad","api_key":"{sentinel}",}}\r'.encode())

        while b"entering fields manually" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                output += os.read(master, 4096)
        # Now enter fields manually (with backspace test: "CosmX\x7faxis")
        os.write(master, b"CosmX\x7faxis\r")
        while b"API URL" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                output += os.read(master, 4096)
        os.write(master, b"https://api.example.com/v1\r")
        while b"API key" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                output += os.read(master, 4096)
        os.write(master, b"sk-manual-key\r")
        while b"Models" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                output += os.read(master, 4096)
        os.write(master, b"pro\r")
        while b"Advanced model options" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                output += os.read(master, 4096)
        os.write(master, b"\r")
        while b"Key storage" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                output += os.read(master, 4096)
        os.write(master, b"\r")

        while child.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                output += os.read(master, 4096)
        child.wait(timeout=2)
        while select.select([master], [], [], 0)[0]:
            output += os.read(master, 4096)

        assert child.returncode == 0
        assert sentinel.encode("utf-8") not in output
        assert b"sk-manual-key" not in output
        assert b"NAME=Cosmaxis" in output
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(master)
        os.close(slave)
