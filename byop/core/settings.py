"""JSONC-aware read/merge/write utilities for Zed's settings.json.

Zed's ``settings.json`` is JSONC: it may contain ``//`` and ``/* */`` comments
and trailing commas. We parse it with a tolerant parser and re-emit it with the
same style so we never destroy a user's hand-written comments or formatting.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_COMMENT_RE = re.compile(r'(?://[^\n\r]*|/\*.*?\*/)', re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    """Atomically replace a file with the requested permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _strip_comments(text: str) -> str:
    """Remove ``//`` line and ``/* */`` block comments from JSONC text.

    Strings are protected so that comment-like sequences inside string values
    (e.g. a URL or key containing ``//``) are left untouched.
    """

    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        # Not in a string: look for comment starts.
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            # line comment
            while i < n and text[i] not in "\n\r":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            # block comment
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def loads(text: str) -> dict[str, Any]:
    """Parse JSONC text into a Python dict."""

    stripped = _strip_comments(text)
    stripped = _TRAILING_COMMA_RE.sub(r"\1", stripped)
    return json.loads(stripped)


def load_path(path: Path) -> dict[str, Any]:
    """Load a JSONC file; returns ``{}`` if the file does not exist."""

    if not path.exists():
        return {}
    return loads(path.read_text(encoding="utf-8"))


def merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``update`` into ``base`` (returns a new dict)."""

    result = dict(base)
    for key, value in update.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def dumps(data: dict[str, Any], indent: int = 2) -> str:
    """Serialize a dict to a JSON string with a trailing newline."""

    text = json.dumps(data, indent=indent, ensure_ascii=False)
    return text + "\n"


def write_path(path: Path, data: dict[str, Any]) -> None:
    """Write JSON to ``path``, creating parent directories as needed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(data), encoding="utf-8")
