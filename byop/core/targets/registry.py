"""Registry of configurable application targets.

Add a new application by implementing a target module and listing it here.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .base import Target
from .claude import ClaudeTarget
from .codex import CodexTarget
from .omp import OmpTarget, omp_config_root
from .opencode import OpencodeTarget
from .py import PyTarget
from .warp import WarpTarget
from .zed import ZedTarget

# Targets that are fully implemented and offered to the user.
ALL_TARGETS: list[type[Target]] = [
    ZedTarget,
    PyTarget,
    ClaudeTarget,
    CodexTarget,
    OmpTarget,
    OpencodeTarget,
    WarpTarget,
]


def available_targets(
    settings_path: Path | None = None,
    *,
    omp_profiles: Sequence[str] | None = None,
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
            if omp_profiles is not None:
                targets.extend(cls(profile=profile, config_root=omp_config_root()) for profile in omp_profiles)
            else:
                targets.append(cls(models_path=omp_models_path))
        elif cls is ClaudeTarget:
            targets.append(cls(
                permission_mode=claude_permission_mode,
                auto_mode_model=claude_auto_mode_model,
            ))
        else:
            targets.append(cls())
    return targets
