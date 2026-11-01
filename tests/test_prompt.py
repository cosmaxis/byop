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
