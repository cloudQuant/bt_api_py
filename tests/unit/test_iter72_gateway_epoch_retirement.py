"""Exact, opt-in acknowledgement of Gateway sessions retired by one restart."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.unit.test_service_registry import (
    _business,
    _identity,
    _make_registry,
    _next_request,
    _register,
    _strategy_scope,
)


def _registry_with_retired_ack(
    path: Path,
    *,
    enable_gateway_retired_session_ack: bool | None = True,
) -> ServiceRegistry:
    identity = _identity()
    operations = frozenset({"account.read", "orders.submit", "orders.cancel"})
    capability = ServiceCapability(
        "account-instance",
        "service.account_read",
        operations,
        frozenset({"orders.submit", "orders.cancel"}),
    )
    resource = ResourceGrant(
        "account-instance",
        "service.account_read",
        "account-grant-a",
        _strategy_scope(run_id=None),
        frozenset({"account.read", "orders.submit"}),
    )
    kwargs: dict[str, Any] = {}
    if enable_gateway_retired_session_ack is not None:
        kwargs["enable_gateway_retired_session_ack"] = enable_gateway_retired_session_ack
    return ServiceRegistry(
        path,
        service_id="account-service-a",
        capabilities=(capability,),
        grants={"curve-a": (PrincipalGrant(identity, (resource,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        lease_seconds=10,
        clock=lambda: 100.0,
        **kwargs,
    )


def _unregister(
    registry: ServiceRegistry,
    registration: dict[str, Any],
    *,
    principal: str = "curve-a",
    identity=None,
    scope=None,
    registration_id: str | None = None,
    service_epoch: str | None = None,
) -> dict[str, Any]:
    payload = {
        "registration_id": registration_id or registration["registration_id"],
        "service_epoch": service_epoch or registration["service_epoch"],
    }
    response = registry.handle(
        principal,
        _next_request(
            "service.unregister",
            payload,
            identity=_identity() if identity is None else identity,
            scope=_strategy_scope() if scope is None else scope,
        ),
    )
    return dict(response)


def _success_receipt(registration: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "code": "OK",
        "result": {
            "unregistered": True,
            "registration_id": registration["registration_id"],
        },
    }


def test_opt_in_ack_is_exact_and_does_not_revive_old_business_registration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "retired-ack.sqlite"
    previous = _make_registry(path)
    old_registration = _register(previous)["result"]
    old_epoch = previous.service_epoch
    previous.close()

    registry = _registry_with_retired_ack(path)
    business_calls: list[tuple[object, dict[str, Any]]] = []
    registry.register_handler(
        "account-instance",
        "orders.submit",
        lambda context, arguments: business_calls.append((context, dict(arguments)))
        or {"accepted": True},
    )
    try:
        assert registry.service_epoch != old_epoch
        assert registry.active_registration_ids(instance_id="account-instance") == frozenset()

        stale_before_ack = _business(registry, old_registration)
        assert stale_before_ack["ok"] is False
        assert stale_before_ack["code"] == "STALE_EPOCH"
        assert business_calls == []

        expected = _success_receipt(old_registration)
        assert _unregister(registry, old_registration) == expected
        assert registry.active_registration_ids(instance_id="account-instance") == frozenset()

        # The old business lease stays stale, and the acknowledgement itself
        # does not register or dispatch anything. Exact repeats are idempotent.
        stale_after_ack = _business(registry, old_registration)
        assert stale_after_ack["ok"] is False
        assert stale_after_ack["code"] == "STALE_EPOCH"
        assert business_calls == []
        assert registry.active_registration_ids(instance_id="account-instance") == frozenset()
        assert _unregister(registry, old_registration) == expected
    finally:
        registry.close()


@pytest.mark.parametrize(
    ("mismatch", "expected_code"),
    [
        ("registration_id", "STALE_EPOCH"),
        ("service_epoch", "STALE_EPOCH"),
        ("principal", "STALE_EPOCH"),
        ("identity", "DENIED"),
        ("scope", "SCOPE_DENIED"),
    ],
)
def test_retired_ack_rejects_any_registration_epoch_principal_identity_or_scope_mismatch(
    tmp_path: Path,
    mismatch: str,
    expected_code: str,
) -> None:
    path = tmp_path / f"mismatch-{mismatch}.sqlite"
    previous = _make_registry(path)
    old_registration = _register(previous)["result"]
    previous.close()

    registry = _registry_with_retired_ack(path)
    try:
        options: dict[str, Any] = {}
        if mismatch == "registration_id":
            options["registration_id"] = f"unknown-{old_registration['registration_id']}"
        elif mismatch == "service_epoch":
            options["service_epoch"] = f"not-{old_registration['service_epoch']}"
        elif mismatch == "principal":
            options["principal"] = "curve-foreign"
        elif mismatch == "identity":
            options["identity"] = replace(_identity(), actor_ref="actor-foreign")
        elif mismatch == "scope":
            options["scope"] = replace(_strategy_scope(), run_id="run-foreign")
        else:  # pragma: no cover - parametrization owns this set
            raise AssertionError(f"unhandled mismatch: {mismatch}")

        rejected = _unregister(registry, old_registration, **options)
        assert rejected["ok"] is False
        assert rejected["code"] == expected_code
        assert registry.active_registration_ids(instance_id="account-instance") == frozenset()

        # A failed mismatch attempt neither consumes nor broadens the exact
        # predecessor row; its original authenticated tuple still succeeds.
        assert _unregister(registry, old_registration) == _success_receipt(old_registration)
        assert registry.active_registration_ids(instance_id="account-instance") == frozenset()
    finally:
        registry.close()


def test_retired_ack_is_disabled_by_default_for_same_persisted_registry(tmp_path: Path) -> None:
    path = tmp_path / "default-disabled.sqlite"
    previous = _make_registry(path)
    old_registration = _register(previous)["result"]
    previous.close()

    # _make_registry omits the opt-in entirely: default construction must not
    # preserve or acknowledge the previous process epoch.
    registry = _make_registry(path)
    try:
        rejected = _unregister(registry, old_registration)
        assert rejected == {"ok": False, "code": "STALE_EPOCH", "result": {}}
        assert registry.active_registration_ids(instance_id="account-instance") == frozenset()
        assert _business(registry, old_registration)["code"] == "STALE_EPOCH"
    finally:
        registry.close()


def test_retired_ack_from_another_sqlite_cannot_authorize_this_registry(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite"
    source = _make_registry(source_path)
    source_registration = _register(source)["result"]
    source.close()

    foreign_path = tmp_path / "foreign.sqlite"
    foreign_previous = _make_registry(foreign_path)
    foreign_registration = _register(foreign_previous)["result"]
    foreign_previous.close()
    assert foreign_registration["registration_id"] != source_registration["registration_id"]

    foreign = _registry_with_retired_ack(foreign_path)
    try:
        rejected = _unregister(foreign, source_registration)
        assert rejected == {"ok": False, "code": "STALE_EPOCH", "result": {}}
        assert foreign.active_registration_ids(instance_id="account-instance") == frozenset()

        # The same protected Registry still accepts its own exact prior row.
        assert _unregister(foreign, foreign_registration) == _success_receipt(
            foreign_registration
        )
        assert foreign.active_registration_ids(instance_id="account-instance") == frozenset()
    finally:
        foreign.close()


def test_opt_in_ack_covers_only_the_immediately_preceding_persisted_epoch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "one-predecessor.sqlite"
    first = _make_registry(path)
    first_registration = _register(first)["result"]
    first.close()

    second = _registry_with_retired_ack(path)
    assert _unregister(second, first_registration) == _success_receipt(first_registration)
    second_registration = _register(
        second,
        key="registration-epoch-two",
        client_session_id="client-session-epoch-two",
    )["result"]
    second.close()

    third = _registry_with_retired_ack(path)
    try:
        too_old = _unregister(third, first_registration)
        assert too_old == {"ok": False, "code": "STALE_EPOCH", "result": {}}
        assert _unregister(third, second_registration) == _success_receipt(second_registration)
        assert third.active_registration_ids(instance_id="account-instance") == frozenset()
    finally:
        third.close()
