"""Oh My Pi (omp) target.

omp (https://omp.sh) is a fork of py.dev by can1357; it shares the same
provider schema but reads ``~/.omp/agent/models.yml`` (NOT models.json).
We subclass :class:`PyTarget` for the fragment logic but override
``_load`` / ``_write`` to use YAML. A small stdlib emitter/parser handles
the fixed schema PyTarget.build_fragment produces, so no PyYAML dep.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from ..config import ProviderConfig
from .py import PyTarget

DEFAULT_MODELS_PATH = Path.home() / ".omp" / "agent" / "models.yml"


def profile_models_path(profile: str, config_root: Path | None = None) -> Path:
    """Return the profile-scoped OMP models.yml path."""
    if not profile or Path(profile).name != profile or profile in {".", ".."}:
        raise ValueError("OMP profile must be a simple directory name")
    root = config_root or (Path.home() / ".omp")
    return root / "profiles" / profile / "agent" / "models.yml"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _yaml_scalar(value: object) -> str:
    """Render a Python value as a YAML scalar on one line.

    Only the subset needed by PyTarget.build_fragment is supported:
    strings (with safe quoting when needed), ints, floats, bools, None.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    # YAML and the reader coerce unquoted booleans, nulls, and numbers.
    reserved = {"true", "false", "null", "yes", "no", "on", "off", "y", "n", "inf", "infinity", "nan"}
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./-]*", s) and s.lower() not in reserved:
        return s
    return "'" + s.replace("'", "''") + "'"


