"""Terminal UI primitives for byop.

Clack-inspired rail layout (`┌ │ ◆ ◇ ✓ ! ✗ └`) with single-write flicker-free
cursor menus and spinners on interactive TTYs, degrading cleanly to plain
line-oriented I/O when piped, narrow, or `BYOP_PLAIN=1` / `NO_COLOR` is set.
"""

from __future__ import annotations

import codecs
import contextlib
import getpass
import os
import re
import select
import shutil
import sys
import threading
from collections.abc import Iterator

try:  # pragma: no cover - optional readline support for arrow/backspace editing
    import readline as _readline  # noqa: F401
except Exception:  # pragma: no cover
    pass

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_RICH_TAG_RE = re.compile(r"\[/?[a-zA-Z0-9_ #]+\]")


def _is_tty() -> bool:
    return (
        sys.stdin.isatty()
        and sys.stdout.isatty()
        and os.environ.get("TERM") != "dumb"
        and os.environ.get("BYOP_PLAIN") != "1"
    )


def _use_ansi(stream=None) -> bool:
    target = stream or sys.stdout
    return (
        target.isatty()
        and os.environ.get("TERM") != "dumb"
        and os.environ.get("BYOP_PLAIN") != "1"
        and "NO_COLOR" not in os.environ
    )


def _style(text: str, *codes: str, stream=None) -> str:
    if not codes or not _use_ansi(stream):
        return text
    return "".join(codes) + text + _RESET


def _strip_rich_tags(text: str) -> str:
    return _RICH_TAG_RE.sub("", text)


def _strip_escape_chars(text: str) -> str:
    """Remove stray ESC bytes from terminal line input."""
    return text.replace("\x1b", "")


# ===========================================================================
# Session framing & output rows
# ===========================================================================
def intro(title: str, subtitle: str = "") -> None:
    if _use_ansi():
        badge = _style(f" {title} ", _BOLD, "\033[46;30m")
        sub = f"  {_style(subtitle, _DIM)}" if subtitle else ""
        print(f"\n{_style('┌', _DIM)}  {badge}{sub}")
        print(_style("│", _DIM))
    else:
        line = f"{title} — {subtitle}" if subtitle else title
        print(line)


def outro(message: str) -> None:
    if _use_ansi():
        print(_style("│", _DIM))
        print(f"{_style('└', _DIM)}  {_style(message, _GREEN)}")
    else:
        print(message)


def kv(key: str, value: str) -> None:
    if _use_ansi():
        print(f"{_style('│', _DIM)}  {_style(f'{key:<10}', _DIM)} {value}")
    else:
        print(f"  {key:<10} {value}")


def step(label: str, detail: str = "", *, status: str = "ok") -> None:
    glyphs = {
        "ok": ("✓", _GREEN),
        "skip": ("–", _DIM),
        "warn": ("!", _YELLOW),
        "error": ("✗", _RED),
    }
    glyph, color = glyphs.get(status, ("•", _CYAN))
    if _use_ansi():
        icon = _style(glyph, color)
        name = _style(f"{label:<15}", _BOLD if status == "ok" else "")
        det_color = _YELLOW if status == "warn" else (_RED if status == "error" else _DIM)
        tail = f" {_style(detail, det_color)}" if detail else ""
        print(f"{icon}  {name}{tail}")
    else:
        tail = f"  {detail}" if detail else ""
        print(f"{glyph} {label}{tail}")


def spacer() -> None:
    if _use_ansi():
        print(_style("│", _DIM))


def header(title: str) -> None:
    if _use_ansi():
        print(f"\n{_style('┌', _DIM)}  {_style(title, _BOLD)}")
        print(_style("│", _DIM))
    else:
        print(f"\n{title}")


def info(message: str) -> None:
    clean = _strip_rich_tags(message)
    if _use_ansi():
        print(f"{_style('│', _DIM)}  {clean}")
    else:
        print(clean)


def success(message: str) -> None:
    clean = _strip_rich_tags(message)
    if _use_ansi():
        print(f"{_style('◇', _GREEN)}  {clean}")
    else:
        print(f"✓ {clean}")


