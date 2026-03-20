"""OrderRefAllocator — monotonically increasing CTP OrderRef allocator.

CTP requires each order to carry a unique, monotonically increasing
``OrderRef`` (string of up to 13 digits). The allocator:

1. Reads the last-used value from a persistent JSON state file.
2. On CTP login, aligns with ``MaxOrderRef`` returned by the front.
3. Allocates the next value atomically (thread-safe).
4. Persists the high-water mark back to the state file.

State file path: ``{state_dir}/gateway_{account_id}_state.json``
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_KEY = "last_order_ref"


class OrderRefAllocator:
    """Thread-safe, persistent CTP OrderRef allocator.

    Args:
        account_id: CTP account identifier (used for state file naming).
        state_dir: Directory where the state file is stored.
        initial_value: Starting value if no state file exists.

    Usage::

        alloc = OrderRefAllocator("acc-1", "/tmp/gw_state")
        alloc.align_with_max(100)  # after CTP login
        ref = alloc.next()         # "101"
        ref2 = alloc.next()        # "102"
    """

    def __init__(
        self,
        account_id: str,
        state_dir: str | Path = "/tmp/bt_gateway_state",
        initial_value: int = 0,
    ) -> None:
        self._account_id = account_id
        self._state_dir = Path(state_dir)
        self._state_file = self._state_dir / f"gateway_{account_id}_state.json"
        self._lock = threading.RLock()
        self._value: int = initial_value

        self._load()

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def next(self) -> str:
        """Allocate the next OrderRef value (string).

        The value is incremented, persisted, and returned.
        """
        with self._lock:
            self._value += 1
            val = self._value
            self._persist_locked()
            return str(val)

    def current(self) -> int:
        """Return the current (last allocated) value without incrementing."""
        with self._lock:
            return self._value

    def align_with_max(self, max_order_ref: int | str) -> None:
        """Align the allocator with CTP's ``MaxOrderRef`` after login.

        If ``max_order_ref`` is greater than the current value, the
        allocator jumps forward to ensure monotonicity.

        Args:
            max_order_ref: The ``MaxOrderRef`` string returned by
                ``OnRspSettlementInfoConfirm`` or ``OnRspUserLogin``.
        """
        max_val = int(max_order_ref)
        with self._lock:
            if max_val > self._value:
                logger.info(
                    "OrderRefAllocator[%s]: aligning %d -> %d",
                    self._account_id,
                    self._value,
                    max_val,
                )
                self._value = max_val
            self._persist_locked()

    def reset(self, value: int = 0) -> None:
        """Reset the allocator to a specific value. Use with caution."""
        with self._lock:
            self._value = value
            self._persist_locked()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load the last value from the state file."""
        if not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise TypeError(f"state payload must be an object, got {type(data).__name__}")
            saved = int(data.get(_STATE_KEY, 0) or 0)
            with self._lock:
                if saved > self._value:
                    self._value = saved
            logger.info(
                "OrderRefAllocator[%s]: loaded last_order_ref=%d from %s",
                self._account_id,
                self._value,
                self._state_file,
            )
        except (TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "OrderRefAllocator[%s]: failed to load state: %s",
                self._account_id,
                exc,
            )

    def _persist(self) -> None:
        """Persist the current value to the state file."""
        with self._lock:
            self._persist_locked()

    def _persist_locked(self) -> None:
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            existing: dict[str, Any] = {}
            if self._state_file.exists():
                try:
                    loaded = json.loads(self._state_file.read_text(encoding="utf-8"))
                    if not isinstance(loaded, dict):
                        raise TypeError(
                            f"state payload must be an object, got {type(loaded).__name__}"
                        )
                    existing = loaded
                except (TypeError, json.JSONDecodeError, OSError) as exc:
                    logger.warning(
                        "OrderRefAllocator[%s]: failed to read existing state during persist: %s",
                        self._account_id,
                        exc,
                    )
            existing[_STATE_KEY] = int(self._value)
            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    delete=False,
                    dir=self._state_dir,
                    prefix=f"{self._state_file.stem}_",
                    suffix=".tmp",
                    encoding="utf-8",
                ) as handle:
                    json.dump(existing, handle, indent=2)
                    tmp_path = Path(handle.name)
                os.replace(str(tmp_path), str(self._state_file))
            finally:
                if tmp_path is not None and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError as exc:
                        logger.warning(
                            "OrderRefAllocator[%s]: failed to clean up temp state file %s: %s",
                            self._account_id,
                            tmp_path,
                            exc,
                        )
        except (TypeError, ValueError, OSError) as exc:
            logger.warning(
                "OrderRefAllocator[%s]: failed to persist state: %s",
                self._account_id,
                exc,
            )

    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable snapshot for diagnostics."""
        with self._lock:
            return {
                "account_id": self._account_id,
                "last_order_ref": self._value,
                "state_file": str(self._state_file),
            }
