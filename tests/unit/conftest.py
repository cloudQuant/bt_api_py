"""Unit-test fixtures and per-test process-state isolation.

The cross-module provider modules are registered through ``pytest_plugins`` in
the repository-root conftest: pytest rejects that declaration in a non-top-level
conftest. This file only carries fixtures and hooks.
"""

import sys

import pytest


@pytest.fixture(autouse=True)
def _fresh_risk_fence_process_state():
    """Give each unit test a fresh Risk fence process image.

    ``bt_api_risk.risk_order_journal`` keeps a provisional-owner registry and a
    quarantine set for the whole process and production never clears them.
    Genuinely uncertain entries (and controlled post-send UNKNOWN results) keep
    poisoning that process; the exact-type ``FENCE_ENTRY_UNTRUSTED`` refusal is
    COMPLETE and no longer quarantines the account scope.  Resetting after the
    test still lets each case observe its own in-process guard without one
    uncertain case cascading into ``FENCE_ENTRY_OWNER_ACTIVE`` for every later
    fence in the same pytest worker (iteration-65 handoff section 10.5).
    """
    yield
    module = sys.modules.get("bt_api_risk.risk_order_journal")
    if module is None:
        return
    with module._FENCE_PROVISIONAL_GUARD:
        module._FENCE_PROVISIONAL_OWNERS.clear()
        module._FENCE_ENTRY_QUARANTINED_KEYS.clear()
        module._FENCE_ENTRY_QUARANTINED_ALL = False
        module._FENCE_ENTRY_UNCERTAIN.clear()