def warn(message: str) -> None:
    clean = _strip_rich_tags(message)
    if _use_ansi():
        print(f"{_style('▲', _YELLOW)}  {_style(clean, _YELLOW)}")
    else:
        print(f"! {clean}")


def error(message: str) -> None:
    clean = _strip_rich_tags(message)
    if _use_ansi(sys.stderr):
        print(f"{_style('✗', _RED, stream=sys.stderr)}  {_style(clean, _RED, stream=sys.stderr)}", file=sys.stderr)
    else:
        print(f"✗ {clean}", file=sys.stderr)


@contextlib.contextmanager
def spinner(message: str) -> Iterator[None]:
    """Transient single-line Braille spinner on interactive TTYs; no-op otherwise."""
    if not (_is_tty() and _use_ansi()):
        yield
        return

    stop = threading.Event()

    def _spin() -> None:
        idx = 0
        rail = _style("│", _DIM)
        while not stop.is_set():
            frame = _style(_SPINNER_FRAMES[idx % len(_SPINNER_FRAMES)], _CYAN)
            sys.stdout.write(f"\r\033[2K{rail}  {frame} {_style(message, _DIM)}")
            sys.stdout.flush()
            idx += 1
            stop.wait(0.08)

    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    worker = threading.Thread(target=_spin, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=0.5)
        sys.stdout.write("\r\033[2K\033[?25h")
        sys.stdout.flush()


# ===========================================================================
# Text, secret, integer & boolean prompts
# ===========================================================================
def _brace_balanced(text: str) -> bool:
    depth = 0
    in_str = False
    quote = ""
    esc = False
    saw_open = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
        elif ch == "{":
            depth += 1
            saw_open = True
        elif ch == "}":
            depth -= 1
    return saw_open and depth <= 0


def _summarize_answer(val: str, *, allow_paste: bool) -> str:
    if not val:
        return "none"
    stripped = val.strip()
    if allow_paste and (
        stripped.startswith("{")
        or "\n" in stripped
        or "api_key" in stripped.lower()
        or "apikey" in stripped.lower()
    ):
        from .paste import looks_like_provider_paste, parse_provider_paste

        if looks_like_provider_paste(stripped):
            try:
                cfg, missing = parse_provider_paste(stripped)
                if "provider_name" not in missing and cfg.provider_name:
                    return f"{cfg.provider_name} (pasted JSON · key redacted)"
            except ValueError:
                pass
        return "pasted JSON (redacted)"
    return val


