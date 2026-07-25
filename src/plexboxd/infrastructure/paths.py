from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """Directory that holds ``data/``, ``logs/`` and ``node_modules/``.

    The bot runs with different working directories (``src/`` locally and in Docker,
    the repository root from tests), so relative paths like ``data/…`` cannot be
    trusted. Anchoring them here keeps every entry point pointed at the same files
    instead of silently creating an empty second browser profile under ``src/data``.
    """
    override = os.getenv("PLEXBOXD_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    # paths.py -> infrastructure -> plexboxd -> src -> <root>
    return Path(__file__).resolve().parents[3]


def resolve_data_path(value: str | Path) -> Path:
    """Resolve a possibly-relative path against the project root."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (project_root() / path).resolve()