def _parse_models_yaml(text: str) -> dict:
    """Parse the YAML subset that _emit_models_yaml produces.

    Supports: nested maps with string keys, scalar values (string/number/
    bool/null), block sequences ('- key: val' or '- scalar'), inline flow
    lists ('[a, b]'), single-quoted strings. Sufficient for byop-emitted
    models.yml — not a general YAML parser.
    """
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        lines.append((indent, raw.lstrip()))

    state = {"idx": 0}

    def at() -> tuple[int, str] | None:
        i = state["idx"]
        if i >= len(lines):
            return None
        return lines[i]

    def parse_scalar(tok: str) -> object:
        tok = tok.strip()
        if tok == "" or tok.lower() == "null" or tok == "~":
            return None
        if tok == "{}":
            return {}
        if tok.lower() == "true":
            return True
        if tok.lower() == "false":
            return False
        if len(tok) >= 2 and tok[0] == "'" and tok[-1] == "'":
            return tok[1:-1].replace("''", "'")
        if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
            return tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        try:
            return int(tok)
        except ValueError:
            pass
        try:
            return float(tok)
        except ValueError:
            pass
        return tok

    def parse_flow_list(s: str) -> list:
        s = s.strip()
        if not (s.startswith("[") and s.endswith("]")):
            raise ValueError(f"Expected flow list [ ... ], got {s!r}")
        body = s[1:-1].strip()
        if not body:
            return []
        out, buf, in_str, quote = [], "", False, None
        for ch in body:
            if in_str:
                buf += ch
                if ch == quote:
                    in_str = False
                    quote = None
            elif ch in "\"'" :
                in_str = True
                quote = ch
                buf += ch
            elif ch == ",":
                out.append(parse_scalar(buf))
                buf = ""
            else:
                buf += ch
        if buf.strip():
            out.append(parse_scalar(buf))
        return out

    def parse_map(indent: int) -> dict:
        out: dict = {}
        while True:
            cur = at()
            if cur is None or cur[0] < indent:
                break
            _, content = cur
            if content.startswith("- "):
                break
            if ":" not in content:
                raise ValueError(f"Expected ':' in YAML line: {content!r}")
            key, _, rest = content.partition(":")
            key = key.strip()
            if key.startswith(("'", '"')):
                key = str(parse_scalar(key))
            rest = rest.strip()
            if rest == "":
                state["idx"] += 1
                nxt = at()
                if nxt is not None and nxt[0] > indent:
                    if nxt[1].startswith("- "):
                        out[key] = parse_seq(nxt[0])
                    else:
                        out[key] = parse_map(nxt[0])
                else:
                    out[key] = None
            elif rest.startswith("["):
                out[key] = parse_flow_list(rest)
                state["idx"] += 1
            else:
                out[key] = parse_scalar(rest)
                state["idx"] += 1
        return out

    def parse_seq(indent: int) -> list:
        out: list = []
        while True:
            cur = at()
            if cur is None or cur[0] < indent:
                break
            _, content = cur
            if not content.startswith("- "):
                break
            item_str = content[2:].strip()
            # Track the indent of this dash so siblings at deeper indent
            # are recognized as part of the same hash.
            dash_indent = cur[0]
            if item_str == "":
                state["idx"] += 1
                nxt = at()
                if nxt is not None and nxt[0] > indent:
                    if nxt[1].startswith("- "):
                        out.append(parse_seq(nxt[0]))
                    else:
                        out.append(parse_map(nxt[0]))
                else:
                    out.append(None)
                continue
            if ":" in item_str and not item_str.startswith("["):
                key, _, rest = item_str.partition(":")
                key = key.strip()
                rest = rest.strip()
                if rest == "":
                    state["idx"] += 1
                    nxt = at()
                    val: object = None
                    if nxt is not None and nxt[0] >= indent + 2:
                        if nxt[1].startswith("- "):
                            val = parse_seq(nxt[0])
                        else:
                            val = parse_map(nxt[0])
                    out.append({key: val})
                elif rest.startswith("["):
                    out.append({key: parse_flow_list(rest)})
                    state["idx"] += 1
                else:
                    out.append({key: parse_scalar(rest)})
                    state["idx"] += 1
            else:
                out.append(parse_scalar(item_str))
                state["idx"] += 1
            # Merge sibling keys deeper than the dash into the last hash.
            # omp-emitted model entries put name/contextWindow/maxTokens/
            # input at indent + 2 below the dash line as siblings of id.
            while True:
                cur = at()
                if cur is None or cur[0] <= dash_indent or cur[1].startswith("- "):
                    break
                if ":" not in cur[1]:
                    break
                k, _, r = cur[1].partition(":")
                k = k.strip()
                r = r.strip()
                if r == "":
                    state["idx"] += 1
                    nxt = at()
                    if nxt is not None and nxt[0] >= cur[0] + 2:
                        if nxt[1].startswith("- "):
                            v2: object = parse_seq(nxt[0])
                        else:
                            v2 = parse_map(nxt[0])
                    else:
                        v2 = None
                elif r.startswith("["):
                    v2 = parse_flow_list(r)
                    state["idx"] += 1
                else:
                    v2 = parse_scalar(r)
                    state["idx"] += 1
                if out and isinstance(out[-1], dict):
                    out[-1][k] = v2
                else:
                    break
        return out

    if not lines:
        return {}
    top_indent = lines[0][0]
    if lines[0][1].startswith("- "):
        return parse_seq(top_indent)  # type: ignore[return-value]
    return parse_map(top_indent)


def _emit_value(value: object, indent: int) -> list[str]:
    """Recursively emit a Python value as YAML lines, with one key/value
    or list item per line and scalars inline next to their key/dash."""
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{pad}{{}}"]
        out: list[str] = []
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                if isinstance(v, dict) and not v:
                    out.append(f"{pad}{k}: {{}}")
                elif isinstance(v, dict):
                    # Key on its own line, nested block follows indented.
                    out.append(f"{pad}{k}:")
                    out.extend(_emit_value(v, indent + 2))
                elif isinstance(v, list) and v and all(
                    isinstance(x, (str, int, float, bool)) or x is None
                    for x in v
                ):
                    # Inline flow list of scalars: - a, - b
                    out.append(
                        f"{pad}{k}: ["
                        + ", ".join(_yaml_scalar(x) for x in v)
                        + "]"
                    )
                elif isinstance(v, list) and not v:
                    out.append(f"{pad}{k}: []")
                else:
                    out.append(f"{pad}{k}:")
                    out.extend(_emit_value(v, indent + 2))
            else:
                out.append(f"{pad}{k}: {_yaml_scalar(v)}")
        return out
    if isinstance(value, list):
        if not value:
            return [f"{pad}[]"]
        out = []
        for item in value:
            if isinstance(item, (dict, list)):
                out.append(f"{pad}-")
                out.extend(_emit_value(item, indent + 2))
            else:
                out.append(f"{pad}- {_yaml_scalar(item)}")
        return out
    return [f"{pad}{_yaml_scalar(value)}"]


