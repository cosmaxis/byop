"""Conflict-resolution enum for the per-target configuration step."""

from __future__ import annotations

from enum import Enum


# `str, Enum` is required by the plan so .value exposes "replace"/"skip"/"append"
# to argparse and env vars naturally; UP042 (inheriting from str) is suppressed.
class ConflictAction(str, Enum):  # noqa: UP042
    """What to do when a provider already exists for the current target."""

    REPLACE = "replace"
    SKIP = "skip"
    APPEND = "append"


def default_conflict_action(target_name: str, *, interactive: bool) -> str | None:
    """Return the non-interactive default for a given target, or None for interactive.

    Rule: zed/claude REPLACE the active default; py/omp/opencode APPEND a new entry.
    Interactive runs always return None to signal "ask the user".
    """
    if interactive:
        return None
    return "replace" if target_name in {"zed", "claude", "warp"} else "append"


# Names of the targets that allow multiple concurrent provider entries.
_TARGETS_THAT_APPEND = frozenset({"py", "omp", "opencode"})
_VALID_CONFLICT_FLAGS = frozenset({"replace", "skip", "append"})


def resolve_conflict_action(
    target_name: str,
    *,
    has_collision: bool,
    interactive: bool,
    conflict_flag: str | None,
) -> ConflictAction | None:
    """Decide what to do when configuring ``target_name`` for a given provider.

    Pure function — no I/O, no prompting. The CLI runs the actual user prompt
    when this returns ``None`` (interactive + collision + no CLI override).

    Returns ``None`` only when the CLI must prompt the user interactively.
    """
    if conflict_flag is not None and conflict_flag not in _VALID_CONFLICT_FLAGS:
        raise ValueError(
            f"conflict_flag must be one of {sorted(_VALID_CONFLICT_FLAGS)}, "
            f"got {conflict_flag!r}"
        )

    # Explicit --conflict always wins.
    if conflict_flag is not None:
        return ConflictAction(conflict_flag)

    # No collision: nothing to ask about, even in interactive mode.
    if not has_collision:
        default_action = default_conflict_action(target_name, interactive=False)
        assert default_action is not None
        return ConflictAction(default_action)

    chosen = default_conflict_action(target_name, interactive=interactive)
    if chosen is not None:
        return ConflictAction(chosen)
    # Interactive + collision: caller will run the chooser.
    assert interactive and has_collision
    return None


def supports_append(target_name: str) -> bool:
    """True if a target supports the ``append`` conflict action (multi-provider)."""
    return target_name in _TARGETS_THAT_APPEND

