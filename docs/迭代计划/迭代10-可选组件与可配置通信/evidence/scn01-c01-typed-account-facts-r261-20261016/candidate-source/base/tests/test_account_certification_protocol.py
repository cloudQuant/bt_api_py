from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from bt_api_base.account_certification_protocol import (
    AccountConfigurationObservation,
    ExchangeServerTimeObservation,
)

_ACCOUNT_ID = "account-" + "a" * 64
_COMMON = {
    "account_key_id": _ACCOUNT_ID,
    "session_generation": "generation-one",
    "observation_id": "sample-one",
    "collected_at_utc": "2026-10-16T04:00:00.000000Z",
}


def test_account_configuration_preserves_unknown_and_round_trips_exactly():
    sample = AccountConfigurationObservation(
        **_COMMON,
        can_trade=True,
        can_deposit=None,
        can_withdraw=False,
        dual_side_position=True,
    )
    assert AccountConfigurationObservation.from_dict(sample.to_dict()) == sample
    assert sample.to_dict()["can_deposit"] is None
    with pytest.raises(FrozenInstanceError):
        sample.can_trade = False  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    (
        {"can_trade": 1},
        {"account_key_id": "account-alias"},
        {"extra_fact": "ignored"},
        {"collected_at_utc": "2026-10-16T04:00:00+00:00"},
    ),
)
def test_account_configuration_rejects_malformed_or_unbounded_projection(changes):
    values = {**_COMMON, "can_trade": True, "can_deposit": False,
              "can_withdraw": False, "dual_side_position": True}
    values.update(changes)
    if "extra_fact" in changes:
        with pytest.raises(ValueError):
            AccountConfigurationObservation.from_dict(values)
    else:
        with pytest.raises((TypeError, ValueError)):
            AccountConfigurationObservation(**values)


def test_server_time_is_provider_fact_and_uses_literal_integer():
    sample = ExchangeServerTimeObservation(**_COMMON, server_time_ms=1792123200000)
    assert ExchangeServerTimeObservation.from_dict(sample.to_dict()) == sample
    with pytest.raises(ValueError):
        ExchangeServerTimeObservation(**_COMMON, server_time_ms=True)
    with pytest.raises(ValueError):
        ExchangeServerTimeObservation(**{**_COMMON, "server_time_ms": 0.0})
