"""Read one local stop marker issued by a CLI owner."""

import stat
from pathlib import Path
from uuid import uuid4

from bt_api_py.monitor_service_config import read_bounded


def new_marker(directory: Path, prefix: str) -> Path:
    return directory / f'{prefix}-{uuid4().hex}.stop'


def marker_requested(marker: Path | None) -> bool:
    if marker is None:
        return False
    try:
        return (stat.S_ISREG(marker.lstat().st_mode)
                and read_bounded(marker, 0, protected=True) == b'')
    except (OSError, ValueError):
        return False
