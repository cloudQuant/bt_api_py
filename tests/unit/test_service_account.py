"""Account-state facade validation and freshness tests using the real registry."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest
from bt_api_base.account_protocol import (
    AccountBalance,
    AccountKey,
    AccountObservation,
    AccountOpenOrder,
    AccountPosition,
    AccountResourceKey,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_account import (
    AccountStateView,
    ServiceAccountClient,
    ServiceAccountError,
)
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import ServiceClientRuntime

_CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "account-client", "strategy-account")
_PEER = WireIdentity(WireRole.SERVICE, "account-service-principal", "account-service")
_ACCOUNT_REF = "paper:account-one"
_ACCOUNT = AccountKey(
    provider="synthetic",
    venue="unit",
    environment="paper",
    account_id="account-one",
)
_OTHER_ACCOUNT = AccountKey(
    provider="synthetic",
    venue="unit",
    environment="paper",
    account_id="account-two",
)
_SPOT = AccountResourceKey("spot", "cash")
_PERPETUAL = AccountResourceKey("perpetual", "cross")
_COVERAGE = (_SPOT, _PERPETUAL)
_OPERATIONS = frozenset({"account.snapshot"})
_SCOPE = WireScope(
    "paper:domain",
    account_ref=_ACCOUNT_REF,
    strategy_ref=_CLIENT.actor_ref,
)


class ManualClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _instrument(product_type: str, instrument_id: str) -> MarketInstrumentKey:
    return MarketInstrumentKey(
        provider="synthetic",
        venue="unit",
        environment="paper",
        product_type=product_type,
        instrument_id=instrument_id,
        native_symbol=instrument_id.upper(),
    )


def _observation(
    *,
    account: AccountKey = _ACCOUNT,
    coverage: tuple[AccountResourceKey, ...] = _COVERAGE,
    balances_complete: bool = True,
    positions_complete: bool = True,
    valuation=None,
) -> AccountObservation:
    observed = "2026-10-14T00:00:00Z"
    balances = tuple(
        AccountBalance(
            resource=resource,
            currency=currency,
            total=total,
            available=available,
            locked=locked,
            observed_at_utc=observed,
        )
        for resource, currency, total, available, locked in (
            (_SPOT, "USD", "100", "95", "5"),
            (_PERPETUAL, "USDT", "250", "200", "50"),
        )
        if resource in coverage
    )
    positions = tuple(
        AccountPosition(
            resource=resource,
            instrument=instrument,
            side="long",
            quantity=quantity,
            qty_unit=unit,
            average_price=price,
            price_currency=currency,
            observed_at_utc=observed,
        )
        for resource, instrument, quantity, unit, price, currency in (
            (_SPOT, _instrument("spot", "asset-a"), "3", "base_units", "10", "USD"),
            (
                _PERPETUAL,
                _instrument("perpetual", "asset-b"),
                "2",
                "contracts",
                "125",
                "USDT",
            ),
        )
        if resource in coverage
    )
    return AccountObservation(
        account_key=account,
        observation_id="observation-1",
        coverage=coverage,
        balances=balances,
        positions=positions,
        valuation=valuation,
        started_at_utc=observed,
        completed_at_utc=observed,
        consistency="multi_query",
        balances_complete=balances_complete,
        positions_complete=positions_complete,
    )


def _open_order() -> AccountOpenOrder:
    observed = "2026-10-14T00:00:00Z"
    return AccountOpenOrder(
        resource=_SPOT,
        instrument=_instrument("spot", "asset-a"),
        order_id="order-1",
        side="buy",
        position_side="net",
        order_type="limit",
        status="open",
        quantity="1",
        filled_quantity="0",
        qty_unit="base_units",
        created_at_utc=observed,
        observed_at_utc=observed,
    )


def _reply(
    observation: AccountObservation | None = None,
    *,
    generation: str = "generation-one",
    status_generation: str | None = None,
    ready: bool = True,
    fresh_for_seconds: float = 60.0,
    cursor: int = 7,
    snapshot_id: str = "snapshot-1",
    schema_version: int | None = None,
    events=(),
    next_cursor: int | None = None,
    latest_cursor: int | None = None,
) -> dict[str, object]:
    observation = observation or _observation()
    if schema_version is None:
        schema_version = 1 if observation.open_orders is None else 2
    return {
        "requested_cursor": None,
        "snapshot": {
            "account_scope": "journal-account-scope",
            "strategy_scope": "journal-account-strategy",
            "snapshot_id": snapshot_id,
            "cursor": cursor,
            "payload": {
                "schema_id": "sdk.account.state",
                "schema_version": schema_version,
                "session_generation": generation,
                "observation": observation.to_dict(),
            },
            "captured_at": 1_791_936_000.0,
        },
        "events": list(events),
        "next_cursor": cursor if next_cursor is None else next_cursor,
        "latest_cursor": cursor if latest_cursor is None else latest_cursor,
        "recovered_from_snapshot": False,
        "snapshot_source": "persisted_journal_baseline",
        "state_status": {
            "session_generation": (generation if status_generation is None else status_generation),
            "ready": ready,
            "fresh_for_seconds": fresh_for_seconds,
        },
    }


class AccountService:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[object, object]] = []

    def snapshot(self, context, arguments):
        self.calls.append((context, arguments))
        return self.response


class LocalControlTransport:
    def __init__(self, registry: ServiceRegistry, clock: ManualClock) -> None:
        self.registry = registry
        self.clock = clock
        self.requests: list[WireEnvelope] = []
        self.response_delay = 0.0
        self.fail_snapshot_after_dispatch = False
        self.closed = False

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        del timeout
        self.requests.append(request)
        result = self.registry.handle(_CLIENT.principal_ref, request)
        if request.header.operation == "account.snapshot":
            self.clock.value += self.response_delay
            if self.fail_snapshot_after_dispatch:
                raise TimeoutError("synthetic reply loss after dispatch")
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=_PEER,
            ),
            result,
        )

    def close(self) -> None:
        self.closed = True


def _make_client(
    tmp_path,
    *,
    require_open_orders: bool = False,
    expected_scope_ref: str | None = None,
):
    capability = ServiceCapability("account-instance", "service.account_read", _OPERATIONS)
    grant = ResourceGrant(
        "account-instance",
        "service.account_read",
        "account-scope-v1",
        _SCOPE,
        _OPERATIONS,
    )
    registry = ServiceRegistry(
        tmp_path / f"account-{uuid4().hex}.sqlite3",
        service_id="account-service",
        capabilities=(capability,),
        grants={_CLIENT.principal_ref: (PrincipalGrant(_CLIENT, (grant,)),)},
        policy_version="account-policy-v1",
        requirements_version="account-requirements-v1",
        lease_seconds=30,
    )
    source = AccountService(_reply())
    registry.register_handler("account-instance", "account.snapshot", source.snapshot)
    registry.set_ready(True)
    clock = ManualClock()
    transport = LocalControlTransport(registry, clock)
    config = parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": _CLIENT.actor_ref},
            "clients": {
                "gateway": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "store.account",
                                "service_id": "account-service",
                                "instance_id": "account-instance",
                                "scope_ref": "account-scope-v1",
                                "account_ref": _ACCOUNT_REF,
                                "transport_ref": "account-rpc",
                            }
                        ],
                        "options": {"request_timeout_ms": 1000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "account-rpc": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7811"},
                        "security_profile_ref": "account-keys",
                    }
                },
            },
        }
    )
    runtime = ServiceClientRuntime(
        config,
        identity=_CLIENT,
        scope_resolver=lambda _scope_ref: _SCOPE,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=lambda _profile, _peer: transport,
        run_id="account-run-1",
        requested_operations={"store.account": _OPERATIONS},
        monotonic_clock=clock,
    )
    runtime.start()
    client = ServiceAccountClient(
        runtime,
        "store.account",
        account_ref=_ACCOUNT_REF,
        expected_account=_ACCOUNT,
        required_coverage=_COVERAGE,
        require_open_orders=require_open_orders,
        expected_scope_ref=expected_scope_ref,
        monotonic_clock=clock,
    )
    return client, runtime, registry, source, transport, clock


def _close_all(client, runtime, registry) -> None:
    try:
        client.close()
    finally:
        try:
            if runtime.state != "closed":
                runtime.close()
        finally:
            registry.close()


def test_refresh_preserves_provider_units_and_cached_view_is_immutable(tmp_path):
    client, runtime, registry, source, transport, _clock = _make_client(tmp_path)
    try:
        view = client.refresh()
        assert isinstance(view, AccountStateView)
        assert client.snapshot() is view
        assert client.healthy is True
        assert view.observation.valuation is None
        assert view.observation.open_orders is None
        assert view.observation.open_orders_complete is False
        assert [(row.currency, row.total) for row in view.observation.balances] == [
            ("USD", "100"),
            ("USDT", "250"),
        ]
        assert [(row.quantity, row.qty_unit) for row in view.observation.positions] == [
            ("3", "base_units"),
            ("2", "contracts"),
        ]
        with pytest.raises(FrozenInstanceError):
            view.cursor = 8

        request = next(
            request
            for request in transport.requests
            if request.header.operation == "account.snapshot"
        )
        assert request.payload["arguments"] == {"limit": 1}
        assert request.header.scope.account_ref == _ACCOUNT_REF
        assert source.calls[0][1] == {"limit": 1}

        client.close()
        assert runtime.state == "registered"
        assert transport.closed is False
    finally:
        _close_all(client, runtime, registry)


def test_expected_scope_ref_accepts_the_registered_scope(tmp_path):
    client, runtime, registry, _source, transport, _clock = _make_client(
        tmp_path, expected_scope_ref="account-scope-v1"
    )
    try:
        client.refresh()

        snapshot_requests = [
            request
            for request in transport.requests
            if request.header.operation == "account.snapshot"
        ]
        assert len(snapshot_requests) == 1
        assert snapshot_requests[0].header.scope.account_ref == _ACCOUNT_REF
        assert runtime.state == "registered"
    finally:
        _close_all(client, runtime, registry)


def test_expected_scope_ref_rejects_change_after_maintain_before_rpc(tmp_path, monkeypatch):
    client, runtime, registry, _source, transport, _clock = _make_client(
        tmp_path, expected_scope_ref="account-scope-v1"
    )
    original_maintain = runtime.maintain
    original_account_binding_view = runtime.account_binding_view
    changed = False

    def maintain_then_change_scope():
        nonlocal changed
        result = original_maintain()
        changed = True
        return result

    def account_binding_view_after_renewal(*args, **kwargs):
        binding = original_account_binding_view(*args, **kwargs)
        if not changed:
            return binding
        return replace(binding, scope_ref="account-scope-rebound")

    monkeypatch.setattr(runtime, "maintain", maintain_then_change_scope)
    monkeypatch.setattr(runtime, "account_binding_view", account_binding_view_after_renewal)
    try:
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()

        assert error.value.code == "SCOPE_DENIED"
        assert not any(
            request.header.operation == "account.snapshot" for request in transport.requests
        )
    finally:
        _close_all(client, runtime, registry)


@pytest.mark.parametrize("orders", [(_open_order(),), ()])
def test_v2_open_orders_preserves_nonempty_and_complete_empty_state(tmp_path, orders):
    client, runtime, registry, source, _transport, _clock = _make_client(tmp_path)
    try:
        observation = replace(
            _observation(),
            open_orders=orders,
            open_orders_complete=True,
        )
        source.response = _reply(observation)
        view = client.refresh()
        assert view.observation.open_orders == orders
        assert view.observation.open_orders_complete is True
        assert client.snapshot() is view
    finally:
        _close_all(client, runtime, registry)


@pytest.mark.parametrize(
    ("observation", "schema_version"),
    [
        (_observation(), 1),
        (
            replace(
                _observation(),
                open_orders=(),
                open_orders_complete=False,
            ),
            2,
        ),
    ],
)
def test_required_open_orders_rejects_missing_or_incomplete_state(
    tmp_path, observation, schema_version
):
    client, runtime, registry, source, _transport, _clock = _make_client(
        tmp_path, require_open_orders=True
    )
    try:
        source.response = _reply(observation, schema_version=schema_version)
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == "ACCOUNT_OPEN_ORDERS_INCOMPLETE"
        assert client.healthy is False
    finally:
        _close_all(client, runtime, registry)


@pytest.mark.parametrize(
    ("observation", "schema_version"),
    [
        (
            replace(
                _observation(),
                open_orders=(_open_order(),),
                open_orders_complete=True,
            ),
            1,
        ),
        (_observation(), 2),
    ],
)
def test_open_orders_presence_must_match_schema_version(tmp_path, observation, schema_version):
    client, runtime, registry, source, _transport, _clock = _make_client(tmp_path)
    try:
        source.response = _reply(observation, schema_version=schema_version)
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == "ACCOUNT_STATE_SCHEMA_MISMATCH"
        assert client.healthy is False
    finally:
        _close_all(client, runtime, registry)


def test_runtime_binding_and_validation_policy_are_read_only(tmp_path):
    client, runtime, registry, _source, _transport, _clock = _make_client(tmp_path)
    try:
        replacements = {
            "mount": "orders",
            "account_ref": "paper:other-account",
            "expected_account": _OTHER_ACCOUNT,
            "required_coverage": (_SPOT,),
            "require_open_orders": True,
        }
        for name, replacement in replacements.items():
            with pytest.raises(AttributeError):
                setattr(client, name, replacement)

        assert client.mount == "store.account"
        assert client.account_ref == _ACCOUNT_REF
        assert client.expected_account == _ACCOUNT
        assert client.required_coverage == _COVERAGE
        assert client.require_open_orders is False
        assert client.refresh().observation.account_key == _ACCOUNT
    finally:
        _close_all(client, runtime, registry)


@pytest.mark.parametrize(
    ("observation", "expected_code"),
    [
        (_observation(account=_OTHER_ACCOUNT), "ACCOUNT_IDENTITY_MISMATCH"),
        (_observation(coverage=(_SPOT,)), "ACCOUNT_COVERAGE_MISMATCH"),
    ],
)
def test_refresh_rejects_wrong_account_or_coverage(tmp_path, observation, expected_code):
    client, runtime, registry, source, _transport, _clock = _make_client(tmp_path)
    try:
        source.response = _reply(observation)
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == expected_code
        assert client.healthy is False
        with pytest.raises(ServiceAccountError) as error:
            client.snapshot()
        assert error.value.code == "STALE_ACCOUNT_STATE"
    finally:
        _close_all(client, runtime, registry)


def test_incomplete_or_eventful_account_batch_is_rejected(tmp_path):
    client, runtime, registry, source, _transport, _clock = _make_client(tmp_path)
    try:
        source.response = _reply(_observation(balances_complete=False))
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == "ACCOUNT_OBSERVATION_INCOMPLETE"

        source.response = _reply(events=[{"kind": "delta"}])
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == "INVALID_ACCOUNT_BASELINE"

        source.response = _reply(next_cursor=6)
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == "INVALID_ACCOUNT_BASELINE"
    finally:
        _close_all(client, runtime, registry)


def test_generation_mismatch_and_cursor_regression_are_rejected(tmp_path):
    client, runtime, registry, source, _transport, _clock = _make_client(tmp_path)
    try:
        source.response = _reply(status_generation="generation-two")
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == "ACCOUNT_GENERATION_MISMATCH"

        source.response = _reply(cursor=9)
        first = client.refresh()
        source.response = _reply(cursor=8)
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == "ACCOUNT_CURSOR_REGRESSION"
        assert client.last_view is first
        assert client.healthy is False
    finally:
        _close_all(client, runtime, registry)


def test_equal_cursor_accepts_exact_duplicate_but_rejects_conflicts(tmp_path):
    client, runtime, registry, source, _transport, _clock = _make_client(tmp_path)
    try:
        source.response = _reply(cursor=7)
        first = client.refresh()
        source.response = _reply(cursor=7)
        duplicate = client.refresh()
        assert duplicate.cursor == first.cursor
        assert duplicate.snapshot_id == first.snapshot_id
        assert duplicate.observation == first.observation
        assert client.snapshot() is duplicate

        source.response = _reply(cursor=7, snapshot_id="snapshot-conflict")
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == "ACCOUNT_CURSOR_CONFLICT"
        assert client.last_view is duplicate
        assert client.healthy is False

        changed_observation = replace(_observation(), observation_id="observation-2")
        source.response = _reply(cursor=7, observation=changed_observation)
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == "ACCOUNT_CURSOR_CONFLICT"
        assert client.last_view is duplicate
    finally:
        _close_all(client, runtime, registry)


def test_rpc_failure_invalidates_current_view_but_preserves_last_and_runtime(
    tmp_path,
):
    client, runtime, registry, _source, transport, _clock = _make_client(tmp_path)
    try:
        first = client.refresh()
        transport.fail_snapshot_after_dispatch = True
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == "ACCOUNT_READ_FAILED"
        assert client.last_view is first
        assert client.healthy is False
        with pytest.raises(ServiceAccountError) as error:
            client.snapshot()
        assert error.value.code == "STALE_ACCOUNT_STATE"
        assert runtime.state == "registered"
    finally:
        _close_all(client, runtime, registry)


@pytest.mark.parametrize(
    ("ready", "fresh_for_seconds", "expected_code"),
    [
        (False, 10.0, "ACCOUNT_SESSION_NOT_READY"),
        (True, 0.0, "STALE_ACCOUNT_STATE"),
    ],
)
def test_not_ready_or_stale_reply_clears_current_but_keeps_last_view(
    tmp_path, ready, fresh_for_seconds, expected_code
):
    client, runtime, registry, source, _transport, _clock = _make_client(tmp_path)
    try:
        first = client.refresh()
        source.response = _reply(ready=ready, fresh_for_seconds=fresh_for_seconds)
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == expected_code
        assert client.healthy is False
        assert client.last_view is first
        with pytest.raises(ServiceAccountError) as error:
            client.snapshot()
        assert error.value.code == "STALE_ACCOUNT_STATE"
    finally:
        _close_all(client, runtime, registry)


def test_late_reply_cannot_extend_freshness_from_response_time(tmp_path):
    client, runtime, registry, source, transport, clock = _make_client(tmp_path)
    try:
        source.response = _reply(fresh_for_seconds=0.5)
        transport.response_delay = 0.6
        with pytest.raises(ServiceAccountError) as error:
            client.refresh()
        assert error.value.code == "STALE_ACCOUNT_STATE"
        assert clock.value == pytest.approx(100.6)
        assert client.healthy is False
    finally:
        _close_all(client, runtime, registry)


def test_changed_runtime_binding_invalidates_cached_view(tmp_path):
    client, runtime, registry, _source, _transport, _clock = _make_client(tmp_path)
    try:
        first = client.refresh()
        runtime.recover()
        assert client.healthy is False
        with pytest.raises(ServiceAccountError) as error:
            client.snapshot()
        assert error.value.code == "STALE_ACCOUNT_STATE"
        assert client.last_view is first

        replacement = client.refresh()
        assert replacement is not first
        assert client.healthy is True
    finally:
        _close_all(client, runtime, registry)
