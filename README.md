# byop

[![CI](https://github.com/Cosmaxis/byop/actions/workflows/ci.yml/badge.svg)](https://github.com/Cosmaxis/byop/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**byop** (**B**ring **Y**our **O**wn **P**rovider) is a production-ready,
interactive CLI that wires a **custom OpenAI-compatible LLM provider** — the
endpoint is yours to supply — into your AI coding tools, starting with
[Zed](https://zed.dev) and [py.dev (Pi)](https://pi.dev), with a single
command.

## Why

Self-hosting or using a third-party OpenAI-compatible gateway (e.g. a private
`/v1` endpoint) means repeating the same fiddly setup in every tool: install the
app, find the right config file, get the provider schema right, and store the
API key somewhere safe. `byop` does all of that for you, consistently, and never
writes your secret to disk in plaintext.

## Features

- **Multi-target** — detects which AI coding tools are installed and asks which
  to configure. Missing apps can be installed for you on the spot.
- **Opt-in installs** — asks before installing missing tools in interactive
  setup; explicit `--target` selections are intended for scripts.
- **One provider, many tools** — configure Zed and py.dev from the same input.
- **Non-destructive** — merges into each app's config, preserving your existing
  settings and comments.
- **Idempotent** — safe to re-run: merging refreshes provider settings, while the
  `agent` / `inline_assistant` / `edit_predictions` blocks are updated to the
  currently selected provider on each run (they are not appended).
- **Secure by default** — API keys live in the macOS login keychain (Zed) or are
  read from the keychain at runtime (py.dev); never plaintext in config files.
- **Scriptable** — full non-interactive flag mode for CI and dotfiles.
- **Extensible** — adding a new tool (e.g. Claude Code) is one module + one line
  in a registry.

## Supported targets

| Target | Config file | Install |
| --- | --- | --- |
| `zed` (Zed) | `~/.config/zed/settings.json` | Homebrew cask / direct download |
| `py` (py.dev / Pi) | `~/.pi/agent/models.json` | Homebrew cask |
| `omp` (oh-my-pi / Oh My Pi) | `~/.omp/agent/models.yml` | `brew install can1357/tap/omp` / `curl -fsSL https://omp.sh/install | sh` |
| `claude` (Claude Code) | `~/.claude/settings.json` | `brew install --cask claude-code` / `npm i -g @anthropic-ai/claude-code` / `curl -fsSL https://claude.ai/install.sh | sh` |
| `codex` (Codex desktop / CLI) | `~/.codex/config.toml` | Install from https://openai.com/codex/ |
| `opencode` (OpenCode) | `~/.config/opencode/opencode.json` | `brew install --cask opencode` / `curl -fsSL https://opencode.ai/install | sh` |
| `warp` (Warp) | `~/.warp/settings.toml` + macOS Keychain | Homebrew cask / official DMG download |

### Claude Code & OpenCode

- **Claude Code (`--target claude`)** configures `apiKeyHelper` (reading from the macOS login keychain), `env.ANTHROPIC_BASE_URL` (automatically stripping a trailing `/v1` segment because Claude Code posts to `$ANTHROPIC_BASE_URL/v1/messages`), and `model` in `~/.claude/settings.json`. Your upstream gateway **must** expose an Anthropic Messages-compatible `/v1/messages` endpoint (e.g. LiteLLM, Bifrost, OpenRouter).
- **OpenCode (`--target opencode`)** writes `{env:<PROVIDER>_API_KEY}` under `provider.<Name>.options.apiKey` in `~/.config/opencode/opencode.json` and exports that variable dynamically from the macOS login keychain in your shell profile (`~/.zshrc`), keeping plaintext secrets off disk. Open a new terminal (or `source ~/.zshrc`) before running `opencode`.

#### Claude gateway model metadata

For Claude Code, BYOP registers the **first configured model** with
`ANTHROPIC_CUSTOM_MODEL_OPTION`, uses its display name (or ID) as
`ANTHROPIC_CUSTOM_MODEL_OPTION_NAME`, and sets `CLAUDE_CODE_MAX_CONTEXT_TOKENS`
and `CLAUDE_CODE_MAX_OUTPUT_TOKENS` from its configured context/output limits.
The wire ID stays unchanged: `pro` remains `pro`, not an impersonated Claude model.
Existing Sonnet/Opus/Haiku mappings and unrelated settings are preserved.

Set the per-model limits explicitly, for example
`--model pro:max_tokens=128000:max_output_tokens=16000`.
These are **operator-supplied limits**, not
discovered capabilities; BYOP defaults to 128,000 context and 32,000 output tokens.
For a routing combo, choose limits supported by every possible target.

Restart Claude after applying. The context override is session-wide, not a
per-model registry: review it when switching models or using different role mappings.
It applies directly to unrecognized gateway IDs like `pro`; recognized Claude IDs,
bare `claude-` IDs, and `[1m]` IDs have additional rules in
[Claude's context documentation](https://code.claude.com/docs/en/model-config#correct-the-window-for-a-gateway-or-custom-model-id).
BYOP does not disable compaction, spoof model families, grant model allowlist access,
or infer thinking/vision support. Older Claude versions may ignore these settings;
upgrade if `/context` does not show the configured window.

#### Claude Auto-mode safety classifier

The coding model and Auto-mode safety classifier are separate. Choosing `pro`
does not guarantee that background safety checks use `pro`; Claude can call
Sonnet separately with the same API key. A failed classifier blocks the tool,
not necessarily the main model.

Add one of these choices to your normal Claude setup invocation:

```bash
# Human approvals; avoids Auto-mode classifier requests.
byop apply --target claude --claude-permission-mode manual

# Keep automatic safety review with an explicitly selected classifier.
byop apply --target claude --claude-permission-mode auto \
  --claude-auto-mode-model claude-sonnet-4-6
```

Manual writes `permissions.defaultMode: "default"` (Claude's Manual value),
not bypass mode. Auto writes `"auto"`; the classifier option writes
`env.CLAUDE_CODE_AUTO_MODE_MODEL` and requires Claude Code 2.1.302 or later.
Supplying only the classifier option does not enable Auto mode.
Omitting both options preserves the existing permission mode and classifier.
Allow/deny rules are preserved; `--conflict skip` leaves existing matching settings unchanged.
These are per-invocation target options, not saved provider-profile fields.

Restart Claude after applying. Session flags, project/managed policy, and resumed
session state can override the user-level default. Do not turn off safety checks
or assume a GPT-backed gateway alias is a compatible classifier: select a model
your Claude version supports and verify gateway access, key policy, billing, and
an actual tool safety check. Setting the option alone does not prove availability.
See [Claude's classifier documentation](https://code.claude.com/docs/en/permission-modes#choose-the-classifier-model).

### Warp

`byop --target warp` (or selecting **Warp** in the interactive checklist) installs Warp automatically if missing and configures a custom endpoint using **OpenAI Chat Completions**. Supply provider details in the wizard, or apply an existing profile with `byop apply --target warp` (add `--skip-install` to write settings without installing). Use `https://api.cosmaxis.com/v1` and model `pro` for the Cosmaxis setup verified on Warp `0.2026.10.21.08.25.02`.

- The installed app's `Contents/Resources/settings_schema.json` must describe the supported endpoint/model format. Older or incompatible versions fall back to redacted manual instructions. `--no-keychain` also requires manual setup; Warp does not use a shell environment-variable reference for this credential.
- Non-secret endpoints live in `agents.custom_endpoints`. Model UUIDs remain stable on reapply. The first model becomes the default agent model when default-agent selection is enabled. Other endpoints, permissions, settings, and comments are preserved; the first original file is backed up to `settings.toml.bak` with owner-only permissions.
- Keys live in Warp Stable's generic-password item: service `dev.warp.Warp-Stable`, account `AiCustomEndpointKeys`, encoded as a JSON map of endpoint IDs to keys. Native Security.framework calls keep new secrets out of process arguments. Existing items are updated in place to preserve their access controls; other endpoint keys are preserved. Keychain authorization may require local interaction. The legacy `AiApiKeys` item is not modified.
- **Save active work, fully quit Warp with Cmd-Q, then reopen it. A new tab is not enough:** Warp caches API keys in memory. Select the custom model, not Auto. `byop` never quits Warp or interrupts terminal tasks automatically.
- Warp requires a public HTTPS endpoint. Prompts, context, endpoint URL, and key pass through **Warp's servers** before reaching your provider. See [Warp's custom endpoint documentation](https://docs.warp.dev/agents/inference/custom-inference-endpoint/).

`--conflict replace` updates the matching provider, preserving other endpoints; `skip` leaves an existing matching provider and key untouched. Replacements that would leave execution profiles referencing removed models are rejected. Symlink-managed settings require manual setup rather than replacing the link. `append` remains unsupported by this target. `--dry-run`, status, and export never read or write Warp's Keychain item. Setup does not perform inference or prove upstream tool/streaming compatibility.

The October 26, 2026 Cosmaxis diagnosis established that Warp's Anthropic Messages path emitted tool schemas with `required: null`, which the configured upstream rejected. The Chat Completions path passed streaming inference and a terminal-tool round trip. This is why the target uses `openai_chat_completions`, rather than assuming Anthropic or Responses compatibility from a basic API test.

## Installation

### One command (recommended)

```bash
curl -sfS https://byop.cosmaxis.com/install | sh
```

This installs `byop` and its declared Python dependencies in an isolated
[pipx](https://pipx.pypa.io) environment, pinned to the latest release. On
macOS, the installer uses Homebrew to install missing Python 3.11+, Git, and
pipx. If Homebrew is missing, it installs that first and may request your
administrator password. Network access and the macOS system tools (including
`curl`) are required.

When installation finishes, run `byop` and follow the prompts to enter your
provider and select tools. If `byop` is not on your PATH yet, use the full
command printed by the installer, or run `pipx ensurepath` and open a new
terminal.

> Prefer to read before you run? Inspect the script first:
> `curl -sfS https://byop.cosmaxis.com/install`

### From source

```bash
git clone https://github.com/Cosmaxis/byop.git
cd byop
python3 -m pip install -e ".[dev]"      # includes test/lint/type tooling
```

### With pipx (from GitHub)

```bash
pipx install "git+https://github.com/Cosmaxis/byop.git"
```

## Usage

### Interactive (recommended)

```bash
byop
```

Use ↑/↓ and Enter to navigate menus; completed steps collapse into a single summary line. When choosing tools, move with ↑/↓, toggle with Space (or press `a` to toggle all), and confirm with Enter. Selecting an uninstalled tool prompts once to install and configure it automatically. Escape or Ctrl-C cancels.

Set `BYOP_PLAIN=1` for line-based numbered menus, including in a terminal.
Piped input and terminals too small for the checklist use this fallback
automatically.

**Add provider** uses the provider name as the saved profile name. Existing
profiles keep their names. Zed-specific questions appear only when setting up
Zed. **Switch provider** selects a profile without changing tool settings.

Non-interactive runs without `--target` configure detected apps only. Pass an
explicit `--target` to select a missing app; with `--skip-install`, byop writes
its configuration without installing it.

### Non-interactive (scripting / CI)

| Flag | Purpose |
| --- | --- |
| `--target {zed,py,omp,claude,codex,opencode,warp}` | Configure only the named target(s); repeatable. Defaults to all detected apps. |
| `--conflict {replace,skip,append,prompt}` | What to do when a provider with the same name already exists on a target. Default: prompt interactively; `replace` for zed/claude/codex/warp, `append` for py/omp/opencode in non-interactive mode. |
| `--settings PATH` | Override the Zed settings path (default `~/.config/zed/settings.json`). |
| `--dry-run` | Print the configuration fragments that *would* be written; change nothing. |
| `--no-keychain` | Skip the macOS keychain (falls back to an embedded key — less secure). |
| `--env-key` | Also export the key as a shell environment variable. |
| `--skip-install` | Configure settings only; do not install/upgrade any app. |
| `--version` | Print version and exit |
| `--export-config` | Print the byop-managed slice of each target's settings as JSON and exit (no writes). Combine with `--target` and `--export-provider` to filter. |

Warp smoke check (uses a temporary settings path and never touches live settings/keychain):
```bash
python -c 'from pathlib import Path; from byop.core.targets.warp import WarpTarget; p=Path("/tmp/byop-warp-settings.toml"); t=WarpTarget(p); print(t.is_installed(), t.settings_path); t.configure(__import__("byop.core.config", fromlist=["ProviderConfig"]).ProviderConfig("Demo", "https://api.example.com/v1", "secret-key-123", [__import__("byop.core.config", fromlist=["ModelConfig"]).ModelConfig("model")]), dry_run=True)'
```

Repeat `--model` for multiple models; the **first** becomes the default for
features that take a single model.

## Inspecting the current configuration

`--export-config` dumps the byop-managed slice of each target's settings as
JSON and exits without writing anything. Useful for diffing between runs or
copying a fragment into another tool:

```bash
byop --export-config                            # all targets, all providers
byop --export-config --target zed               # just Zed
byop --export-config --export-provider Cosmaxis  # only that name
```

Output shape (one key per target, `config_path` shows where the target would
write):

```json
{
  "zed": {
    "config_path": "/Users/you/.config/zed/settings.json",
    "providers": { "Cosmaxis": { "api_url": "...", "available_models": [...] } },
    "agent": { "default_model": { ... } },
    "edit_predictions": { ... }
  },
  "py": { "config_path": "...", "providers": { ... } }
}
```

## Subcommands (v2.0+)

After the first run, `byop` remembers your provider as a **profile** under
`~/.config/byop/profiles/<name>.toml`. Subsequent invocations skip the
wizard:

| Command | Purpose |
| --- | --- |
| `byop` | Interactive menu for tool setup and provider profiles. |
| `byop status` | Read-only dashboard: active profile, target state, drift. |
| `byop apply [flags]` | Sync the active profile to one or more targets. |
| `byop doctor` | Detect drift between the profile and each target's settings. |
| `byop profile list` | Show all saved profiles (active first). |
| `byop profile use <name>` | Switch the active profile. |
| `byop profile new` | Walk through creating a new profile (interactive). |
| `byop profile edit` | Re-run the wizard pre-filled with the active profile. |
| `byop profile export` | Print the active profile as JSON. |
| `byop profile delete <name>` | Delete a saved profile. |
| `byop export-config` | Dump current target settings as JSON (alias of `--export-config`). |

The legacy top-level flags (`byop --provider P --api-url ...`) still work and
are routed to `apply`. So existing scripts and CI pipelines don't need to
change.

## What it writes

For a provider `MyProvider` with model `my-model`:

**Codex desktop / CLI** (`~/.codex/config.toml`):

```toml
model = "my-model"
model_provider = "MyProvider"

[model_providers.MyProvider]
name = "MyProvider"
base_url = "https://api.example.com/v1"
wire_api = "responses"

[model_providers.MyProvider.auth]
command = "security"
args = ["find-internet-password", "-s", "https://api.example.com/v1", "-a", "Bearer", "-w"]
```

Codex custom providers support the Responses API only. The selected provider is
single-valued, so Codex uses `replace` or `skip`; `append` is rejected.

**Zed** (`~/.config/zed/settings.json`):

```json
{
  "language_models": {
    "openai_compatible": {
      "MyProvider": {
        "api_url": "https://api.example.com/v1",
        "available_models": [
          { "name": "my-model", "max_tokens": 128000, "max_output_tokens": 32000 }
        ]
      }
    }
  },
  "agent": {
    "default_model":          { "provider": "MyProvider", "model": "my-model" },
    "inline_assistant_model": { "provider": "MyProvider", "model": "my-model" }
  }
}
```

**py.dev** (`~/.pi/agent/models.json`):

```json
{
  "providers": {
    "MyProvider": {
      "baseUrl": "https://api.example.com/v1",
      "api": "openai-completions",
      "apiKey": "!security find-internet-password -s https://api.example.com/v1 -a Bearer -w",
      "authHeader": true,
      "models": [
        { "id": "my-model", "name": "my-model", "contextWindow": 128000,
          "maxTokens": 32000, "input": ["text"] }
      ]
    }
  }
}
```

The py.dev `apiKey` is a `!command` that reads the secret from the macOS
keychain **at request time**, so the key is never stored in `models.json`. If no
keychain entry exists, `byop` falls back to embedding the key inline (with a
warning).


Codex custom providers must use the OpenAI Responses API (`wire_api =
"responses"`). The base URL cannot contain credentials, a query string, or a
fragment; supply credentials in the API key field. With the default keychain mode, byop stores the secret in the
macOS keychain and configures Codex's command-backed `auth` lookup, so the
secret is not written to `config.toml`. With `--no-keychain`, Codex references
the generated environment variable name instead; byop does not write the secret
into the Codex config. `--env-key` additionally exports the key in the shell
profile. Codex supports one active provider, so `append` is not available for
this target; `skip` preserves the existing active provider.

The command-backed keychain lookup uses macOS's `security` command at request
time. Review the generated fragment with `--dry-run` before applying it, and
ensure the keychain entry is available to the Codex process.

For multiple models or configured reasoning effort, byop builds the Codex model
picker catalog from the installed CLI's bundled metadata. Install or update
the Codex CLI first; the selected gateway must support the Responses API and
the requested reasoning levels. The catalog does not discover gateway capabilities.

## Security

- **Zed**: the API key is written to the **macOS login keychain** keyed by the
  provider `api_url` (account `Bearer`) — the same entry Zed itself uses. It is
  never written to `settings.json` or shell history.
- **py.dev**: the key is read from that same keychain entry via a `!command`
  reference, so `models.json` contains no secret.
- **Codex**: the default configuration uses a command-backed `auth` lookup
  against that keychain entry; `--no-keychain` uses an `env_key` reference.
  `config.toml` contains neither form of the secret.
- The `--env-key` option appends a single `export` line to `~/.zshrc` (or
  another detected profile) and refuses to duplicate it on re-runs.
- No credentials are sent over the network by `byop` itself; it only writes
  local config files and keychain entries.

## Conflict resolution

If a provider with the same name already exists on a target, `byop` asks you
what to do:

- **replace** — overwrite the existing entry. For Zed and Claude Code this also
  switches the active default model to the new one.
- **skip** — leave the file untouched (idempotent re-run). The macOS keychain
  entry is still ensured.
- **append** — write under `<ProviderName>_2`, `<ProviderName>_3`, … so the new
  provider lives alongside the existing one. Only available for targets that
  support multiple concurrent providers (py.dev, omp, opencode).

For non-interactive / scripted runs, pass `--conflict replace|skip|append`
to skip the prompt. The default in non-interactive mode is `replace` for
Zed/Claude Code/Warp and `append` for py.dev/omp/OpenCode.

The wizard's first prompt also accepts a pasted JSON block. If your provider
description is already in a file, you can pipe it: `pbpaste | xargs -0 byop`
isn't right, but `byop` will detect any multi-line JSON shape and skip the
per-field prompts. See `--config-file` for the scripted equivalent.

## Behavioral notes

A few choices `byop` makes for you, so they aren't surprising:

- **Edit Predictions (`edit_predictions`)** — when enabled, `byop` writes
  `"mode": "eager"` and `"allow_data_collection": "no"`. `eager` means Zed
  *auto-accepts* completions from your provider (no manual confirm step); switch
  it to `"on"|"off"` or remove the block if you'd rather review each suggestion.
  `allow_data_collection: "no"` opts out of any edit-telemetry sharing.
- **First model is the default** — wherever a single model is required (Agent,
  Inline Assistant, commit messages, thread summaries), the **first** model you
  configure is used.
- **Re-running overwrites feature blocks** — `agent`, `inline_assistant_model`,
  and `edit_predictions` are set to the currently selected provider on every
  run. They are merged, not appended, so stale provider references are replaced.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| py.dev: "No API key found" | Ensure the keychain entry exists (`byop` writes it) or re-run `byop --target py`. |
| Zed: "provider not configured" | Restart Zed after configuration; the model must be listed under `language_models.openai_compatible`. |
| Tool not detected | Pass `--target zed`/`--target py` explicitly, or install the app and re-run. |
| Dry run shows nothing | You disabled all targets; re-run and answer the target prompts, or pass `--target`. |

## Architecture

```
byop/
├── cli.py                 # argparse entry point, interactive/non-interactive
├── core/
│   ├── config.py          # ProviderConfig model + validation + Zed fragments
│   ├── settings.py        # JSONC-aware read/merge/write for Zed
│   ├── keychain.py        # macOS keychain + env-var key storage
│   ├── zed.py             # Zed install/upgrade (brew + direct download)
│   ├── wizard.py          # interactive prompts (accepts pasted JSON)
│   ├── prompt.py          # rich-based prompts (stdlib fallback)
│   ├── conflict.py        # ConflictAction enum + resolve_conflict_action()
│   ├── paste.py           # detect/parse pasted provider JSON
│   ├── apply.py           # legacy Zed helper (delegates to targets)
│   └── targets/           # one module per app
│       ├── base.py        # Target protocol
│       ├── zed.py         # ZedTarget
│       ├── py.py          # PyTarget (py.dev)
│       ├── claude.py      # ClaudeTarget (Claude Code)
│       ├── codex.py       # CodexTarget (Codex desktop / CLI)
│       ├── omp.py         # OmpTarget (subclass of PyTarget)
│       └── registry.py    # ALL_TARGETS + extension point
└── tests/                 # unit + integration (pytest)
```

## Adding a new target

Implement a class with the `Target` interface
(`is_installed`, `install`, `configure`, `current_provider_names`) in
`byop/core/targets/<name>.py`, then register it in
`byop/core/targets/registry.py::ALL_TARGETS`. The CLI automatically detects it,
asks whether to configure/install it, and wires the provider through your
`configure` method.

## Roadmap

The multi-target architecture is the point: `byop` is built to wire *any* tool
that accepts an OpenAI-compatible endpoint. Today the shipped targets are:

- **Zed** (`zed`) — fully implemented.
- **py.dev / Pi** (`py`) — fully implemented.
- **Claude Code** (`claude`) — fully implemented.
- **Oh My Pi / omp** (`omp`) — fully implemented.
- **Codex desktop / CLI** (`codex`) — fully implemented.

If you want to add another target, the registry is the only place to touch.
- **Hermes / OpenCode** and other agent CLIs that consume `models.json`-style
  provider configs.

Contributions adding a target are a single module + one registry line.

## Development

```bash
python3 -m pip install -e ".[dev]"
pytest                 # unit + integration tests
ruff check byop tests # lint
mypy byop              # type check
```

CI (`.github/workflows/ci.yml`) runs lint, type-check, and tests on macOS
across Python 3.11–3.13, then builds distribution artifacts.

## License

MIT — see [LICENSE](LICENSE).
