"""Registry-backed direct classification; no Gateway v2 write is enabled."""

import copy
import gc
import os
import pickle
import threading
import weakref
from dataclasses import replace
from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.risk_v2_binding import RiskV2BindingError, _install_direct_issuer
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_risk_v2_direct_origin import (
    GatewayRiskV2DirectAdmission,
    GatewayRiskV2DirectAuthority,
    GatewayRiskV2DirectOriginError,
    risk_v2_direct_admission_scope,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceDispatchAdmissionError,
    ServiceRegistry,
)
from tests.unit.test_service_registry import _next_request, _register


@pytest.fixture
def harness(tmp_path, request):
    guard_dir = tmp_path / "guards"
    guard_dir.mkdir()
    holder = {"clock": 100.0}

    def registry_clock():
        callback = holder.get("clock_callback")
        if callback is not None:
            callback()
        return holder["clock"]

    def factory():
        guard = LocalAccountSessionGuard(guard_dir, "acct", "owner")
        generation = guard.acquire()
        holder["guard"] = guard
        journal = GatewayDirectCommandJournal.create(
            tmp_path / "gateway.db", "acct", generation=generation
        )
        dispatcher = GatewayDirectDispatcher(
            journal, "CTP", guard, lambda *_: None, lambda _: ("anchor",), lambda _: True
        )
        holder["journal"] = journal
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

    owner = GatewayDirectOwner(
        factory, queue_size=4, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    assert owner.start(2)
    actor, delegate = getattr(request, "param", ("s1", None))
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "p", actor, delegate)
    scope = WireScope("domain", account_ref="acct", strategy_ref="s1", run_id="run")
    operations = frozenset({"direct.reserve"})
    registry = ServiceRegistry(
        ":memory:",
        service_id="gateway-service",
        capabilities=(
            ServiceCapability("gateway", "service.direct_write", operations, operations),
        ),
        grants={
            "p": (
                PrincipalGrant(
                    identity,
                    (
                        ResourceGrant(
                            "gateway",
                            "service.direct_write",
                            "scope",
                            WireScope("domain", account_ref="acct", strategy_ref="s1"),
                            operations,
                        ),
                    ),
                ),
            )
        },
        policy_version="p",
        requirements_version="r",
        clock=registry_clock,
    )
    authority = GatewayRiskV2DirectAuthority(
        registry=registry, service_id="gateway-service", instance_id="gateway", owner=owner
    )
    holder["authority"] = authority

    def handler(context, arguments):
        if holder.get("capture"):
            holder.setdefault("captures", []).append((context, arguments))
            return {"captured": True}

        def on_owner():
            with risk_v2_direct_admission_scope(
                context=context,
                arguments=arguments,
                admission=context.dispatch_admission,
                journal=holder["journal"],
                owner=owner,
                authority=authority,
            ) as origin:
                holder["origin"] = origin
                holder["binding"] = origin.binding
                callback = holder.get("callback", lambda _origin: {})
                result = callback(origin)
            after_scope = holder.get("after_scope")
            return result if after_scope is None else after_scope(context, arguments, result)

        try:
            return owner.submit_service_call(on_owner).result(timeout_seconds=2)
        finally:
            context.dispatch_admission.close()

    registry.register_handler("gateway", "direct.reserve", handler)
    registered = _register(
        registry,
        principal="p",
        identity=identity,
        scope=scope,
        instance_id="gateway",
        port="service.direct_write",
        scope_ref="scope",
        operations=tuple(operations),
    )
    assert registered["ok"], registered
    session = registered["result"]

    def reserve(key="first"):
        payload = OrderRequest(
            "rb",
            Side.BUY,
            OrderType.LIMIT,
            Decimal("1"),
            "acct",
            "client",
            price=Decimal("10"),
            idempotency_key=key,
            exchange_id="SHFE",
            offset="open",
            quantity_unit="lots",
        ).to_dict()
        return registry.handle(
            "p",
            _next_request(
                "direct.reserve",
                {
                    "registration_id": session["registration_id"],
                    "service_epoch": session["service_epoch"],
                    "instance_id": "gateway",
                    "arguments": {"request_key": key, "payload": payload, "ttl_seconds": 10},
                },
                identity=identity,
                scope=scope,
            ),
        )

    try:
        yield holder, registry, owner, reserve
    finally:
        registry.close()
        assert owner.close(2)


