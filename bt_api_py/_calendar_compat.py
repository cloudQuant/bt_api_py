"""Platform-neutral ``fcntl`` import for the trading-calendar modules.

POSIX keeps the real stdlib module.  On Windows a genuine absence of the
module (``exc.name == 'fcntl'``) is replaced with the msvcrt-backed
``_calendar_fdops.FcntlShim`` so the calendar workflow is no longer
import-blocked there.  An injected import failure whose ``name`` is unset
(the ``test_missing_fcntl`` simulation) yields ``fcntl = None`` so the
existing fail-closed ``UNSUPPORTED_FILESYSTEM`` path is preserved.
"""

from __future__ import annotations

import os

if os.name == "nt":  # pragma: no cover - platform gated
    # The calendar modules use signal.SIGKILL as a default argument at import
    # time; Windows exposes no SIGKILL.  Mirror the test-run sitecustomize shim.
    import signal

    if not hasattr(signal, "SIGKILL"):
        signal.SIGKILL = getattr(signal, "SIGTERM", 15)

try:
    import fcntl as _fcntl
except ImportError as _exc:  # pragma: no cover - platform/test gated
    if os.name == "nt" and getattr(_exc, "name", None) == "fcntl":
        from bt_api_py import _calendar_fdops

        _fcntl = _calendar_fdops.FcntlShim()
    else:
        # A deliberately suppressed fcntl (tests) or a non-Windows host
        # without it: fail closed exactly as the original guarded imports did.
        _fcntl = None

fcntl = _fcntl

__all__ = ["fcntl"]
