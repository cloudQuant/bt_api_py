"""Focused tests for trusted Gateway quota subjects on Risk order dispatch."""

from __future__ import annotations

from types import SimpleNamespace

from bt_api_base.service_protocol import WireScope

from bt_api_py.service_risk_order_entry import RiskOrderEntryService


class _BindingStub:
    def __init__(self, target: object) -> None:
        self._target = target

    def _binding_for_command(self, command: object) -> tuple[None, object, None, None]:
        del command
        return None, self._target, None, None


def _command(scope: WireScope) -> SimpleNamespace:
    return SimpleNamespace(
        wire_scope={
            "execution_domain_ref": scope.execution_domain_ref,
            "account_ref": scope.account_ref,
            "account_refs": list(scope.account_refs),
            "strategy_ref": scope.strategy_ref,
            "run_id": scope.run_id,
            "manual_book_ref": scope.manual_book_ref,
            "portfolio_ref": scope.portfolio_ref,
            "group_id": scope.group_id,
            "leg_id": scope.leg_id,
        },
        canonical_payload={"instrument": "BTC-USD", "position_effect": "OPEN"},
    )


def test_manual_book_dispatch_uses_account_bucket_without_fabricating_strategy_run() -> None:
    target = object()
    service = _BindingStub(target)
    scope = WireScope(
        execution_domain_ref="domain",
        account_ref="account",
        manual_book_ref="manual-book",
    )

    facts = RiskOrderEntryService._quota_dispatch_facts(service, _command(scope), target)

    assert facts == ("account", None, None, "BTC-USD", "OPEN")


def test_strategy_quota_rejects_missing_run_instead_of_using_manual_or_target_identity() -> None:
    target = object()
    service = _BindingStub(target)
    # WireScope permits this partial strategy routing claim; the quota bridge
    # must reject it because no authenticated run subject can be derived.
    scope = WireScope(
        execution_domain_ref="domain",
        account_ref="account",
        strategy_ref="strategy",
    )

    facts = RiskOrderEntryService._quota_dispatch_facts(service, _command(scope), target)

    assert facts is None
