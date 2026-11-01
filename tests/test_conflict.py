"""Tests for byop.core.conflict."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from byop.core.conflict import (
    ConflictAction,
    default_conflict_action,
    resolve_conflict_action,
    supports_append,
)


def test_conflict_action_values():
    assert ConflictAction.REPLACE.value == "replace"
    assert ConflictAction.SKIP.value == "skip"
    assert ConflictAction.APPEND.value == "append"


def test_conflict_action_iteration_order():
    # Order matters for the CLI prompt (replace / skip / append shown in this order).
    assert [a.value for a in ConflictAction] == ["replace", "skip", "append"]


def test_default_replace_for_zed_and_claude():
    assert default_conflict_action("zed", interactive=False) == "replace"
    assert default_conflict_action("claude", interactive=False) == "replace"


def test_default_append_for_py_and_omp():
    assert default_conflict_action("py", interactive=False) == "append"
    assert default_conflict_action("omp", interactive=False) == "append"


def test_interactive_default_is_none():
    # Interactive mode asks the user; the helper returns None sentinel so the
    # CLI knows to call resolve_conflict_action(...) with a chooser instead.
    assert default_conflict_action("zed", interactive=True) is None
    assert default_conflict_action("py", interactive=True) is None


# ----------------------------------------------------------------------
# resolve_conflict_action
# ----------------------------------------------------------------------
def test_resolve_no_collision_non_interactive_uses_target_default():
    # No collision, non-interactive -> default per target.
    assert (
        resolve_conflict_action("zed", has_collision=False,
                                 interactive=False, conflict_flag=None)
        == ConflictAction.REPLACE
    )
    assert (
        resolve_conflict_action("py", has_collision=False,
                                 interactive=False, conflict_flag=None)
        == ConflictAction.APPEND
    )


def test_resolve_no_collision_interactive_uses_target_default():
    # Interactive run against an unconfigured target must not crash or prompt.
    assert (
        resolve_conflict_action("zed", has_collision=False,
                                 interactive=True, conflict_flag=None)
        == ConflictAction.REPLACE
    )
    assert (
        resolve_conflict_action("opencode", has_collision=False,
                                 interactive=True, conflict_flag=None)
        == ConflictAction.APPEND
    )


def test_resolve_no_collision_honors_explicit_flag():
    # Even without a collision, an explicit --conflict flag wins.
    assert (
        resolve_conflict_action("py", has_collision=False,
                                 interactive=False, conflict_flag="skip")
        == ConflictAction.SKIP
    )


def test_resolve_collision_explicit_flag_wins():
    # --conflict always wins, even when interactive.
    assert (
        resolve_conflict_action("zed", has_collision=True,
                                 interactive=True, conflict_flag="skip")
        == ConflictAction.SKIP
    )
    assert (
        resolve_conflict_action("omp", has_collision=True,
                                 interactive=True, conflict_flag="replace")
        == ConflictAction.REPLACE
    )


def test_resolve_collision_interactive_returns_none():
    # Interactive + collision + no flag -> None, so the CLI will run the chooser.
    assert (
        resolve_conflict_action("omp", has_collision=True,
                                 interactive=True, conflict_flag=None)
        is None
    )


def test_resolve_collision_non_interactive_uses_target_default():
    # Non-interactive + collision + no flag -> default per target kind.
    assert (
        resolve_conflict_action("zed", has_collision=True,
                                 interactive=False, conflict_flag=None)
        == ConflictAction.REPLACE
    )
    assert (
        resolve_conflict_action("py", has_collision=True,
                                 interactive=False, conflict_flag=None)
        == ConflictAction.APPEND
    )


def test_resolve_invalid_flag_raises():
    import pytest

    with pytest.raises(ValueError, match="conflict_flag"):
        resolve_conflict_action(
            "zed", has_collision=True, interactive=False,
            conflict_flag="bogus",
        )


def test_supports_append_only_for_multi_provider_targets():
    assert supports_append("py") is True
    assert supports_append("omp") is True
    assert supports_append("opencode") is True
    assert supports_append("zed") is False
    assert supports_append("claude") is False
    assert supports_append("warp") is False
    assert supports_append("unknown") is False
