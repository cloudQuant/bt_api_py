"""Helpers for keeping artifact-validation pip commands inside a local wheelhouse."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


class WheelhousePathError(ValueError):
    """Raised when an explicitly requested local wheelhouse is invalid."""


def resolve_wheelhouse_path(wheelhouse: Path | str | None) -> Path | None:
    """Require an explicit wheelhouse to be an existing absolute directory."""
    if wheelhouse is None:
        return None

    path = Path(wheelhouse)
    if not path.is_absolute():
        raise WheelhousePathError(f"wheelhouse path must be absolute: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise WheelhousePathError(f"wheelhouse path does not exist: {path}") from exc
    if not resolved.is_dir():
        raise WheelhousePathError(f"wheelhouse path is not a directory: {resolved}")
    return resolved


def pip_source_args(wheelhouse: Path | None) -> list[str]:
    """Return pip flags that confine resolution to an explicit wheelhouse."""
    if wheelhouse is None:
        return []
    return ["--no-index", "--find-links", str(wheelhouse), "--no-cache-dir"]


def pip_source_environment(
    environment: Mapping[str, str], wheelhouse: Path | None
) -> dict[str, str]:
    """Return an environment with no inherited pip source when a wheelhouse is set."""
    if not isinstance(environment, Mapping):
        raise TypeError("environment must be a mapping")

    result = dict(environment)
    if wheelhouse is None:
        return result

    for key in tuple(result):
        if key.startswith("PIP_"):
            result.pop(key, None)
    result.update(
        {
            # Ignore user/global pip configuration, which could add another source.
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_NO_INDEX": "1",
            "PIP_FIND_LINKS": str(wheelhouse),
            "PIP_NO_CACHE_DIR": "1",
        }
    )
    return result