def _emit_models_yaml(data: dict) -> str:
    """Emit PyTarget.build_fragment() output as omp-style YAML.

    Schema assumption (matches PyTarget.build_fragment):
        providers:
          <name>:
            baseUrl: ...
            api: openai-completions
            apiKey: ...
            authHeader: true|false
            compat: { nested dict }
            models:
              - id: ...
                name: ...
                contextWindow: ...
                maxTokens: ...
                input: [text, image, ...]
                thinkingLevelMap: { nested dict }
    """
    lines: list[str] = []
    providers = data.get("providers", {})
    lines.append("providers:")
    for pname, pblock in providers.items():
        lines.append(f"  {_yaml_scalar(pname)}:")
        # baseUrl, api, apiKey, authHeader, compat first (preserve order).
        for key in ("baseUrl", "api", "apiKey", "authHeader", "compat"):
            if key in pblock:
                lines.extend(_emit_value({key: pblock[key]}, indent=4))
        # Then any extra scalar/dict/list keys (forward-compat).
        for key, val in pblock.items():
            if key in {"baseUrl", "api", "apiKey", "authHeader", "compat", "models"}:
                continue
            lines.extend(_emit_value({key: val}, indent=4))
        lines.append("    models:")
        for m in pblock.get("models", []):
            lines.append(f"      - id: {_yaml_scalar(m.get('id'))}")
            for key in ("name", "contextWindow", "maxTokens", "input"):
                if key in m:
                    lines.extend(_emit_value({key: m[key]}, indent=8))
            # Pass-through extras (reasoning, thinkingLevelMap, etc.).
            for key, val in m.items():
                if key in {"id", "name", "contextWindow", "maxTokens", "input"}:
                    continue
                lines.extend(_emit_value({key: val}, indent=8))
    return "\n".join(lines) + "\n"