def test_real_registry_scope_binds_all_claims_and_expires_on_exit(harness):
    holder, _, owner, reserve = harness

    def check(origin):
        binding = origin.binding
        assert holder["authority"].validate(origin, binding)
        assert (
            binding.account_scope,
            binding.strategy_scope,
            binding.execution_domain_ref,
            binding.run_id,
            binding.principal_ref,
            binding.actor_ref,
            binding.request_key,
        ) == ("acct", "s1", "domain", "run", "p", "s1", "first")
        assert (binding.journal_id, binding.owner_generation) == (
            holder["journal"].journal_id,
            holder["journal"].generation,
        )
        for field, value in (
            ("account_scope", "other"),
            ("journal_id", "other"),
            ("owner_generation", "other"),
            ("execution_domain_ref", "other"),
            ("run_id", "other"),
            ("principal_ref", "other"),
            ("registration_id", "other"),
            ("service_epoch", "other"),
            ("request_key", "other"),
            ("payload_digest", "f" * 64),
        ):
            assert not holder["authority"].validate(origin, replace(binding, **{field: value}))
        assert not holder["authority"].validate(
            origin, replace(binding, strategy_scope="other", actor_ref="other")
        )
        assert "acct" not in repr(origin) and "first" not in repr(origin)
        for duplicate in (
            lambda: copy.copy(origin),
            lambda: copy.deepcopy(origin),
            lambda: pickle.dumps(origin),
        ):
            with pytest.raises(TypeError):
                duplicate()
        return {"valid": True}

    holder["callback"] = check
    response = reserve()
    assert response["ok"] and response["result"] == {"valid": True}
    origin, binding = holder["origin"], holder["binding"]
    assert (
        owner.submit_service_call(lambda: holder["authority"].validate(origin, binding)).result(
            timeout_seconds=2
        )
        is False
    )


def test_recheck_fails_closed_after_lease_expires_inside_scope(harness):
    holder, _, _, reserve = harness

    def expire(origin):
        binding = origin.binding
        holder["clock"] = 10_000.0
        assert holder["authority"].validate(origin, binding) is False
        with pytest.raises(GatewayRiskV2DirectOriginError, match="DIRECT_ORIGIN_UNAVAILABLE"):
            _ = origin.binding
        return {"expired": True}

    holder["callback"] = expire
    result = reserve()
    assert result["ok"] and result["result"] == {"expired": True}


def test_recheck_fails_closed_after_local_principal_revocation(harness):
    holder, registry, _, reserve = harness

    def revoke(origin):
        binding = origin.binding
        assert registry.revoke_principal("p") is True
        assert holder["authority"].validate(origin, binding) is False
        return {"revoked": True}

    holder["callback"] = revoke
    result = reserve()
    assert result["ok"] and result["result"] == {"revoked": True}


def test_copied_wire_arguments_and_wrong_thread_cannot_issue(harness):
    holder, _, _, reserve = harness

    def check(origin):
        context, arguments, admission = origin._context, origin._arguments, origin._admission
        with (
            pytest.raises(GatewayRiskV2DirectOriginError, match="DIRECT_ORIGIN_UNAVAILABLE"),
            risk_v2_direct_admission_scope(
                context=context,
                arguments=dict(arguments),
                admission=admission,
                journal=holder["journal"],
                owner=origin._owner,
                authority=holder["authority"],
            ),
        ):
            pass
        seen = []
        thread = threading.Thread(
            target=lambda: seen.append(holder["authority"].validate(origin, origin._binding))
        )
        thread.start()
        thread.join(2)
        assert seen == [False]
        return {"checked": True}

    holder["callback"] = check
    result = reserve()
    assert result["ok"] and result["result"] == {"checked": True}


