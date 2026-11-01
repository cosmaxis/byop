"""Registry of configurable application targets.

Add a new application by implementing a target module and listing it here.
"""

from __future__ import annotations

from pathlib import Path

from .base import Target
from .claude import ClaudeTarget
from .omp import OmpTarget
from .opencode import OpencodeTarget
from .py import PyTarget
from .warp import WarpTarget
from .zed import ZedTarget

# Targets that are fully implemented and offered to the user.
ALL_TARGETS: list[type[Target]] = [
    ZedTarget,
    PyTarget,
    ClaudeTarget,
    OmpTarget,
    OpencodeTarget,
    WarpTarget,
]


def available_targets(
    settings_path: Path | None = None,
    *,
    omp_profile: str | None = None,
    omp_models_path: Path | None = None,
    claude_permission_mode: str | None = None,
    claude_auto_mode_model: str | None = None,
) -> list[Target]:
    """Instantiate registered targets with optional per-target paths."""
    targets: list[Target] = []
    for cls in ALL_TARGETS:
        if cls is ZedTarget and settings_path is not None:
            targets.append(cls(settings_path=settings_path))
        elif cls is OmpTarget:
            targets.append(cls(models_path=omp_models_path, profile=omp_profile))
        elif cls is ClaudeTarget:
            targets.append(cls(
                permission_mode=claude_permission_mode,
                auto_mode_model=claude_auto_mode_model,
            ))
        else:
            targets.append(cls())
    return targets
