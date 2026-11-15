"""WIP: enforce the source quarantine through the real 64.13 host chain.

This remains a separate acceptance check: a true available value fails even
if the operator API itself is functioning. It uses synthetic resources only.
"""

from tests.integration._iter64_web_real_host_support import exercise_web_admin_scenario


def _require_source_quarantine(available):
    assert available is False


def test_web_and_independent_admin_share_real_risk_with_source_gate_closed(tmp_path):
    exercise_web_admin_scenario(tmp_path, check_available=_require_source_quarantine)