def test_journal_generation_change_invalidates_live_source(harness):
    holder, _, _, reserve = harness

    def change(origin):
        binding = origin.binding
        journal = holder["journal"]
        original = journal._generation
        try:
            journal._generation = "changed-generation"
            assert holder["authority"].validate(origin, binding) is False
        finally:
            journal._generation = original
        return {"checked": True}

    holder["callback"] = change
    result = reserve()
    assert result["ok"] and result["result"] == {"checked": True}


def test_internal_binding_and_sources_cannot_substitute_authenticated_facts(harness):
    holder, _, _, reserve = harness

    def change(origin):
        binding = origin.binding
        changes = (
            {"account_scope": "other"},
            {"journal_id": "other"},
            {"owner_generation": "other"},
            {"strategy_scope": "other", "actor_ref": "other"},
            {"execution_domain_ref": "other"},
            {"run_id": "other"},
            {"principal_ref": "other"},
            {"registration_id": "other"},
            {"service_epoch": "other"},
            {"request_key": "other"},
            {"payload_digest": "f" * 64},
        )
        with pytest.raises(AttributeError):
            origin._binding = replace(binding, request_key="other")
        for fields in changes:
            forged = replace(binding, **fields)
            object.__setattr__(origin, "_binding", forged)
            try:
                assert not holder["authority"].validate(origin, forged)
            finally:
                object.__setattr__(origin, "_binding", binding)

        context = origin._context
        substitutions = (
            ("_context", replace(context, registration_id="other")),
            ("_context", replace(context, principal_ref="other")),
            ("_context", replace(context, scope=replace(context.scope, run_id="other"))),
            ("_arguments", dict(origin._arguments)),
            ("_journal", None),
            ("_owner", None),
            ("_authority", None),
        )
        for field, value in substitutions:
            original = getattr(origin, field)
            object.__setattr__(origin, field, value)
            try:
                assert not holder["authority"].validate(origin, binding)
            finally:
                object.__setattr__(origin, field, original)
        assert holder["authority"].validate(origin, binding)
        return {"checked": True}

    holder["callback"] = change
    result = reserve()
    assert result["ok"] and result["result"] == {"checked": True}


def test_reentrant_validator_fails_closed_and_outer_check_survives(harness):
    holder, _, _, reserve = harness

    def change(origin):
        binding = origin.binding
        nested = []

        def reenter():
            nested.append(holder["authority"].validate(origin, binding))

        holder["clock_callback"] = reenter
        try:
            assert holder["authority"].validate(origin, binding)
            assert nested and all(value is False for value in nested)
        finally:
            del holder["clock_callback"]
        assert holder["authority"].validate(origin, binding)
        return {"checked": True}

    holder["callback"] = change
    result = reserve()
    assert result["ok"] and result["result"] == {"checked": True}


def test_registry_scope_is_entered_once_and_rechecked_inside_handler(harness, monkeypatch):
    holder, _, _, reserve = harness

    def check(origin):
        assert origin._admission.matches_scope_recheck(origin._recheck)
        assert not origin._admission.matches_scope_recheck(lambda: None)
        seen = []
        thread = threading.Thread(
            target=lambda: seen.append(origin._admission.matches_scope_recheck(origin._recheck))
        )
        thread.start()
        thread.join(2)
        assert seen == [False]
        pid = os.getpid()
        with monkeypatch.context() as patch:
            patch.setattr(os, "getpid", lambda: pid + 1)
            assert not origin._admission.matches_scope_recheck(origin._recheck)
        with (
            pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_BUSY"),
            origin._admission.scope(),
        ):
            pass
        assert holder["authority"].validate(origin, origin.binding)
        return {"checked": True}

    holder["callback"] = check
    result = reserve()
    assert result["ok"] and result["result"] == {"checked": True}
    assert not holder["origin"]._admission.matches_scope_recheck(holder["origin"]._recheck)
    assert holder["origin"]._admission._active_recheck is None


