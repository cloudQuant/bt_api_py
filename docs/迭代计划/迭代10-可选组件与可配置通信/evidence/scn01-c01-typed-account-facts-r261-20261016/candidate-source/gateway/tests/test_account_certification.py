from __future__ import annotations

from datetime import datetime

import pytest

from bt_api_gateway.account_certification import (
    GatewayCertificationFactsError,
    collect_account_configuration,
    collect_exchange_server_time,
)

_ACCOUNT = "account-" + "b" * 64


class _Provider:
    def __init__(self, configuration=None, server_time=None):
        self.configuration = configuration
        self.server_time = server_time
        self.calls = []

    def read_account_configuration(self, *, generation, timeout):
        self.calls.append(("configuration", generation, timeout))
        return self.configuration

    def read_exchange_server_time(self, *, generation, timeout):
        self.calls.append(("server_time", generation, timeout))
        return self.server_time


def test_configuration_collects_only_typed_allowlisted_values_and_gateway_time():
    provider = _Provider(
        {
            "canTrade": True,
            "canDeposit": None,
            "canWithdraw": False,
            "dualSidePosition": True,
            "unrelated_native_field": "not forwarded",
        }
    )
    observation = collect_account_configuration(
        provider,
        account_key_id=_ACCOUNT,
        generation="session-g7",
        timeout=1.0,
        assert_owned=lambda: None,
        monotonic_clock=lambda: 10.0,
        wall_clock=lambda: 1_792_123_200.25,
    )
    assert observation.account_key_id == _ACCOUNT
    assert observation.session_generation == "session-g7"
    assert observation.can_trade is True
    assert observation.can_deposit is None
    assert observation.can_withdraw is False
    assert observation.dual_side_position is True
    assert observation.collected_at_utc == "2026-10-16T04:00:00.250000Z"
    assert "unrelated_native_field" not in observation.to_dict()
    assert provider.calls == [("configuration", "session-g7", 1.0)]


def test_server_time_uses_provider_leaf_not_gateway_receipt_clock():
    provider = _Provider(server_time={"serverTime": 1_792_123_321})
    observation = collect_exchange_server_time(
        provider,
        account_key_id=_ACCOUNT,
        generation="session-g7",
        timeout=1.0,
        assert_owned=lambda: None,
        monotonic_clock=lambda: 20.0,
        wall_clock=lambda: 1_792_123_200.0,
    )
    assert observation.server_time_ms == 1_792_123_321
    assert observation.collected_at_utc == "2026-10-16T04:00:00.000000Z"
    assert observation.server_time_ms != int(
        datetime.fromisoformat(observation.collected_at_utc[:-1] + "+00:00").timestamp() * 1000
    )


@pytest.mark.parametrize(
    ("provider", "collector", "code"),
    (
        (_Provider(configuration={"canTrade": 1}), collect_account_configuration,
         "PROVIDER_CONFIGURATION_INVALID"),
        (_Provider(server_time={"serverTime": True}), collect_exchange_server_time,
         "PROVIDER_SERVER_TIME_INVALID"),
        (_Provider(), collect_exchange_server_time, "PROVIDER_SERVER_TIME_INVALID"),
    ),
)
def test_collector_rejects_missing_or_malformed_provider_facts(provider, collector, code):
    with pytest.raises(GatewayCertificationFactsError) as exc_info:
        collector(
            provider,
            account_key_id=_ACCOUNT,
            generation="session-g7",
            timeout=1.0,
            assert_owned=lambda: None,
            monotonic_clock=lambda: 1.0,
            wall_clock=lambda: 1_792_123_200.0,
        )
    assert exc_info.value.code == code


def test_collector_refuses_missing_provider_method_and_late_response():
    class Missing:
        pass

    with pytest.raises(GatewayCertificationFactsError) as missing:
        collect_exchange_server_time(
            Missing(),
            account_key_id=_ACCOUNT,
            generation="session-g7",
            timeout=1.0,
            assert_owned=lambda: None,
            monotonic_clock=lambda: 1.0,
        )
    assert missing.value.code == "PROVIDER_FACT_UNAVAILABLE"

    ticks = iter((1.0, 2.1))
    provider = _Provider(server_time={"serverTime": 1_792_123_321})
    with pytest.raises(GatewayCertificationFactsError) as late:
        collect_exchange_server_time(
            provider,
            account_key_id=_ACCOUNT,
            generation="session-g7",
            timeout=1.0,
            assert_owned=lambda: None,
            monotonic_clock=lambda: next(ticks),
        )
    assert late.value.code == "PROVIDER_RESPONSE_LATE"


def test_server_time_leaf_rejects_extra_provider_fields():
    provider = _Provider(server_time={"serverTime": 1_792_123_321, "extra": "ignored"})
    with pytest.raises(GatewayCertificationFactsError) as exc_info:
        collect_exchange_server_time(
            provider,
            account_key_id=_ACCOUNT,
            generation="session-g7",
            timeout=1.0,
            assert_owned=lambda: None,
            monotonic_clock=lambda: 1.0,
        )
    assert exc_info.value.code == "PROVIDER_SERVER_TIME_INVALID"

