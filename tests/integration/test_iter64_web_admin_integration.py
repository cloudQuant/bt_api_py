"""64.13 operator API integration; status does not prove order authority.

The real HTTP, CURVE, Registry and Risk components use synthetic identities,
provider ports and temporary ledgers; no account, source or release acceptance.
"""

from tests.integration._iter64_web_real_host_support import exercise_web_admin_scenario


def _require_boolean_status(available):
    assert type(available) is bool


def test_web_and_independent_admin_use_the_same_risk_service(tmp_path):
    exercise_web_admin_scenario(tmp_path, check_available=_require_boolean_status)
