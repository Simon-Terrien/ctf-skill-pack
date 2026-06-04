"""Bibi — note-taking companion built on the CMS runtime."""

from bibi.app import BibiApp, TurnSummary
from bibi.config import BibiConfig, LLMConfig, SessionConfig, StorageConfig

__all__ = [
    "BibiApp",
    "TurnSummary",
    "BibiConfig",
    "LLMConfig",
    "SessionConfig",
    "StorageConfig",
]

__version__ = "0.1.0"
