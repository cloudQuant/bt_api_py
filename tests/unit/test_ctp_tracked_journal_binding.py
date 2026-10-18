"""Focused validation of hash-valid CTP Risk dispatch bindings.

This uses a real SQLite RiskOrderJournal and DurableRiskGate with synthetic
order metadata. It checks the narrow claim-binding validator; it is not CTP
callback, native-dispatch, or end-to-end execution evidence.
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

import pytest

from bt_api_risk import AccountScope, DurableRiskGate, IntentAction, RiskIntent, RiskPolicy
from bt_api_py.service_risk_order_journal import (
    RiskOrderJournal,
    canonical_json,
    sha256_text,
    _ctp_dispatch_binding_claim_valid,
)


_RISK_SCOPE = AccountScope("ctp", "binding-fixture-account", "simnow")
_PRINCIPAL = "binding-fixture-principal"
_MANAGED_INTENT_ID = "managed-ctp-binding-1"
_RISK_INTENT_ID = "risk-ctp-binding-1"
_INSTRUMENT = "rb2710"


def _binding(claim) -> dict[str, object]:
    return {
        "schema": "ctp-risk-ctp-submit-binding-v1",
        "risk_intent_id": claim.intent_id,
        "permit_id": claim.permit_id,
        "risk_claim_digest": claim.claim_digest,
        "risk_intent_hash": claim.intent_hash,
        "submit_command_id": "ctp-submit-command-1",
        "managed_intent_id": _MANAGED_INTENT_ID,
        "runtime_order_id": "runtime-order-1",
        "order_ref": "20261019-000001",
        "provider_order_id": None,
        "account_key": "ctp-account-key-1",
        "scope_key": "execution-scope:ctp:binding-fixture-account:strategy-1",
        "risk_scope_key": _RISK_SCOPE.key,
        "trading_day": "20261019",
        "reservation_digest_sha256": "b" * 64,
        "callback_owner_intent_id": "ctp-callback-owner-1",
        "session_generation_id": "ctp-session-generation-1",
        "session_binding_sha256": "c" * 64,
        "request_payload_sha256": "d" * 64,
        "native_request_payload_sha256": "e" * 64,
        "native_request_id": 17,
        "local_queue_receipt_id": "ctp-queue-receipt-1",
        "instrument_id": _INSTRUMENT,
        "exchange_id": "SHFE",
    }


@contextmanager
def _append_dispatching(tmp_path, binding_change=None):
    journal = RiskOrderJournal(
        str(tmp_path / "ctp-binding-journal.sqlite3"), writer_id="ctp-binding-journal-writer"
    )
    gate = DurableRiskGate(
        str(tmp_path / "ctp-binding-risk.sqlite3"),
        RiskPolicy(
            "ctp-binding-policy",
            Decimal("1000"),
            max_increase_count=1,
            notional_unit="USD",
        ),
        execution_journal_authority=journal,
    )
    risk_intent = RiskIntent(
        intent_id=_RISK_INTENT_ID,
        scope=_RISK_SCOPE,
        action=IntentAction.INCREASE,
        notional=Decimal("100"),
        payload_fingerprint="a" * 64,
        notional_unit="USD",
    )
    request = {
        "client_order_id": "ctp-binding-client-order-1",
        "price": "100",
        "quantity": "1",
        "side": "BUY",
        "symbol": _INSTRUMENT,
    }
    canonical_payload = {
        "instrument_id": _INSTRUMENT,
        "position_effect": "OPEN",
        "quantity": "1",
        "side": "BUY",
    }
    request_digest = sha256_text(
        canonical_json(
            {
                "exchange_name": "CTP",
                "operation": "BtApi.make_order(normalized=True)",
                "request": request,
            }
        )
    )
    wire_scope = {
        "account_ref": "binding-fixture-account",
        "account_refs": [],
        "execution_domain_ref": "ctp-execution-domain",
        "group_id": None,
        "leg_id": None,
        "manual_book_ref": None,
        "portfolio_ref": None,
        "run_id": "ctp-binding-test-run",
        "strategy_ref": "strategy-1",
    }
    try:
        with journal.writer_fence(_RISK_SCOPE) as fence:
            command, created = journal.prepare_or_get(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=_MANAGED_INTENT_ID,
                risk_intent_id=_RISK_INTENT_ID,
                payload_sha256=sha256_text(canonical_json(canonical_payload)),
                canonical_payload=canonical_payload,
                wire_scope=wire_scope,
                exchange_name="CTP",
                provider_account_id="binding-fixture-account",
                client_order_id=request["client_order_id"],
                order_request=request,
                order_request_sha256=request_digest,
            )
            assert created is True
            assert command.state == "PREPARED"

            permit = gate.reserve(risk_intent)
            gate.claim_for_dispatch(permit.permit_id, risk_intent)
            claim = gate.dispatch_claim_binding(permit.permit_id)
            assert claim.intent_id == _RISK_INTENT_ID

            journal.append_event(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=_MANAGED_INTENT_ID,
                event_type="RESERVED",
                payload={"permit_id": permit.permit_id, "risk_intent_id": claim.intent_id},
            )
            journal.append_event(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=_MANAGED_INTENT_ID,
                event_type="CLAIMED",
                payload={
                    "claim": {
                        "cause_id": claim.cause_id,
                        "claim_digest": claim.claim_digest,
                        "claimed_at": claim.claimed_at,
                        "intent_hash": claim.intent_hash,
                        "intent_id": claim.intent_id,
                        "permit_id": claim.permit_id,
                        "scope_key": claim.scope.key,
                    },
                    "permit_id": permit.permit_id,
                    "risk_intent_id": claim.intent_id,
                },
            )
            binding = _binding(claim)
            if binding_change is not None:
                binding_change(binding)
            event = journal.append_event(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=_MANAGED_INTENT_ID,
                event_type="DISPATCHING",
                payload={
                    "dispatch_attempt_count": 1,
                    "permit_id": permit.permit_id,
                    "risk_intent_id": claim.intent_id,
                    "ctp_dispatch_binding": binding,
                },
            )
            yield journal, claim, event, binding
    finally:
        gate.close()


def test_valid_ctp_binding_matches_real_claim_in_hash_verified_journal_event(tmp_path) -> None:
    with _append_dispatching(tmp_path) as (journal, claim, event, binding):
        persisted = journal.get_event(
            _RISK_SCOPE, _PRINCIPAL, _MANAGED_INTENT_ID, event.revision
        )
        assert persisted == event
        assert len(persisted.record_sha256) == 64
        assert journal.get_command(_RISK_SCOPE, _PRINCIPAL, _MANAGED_INTENT_ID).state == "DISPATCHING"
        assert _ctp_dispatch_binding_claim_valid(
            binding, claim=claim, risk_scope_key=_RISK_SCOPE.key
        ) is True


@pytest.mark.parametrize(
    "mutation",
    ["wrong_claim", "wrong_scope", "bool_request_id", "unknown_key"],
)
def test_hash_valid_dispatch_event_rejects_malformed_ctp_claim_binding(tmp_path, mutation) -> None:
    def alter(binding: dict[str, object]) -> None:
        if mutation == "wrong_claim":
            binding["risk_claim_digest"] = "9" * 64
        elif mutation == "wrong_scope":
            binding["risk_scope_key"] = "risk-scope:foreign-account"
        elif mutation == "bool_request_id":
            binding["native_request_id"] = True
        else:
            binding["unrecognized"] = "must-not-be-ignored"

    with _append_dispatching(tmp_path, alter) as (journal, claim, event, binding):
        persisted = journal.get_event(
            _RISK_SCOPE, _PRINCIPAL, _MANAGED_INTENT_ID, event.revision
        )
        assert persisted == event
        assert persisted.payload["ctp_dispatch_binding"] == binding
        assert len(persisted.record_sha256) == 64
        assert _ctp_dispatch_binding_claim_valid(
            binding, claim=claim, risk_scope_key=_RISK_SCOPE.key
        ) is False
