"""Exercise the real Risk scope resolver before deriving quota subjects."""

from __future__ import annotations

from types import SimpleNamespace

from bt_api_base.service_protocol import WireScope

from bt_api_py.service_risk_order_entry import RiskOrderEntryService


def _manual_scope(manual_book_ref: str, *, portfolio_ref: str | None = None) -> WireScope:
    return WireScope(
        execution_domain_ref="domain:quota-selector",
        account_ref="account:quota-selector",
        manual_book_ref=manual_book_ref,
        portfolio_ref=portfolio_ref,
    )


def _command(scope: WireScope, account_scope: object) -> SimpleNamespace:
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
        canonical_payload={"instrument": "BTC-USD", "position_effect": "CLOSE"},
        scope=account_scope,
        exchange_name="synthetic-exchange",
        provider_account_id="provider-account",
    )


def _service(scope: WireScope, target: SimpleNamespace) -> RiskOrderEntryService:
    service = object.__new__(RiskOrderEntryService)
    service._targets = {scope: target}
    service._admission = {scope: object()}
    service._provider_contracts = {}
    return service


def test_manual_quota_subject_requires_exact_resolved_manual_book_scope() -> None:
    account_scope = object()
    trusted_scope = _manual_scope("manual-book:trusted")
    target = SimpleNamespace(
        account_scope=account_scope,
        strategy_id="strategy:internal-mapper-label",
        exchange_name="synthetic-exchange",
        provider_account_id="provider-account",
    )
    service = _service(trusted_scope, target)

    facts = RiskOrderEntryService._quota_dispatch_facts(
        service,
        _command(trusted_scope, account_scope),
        target,
    )
    assert facts == (
        "account:quota-selector",
        None,
        None,
        "BTC-USD",
        "CLOSE",
    )

    other_book = _manual_scope("manual-book:other")
    assert (
        RiskOrderEntryService._quota_dispatch_facts(
            service,
            _command(other_book, account_scope),
            target,
        )
        is None
    )

    contaminated_scope = _manual_scope(
        "manual-book:trusted", portfolio_ref="portfolio:unexpected"
    )
    contaminated_service = _service(contaminated_scope, target)
    assert (
        RiskOrderEntryService._quota_dispatch_facts(
            contaminated_service,
            _command(contaminated_scope, account_scope),
            target,
        )
        is None
    )