def test_real_admissions_reject_cross_scope_source_substitution(harness):
    holder, _, owner, reserve = harness
    holder["capture"] = True
    assert reserve("a")["ok"]
    assert reserve("b")["ok"]
    (context_a, args_a), (context_b, args_b) = holder["captures"]
    admission_a, admission_b = context_a.dispatch_admission, context_b.dispatch_admission
    assert admission_a is not admission_b

    def check():
        authority, journal = holder["authority"], holder["journal"]
        with risk_v2_direct_admission_scope(
            context=context_b,
            arguments=args_b,
            admission=admission_b,
            journal=journal,
            owner=owner,
            authority=authority,
        ) as source_b:
            binding_b = source_b.binding
            recheck_b = source_b._recheck
        assert not admission_b.matches_scope_recheck(recheck_b)

        with risk_v2_direct_admission_scope(
            context=context_a,
            arguments=args_a,
            admission=admission_a,
            journal=journal,
            owner=owner,
            authority=authority,
        ) as source_a:
            assert authority.validate(source_a, source_a.binding)
            originals = {
                field: getattr(source_a, field)
                for field in ("_context", "_arguments", "_admission", "_binding")
            }
            for field, value in (
                ("_context", context_b),
                ("_arguments", args_b),
                ("_admission", admission_b),
                ("_binding", binding_b),
            ):
                object.__setattr__(source_a, field, value)
            try:
                assert not authority.validate(source_a, binding_b)
                with pytest.raises(GatewayRiskV2DirectOriginError):
                    authority.admission_identity(source_a)
            finally:
                for field, value in originals.items():
                    object.__setattr__(source_a, field, value)
            assert authority.validate(source_a, source_a.binding)
        return True

    try:
        assert owner.submit_service_call(check).result(timeout_seconds=2)
    finally:
        admission_a.close()
        admission_b.close()


def test_registry_recheck_identity_clears_after_exception_and_close(harness):
    holder, _, owner, reserve = harness
    holder["capture"] = True
    assert reserve("one")["ok"]
    admission = holder["captures"][0][0].dispatch_admission

    def check():
        recheck = None
        with pytest.raises(RuntimeError, match="abort"), admission.scope() as current:
            recheck = current
            assert admission.matches_scope_recheck(recheck)
            raise RuntimeError("abort")
        assert not admission.matches_scope_recheck(recheck)
        assert admission._active_recheck is None
        with admission.scope() as current:
            assert current is not recheck
            assert not admission.matches_scope_recheck(recheck)
            assert admission.matches_scope_recheck(current)
            admission.close()
            assert not admission.matches_scope_recheck(current)
        assert not admission.matches_scope_recheck(current)
        assert admission._active_recheck is None
        return True

    assert owner.submit_service_call(check).result(timeout_seconds=2)


