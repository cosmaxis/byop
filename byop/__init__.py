"""byop: interactive setup of custom LLM providers for your AI coding tools."""

from .core.apply import apply_provider, default_model_capabilities
from .core.config import ModelConfig, ProviderConfig

__all__ = [
    "ProviderConfig",
    "ModelConfig",
    "apply_provider",
    "default_model_capabilities",
]

try:  # pragma: no cover - resolved at install time
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("byop")
except Exception:  # pragma: no cover
    __version__ = "2.1.10"  # keep in sync with pyproject.toml
