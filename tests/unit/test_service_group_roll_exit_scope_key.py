"""Risk AccountScope wire-key validation for roll-exit close receipts."""

from __future__ import annotations

import json

import pytest
from bt_api_risk.core.admission import AccountScope

from bt_api_py.service_group_roll_exit import GroupRollExitObservationV1, _sha256
from tests.unit.test_service_group_roll_exit_claim_clock import _observation_payload


def _payload_with_scope_key(scope_key: str) -> dict[str, object]:
    payload = _observation_payload(1234.5)
    receipt = payload["close_receipt"]
    receipt["claim"]["scope_key"] = scope_key
    unsigned_receipt = dict(receipt)
    unsigned_receipt.pop("receipt_sha256")
    receipt_digest = _sha256(unsigned_receipt)
    receipt["receipt_sha256"] = receipt_digest
    payload["close_receipt_sha256"] = receipt_digest
    return payload


def _canonical_scope_key(fields: dict[str, object]) -> str:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def test_real_risk_account_scope_key_is_accepted_in_close_receipt() -> None:
    scope = AccountScope(
        provider="fixture",
        account_id="provider-account-a",
        environment="simulation",
    )

    observation = GroupRollExitObservationV1.from_payload(_payload_with_scope_key(scope.key))

    assert observation.close_receipt is not None
    assert observation.close_receipt["claim"]["scope_key"] == scope.key
    assert scope.key == (
        '{"account_id":"provider-account-a","environment":"simulation",'
        '"provider":"fixture"}'
    )


@pytest.mark.parametrize(
    "scope_key",
    [
        '{"account_id":"provider-account-a", "environment":"simulation","provider":"fixture"}',
        '{"account_id":"provider-account-é","environment":"simulation","provider":"fixture"}',
        '{"account_id":"provider-account-a","account_id":"other","environment":"simulation","provider":"fixture"}',
        _canonical_scope_key({"account_id": "provider-account-a", "environment": "simulation"}),
        _canonical_scope_key({"account_id": " ", "environment": "simulation", "provider": "fixture"}),
        _canonical_scope_key({"account_id": "provider-account-a", "environment": "simulation", "provider": 7}),
    ],
)
def test_noncanonical_or_invalid_risk_account_scope_key_is_rejected(scope_key: str) -> None:
    payload = _payload_with_scope_key(scope_key)

    with pytest.raises(ValueError, match="invalid Risk account scope key"):
        GroupRollExitObservationV1.from_payload(payload)


def test_risk_account_scope_key_has_an_explicit_size_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bt_api_py.service_group_roll_exit._MAX_RISK_SCOPE_KEY_BYTES", 128)
    scope_key = _canonical_scope_key(
        {"account_id": "provider-account-a", "environment": "simulation", "provider": "x" * 100}
    )

    with pytest.raises(ValueError, match="invalid Risk account scope key"):
        GroupRollExitObservationV1.from_payload(_payload_with_scope_key(scope_key))


def test_scope_key_validation_does_not_relax_other_receipt_references() -> None:
    scope = AccountScope(
        provider="fixture",
        account_id="provider-account-a",
        environment="simulation",
    )
    payload = _payload_with_scope_key(scope.key)
    receipt = payload["close_receipt"]
    receipt["claim"]["permit_id"] = "invalid permit ref"
    unsigned_receipt = dict(receipt)
    unsigned_receipt.pop("receipt_sha256")
    receipt_digest = _sha256(unsigned_receipt)
    receipt["receipt_sha256"] = receipt_digest
    payload["close_receipt_sha256"] = receipt_digest

    with pytest.raises(ValueError, match="invalid permit_id"):
        GroupRollExitObservationV1.from_payload(payload)