def test_other_real_registry_self_grant_cannot_use_installed_authority(harness):
    holder, registry, owner, reserve = harness

    def install(origin):
        issuer = _install_direct_issuer(
            holder["journal"],
            holder["guard"],
            GatewayRiskV2DirectAdmission,
            holder["authority"].validate,
            admission_identity=holder["authority"].admission_identity,
        )
        binding = origin.binding
        issuer.issue(origin, binding)
        issuer.recheck(origin, binding)
        with pytest.raises(RiskV2BindingError):
            issuer.recheck(origin, replace(binding, request_key="other"))
        holder["issuer"] = issuer
        return {"installed": True}

    holder["callback"] = install
    response = reserve()
    assert response["ok"] and response["result"] == {"installed": True}
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "p", "s1", None)
    scope = WireScope("domain", account_ref="acct", strategy_ref="s1", run_id="run")
    operations = frozenset({"direct.reserve"})
    foreign = ServiceRegistry(
        ":memory:",
        service_id=registry.service_id,
        capabilities=(
            ServiceCapability("gateway", "service.direct_write", operations, operations),
        ),
        grants={
            "p": (
                PrincipalGrant(
                    identity,
                    (
                        ResourceGrant(
                            "gateway",
                            "service.direct_write",
                            "scope",
                            WireScope("domain", account_ref="acct", strategy_ref="s1"),
                            operations,
                        ),
                    ),
                ),
            )
        },
        policy_version="p",
        requirements_version="r",
        clock=lambda: holder["clock"],
    )

    def handler(context, arguments):
        def on_owner():
            foreign_authority = GatewayRiskV2DirectAuthority(
                registry=foreign, service_id=registry.service_id, instance_id="gateway", owner=owner
            )
            with risk_v2_direct_admission_scope(
                context=context,
                arguments=arguments,
                admission=context.dispatch_admission,
                journal=holder["journal"],
                owner=owner,
                authority=foreign_authority,
            ) as valid:
                assert foreign_authority.validate(valid, valid.binding)
                assert not holder["authority"].validate(valid, valid.binding)
                with pytest.raises(RiskV2BindingError):
                    holder["issuer"].issue(valid, valid.binding)
                binding = valid.binding
                object.__setattr__(valid, "_authority", holder["authority"])
                try:
                    with pytest.raises(RiskV2BindingError):
                        holder["issuer"].issue(valid, binding)
                finally:
                    object.__setattr__(valid, "_authority", foreign_authority)
            with (
                pytest.raises(GatewayRiskV2DirectOriginError, match="DIRECT_ORIGIN_UNAVAILABLE"),
                risk_v2_direct_admission_scope(
                    context=context,
                    arguments=arguments,
                    admission=context.dispatch_admission,
                    journal=holder["journal"],
                    owner=owner,
                    authority=holder["authority"],
                ),
            ):
                raise AssertionError("foreign registry was accepted")
            return {"rejected": True}

        try:
            return owner.submit_service_call(on_owner).result(timeout_seconds=2)
        finally:
            context.dispatch_admission.close()

    foreign.register_handler("gateway", "direct.reserve", handler)
    try:
        registered = _register(
            foreign,
            principal="p",
            identity=identity,
            scope=scope,
            instance_id="gateway",
            port="service.direct_write",
            scope_ref="scope",
            operations=tuple(operations),
        )
        assert registered["ok"]
        session = registered["result"]
        payload = OrderRequest(
            "rb",
            Side.BUY,
            OrderType.LIMIT,
            Decimal("1"),
            "acct",
            "client",
            price=Decimal("10"),
            idempotency_key="x",
            exchange_id="SHFE",
            offset="open",
            quantity_unit="lots",
        ).to_dict()
        response = foreign.handle(
            "p",
            _next_request(
                "direct.reserve",
                {
                    "registration_id": session["registration_id"],
                    "service_epoch": session["service_epoch"],
                    "instance_id": "gateway",
                    "arguments": {"request_key": "x", "payload": payload, "ttl_seconds": 1},
                },
                identity=identity,
                scope=scope,
            ),
        )
        assert response["ok"] and response["result"] == {"rejected": True}
    finally:
        foreign.close()


def test_authority_close_revokes_live_admission(harness):
    holder, _, _, reserve = harness

    def close(origin):
        binding = origin.binding
        issuer = _install_direct_issuer(
            holder["journal"],
            holder["guard"],
            GatewayRiskV2DirectAdmission,
            holder["authority"].validate,
            admission_identity=holder["authority"].admission_identity,
        )
        issuer.issue(origin, binding)
        holder["authority"].close()
        assert not holder["authority"].validate(origin, binding)
        with pytest.raises(RiskV2BindingError):
            issuer.recheck(origin, binding)
        return {"closed": True}

    holder["callback"] = close
    result = reserve()
    assert result["ok"] and result["result"] == {"closed": True}


def test_closed_admission_can_be_weakly_referenced_and_collected(harness):
    holder, _, owner, reserve = harness

    def check(origin):
        holder["origin_ref"] = weakref.ref(origin)
        assert holder["origin_ref"]() is origin
        return {"checked": True}

    holder["callback"] = check
    result = reserve()
    assert result["ok"] and result["result"] == {"checked": True}
    origin = holder.pop("origin")
    binding = holder["binding"]
    assert not owner.submit_service_call(
        lambda: holder["authority"].validate(holder["origin_ref"](), binding)
    ).result(timeout_seconds=2)
    del origin
    gc.collect()
    assert holder["origin_ref"]() is None