def _read_paste_safe_tty(prefix: str) -> str:
    """Read a line or pasted JSON object from a TTY without echoing pasted secrets."""
    import termios

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~(termios.ICANON | termios.ECHO)
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0

    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    chars: list[str] = []
    pasting_json = False
    hidden_badge = _style("[JSON paste · hidden]", _DIM)

    def _read_char_buf(data: bytes) -> str:
        return decoder.decode(data, final=False)

    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
        sys.stdout.write(prefix)
        sys.stdout.flush()

        while True:
            chunk = os.read(fd, 1024)
            if not chunk:
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise EOFError

            i = 0
            while i < len(chunk):
                b = chunk[i : i + 1]
                i += 1
                if b == b"\x03":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    raise KeyboardInterrupt
                if b == b"\x04":
                    if not chars:
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        raise EOFError
                    continue
                if b == b"\x1b":
                    # Gather remaining bytes if an escape/CSI sequence was split across reads.
                    if i >= len(chunk) and select.select([fd], [], [], 0.05)[0]:
                        chunk += os.read(fd, 256)
                    if i >= len(chunk):
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        raise KeyboardInterrupt
                    nxt = chunk[i : i + 1]
                    if nxt in (b"[", b"O"):
                        i += 1
                        while i < len(chunk) and 0x20 <= chunk[i] <= 0x3F:
                            i += 1
                            if i >= len(chunk) and select.select([fd], [], [], 0.02)[0]:
                                chunk += os.read(fd, 256)
                        if i < len(chunk) and 0x40 <= chunk[i] <= 0x7E:
                            i += 1
                    continue
                if b in (b"\x7f", b"\x08"):
                    if chars:
                        chars.pop()
                        if not pasting_json:
                            sys.stdout.write("\b \b")
                            sys.stdout.flush()
                        elif not "".join(chars).strip():
                            pasting_json = False
                            sys.stdout.write(f"\r\033[2K{prefix}")
                            sys.stdout.flush()
                    continue
                if b == b"\x15":
                    chars.clear()
                    pasting_json = False
                    sys.stdout.write(f"\r\033[2K{prefix}")
                    sys.stdout.flush()
                    continue
                if b in (b"\r", b"\n"):
                    if not pasting_json:
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        return "".join(chars)
                    # Absorb CRLF pairs as a single newline.
                    if b == b"\r" and i < len(chunk) and chunk[i : i + 1] == b"\n":
                        i += 1
                    chars.append("\n")
                    text_now = "".join(chars)
                    has_more = i < len(chunk) or bool(select.select([fd], [], [], 0.04)[0])
                    if not has_more and (_brace_balanced(text_now) or text_now.endswith("\n\n")):
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        return text_now
                    continue

                decoded = _read_char_buf(b)
                if not decoded:
                    continue
                chars.append(decoded)
                if not pasting_json and "".join(chars).lstrip().startswith("{"):
                    pasting_json = True
                    if len(chars) == 1:
                        sys.stdout.write(hidden_badge)
                    else:
                        clear = "\r\033[2K" if _use_ansi() else "\r"
                        sys.stdout.write(f"{clear}{prefix}{hidden_badge}")
                    sys.stdout.flush()
                elif not pasting_json:
                    sys.stdout.write(decoded)
                    sys.stdout.flush()

            if pasting_json:
                text_now = "".join(chars)
                if _brace_balanced(text_now) and not select.select([fd], [], [], 0.05)[0]:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return text_now
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def _collapse_input_line(label: str, summary: str) -> None:
    if _is_tty() and _use_ansi():
        # Overwrite the answered input line with a compact completed step.
        sys.stdout.write(
            f"\033[1A\r\033[2K{_style('◇', _GREEN)}  {label} {_style('·', _DIM)} {_style(summary, _DIM)}\n"
        )
        sys.stdout.flush()


def ask(
    prompt: str,
    default: str = "",
    allow_empty: bool = False,
    *,
    hint: str = "",
    allow_paste: bool = False,
) -> str:
    while True:
        if _use_ansi():
            meta_parts = []
            if hint:
                meta_parts.append(hint)
            if default:
                meta_parts.append(f"default: {default}")
            meta = f" {_style('(' + ', '.join(meta_parts) + ')', _DIM)}" if meta_parts else ""
            prefix = f"{_style('◆', _CYAN)}  {_style(prompt, _BOLD)}{meta}: "
        else:
            suffix = f" ({hint})" if hint else ""
            def_part = f" [{default}]" if default else ""
            prefix = f"› {prompt}{suffix}{def_part}: "

        if allow_paste and sys.stdin.isatty() and sys.stdout.isatty():
            raw = _read_paste_safe_tty(prefix)
        else:
            raw = input(prefix)
        val = _strip_escape_chars(raw).strip()
        if not val and default:
            val = default
        if val or allow_empty:
            summary = _summarize_answer(val, allow_paste=allow_paste)
            if "\n" not in summary:
                _collapse_input_line(prompt, summary)
            return val
        warn(f"{prompt} is required.")

def ask_secret(prompt: str, *, hint: str = "", allow_empty: bool = False) -> str:
    while True:
        if _use_ansi():
            meta = f" {_style('(' + hint + ')', _DIM)}" if hint else ""
            prefix = f"{_style('◆', _CYAN)}  {_style(prompt, _BOLD)}{meta}: "
        else:
            suffix = f" ({hint})" if hint else ""
            prefix = f"› {prompt}{suffix}: "
        val = _strip_escape_chars(getpass.getpass(prefix)).strip()
        if val or allow_empty:
            shown = "••••••••" if val else "kept existing"
            _collapse_input_line(prompt, shown)
            return val
        warn(f"{prompt} is required.")


def ask_int(prompt: str, default: int) -> int:
    while True:
        raw = ask(prompt, default=str(default))
        try:
            return int(raw)
        except ValueError:
            warn("Enter a whole number.")


