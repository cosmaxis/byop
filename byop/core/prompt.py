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


def _use_cursor_menu(options: list[str], *, multiple: bool = False) -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if os.environ.get("TERM") == "dumb" or os.environ.get("BYOP_PLAIN") == "1":
        return False
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
    except OSError:
        size = shutil.get_terminal_size(fallback=(80, 24))
    if not size.columns or not size.lines:
        size = shutil.get_terminal_size(fallback=(80, 24))
    prefix = 6 if multiple else 2
    return size.lines >= len(options) + 3 and all(len(option) + prefix < size.columns for option in options)


def _cursor_menu(prompt: str, options: list[str], default_index: int, *, multiple: bool) -> list[int]:
    import termios
    import tty

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    index = default_index
    selected: set[int] = set()

    def draw() -> None:
        lines = []
        for i, option in enumerate(options):
            marker = ">" if i == index else " "
            checkbox = ("[x] " if i in selected else "[ ] ") if multiple else ""
            lines.append(f"\r\033[2K{marker} {checkbox}{option}\n")
        sys.stdout.write("".join(lines))
        sys.stdout.flush()

    try:
        tty.setcbreak(fd)
        if multiple:
            sys.stdout.write(f"{_PROMPT_PREFIX}{prompt}\n  ↑/↓ move  Space select  Enter continue  Esc cancel\n")
        else:
            sys.stdout.write(f"{_PROMPT_PREFIX}{prompt} (↑/↓ to move, Enter to select)\n")
        draw()
        while True:
            key = os.read(fd, 1)
            if not key or key == b"\x04":
                raise EOFError
            if key in (b"\r", b"\n"):
                return sorted(selected) if multiple else [index]
            if key == b" " and multiple:
                if index in selected:
                    selected.remove(index)
                else:
                    selected.add(index)
            elif key == b"\x1b":
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


def choose(prompt: str, options: list[str], default_index: int = 0) -> str:
    if _use_cursor_menu(options):
        return options[_cursor_menu(prompt, options, default_index, multiple=False)[0]]

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


def select_many(prompt: str, options: list[str]) -> list[int]:
    """Select zero or more options, returning their indexes in display order."""
    if not options:
        return []
    if _use_cursor_menu(options, multiple=True):
        return _cursor_menu(prompt, options, 0, multiple=True)

    print(_PROMPT_PREFIX + prompt)
    for i, option in enumerate(options):
        print(f"  {i + 1}. {option}")
    while True:
        raw = _strip_escape_chars(input("Numbers separated by commas (Enter for none): ")).strip()
        if not raw:
            return []
        parts = [part.strip() for part in raw.split(",")]
        if all(part.isdigit() and 1 <= int(part) <= len(options) for part in parts):
            return sorted({int(part) - 1 for part in parts})
        warn("Enter displayed numbers separated by commas.")


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
