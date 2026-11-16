"""INV64-10: the observed strategy comes only from the Gateway event scope.

The Risk host maps one Gateway account-observation event to the engine's
``ObservedExecutionEvent``. The strategy attribution must be the event's
Gateway-verified ``strategy_scope`` label; a caller-supplied intent payload, the
order's ``request_key`` or any other payload field must never override it, and
an event without a scope is refused instead of guessed.

Evidence: UNIT/FAKE fakes only; no Gateway process, account or provider.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from bt_api_py.risk_service_gateway import observed_event_from_account_event
from tests.unit.test_iter64_risk_service_gateway import EPOCH, RISK, account_event


def test_bypass_event_keeps_the_gateway_strategy_scope_and_ignores_payload():
    called = []

    def payload(key):
        called.append(key)
        return {"strategy": "payload-strategy", "strategy_scope": "payload-scope"}

    item = account_event(5, principal="other")  # no Risk permit for this order
    event = observed_event_from_account_event(
        item, epoch=EPOCH, risk_principal=RISK.principal_ref, intent_payload=payload
    )
    assert event.strategy_ref == "strategy-a"
    assert event.origin == "self" and event.kind == "order"
    # A bypass order has no Risk intent at all; the payload is not consulted.
    assert called == []


def test_own_event_payload_cannot_override_the_gateway_strategy_scope():
    payload = {
        "side": "buy",
        "offset": "open",
        "quantity": "1",
        "strategy": "payload-strategy",
        "strategy_scope": "payload-scope",
        "strategy_ref": "payload-ref",
    }
    item = account_event(5, principal=RISK.principal_ref)
    event = observed_event_from_account_event(
        item, epoch=EPOCH, risk_principal=RISK.principal_ref,
        intent_payload=lambda key: payload,
    )
    # The intent supplies only economic fields; identity comes from the stream.
    assert event.strategy_ref == "strategy-a"


def test_event_without_a_gateway_scope_is_refused_not_guessed():
    from bt_api_risk.pretrade import RiskFactError

    item = replace(account_event(5), strategy_scope=None)
    with pytest.raises(RiskFactError) as caught:
        observed_event_from_account_event(
            item, epoch=EPOCH, risk_principal=RISK.principal_ref,
            intent_payload=lambda key: {"strategy": "payload-strategy"},
        )
    assert caught.value.code == "RISK_OBSERVATION_INVALID"
