"""Interactive prompt helpers for byop.

Uses `rich` for a polished experience when available, and degrades gracefully
to plain stdlib input/output otherwise so the tool works in minimal CI shells.
"""

from __future__ import annotations

import getpass
import os
import select
import shutil
import sys

try:  # pragma: no cover - optional dependency
    from rich.console import Console
    from rich.prompt import Confirm, IntPrompt, Prompt

    _HAS_RICH = True
except Exception:  # pragma: no cover
    _HAS_RICH = False

_PROMPT_PREFIX = "› "


def _console() -> Console:
    return Console()


def _strip_escape_chars(text: str) -> str:
    """Remove stray ESC / caret-notation bytes from terminal input.

    Rich's ``Prompt.ask`` reads input character-by-character; if the user
    presses ESC (sometimes accidentally while clicking out of focus) the
    terminal delivers ``\\x1b`` and rich echoes it as ``^[``. A stray
    ESC in the middle of an interactive prompt is never a meaningful
    answer — strip it so the next ``input()`` call sees a clean value.
    """
    return text.replace("\x1b", "")


def ask(prompt: str, default: str = "", allow_empty: bool = False) -> str:
    if _HAS_RICH:
        val = Prompt.ask(_PROMPT_PREFIX + prompt, default=default)
    else:
        suffix = f" [{default}]" if default else ""
        val = input(f"{_PROMPT_PREFIX}{prompt}{suffix}: ").strip()
        if not val and default:
            val = default
    val = _strip_escape_chars(val)
    if not allow_empty and not val:
        # Retry once for critical fields.
        return ask(prompt, default=default, allow_empty=allow_empty)
    return val


def ask_secret(prompt: str) -> str:
    if _HAS_RICH:
        return Prompt.ask(_PROMPT_PREFIX + prompt, password=True)
    return _strip_escape_chars(getpass.getpass(f"{_PROMPT_PREFIX}{prompt}: "))


def ask_int(prompt: str, default: int) -> int:
    if _HAS_RICH:
        return IntPrompt.ask(_PROMPT_PREFIX + prompt, default=default)
    raw = _strip_escape_chars(
        input(f"{_PROMPT_PREFIX}{prompt} [{default}]: ")
    ).strip()
    return int(raw) if raw else default


def confirm(prompt: str, default: bool = False) -> bool:
    if _HAS_RICH:
        return Confirm.ask(_PROMPT_PREFIX + prompt, default=default)
    raw = _strip_escape_chars(
        input(f"{_PROMPT_PREFIX}{prompt} (y/N): ")
    ).strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "1", "true")


def choose(prompt: str, options: list[str], default_index: int = 0) -> str:
    if sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM") != "dumb":
        import termios
        import tty

        fd = sys.stdin.fileno()
        previous = termios.tcgetattr(fd)
        index = default_index

        def draw() -> None:
            width = max(1, shutil.get_terminal_size(fallback=(80, 24)).columns - 4)
            for i, option in enumerate(options):
                marker = "›" if i == index else " "
                sys.stdout.write(f"\r\033[2K{marker} {option[:width]}\n")
            sys.stdout.flush()

        try:
            tty.setcbreak(fd)
            sys.stdout.write(f"{_PROMPT_PREFIX}{prompt} (↑/↓ to move, Enter to select)\n")
            draw()
            while True:
                key = os.read(fd, 1)
                if not key or key == b"\x04":
                    raise EOFError
                if key in (b"\r", b"\n"):
                    return options[index]
                if key == b"\x1b":
                    if not select.select([fd], [], [], 0.1)[0]:
                        raise KeyboardInterrupt
                    prefix = os.read(fd, 1)
                    if prefix not in (b"[", b"O") or not select.select([fd], [], [], 0.1)[0]:
                        continue
                    key = os.read(fd, 1)
                    if key == b"A":
                        index = (index - 1) % len(options)
                    elif key == b"B":
                        index = (index + 1) % len(options)
                    else:
                        continue
                elif key in (b"k", b"j"):
                    index = (index + (-1 if key == b"k" else 1)) % len(options)
                else:
                    continue
                sys.stdout.write(f"\033[{len(options)}A")
                draw()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)

    # Non-interactive input keeps a numbered fallback for scripts and pipes.
    print(_PROMPT_PREFIX + prompt)
    for i, option in enumerate(options):
        print(f"  {i + 1}. {option}")
    while True:
        raw = _strip_escape_chars(input(f"Select [1-{len(options)}] [{default_index + 1}]: ")).strip()
        if not raw:
            return options[default_index]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        warn("Choose one of the displayed numbers.")


def header(title: str) -> None:
    if _HAS_RICH:
        _console().rule(title)
    else:
        print(f"\n=== {title} ===")


def choose_action(target_label: str, options: list[str], default: str = "replace") -> str:
    """Prompt for one of ``options`` and return it as a string.

    Used by the per-target conflict prompt. ``default`` is returned on empty
    input (so a stray Enter just picks the safe option).
    """
    if _HAS_RICH:
        from rich.prompt import Prompt as _P
        return _P.ask(
            f"  Action for {target_label}",
            choices=options,
            default=default,
        )
    raw = _strip_escape_chars(
        input(
            f"  Action for {target_label} [{'/'.join(options)}] ({default}): "
        )
    ).strip().lower()
    if raw in options:
        return raw
    return default


def info(message: str) -> None:
    if _HAS_RICH:
        _console().print(f"[cyan]{message}[/cyan]")
    else:
        print(message)


def success(message: str) -> None:
    if _HAS_RICH:
        _console().print(f"[green]✓[/green] {message}")
    else:
        print(f"✓ {message}")


def warn(message: str) -> None:
    if _HAS_RICH:
        _console().print(f"[yellow]![/yellow] {message}")
    else:
        print(f"! {message}")


def error(message: str) -> None:
    if _HAS_RICH:
        Console(stderr=True).print(f"[red]✗ {message}[/red]")
    else:
        print(f"✗ {message}", file=sys.stderr)