def confirm(prompt: str, default: bool = False) -> bool:
    yn = "Y/n" if default else "y/N"
    if _use_ansi():
        prefix = f"{_style('◆', _CYAN)}  {_style(prompt, _BOLD)} {_style(f'[{yn}]', _DIM)}: "
    else:
        prefix = f"› {prompt} ({yn}): "
    raw = _strip_escape_chars(input(prefix)).strip().lower()
    result = default if not raw else raw in ("y", "yes", "1", "true")
    _collapse_input_line(prompt, "Yes" if result else "No")
    return result


# ===========================================================================
# Interactive single-select & multi-select cursor menus
# ===========================================================================
def _format_hint(hint: str) -> str:
    if not hint:
        return ""
    low = hint.lower()
    if "configured" in low and "not" not in low:
        return _style(hint, _GREEN)
    if "not installed" in low:
        return _style(hint, _DIM)
    return _style(hint, _DIM)


def _use_cursor_menu(
    options: list[str],
    *,
    hints: list[str] | None = None,
    multiple: bool = False,
) -> bool:
    if not _is_tty():
        return False
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
    except OSError:
        size = shutil.get_terminal_size(fallback=(80, 24))
    if not size.columns or not size.lines:
        size = shutil.get_terminal_size(fallback=(80, 24))
    prefix = 7 if multiple else 6
    pad = max((len(o) for o in options), default=0)
    widths = [
        (pad if hints and hints[i] else len(opt))
        + (2 + len(hints[i]) if hints and i < len(hints) and hints[i] else 0)
        + prefix
        for i, opt in enumerate(options)
    ]
    return size.lines >= len(options) + 3 and all(w < size.columns for w in widths)


def _cursor_menu(
    prompt: str,
    options: list[str],
    default_index: int,
    *,
    hints: list[str] | None = None,
    defaults: list[int] | None = None,
    multiple: bool,
) -> list[int]:
    import termios
    import tty

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    index = max(0, min(default_index, len(options) - 1)) if options else 0
    selected: set[int] = set(defaults or ())
    total_lines = len(options) + 2  # header + options + footer
    pad = max((len(o) for o in options), default=0) if hints else 0

    footer = (
        "↑/↓ move · Space toggle · a all · Enter confirm · Esc cancel"
        if multiple
        else "↑/↓ move · Enter to select · Esc cancel"
    )

    def render_frame(*, move_up: bool) -> None:
        buf: list[str] = []
        if move_up:
            buf.append(f"\033[{total_lines}A")
        buf.append(f"\r\033[2K{_style('◆', _CYAN)}  {_style(prompt, _BOLD)}\n")
        rail = _style("│", _DIM)
        for i, option in enumerate(options):
            active = i == index
            if multiple:
                checked = i in selected
                box = _style("◼", _CYAN if active else _GREEN) if checked else _style("◻", _CYAN if active else _DIM)
                label_txt = f"{option:<{pad}}" if hints else option
                label = _style(label_txt, _BOLD) if active else (label_txt if checked else _style(label_txt, _DIM))
                marker = f"{box} {label}"
            else:
                dot = _style("●", _CYAN) if active else _style("○", _DIM)
                label_txt = f"{option:<{pad}}" if hints else option
                label = _style(label_txt, _BOLD) if active else _style(label_txt, _DIM)
                marker = f"{dot} {label}"
            hint_txt = f"  {_format_hint(hints[i])}" if hints and i < len(hints) and hints[i] else ""
            buf.append(f"\r\033[2K{rail}  {marker}{hint_txt}\n")
        buf.append(f"\r\033[2K{_style('└', _DIM)}  {_style(footer, _DIM)}\n")
        sys.stdout.write("".join(buf))
        sys.stdout.flush()

    def collapse(summary: str, *, cancelled: bool = False) -> None:
        buf = [f"\033[{total_lines}A"]
        buf.extend("\r\033[2K\n" for _ in range(total_lines))
        buf.append(f"\033[{total_lines}A")
        icon = _style("■", _YELLOW) if cancelled else _style("◇", _GREEN)
        buf.append(f"\r\033[2K{icon}  {prompt} {_style('·', _DIM)} {_style(summary, _DIM)}\n")
        sys.stdout.write("".join(buf))
        sys.stdout.flush()

    try:
        tty.setcbreak(fd)
        sys.stdout.write("\033[?25l")
        render_frame(move_up=False)
        while True:
            key = os.read(fd, 1)
            if not key or key == b"\x04":
                collapse("cancelled", cancelled=True)
                raise EOFError
            if key == b"\x03":
                collapse("cancelled", cancelled=True)
                raise KeyboardInterrupt
            if key in (b"\r", b"\n"):
                chosen = sorted(selected) if multiple else [index]
                summary = ", ".join(options[i] for i in chosen) if chosen else "none"
                collapse(summary)
                return chosen
            if key == b" " and multiple:
                if index in selected:
                    selected.remove(index)
                else:
                    selected.add(index)
            elif key in (b"a", b"A") and multiple:
                if len(selected) == len(options):
                    selected.clear()
                else:
                    selected = set(range(len(options)))
            elif key == b"\x1b":
                if not select.select([fd], [], [], 0.08)[0]:
                    collapse("cancelled", cancelled=True)
                    raise KeyboardInterrupt
                prefix = os.read(fd, 1)
                if prefix not in (b"[", b"O") or not select.select([fd], [], [], 0.08)[0]:
                    continue
                code = os.read(fd, 1)
                if code == b"A":
                    index = (index - 1) % len(options)
                elif code == b"B":
                    index = (index + 1) % len(options)
                else:
                    continue
            elif key in (b"k", b"j"):
                index = (index + (-1 if key == b"k" else 1)) % len(options)
            else:
                continue
            render_frame(move_up=True)
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def choose(
    prompt: str,
    options: list[str],
    default_index: int = 0,
    *,
    hints: list[str] | None = None,
) -> str:
    if _use_cursor_menu(options, hints=hints):
        return options[_cursor_menu(prompt, options, default_index, hints=hints, multiple=False)[0]]

    print(f"› {prompt}")
    for i, option in enumerate(options):
        hint_txt = f" ({hints[i]})" if hints and i < len(hints) and hints[i] else ""
        print(f"  {i + 1}. {option}{hint_txt}")
    while True:
        raw = _strip_escape_chars(input(f"Select [1-{len(options)}] [{default_index + 1}]: ")).strip()
        if not raw:
            return options[default_index]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        warn("Choose one of the displayed numbers.")


