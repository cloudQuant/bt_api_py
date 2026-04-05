from __future__ import annotations

from datetime import timezone
from enum import Enum

try:
    from typing import Never as _Never
    from typing import ParamSpec as _ParamSpec
    from typing import Self as _Self
except ImportError:
    from typing_extensions import Never as _Never
    from typing_extensions import ParamSpec as _ParamSpec
    from typing_extensions import Self as _Self

try:
    from enum import StrEnum as _StrEnum
except ImportError:

    class _StrEnum(str, Enum):
        """Backport for Python < 3.11."""


Never = _Never
ParamSpec = _ParamSpec
Self = _Self
StrEnum = _StrEnum
UTC = timezone.utc

__all__ = ["Never", "ParamSpec", "Self", "StrEnum", "UTC"]