def test_gateway_issuer_retains_only_live_admissions_without_reissuing(harness):
    holder, _, owner, reserve = harness
    journal = holder["journal"]
    original_limit = journal._max_records
    journal._max_records = 2

    def issue(origin):
        issuer = holder.get("issuer")
        if issuer is None:
            issuer = _install_direct_issuer(
                journal,
                holder["guard"],
                GatewayRiskV2DirectAdmission,
                holder["authority"].validate,
                admission_identity=holder["authority"].admission_identity,
            )
            holder["issuer"] = issuer
        binding = origin.binding
        issuer.issue(origin, binding)
        with pytest.raises(RiskV2BindingError):
            issuer.issue(origin, binding)
        return {"issued": True}

    holder["callback"] = issue
    try:
        for index in range(6):
            result = reserve(key=f"request-{index}")
            assert result["ok"] and result["result"] == {"issued": True}
            origin = holder.pop("origin")
            binding = holder["binding"]
            ref = weakref.ref(origin)

            def reject_closed(ref=ref, binding=binding):
                with pytest.raises(RiskV2BindingError):
                    holder["issuer"].issue(ref(), binding)

            owner.submit_service_call(reject_closed).result(timeout_seconds=2)
            del origin
            gc.collect()
            assert ref() is None
    finally:
        journal._max_records = original_limit


def test_gateway_issuer_deduplicates_registry_admission_across_serial_scopes(harness):
    holder, _, owner, reserve = harness

    def first(origin):
        authority = holder["authority"]
        issuer = _install_direct_issuer(
            holder["journal"],
            holder["guard"],
            GatewayRiskV2DirectAdmission,
            authority.validate,
            admission_identity=authority.admission_identity,
        )
        source = authority.admission_identity(origin)
        holder["source_ref"] = weakref.ref(source)
        issuer.issue(origin, origin.binding)
        holder["issuer"] = issuer
        return {"first": True}

    def second(context, arguments, result):
        assert result == {"first": True}
        with pytest.raises(GatewayRiskV2DirectOriginError):
            holder["authority"].admission_identity(holder["origin"])
        with risk_v2_direct_admission_scope(
            context=context,
            arguments=arguments,
            admission=context.dispatch_admission,
            journal=holder["journal"],
            owner=owner,
            authority=holder["authority"],
        ) as origin:
            assert origin is not holder["origin"]
            assert holder["authority"].admission_identity(origin) is holder["source_ref"]()
            with pytest.raises(RiskV2BindingError):
                holder["issuer"].issue(origin, origin.binding)
        return {"second_rejected": True}

    holder["callback"] = first
    holder["after_scope"] = second
    result = reserve()
    assert result["ok"] and result["result"] == {"second_rejected": True}


def test_authority_service_instance_and_lifetime_are_fixed(harness):
    holder, registry, owner, reserve = harness
    with pytest.raises(GatewayRiskV2DirectOriginError, match="DIRECT_ORIGIN_UNAVAILABLE"):
        GatewayRiskV2DirectAuthority(
            registry=registry, service_id="other", instance_id="gateway", owner=owner
        )
    authority = holder["authority"]
    with pytest.raises(AttributeError):
        authority._registry = None
    for duplicate in (
        lambda: copy.copy(authority),
        lambda: copy.deepcopy(authority),
        lambda: pickle.dumps(authority),
    ):
        with pytest.raises(TypeError):
            duplicate()

    def check(origin):
        wrong_instance = GatewayRiskV2DirectAuthority(
            registry=registry, service_id=registry.service_id, instance_id="other", owner=owner
        )
        assert not wrong_instance._matches(origin._admission, owner)
        return {"checked": True}

    holder["callback"] = check
    result = reserve()
    assert result["ok"] and result["result"] == {"checked": True}


def test_forged_uninitialized_admission_is_not_accepted(harness):
    holder, _, _, _ = harness
    forged = object.__new__(GatewayRiskV2DirectAdmission)
    assert holder["authority"].validate(forged, object()) is False


@pytest.mark.parametrize("harness", [("other", None), ("s1", "delegate")], indirect=True)
def test_actor_must_be_strategy_and_delegate_absent(harness):
    holder, _, _, reserve = harness
    result = reserve()
    assert not result["ok"]
    assert "origin" not in holder
