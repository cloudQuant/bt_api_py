"""Which failures of an account read leave the owner running (I63-E146).

A read of the read model can fail without the session having changed: a failed or incomplete
query, or a read that ran past its budget. These fail the read only. Any other provider error
during a read is not a known way for a read to fail and stays fatal.
"""
import pytest
from bt_api_ctp.account_provider import CtpAccountSessionProviderError

from bt_api_py.ctp_gateway_trading import _FAILED_ACCOUNT_READ_CODES, _connection_transition_error

SESSION_CODES = ("SESSION_NOT_CONNECTED", "SESSION_SCOPE_UNTRUSTED", "QUERY_SCOPE_CHANGED")


def test_the_failed_read_codes_are_exactly_these_three():
    assert _FAILED_ACCOUNT_READ_CODES == {"QUERY_FAILED", "QUERY_INCOMPLETE", "QUERY_BUDGET_EXCEEDED"}


@pytest.mark.parametrize("code", sorted(_FAILED_ACCOUNT_READ_CODES))
def test_a_failed_read_is_recognised_only_while_an_account_read_is_active(code):
    error = CtpAccountSessionProviderError(code)
    assert _connection_transition_error(error, account_read=True) is True
    assert _connection_transition_error(error, account_read=False) is False
    assert _connection_transition_error(error) is False


@pytest.mark.parametrize("code", SESSION_CODES)
def test_session_changes_are_recognised_with_or_without_a_read(code):
    error = CtpAccountSessionProviderError(code)
    assert _connection_transition_error(error, account_read=True) is True
    assert _connection_transition_error(error, account_read=False) is True


@pytest.mark.parametrize("code", ["QUERY_PENDING", "QUERY_REQUEST_UNKNOWN", "QUERY_METHOD_UNAVAILABLE",
                                  "QUERY_RESULT_INVALID", "PROVIDER_CLOSED"])
def test_other_provider_errors_during_a_read_are_not_excused(code):
    error = CtpAccountSessionProviderError(code)
    assert _connection_transition_error(error, account_read=True) is False
    assert _connection_transition_error(error, account_read=False) is False



def test_the_read_models_own_late_finding_is_a_failed_read_too():
    from bt_api_gateway.account_read_model import GatewayAccountSessionWorkerError

    late = GatewayAccountSessionWorkerError("PROVIDER_RESPONSE_LATE")
    assert _connection_transition_error(late, account_read=True) is True
    assert _connection_transition_error(late, account_read=False) is False
    # Its other findings say the read model itself is in trouble: never excused.
    for code in ("STATE_WRITE_FAILED", "OBSERVATION_INVALID", "REFRESH_TOKEN_INVALID", "SESSION_OWNERSHIP_UNAVAILABLE"):
        assert _connection_transition_error(GatewayAccountSessionWorkerError(code), account_read=True) is False
