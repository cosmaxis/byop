# Repository Guidelines

## Project Overview

`byop` (Bring Your Own Provider) is a macOS-focused Python 3.11+ CLI that configures one custom OpenAI-compatible LLM provider across AI coding tools: Zed, py.dev/Pi, Oh My Pi, Claude Code, OpenCode, and Warp. It supports interactive setup, profile-based apply/status/doctor workflows, and non-interactive scripting. Secrets are kept in the macOS login keychain or referenced through environment variables rather than written to provider config files.

## Architecture & Data Flow

The main flow is:

1. `byop/cli.py` parses argparse subcommands and legacy flags.
2. Input comes from CLI flags, a config file, an interactive wizard, or pasted JSON/JSONC.
3. `ProviderConfig` and `ModelConfig` in `byop/core/config.py` validate and normalize provider data.
4. Profiles persist non-secret configuration as TOML under `~/.config/byop/profiles/`; the active profile is recorded in `~/.config/byop/config.toml`.
5. `byop/core/keychain.py` stores or retrieves API keys through macOS `security` commands; target configs contain command references or environment references, not plaintext secrets.
6. Registered `Target` implementations translate the provider into each tool's native config using merge, conflict, dry-run, and export behavior.

Targets implement the protocol in `byop/core/targets/base.py` and are registered in `byop/core/targets/registry.py`. Add a target by adding one module under `byop/core/targets/` and registering it in `ALL_TARGETS`; do not create a parallel abstraction.

Important safety behavior: settings merges are non-destructive and intended to be idempotent. Warp checks the installed endpoint schema before writing comment-preserving TOML, stores endpoint keys through native macOS Security.framework calls, and falls back to redacted manual instructions on incompatible versions or without Keychain. Preserve other endpoints, stable model IDs, permissions, and existing Keychain access controls. Require a full Warp restart after credential changes; never quit the application automatically.

## Key Directories

- `byop/`: installable Python package and `byop.cli:main` console entry point.
- `byop/core/`: configuration, profiles, paths, keychain, wizard, settings, conflict handling, and application orchestration.
- `byop/core/targets/`: per-tool installers/configurators (`zed.py`, `py.py`, `omp.py`, `claude.py`, `opencode.py`, `warp.py`).
- `tests/`: flat pytest suite, generally one test module per core/target concern.
- `docs/prd/`: product requirements and explicit scope/safety decisions, including Warp limitations.
- `docs/superpowers/`: release/design specifications.
- `.github/workflows/`: GitHub Actions CI and distribution checks.
- `install.sh`, `worker.js`, `wrangler.toml`: macOS installer and Cloudflare Worker delivery path.

## Development Commands

From a checkout:

```bash
python3 -m pip install -e ".[dev]"
pytest
ruff check byop tests
mypy byop
pytest --cov=byop --cov-report=term-missing
python -m build
```

Run the CLI with `byop`, or use `python -m byop.cli` when testing the module entry point. Useful non-interactive checks include `byop --dry-run`, `byop status`, `byop doctor`, and `byop export-config`. Use `--target`, `--skip-install`, and a temporary settings/config directory when testing writes.

There is no Makefile or lockfile. CI installs the editable package with the `dev` extra, then runs Ruff, mypy, pytest with coverage, and builds wheel/sdist artifacts.

## Code Conventions & Common Patterns

- Python target is 3.11; use type annotations and dataclasses for configuration/state records.
- Ruff uses line length 100, target `py311`, and rules `E,F,W,I,B,C4,UP`; `E501` is ignored. Keep imports sorted by Ruff.
- Keep the CLI thin: collect/validate provider input, resolve profiles/targets, and delegate target-specific translation to `Target` implementations.
- Prefer standard-library implementations where the project already does so: `subprocess` for `security`, hand-written limited YAML handling for OMP, and `unittest.mock`/`monkeypatch` in tests. Do not add dependencies for these existing patterns.
- Config writers load, merge, and write native files; preserve unrelated user settings and comments where supported (`settings.py` handles JSONC).
- Treat conflicts explicitly through `byop/core/conflict.py`. Multi-provider targets typically support `append`; single-provider targets use `replace` or `skip` and reject unsupported actions.
- Normalize and validate at the `ProviderConfig` boundary: provider names, URLs, API keys, models, capabilities, and reasoning options should not be revalidated independently by every caller.
- Keep secrets out of files and output. Use keychain command references, redaction, and `--dry-run` safety guards. Do not weaken validation or bypass the keychain for convenience.
- `BYOP_CONFIG_DIR` takes precedence over `XDG_CONFIG_HOME`, then the platform default. Tests should use temporary paths and mocks rather than real user settings/keychains.
- Deliberate simplifications and hard limits are marked with `ponytail:` comments; preserve the rationale when changing them.

## Important Files

- `pyproject.toml`: package metadata, dependencies, console script, Ruff/mypy/pytest settings.
- `byop/cli.py`: argparse entry point and subcommand orchestration.
- `byop/core/config.py`: `ProviderConfig`, `ModelConfig`, validation, provider fragments.
- `byop/core/profiles.py`: profile TOML persistence and active-profile management.
- `byop/core/paths.py`: configuration-directory resolution.
- `byop/core/keychain.py`: macOS keychain integration and shell command references.
- `byop/core/targets/base.py`: target protocol and shared target helpers.
- `byop/core/targets/registry.py`: target registration and construction.
- `byop/core/settings.py`: JSONC-aware settings load/merge/write.
- `.github/workflows/ci.yml`: supported Python matrix and required CI checks.
- `README.md`: supported targets, user workflows, flags, security behavior, and examples.

## Runtime/Tooling Preferences

- Required runtime: Python 3.11+; CI covers 3.11, 3.12, and 3.13 on macOS.
- Platform: macOS is the supported runtime because keychain and target installation use macOS tooling. `rich` is only installed on Darwin.
- Packaging: setuptools with `python -m build`; editable development install is `python -m pip install -e "[dev]"`.
- Runtime dependencies are intentionally small: `platformdirs`, `tomli-w`, and Darwin-only `rich`.
- The `byop` console script is defined as `byop = byop.cli:main`.
- Use the repository's configured Ruff and mypy rather than introducing formatters, frameworks, or new build systems.

## Testing & QA

Tests use pytest and pytest-cov with `tests/` as the test path and quiet output by default. The suite covers CLI behavior, dry-run/non-persistence guarantees, every target, profiles, config validation, settings merges, keychain wrappers, paste parsing, conflicts, wizard flows, apply, and export behavior.

Tests are flat and do not use a shared `conftest.py`; use `tmp_path`, `monkeypatch`, stdlib `unittest.mock`, and small inline fake targets. Patch dependencies at their usage site. Keep tests deterministic and isolated from the real home directory, settings files, network, and keychain.

Run the same checks CI runs:

```bash
ruff check byop tests
mypy byop
pytest --cov=byop --cov-report=term-missing
```

No coverage threshold is configured, but coverage output is part of CI. The build job runs only after the lint/type/test job and executes `python -m build`.
