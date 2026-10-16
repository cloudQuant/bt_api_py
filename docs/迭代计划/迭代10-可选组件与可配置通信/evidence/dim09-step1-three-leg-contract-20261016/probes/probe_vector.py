"""Probe: residual_vector is serialized/valid only for version-3 groups.

NEW package only (worktree under review). All output to the review directory.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "D:/bt_api_py/bt_api/bt_api_execution/tests")

import test_group_coordinator_three_leg as t  # noqa: E402
from bt_api_execution import (  # noqa: E402
    ExecutionState,
    GroupOrderState,
)

ROOT = Path("D:/temp/it10dim09-review-20261016-r1/compat/vector-probe")

# --- v2: no key in payload, constructor forbids a non-None vector ---------
request2 = t._make_request_v2()
d2 = ROOT / "v2"
d2.mkdir(parents=True, exist_ok=True)
coordinator, store, _r, _o = t._coordinator(d2, request2)
accepted_v2 = coordinator.accept(request2)
payload_v2 = accepted_v2.to_payload()
assert accepted_v2.request.version == 2
print(f"V2_payload_has_residual_vector_key={'residual_vector' in payload_v2}")
try:
    replace(accepted_v2, residual_vector=(Decimal("0"), Decimal("0")))
    print("V2_non_none_vector_rejected=False")
except t.ContractValidationError as error:
    print(f"V2_non_none_vector_rejected=True msg={error}")
store.close()

# --- v3: key present (None until a residual exists), list after finish ----
request3 = t._make_request_v3()
d3 = ROOT / "v3"
d3.mkdir(parents=True, exist_ok=True)
coordinator, store, _r, orders = t._coordinator(d3, request3)
put, fut, call = t._chain_intent_ids(request3)
orders["account-a"].terminal_on_submit[put] = (ExecutionState.CANCELLED, Decimal("1.5"), 1, 3)
orders["account-a"].terminal_on_submit[fut] = (ExecutionState.FILLED, Decimal("1.25"), 1, 5)
orders["account-a"].terminal_on_submit[call] = (ExecutionState.FILLED, Decimal("1.5"), 1, 7)
accepted_v3 = coordinator.accept(request3)
payload_v3_accept = accepted_v3.to_payload()
print(f"V3_accept_key_present={'residual_vector' in payload_v3_accept} value={payload_v3_accept['residual_vector']}")
result = coordinator.advance_group(request3.portfolio_ref, request3.group_id)
assert result.state is GroupOrderState.RECOVERY_REQUIRED
payload_v3 = result.to_payload()
print(f"V3_finish_key_value={json.dumps(payload_v3['residual_vector'])}")
print(f"V3_finish_vector={result.residual_vector} scalar={result.residual_base_units}")
roundtrip = type(result).from_payload(payload_v3)
assert roundtrip.residual_vector == result.residual_vector
print("V3_roundtrip_vector_equal=True")
store.close()
print("VECTOR_PROBE_DONE")
