---
name: byop-conventions
description: Apply byop's Python architecture, target-integration, and testing conventions when changing this repository.
---

# byop conventions

Follow the repository's root `AGENTS.md` for architecture and safety requirements.

- Keep the CLI thin; add target-specific behavior in `byop/core/targets/` and register each target in the existing registry.
- Use Python 3.11+ conventions: `snake_case` files and functions, `PascalCase` classes, and type annotations.
- Preserve unrelated user configuration and comments where supported. Keep secrets out of logs and dry-run/export output.
- Exercise target behavior with isolated temporary paths and mocked system services; do not touch the user's real keychain or settings.
- Run the relevant tests, `ruff check byop tests`, and `mypy byop`; CI also builds the package.