def select_many(
    prompt: str,
    options: list[str],
    *,
    hints: list[str] | None = None,
    defaults: list[int] | None = None,
) -> list[int]:
    """Select zero or more options, returning their indexes in display order."""
    if not options:
        return []
    if _use_cursor_menu(options, hints=hints, multiple=True):
        return _cursor_menu(
            prompt, options, 0, hints=hints, defaults=defaults, multiple=True
        )

    print(f"› {prompt}")
    default_set = set(defaults or ())
    for i, option in enumerate(options):
        hint_txt = f" ({hints[i]})" if hints and i < len(hints) and hints[i] else ""
        mark = "*" if i in default_set else " "
        print(f" {mark}{i + 1}. {option}{hint_txt}")
    default_hint = (
        ",".join(str(i + 1) for i in sorted(default_set))
        if default_set
        else "Enter for none"
    )
    while True:
        raw = _strip_escape_chars(input(f"Numbers separated by commas ({default_hint}): ")).strip()
        if not raw:
            return sorted(default_set)
        parts = [part.strip() for part in raw.split(",")]
        if all(part.isdigit() and 1 <= int(part) <= len(options) for part in parts):
            return sorted({int(part) - 1 for part in parts})
        warn("Enter displayed numbers separated by commas.")


def choose_action(target_label: str, options: list[str], default: str = "replace") -> str:
    """Prompt for a conflict-resolution action on ``target_label``."""
    default_idx = options.index(default) if default in options else 0
    if _use_cursor_menu(options):
        return choose(f"{target_label} already configured", options, default_index=default_idx)
    raw = _strip_escape_chars(
        input(f"  {target_label} [{'/'.join(options)}] ({default}): ")
    ).strip().lower()
    return raw if raw in options else default
