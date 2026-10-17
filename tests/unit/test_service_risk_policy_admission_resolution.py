"""Account-owner startup binds only the exact persisted Risk policy."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireScope
from bt_api_risk.core.admission import (
    AccountScope,
    DurableRiskGate,
    IntentAction,
    RiskPolicy,
    RiskIntent,
)
from bt_api_risk.core.instrument import InstrumentRiskMetadata, InstrumentRiskRegistry
from bt_api_risk.order_admission import (
    DurableRiskAdmissionAdapter,
    RiskAdmissionDeniedError,
)
from bt_api_risk.policy_admission import PolicyCatalogAccountAdmissionResolver
from bt_api_risk.service_component import (
    ConfirmedPolicyStore,
    PolicyCatalogService,
    PolicyCatalogStore,
)

from bt_api_py import service_risk_order_entry
from bt_api_py.service_components import (
    ComponentResources,
    ComponentSpec,
    ServiceComponentError,
    _account_risk_policy_metadata,
)

_DOMAIN = "paper:confirmed-account-policy-startup"
_STRATEGY = "policy-startup-strategy"
_PRINCIPAL = "policy-startup-confirmed-owner"


def _owner_scope(account_ref: str) -> WireScope:
    return WireScope(_DOMAIN, account_ref=account_ref)


def _confirmed_receipt(account_ref: str, policy_ref: str, limit: str):
    content = {
        "risk_policy": {
            "policy_id": policy_ref,
            "max_increase_notional": limit,
            "max_increase_count": 3,
            "permit_ttl_seconds": 30.0,
            "require_strategy_allocation": False,
            "notional_unit": "USD",
        }
    }
    content_json = json.dumps(
        content, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    wire_scope = _owner_scope(account_ref)
    receipt = {
        "policy_ref": policy_ref,
        "policy_version": hashlib.sha256(content_json.encode("utf-8")).hexdigest(),
        "owner_principal": _PRINCIPAL,
        "owner_scope": {
            "execution_domain_ref": wire_scope.execution_domain_ref,
            "account_ref": wire_scope.account_ref,
            "account_refs": [],
            "strategy_ref": None,
            "run_id": None,
            "manual_book_ref": None,
            "portfolio_ref": None,
            "group_id": None,
            "leg_id": None,
        },
        "published_utc": "2026-10-17T00:00:00Z",
    }
    return receipt, content


def _target(account_scope: AccountScope):
    registry = InstrumentRiskRegistry(
        (
            InstrumentRiskMetadata(
                instrument="POLICYTEST",
                metadata_version="policy-startup-v1",
                as_of_ns=1,
                expires_at_ns=2,
                tick_size=Decimal("0.01"),
                quantity_step=Decimal("1"),
                contract_multiplier=Decimal("1"),
                max_gross_notional=Decimal("1000"),
                valuation_unit="USD",
            ),
        )
    )
    return service_risk_order_entry.RiskOrderTarget(
        account_scope=account_scope,
        strategy_id=_STRATEGY,
        exchange_name="paper___spot",
        provider_account_id=account_scope.account_id,
        registry=registry,
    )


def _resource_set(
    *,
    gate: DurableRiskGate,
    target,
    scope: WireScope,
    resolver: PolicyCatalogAccountAdmissionResolver,
    policy_scope: WireScope,
    account_scope: AccountScope,
):
    def map_order(order):
        return RiskIntent(
            intent_id=order.intent_id,
            scope=account_scope,
            action=IntentAction.INCREASE,
            notional=order.notional,
            payload_fingerprint="a" * 64,
            notional_unit=gate.policy.notional_unit,
        )

    admission = DurableRiskAdmissionAdapter(gate, map_order)
    values = {
        "gate": gate,
        "targets": {scope: target},
        "admission": {scope: admission},
        "account_policy_admission_resolver": resolver,
        "account_policy_scope": policy_scope,
    }
    return ComponentResources.borrowed(values, ready=lambda: True), admission


def _bind_confirmed_account_policy(resources, policy_ref: str):
    spec = ComponentSpec(
        component="risk",
        instance_id="confirmed-account-owner",
        bind_ports=("service.order_entry",),
        options={"role": "account_owner", "account_policy_ref": policy_ref},
    )
    return _account_risk_policy_metadata(
        resources,
        spec=spec,
        options=spec.options,
        module=service_risk_order_entry,
    )


def test_account_owner_reopens_confirmed_policy_and_gate_enforces_each_accounts_version(
    tmp_path,
):
    confirmed_path = tmp_path / "confirmed-policies.sqlite3"
    writer = ConfirmedPolicyStore(str(confirmed_path))
    for account_ref, policy_ref, limit in (
        ("policy-account-a", "account-policy-a-v1", "10"),
        ("policy-account-b", "account-policy-b-v1", "30"),
    ):
        receipt, content = _confirmed_receipt(account_ref, policy_ref, limit)
        writer.confirm_document(receipt, content)
    writer.close()

    confirmed = ConfirmedPolicyStore(str(confirmed_path))
    resolver = PolicyCatalogAccountAdmissionResolver(None, confirmed)
    stores = []
    try:
        outcomes = {}
        for account_ref, policy_ref in (
            ("policy-account-a", "account-policy-a-v1"),
            ("policy-account-b", "account-policy-b-v1"),
        ):
            wire_scope = WireScope(_DOMAIN, account_ref=account_ref, strategy_ref=_STRATEGY)
            policy_scope = _owner_scope(account_ref)
            account_scope = AccountScope("paper", account_ref, "demo")
            resolved = resolver.resolve_confirmed(policy_ref, account_ref, scope=policy_scope)
            gate = DurableRiskGate(
                str(tmp_path / f"{account_ref}-risk.sqlite3"), resolved.risk_policy
            )
            target = _target(account_scope)
            resources, admission = _resource_set(
                gate=gate,
                target=target,
                scope=wire_scope,
                resolver=resolver,
                policy_scope=policy_scope,
                account_scope=account_scope,
            )
            bindings, required_ports = _bind_confirmed_account_policy(resources, policy_ref)

            assert required_ports == frozenset({"service.order_entry"})
            assert len(bindings) == 1
            assert bindings[0].scope == wire_scope
            assert bindings[0].policy_ref == policy_ref
            assert bindings[0].policy_version == resolved.risk_policy.fingerprint
            assert gate.bind_policy(account_scope).policy_fingerprint == (
                resolved.risk_policy.fingerprint
            )
            stores.append(resources)

            order = SimpleNamespace(
                intent_id=f"{account_ref}-increase",
                scope=SimpleNamespace(
                    provider="paper",
                    account_ref=account_ref,
                    environment="demo",
                    strategy_id=_STRATEGY,
                ),
                notional=Decimal("20"),
            )
            if account_ref == "policy-account-a":
                with pytest.raises(RiskAdmissionDeniedError) as denied:
                    admission.reserve(order)
                assert denied.value.code == "ACCOUNT_LIMIT_EXHAUSTED"
                outcomes[account_ref] = "denied"
            else:
                permit = admission.reserve(order)
                assert permit.policy_fingerprint == resolved.risk_policy.fingerprint
                gate.release(permit.permit_id, "test-not-dispatched")
                outcomes[account_ref] = "reserved"

        assert outcomes == {
            "policy-account-a": "denied",
            "policy-account-b": "reserved",
        }
    finally:
        for resources in stores:
            resources.close()
        confirmed.close()


def test_account_owner_refuses_confirmed_policy_scope_for_another_account(tmp_path):
    confirmed_path = tmp_path / "wrong-account-confirmed.sqlite3"
    confirmed = ConfirmedPolicyStore(str(confirmed_path))
    receipt, content = _confirmed_receipt("policy-account-a", "account-policy-a-v1", "10")
    confirmed.confirm_document(receipt, content)
    resolver = PolicyCatalogAccountAdmissionResolver(None, confirmed)
    account_scope = AccountScope("paper", "policy-account-b", "demo")
    resolved = resolver.resolve_confirmed(
        "account-policy-a-v1",
        "policy-account-a",
        scope=_owner_scope("policy-account-a"),
    )
    gate = DurableRiskGate(str(tmp_path / "wrong-account-risk.sqlite3"), resolved.risk_policy)
    resources, _ = _resource_set(
        gate=gate,
        target=_target(account_scope),
        scope=WireScope(_DOMAIN, account_ref="policy-account-b", strategy_ref=_STRATEGY),
        resolver=resolver,
        policy_scope=_owner_scope("policy-account-a"),
        account_scope=account_scope,
    )
    try:
        with pytest.raises(ServiceComponentError) as mismatch:
            _bind_confirmed_account_policy(resources, "account-policy-a-v1")
        assert mismatch.value.code == "ACCOUNT_POLICY_NOT_EFFECTIVE"
    finally:
        resources.close()
        confirmed.close()

def test_manual_account_policy_accepts_only_its_exact_single_target_scope(tmp_path):
    account_ref = "policy-manual-account"
    policy_ref = "manual-account-policy-v1"
    manual_book_ref = "manual-book-a"
    policy_scope = WireScope(
        _DOMAIN, account_ref=account_ref, manual_book_ref=manual_book_ref
    )
    confirmed = ConfirmedPolicyStore(str(tmp_path / "manual-confirmed.sqlite3"))
    receipt, content = _confirmed_receipt(account_ref, policy_ref, "25")
    receipt["owner_scope"]["manual_book_ref"] = manual_book_ref
    confirmed.confirm_document(receipt, content)
    resolver = PolicyCatalogAccountAdmissionResolver(None, confirmed)
    account_scope = AccountScope("paper", account_ref, "demo")
    resolved = resolver.resolve_confirmed(
        policy_ref, account_ref, scope=policy_scope
    )
    gate = DurableRiskGate(
        str(tmp_path / "manual-single-risk.sqlite3"), resolved.risk_policy
    )
    resources, _ = _resource_set(
        gate=gate,
        target=_target(account_scope),
        scope=policy_scope,
        resolver=resolver,
        policy_scope=policy_scope,
        account_scope=account_scope,
    )
    try:
        bindings, ports = _bind_confirmed_account_policy(resources, policy_ref)
        assert ports == frozenset({"service.order_entry"})
        assert len(bindings) == 1
        assert bindings[0].scope == policy_scope
        assert gate.bind_policy(account_scope).policy_fingerprint == (
            resolved.risk_policy.fingerprint
        )
    finally:
        resources.close()
        gate.close()
        confirmed.close()


def test_manual_account_policy_rejects_an_additional_manual_target_without_rebinding(
    tmp_path,
):
    account_ref = "policy-manual-account"
    policy_ref = "manual-account-policy-v1"
    policy_scope = WireScope(
        _DOMAIN, account_ref=account_ref, manual_book_ref="manual-book-a"
    )
    confirmed = ConfirmedPolicyStore(str(tmp_path / "manual-multi-confirmed.sqlite3"))
    receipt, content = _confirmed_receipt(account_ref, policy_ref, "25")
    receipt["owner_scope"]["manual_book_ref"] = "manual-book-a"
    confirmed.confirm_document(receipt, content)
    resolver = PolicyCatalogAccountAdmissionResolver(None, confirmed)
    account_scope = AccountScope("paper", account_ref, "demo")
    initial_policy = RiskPolicy(
        policy_id=policy_ref,
        max_increase_notional=Decimal("5"),
        max_increase_count=2,
        notional_unit="USD",
    )
    gate = DurableRiskGate(
        str(tmp_path / "manual-multi-risk.sqlite3"), initial_policy
    )
    scope_a = policy_scope
    scope_b = WireScope(
        _DOMAIN, account_ref=account_ref, manual_book_ref="manual-book-b"
    )
    resources = ComponentResources.borrowed(
        {
            "gate": gate,
            "targets": {
                scope_a: _target(account_scope),
                scope_b: _target(account_scope),
            },
            "account_policy_admission_resolver": resolver,
            "account_policy_scope": policy_scope,
        },
        ready=lambda: True,
    )
    try:
        with pytest.raises(ServiceComponentError) as mismatch:
            _bind_confirmed_account_policy(resources, policy_ref)
        assert mismatch.value.code == "ACCOUNT_POLICY_SCOPE_MISMATCH"
        assert gate.bind_policy(account_scope).policy_fingerprint == (
            initial_policy.fingerprint
        )
    finally:
        resources.close()
        gate.close()
        confirmed.close()

def test_account_owner_first_activation_binds_and_persists_exact_published_policy(
    tmp_path,
):
    catalog = PolicyCatalogStore(
        str(tmp_path / "first-activation-catalog.sqlite3"),
        trusted_approver_principals=("policy-startup-checker",),
    )
    confirmed = ConfirmedPolicyStore(
        str(tmp_path / "first-activation-confirmed.sqlite3")
    )
    service = PolicyCatalogService(catalog, confirmation_store=confirmed)
    account_ref = "policy-account-a"
    policy_ref = "account-policy-a-v2"
    policy_scope = _owner_scope(account_ref)
    _receipt, content = _confirmed_receipt(account_ref, policy_ref, "15")
    gate = None
    resources = None
    service_closed = False
    catalog_closed = False
    try:
        assert service.handle(
            "policy.draft",
            principal_ref=_PRINCIPAL,
            scope=policy_scope,
            arguments={"policy_ref": policy_ref, "content": content},
        )["ok"] is True
        assert service.handle(
            "policy.approve",
            principal_ref="policy-startup-checker",
            scope=policy_scope,
            arguments={"policy_ref": policy_ref},
        )["ok"] is True
        assert service.handle(
            "policy.publish",
            principal_ref=_PRINCIPAL,
            scope=policy_scope,
            arguments={"policy_ref": policy_ref},
        )["ok"] is True

        resolver = PolicyCatalogAccountAdmissionResolver(catalog, confirmed)
        published = resolver.resolve_published(
            policy_ref, account_ref, scope=policy_scope
        )
        account_scope = AccountScope("paper", account_ref, "demo")
        gate = DurableRiskGate(
            str(tmp_path / "first-activation-risk.sqlite3"), published.risk_policy
        )
        wire_scope = WireScope(_DOMAIN, account_ref=account_ref, strategy_ref=_STRATEGY)
        resources, _admission = _resource_set(
            gate=gate,
            target=_target(account_scope),
            scope=wire_scope,
            resolver=resolver,
            policy_scope=policy_scope,
            account_scope=account_scope,
        )

        bindings, required_ports = _bind_confirmed_account_policy(resources, policy_ref)
        assert required_ports == frozenset({"service.order_entry"})
        assert len(bindings) == 1
        assert bindings[0].policy_ref == policy_ref
        assert bindings[0].policy_version == published.risk_policy.fingerprint
        confirmed_row = confirmed.resolve_document(policy_ref)
        assert confirmed_row["policy_version"] == published.policy_version
        assert confirmed_row["owner_principal"] == _PRINCIPAL
        assert confirmed_row["content"] == content
        assert gate.bind_policy(account_scope).policy_fingerprint == (
            published.risk_policy.fingerprint
        )

        # Normal restart stays on the durable exact document when the catalog
        # and its mutable publication pointer are unavailable.
        resources.close()
        resources = None
        service.close()
        service_closed = True
        catalog.close()
        catalog_closed = True
        offline = PolicyCatalogAccountAdmissionResolver(None, confirmed)
        restarted, _ = _resource_set(
            gate=gate,
            target=_target(account_scope),
            scope=wire_scope,
            resolver=offline,
            policy_scope=policy_scope,
            account_scope=account_scope,
        )
        try:
            restarted_bindings, _ = _bind_confirmed_account_policy(
                restarted, policy_ref
            )
            assert restarted_bindings[0].policy_version == bindings[0].policy_version
            assert gate.bind_policy(account_scope).policy_fingerprint == (
                published.risk_policy.fingerprint
            )
        finally:
            restarted.close()
    finally:
        if resources is not None:
            resources.close()
        if gate is not None:
            gate.close()
        if not service_closed:
            service.close()
        if not catalog_closed:
            catalog.close()
        confirmed.close()

