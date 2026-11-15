"""Synthetic single-Registry strategy write-owner tests; not a global fence."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_py.execution_parent_grant import PORT, QUERY, SUBMIT, ExecutionParentGrant
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import (
    _business,
    _identity,
    _next_request,
    _register,
    _strategy_scope,
)


@pytest.fixture
def registry_fixture(tmp_path):
    clock = [100.0]
    identities = tuple(_identity(principal_ref=f"principal-{i}", actor_ref=f"actor-{i}") for i in range(2))
    scopes = tuple(_strategy_scope(account=account, strategy=strategy, run_id=None)
                   for account, strategy in (("account-a", "strategy-a"),
                                             ("account-b", "strategy-a"),
                                             ("account-a", "strategy-b")))
    resources = tuple(ResourceGrant("account-instance", "service.account_read", "account-grant-a",
                                    scope, frozenset({"orders.submit"})) for scope in scopes)
    registry = ServiceRegistry(
        tmp_path / "registry.sqlite", service_id="service-a",
        capabilities=(ServiceCapability("account-instance", "service.account_read",
                                        frozenset({"orders.submit"}), frozenset({"orders.submit"})),),
        grants={identity.principal_ref: (PrincipalGrant(identity, resources),) for identity in identities},
        policy_version="policy-v1", requirements_version="requirements-v1",
        lease_seconds=10, clock=lambda: clock[0],
    )
    writes = []
    registry.register_handler("account-instance", "orders.submit",
                              lambda context, args: writes.append(context) or {"accepted": True})
    try:
        yield registry, clock, identities, writes
    finally:
        registry.close()


def _register_as(registry, identity, scope, key):
    return _register(registry, principal=identity.principal_ref, identity=identity, scope=scope,
                     key=key, client_session_id=f"session-{key}", operations=("orders.submit",))


def _write(registry, identity, scope, registration):
    return _business(registry, registration, principal=identity.principal_ref,
                     identity=identity, scope=scope)


@pytest.mark.parametrize("other_run", ["run-a", "run-b"])
def test_different_principals_cannot_register_same_strategy_while_active(registry_fixture, other_run):
    registry, _, (first, second), writes = registry_fixture
    first_scope = _strategy_scope(run_id="run-a")
    registration = _register_as(registry, first, first_scope, "first")
    assert registration["ok"], registration
    assert _write(registry, first, first_scope, registration["result"])["ok"]
    competing = _register_as(registry, second, _strategy_scope(run_id=other_run), "second")
    assert not competing["ok"] and competing["code"] == "DUPLICATE_ACTIVE_RUN", competing
    assert len(writes) == 1


def test_concurrent_distinct_principals_have_exactly_one_writer(registry_fixture):
    registry, _, identities, writes = registry_fixture
    barrier = Barrier(2)
    scopes = tuple(_strategy_scope(run_id=f"run-{i}") for i in range(2))
    # Construct distinct requests before starting threads, including real principals and keys.
    requests = tuple(_next_request("service.register", {
        "registration_key": f"key-{i}", "client_session_id": f"session-{i}",
        "instance_id": "account-instance", "port": "service.account_read",
        "scope_ref": "account-grant-a", "operations": ("orders.submit",),
    }, identity=identity, scope=scopes[i]) for i, identity in enumerate(identities))

    def compete(index):
        barrier.wait(timeout=5)
        return registry.handle(identities[index].principal_ref, requests[index])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    winners = [i for i, result in enumerate(results) if result["ok"]]
    assert len(winners) == 1, results
    loser = 1 - winners[0]
    assert results[loser]["code"] == "DUPLICATE_ACTIVE_RUN", results
    winner = winners[0]
    assert _write(registry, identities[winner], scopes[winner], results[winner]["result"])["ok"]
    assert len(writes) == 1


@pytest.mark.parametrize("release", ["unregister", "expire"])
def test_released_owner_cannot_write_and_original_run_can_reconnect(registry_fixture, release):
    registry, clock, (first, second), writes = registry_fixture
    scope = _strategy_scope(run_id="run-a")
    response = _register_as(registry, first, scope, "first")
    assert response["ok"], response
    old = response["result"]
    if release == "unregister":
        response = registry.handle(first.principal_ref, _next_request(
            "service.unregister", {"registration_id": old["registration_id"],
                                   "service_epoch": old["service_epoch"]},
            identity=first, scope=scope))
        assert response["ok"], response
    else:
        clock[0] += 10
    stale = _write(registry, first, scope, old)
    assert not stale["ok"] and stale["code"] == "NOT_REGISTERED", stale
    # The original logical run reconnects with a fresh authenticated session.
    reconnect = _register_as(registry, second, scope, "reconnect")
    assert reconnect["ok"], reconnect
    assert reconnect["result"]["registration_id"] != old["registration_id"]
    assert _write(registry, second, scope, reconnect["result"])["ok"]
    stale_again = _write(registry, first, scope, old)
    assert not stale_again["ok"] and stale_again["code"] == "NOT_REGISTERED", stale_again
    assert len(writes) == 1


@pytest.mark.parametrize("other_scope", [
    _strategy_scope(account="account-b", strategy="strategy-a", run_id="run-b"),
    _strategy_scope(account="account-a", strategy="strategy-b", run_id="run-b"),
])
def test_different_account_or_strategy_can_write(registry_fixture, other_scope):
    registry, _, (first, second), writes = registry_fixture
    first_scope = _strategy_scope()
    first_registration = _register_as(registry, first, first_scope, "first")
    second_registration = _register_as(registry, second, other_scope, "second")
    assert first_registration["ok"], first_registration
    assert second_registration["ok"], second_registration
    assert _write(registry, first, first_scope, first_registration["result"])["ok"]
    assert _write(registry, second, other_scope, second_registration["result"])["ok"]
    assert len(writes) == 2


@pytest.mark.parametrize("second_run", ["run-a", "run-b"])
@pytest.mark.parametrize("first_index", [0, 1])
def test_execution_parent_port_rejects_a_second_authorized_writer(tmp_path, second_run, first_index):
    identities = tuple(_identity(principal_ref=f"parent-principal-{i}", actor_ref=f"parent-actor-{i}")
                       for i in range(2))
    scope = _strategy_scope(run_id="run-a")
    template = _strategy_scope(run_id=None)
    operations = frozenset({SUBMIT, QUERY})
    instrument = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")
    resource = ResourceGrant("exec-a", PORT, "scope-a", template, operations)
    parent_grants = tuple(ExecutionParentGrant(
        "exec-a", "scope-a", identity, scope.account_ref, scope.strategy_ref,
        scope.execution_domain_ref, "grant-v1", frozenset({instrument}), operations,
    ) for identity in identities)
    registry = ServiceRegistry(
        tmp_path / "execution-registry.sqlite", service_id="execution-service",
        capabilities=(ServiceCapability("exec-a", PORT, operations, frozenset({SUBMIT})),),
        grants={identity.principal_ref: (PrincipalGrant(identity, (resource,)),) for identity in identities},
        policy_version="policy-v1", requirements_version="requirements-v1",
        lease_seconds=10, clock=lambda: 100.0,
    )
    try:
        registry.configure_execution_parents(grants=parent_grants)

        def register(identity, registration_scope, key):
            return _register(registry, principal=identity.principal_ref, identity=identity,
                             scope=registration_scope, key=key, client_session_id=f"session-{key}",
                             instance_id="exec-a", port=PORT, scope_ref="scope-a", operations=(SUBMIT,))

        first, second = identities[first_index], identities[1 - first_index]
        first_response = register(first, scope, "first")
        assert first_response["ok"], first_response
        denied = register(second, _strategy_scope(run_id=second_run), "second")
        assert not denied["ok"] and denied["code"] == "DUPLICATE_ACTIVE_RUN", denied
        old = first_response["result"]
        released = registry.handle(first.principal_ref, _next_request(
            "service.unregister", {"registration_id": old["registration_id"],
                                   "service_epoch": old["service_epoch"]},
            identity=first, scope=scope))
        assert released["ok"], released
        # The second identity has a real eligible parent grant; the active claim
        # alone prevented it from registering, and the original run can resume.
        resumed = register(second, scope, "resume")
        assert resumed["ok"], resumed
        assert resumed["result"]["registration_id"] != old["registration_id"]
    finally:
        registry.close()