class OmpTarget(PyTarget):
    name = "omp"
    display_name = "Oh My Pi"

    def __init__(
        self,
        models_path: Path | None = None,
        *,
        profile: str | None = None,
        config_root: Path | None = None,
    ) -> None:
        if models_path is None:
            selected = profile if profile is not None else os.environ.get(
                "OMP_PROFILE", os.environ.get("PI_PROFILE", "")
            )
            selected = selected.strip()
            # OMP resolves PI_CONFIG_DIR under HOME, even when it starts with "/".
            root = config_root or (Path.home() / (os.environ.get("PI_CONFIG_DIR") or ".omp").lstrip("/"))
            if selected and selected != "default":
                agent_dir = profile_models_path(selected, root).parent
            else:
                agent_override = os.environ.get("PI_CODING_AGENT_DIR") if config_root is None else None
                agent_dir = Path(agent_override) if agent_override else root / "agent"
            models_path = next(
                (agent_dir / name for name in ("models.yml", "models.yaml", "models.json")
                 if (agent_dir / name).exists()),
                agent_dir / "models.yml",
            )
        super().__init__(models_path=models_path)

    def _api_key_ref(
        self, provider: ProviderConfig, *, use_keychain: bool | None = None, redact: bool = False
    ) -> str:
        if use_keychain is False and provider.api_key_env:
            return provider.api_key_env
        return super()._api_key_ref(provider, use_keychain=use_keychain, redact=redact)

    def _format_dry_run(self, fragment: dict) -> str:
        return _emit_models_yaml(fragment).rstrip("\n")

    # omp reads models.yml (not models.json). Keep the YAML reader limited to
    # byop's known schema, while writes leave other providers' raw text intact.
    # ------------------------------------------------------------------
    def _write(self, data: dict) -> None:
        # Rewrite only the selected provider; other YAML entries and their
        # comments belong to the user and must stay byte-for-byte intact.
        existing = self._load()
        previous = existing.get("providers") or {}
        incoming = data.get("providers") or {}
        changed = {name: block for name, block in incoming.items()
                   if name not in previous or block != previous[name]}
        if not changed:
            return
        text = self.models_path.read_text(encoding="utf-8") if self.models_path.exists() else ""
        if text.lstrip().startswith("{"):
            existing.setdefault("providers", {}).update(changed)
            self.models_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
            return
        if not text:
            text = "providers:\n"
        lines = text.splitlines(keepends=True)
        root = next((i for i, line in enumerate(lines)
                     if re.fullmatch(r"providers:\s*(?:#.*)?\n?", line)), None)
        if root is None:
            raise ValueError("OMP models.yml has no block-style providers section; edit it manually.")
        end = next((i for i in range(root + 1, len(lines))
                    if lines[i].strip() and not lines[i][0].isspace()), len(lines))
        for name, block in changed.items():
            headers = [
                (i, len(line) - len(line.lstrip()), line.strip().split(":", 1)[0].strip("'\""))
                for i, line in enumerate(lines[root + 1:end], root + 1)
                if line.strip() and not line.lstrip().startswith("#")
                and re.match(r"^\s+[^:]+:", line)
            ]
            indent = min((depth for _, depth, _ in headers), default=2)
            siblings = [(i, key) for i, depth, key in headers if depth == indent]
            start = next((i for i, key in siblings if key == name), end)
            stop = next((i for i, _ in siblings if i > start), end)
            if start != end:
                while stop > start and (
                    not lines[stop - 1].strip()
                    or lines[stop - 1].lstrip().startswith("#")
                ):
                    stop -= 1
            rendered = _emit_models_yaml({"providers": {name: block}}).splitlines(keepends=True)[1:]
            if indent != 2:
                rendered = [" " * indent + line[2:] for line in rendered]
            if start and not lines[start - 1].endswith(("\n", "\r")):
                lines[start - 1] += "\n"
            lines[start:stop] = rendered
            end += len(rendered) - (stop - start)
        self.models_path.parent.mkdir(parents=True, exist_ok=True)
        self.models_path.write_text("".join(lines), encoding="utf-8")

    def _load(self) -> dict:
        if not self.models_path.exists():
            return {}
        text = self.models_path.read_text(encoding="utf-8")
        # Try JSON first (legacy files written by older byop / hand edits).
        stripped = text.lstrip()
        if stripped.startswith("{"):
            try:
                return json.loads(text) or {}
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Could not parse {self.models_path}: {exc}"
                ) from exc
        return _parse_models_yaml(text)

    # ------------------------------------------------------------------
    def is_installed(self) -> bool:
        return shutil.which("omp") is not None

    def install(self, log: Callable[[str], None] = print) -> None:
        if shutil.which("brew") is not None:
            log("Installing omp via Homebrew...")
            res = _run(["brew", "install", "can1357/tap/omp"])
            if res.returncode == 0:
                log("omp installed via Homebrew.")
                return
            log(
                f"brew install failed: {res.stderr or res.stdout}; "
                f"falling back to the published installer."
            )

        log("Installing omp via published installer (https://omp.sh/install)...")
        res = _run(["bash", "-c", "curl -fsSL https://omp.sh/install | sh"])
        if res.returncode == 0:
            log("omp installed via published script.")
            return

        if shutil.which("bun") is not None:
            log("Falling back to bun...")
            res = _run(["bun", "install", "-g", "@oh-my-pi/pi-coding-agent"])
            if res.returncode != 0:
                raise RuntimeError(
                    "omp install via bun failed:\n"
                    + (res.stderr or res.stdout)
                )
            log("omp installed via bun.")
            return

        raise RuntimeError(
            "Could not install omp. Try one of:\n"
            "  brew install can1357/tap/omp\n"
            "  curl -fsSL https://omp.sh/install | sh\n"
            "  bun install -g @oh-my-pi/pi-coding-agent"
        )
