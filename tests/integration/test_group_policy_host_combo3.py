"""CFG06b combo-3 host acceptance: group gate + coordinator + account delegation.

Combo 3 of the iteration-10 plan pairs, inside one real deployment:

1. an ``execution`` ``group_coordinator`` component whose trusted resource
   provider also supplies the ``group_admission`` resource,
2. a ``risk`` ``group_policy`` component whose internal
   ``service.group_admission`` port is bound to the *same* ``GroupPolicyService``
   object (proved by identity inside the spawned host), and
3. a minimal real account delegation: one spawned CURVE account host running
   ``group_account_owner`` + ``group_reservations``.

The nodes below drive the real spawned host over CURVE/ZMQ:

* document scope denial (``GROUP_POLICY_SCOPE_DENIED``) before any dispatch,
* document group denial (``GROUP_POLICY_DENIED``) before any dispatch,
* one admitted group end-to-end to ``PAIRED`` plus wire-level public-access
  refusals for ``group.admission.evaluate``,
* durable-state tamper / version drift failing closed at call time, and
* the policy-only host refusing to start (``NO_SUPPORTED_PUBLIC_PORTS``).
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import suppress
from decimal import Decimal
from pathlib import Path

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import ExecutionState, GroupOrderState, Side

from bt_api_py.service_components import component_specs, validate_components
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_runtime import ServiceRuntimeError
from tests.integration import _group_policy_host_fixtures as fixtures
from tests.integration import test_arb03_dim05_group_partial_fill_service as arb03
from tests.unit._service_group_provider_fixtures import _V2_INSTRUMENT

_ACCOUNT = "account-a"
_ACCOUNTS = (_ACCOUNT,)
_GROUP_ADMITTED = "cfg06b-combo3-admitted-01"
_GROUP_SCOPE_DENIED = "cfg06b-combo3-scope-denied-01"
_GROUP_ID_DENIED = "cfg06b-combo3-id-denied-01"
_GROUP_TAMPERED = "cfg06b-combo3-tampered-01"
_GROUP_DRIFTED = "cfg06b-combo3-drifted-01"
_PERP_INSTRUMENT = "BTC-USD-PERP"
_LEGS = (
    ("leg-a", _ACCOUNT, Side.BUY, Decimal("2"), _V2_INSTRUMENT),
    ("leg-b", _ACCOUNT, Side.SELL, Decimal("4"), _PERP_INSTRUMENT),
)
_INSTRUMENTS = {_ACCOUNT: (_V2_INSTRUMENT, _PERP_INSTRUMENT)}
_INITIAL_STATES = {_ACCOUNT: ("cancelled:1.5", "filled")}
_EXPECTED_ORDERS = {
    _ACCOUNT: (
        (_V2_INSTRUMENT, "2", "100", "buy-limit"),
        (_PERP_INSTRUMENT, "3", "100", "sell-limit"),
    )
}
_RULE_SPECS = {_ACCOUNT: {_V2_INSTRUMENT: "2", _PERP_INSTRUMENT: "1"}}


def _cluster(tmp_path, *, admitted, denied=()):
    """Pin one durable policy document, then spawn the real combo-3 cluster."""

    policy_path = str(tmp_path / "group-policy.sqlite3")
    policy_version = fixtures.pin_group_policy(
        policy_path, ref=fixtures._POLICY_REF, admitted=admitted, denied=denied
    )
    return fixtures.PolicyCluster(
        tmp_path,
        policy_path=policy_path,
        policy_ref=fixtures._POLICY_REF,
        policy_version=policy_version,
        accounts=_ACCOUNTS,
        instruments=_INSTRUMENTS,
        initial_states=_INITIAL_STATES,
        expected_orders=_EXPECTED_ORDERS,
        rule_specs=_RULE_SPECS,
    )


def _request(cluster, group_id):
    return arb03._group_request(group_id, cluster.metadata_reference_ns, _LEGS)


def _probe_context(cluster):
    return (
        WireIdentity(WireRole.STRATEGY_CLIENT, arb03._STRATEGY_PRINCIPAL, arb03._STRATEGY),
        WireScope(
            arb03._DOMAIN,
            account_refs=_ACCOUNTS,
            strategy_ref=arb03._STRATEGY,
            portfolio_ref=arb03._PORTFOLIO,
            run_id=arb03._RUN,
        ),
    )


def _assert_combo_wiring(cluster):
    """Both components exist, and both carry the one gate object by identity."""

    assert cluster.gate_ref == fixtures._POLICY_REF
    assert cluster.provider_calls == (
        ("risk", fixtures._POLICY_INSTANCE_ID, "group_policy", False, True),
        ("execution", arb03._COORDINATOR_INSTANCE_ID, "group_coordinator", True, False),
    )


def _assert_denied_without_dispatch(cluster, group_id, code):
    """The exact denial code surfaces and nothing reached the account owner."""

    reply = cluster.submit_reply(_request(cluster, group_id))
    assert reply == {"ok": False, "code": code, "result": {}}
    assert fixtures.read_run_bindings(cluster) == ()
    assert fixtures.read_group_snapshot(cluster, group_id) is None
    writes, counters, pending_holds, remaining = fixtures.account_provider_snapshot(
        cluster, _ACCOUNT
    )
    assert writes == ()
    assert counters == {"order_queries": 0, "fill_queries": 0}
    assert pending_holds == ()
    assert remaining == _EXPECTED_ORDERS[_ACCOUNT]


def test_combo3_document_scope_denial_fails_closed_before_dispatch(tmp_path) -> None:
    """A portfolio outside the pinned document never reaches the account owner."""

    cluster = _cluster(tmp_path, admitted=(fixtures._OTHER_PORTFOLIO,))
    with cluster:
        _assert_combo_wiring(cluster)
        _assert_denied_without_dispatch(
            cluster, _GROUP_SCOPE_DENIED, "GROUP_POLICY_SCOPE_DENIED"
        )


def test_combo3_document_group_denial_fails_closed_before_dispatch(tmp_path) -> None:
    """A denied group inside an admitted portfolio never reaches the account owner."""

    cluster = _cluster(tmp_path, admitted=(arb03._PORTFOLIO,), denied=(_GROUP_ID_DENIED,))
    with cluster:
        _assert_combo_wiring(cluster)
        _assert_denied_without_dispatch(cluster, _GROUP_ID_DENIED, "GROUP_POLICY_DENIED")


def test_combo3_admitted_group_reaches_paired_and_gate_is_not_public(tmp_path) -> None:
    """An admitted group completes over the real host; the gate has no public port."""

    cluster = _cluster(tmp_path, admitted=(arb03._PORTFOLIO,))
    with cluster:
        _assert_combo_wiring(cluster)

        # --- admitted path: real reservations, dispatch, proofs, PAIRED ---
        snapshot = cluster.submit_group(_request(cluster, _GROUP_ADMITTED))
        assert snapshot.state is GroupOrderState.PAIRED
        assert snapshot.error_code is None
        assert snapshot.residual_base_units == 0
        assert len(snapshot.legs) == 2
        assert len(snapshot.reservations) == 1
        assert snapshot.reservations[0].account_ref == _ACCOUNT
        leg_a = next(leg for leg in snapshot.legs if leg.leg_id == "leg-a")
        leg_b = next(leg for leg in snapshot.legs if leg.leg_id == "leg-b")
        assert leg_a.child_status is not None
        assert leg_a.child_status.state is ExecutionState.CANCELLED
        assert leg_a.child_status.filled_quantity == Decimal("1.5")
        assert leg_b.child_status is not None
        assert leg_b.child_status.state is ExecutionState.FILLED
        assert leg_b.child_status.filled_quantity == Decimal("3")

        bindings = fixtures.read_run_bindings(cluster)
        assert len(bindings) == 1
        assert bindings[0].request.portfolio_ref == arb03._PORTFOLIO
        assert bindings[0].request.group_id == _GROUP_ADMITTED
        assert bindings[0].coordinator_principal_ref == arb03._COORDINATOR_PRINCIPAL
        assert fixtures.read_group_snapshot(cluster, _GROUP_ADMITTED) == snapshot
        arb03._assert_provider_writes(Path(cluster.account_paths[_ACCOUNT]["provider"]), 2)
        assert arb03._risk_legs(cluster.account_paths, _ACCOUNT, _GROUP_ADMITTED) == (
            ("leg-a", True, True),
            ("leg-b", True, True),
        )
        arb03._assert_holds_resolved(cluster.account_paths, _ACCOUNT, _GROUP_ADMITTED)

        # --- the gate is reachable through no public port ---
        identity, scope = _probe_context(cluster)
        transport, peer = fixtures.probe_transport(
            endpoint=cluster.coordinator_endpoint,
            server_public=cluster.coordinator_server_public,
            client_public=cluster.strategy_client_public,
            client_secret=cluster.strategy_client_secret,
        )
        try:
            discover = fixtures.raw_control_request(
                transport,
                peer,
                operation="service.discover",
                payload={},
                identity=identity,
                scope=scope,
            )
            assert discover["ok"] is True, discover
            capabilities = discover["result"]["capabilities"]
            assert {(item["instance_id"], item["port"]) for item in capabilities} == {
                (arb03._COORDINATOR_INSTANCE_ID, "service.order_group_entry")
            }
            assert [
                item["operations"] for item in capabilities
            ] == [("groups.query", "groups.submit")]
            assert all(
                "group.admission.evaluate" not in item["operations"] for item in capabilities
            )
            assert all(item["port"] != "service.group_admission" for item in capabilities)

            client_session_id = uuid.uuid4().hex
            denied_on_real_port = fixtures.raw_control_request(
                transport,
                peer,
                operation="service.register",
                payload=fixtures.raw_register_payload(
                    client_session_id=client_session_id,
                    instance_id=arb03._COORDINATOR_INSTANCE_ID,
                    port="service.order_group_entry",
                    scope_ref=arb03._COORDINATOR_SCOPE_REF,
                    operations=("group.admission.evaluate",),
                ),
                identity=identity,
                scope=scope,
            )
            assert denied_on_real_port == {
                "ok": False,
                "code": "CAPABILITY_UNAVAILABLE",
                "result": {},
            }
            denied_on_internal_port = fixtures.raw_control_request(
                transport,
                peer,
                operation="service.register",
                payload=fixtures.raw_register_payload(
                    client_session_id=client_session_id,
                    instance_id=fixtures._POLICY_INSTANCE_ID,
                    port="service.group_admission",
                    scope_ref=fixtures._POLICY_REF,
                    operations=("group.admission.evaluate",),
                ),
                identity=identity,
                scope=scope,
            )
            assert denied_on_internal_port == {
                "ok": False,
                "code": "CAPABILITY_UNAVAILABLE",
                "result": {},
            }

            # Even holding the exact live strategy session, a raw call for the
            # gate operation is refused at the registry before any handler.
            strategy_binding = cluster.strategy_runtime.bindings[0]
            denied_call = fixtures.raw_control_request(
                transport,
                peer,
                operation="group.admission.evaluate",
                payload={
                    "registration_id": strategy_binding.registration_id,
                    "service_epoch": strategy_binding.service_epoch,
                    "instance_id": arb03._COORDINATOR_INSTANCE_ID,
                    "arguments": {},
                },
                identity=identity,
                scope=strategy_binding.scope,
                schema_id="sdk.service.call",
            )
            assert denied_call == {
                "ok": False,
                "code": "CAPABILITY_UNAVAILABLE",
                "result": {},
            }
        finally:
            transport.close()

        # The SDK itself has no route for the operation on any mount.
        with pytest.raises(ServiceRuntimeError) as static_refusal:
            fixtures.probe_runtime(
                endpoint=cluster.coordinator_endpoint,
                instance_id=arb03._COORDINATOR_INSTANCE_ID,
                scope_ref=arb03._COORDINATOR_SCOPE_REF,
                operations=frozenset({"group.admission.evaluate"}),
                accounts=_ACCOUNTS,
                server_public=cluster.coordinator_server_public,
                client_public=cluster.strategy_client_public,
                client_secret=cluster.strategy_client_secret,
            )
        assert static_refusal.value.code == "INVALID_ACTION_REQUEST"

        # A client pinned to the policy instance is refused by wire discovery.
        wire_runtime = fixtures.probe_runtime(
            endpoint=cluster.coordinator_endpoint,
            instance_id=fixtures._POLICY_INSTANCE_ID,
            scope_ref=arb03._COORDINATOR_SCOPE_REF,
            operations=frozenset({"groups.submit"}),
            accounts=_ACCOUNTS,
            server_public=cluster.coordinator_server_public,
            client_public=cluster.strategy_client_public,
            client_secret=cluster.strategy_client_secret,
        )
        try:
            with pytest.raises(ServiceRuntimeError) as wire_refusal:
                wire_runtime.start()
            assert wire_refusal.value.code == "CAPABILITY_UNAVAILABLE"
        finally:
            with suppress(BaseException):
                wire_runtime.close()


def test_combo3_durable_tamper_and_version_drift_fail_closed(tmp_path) -> None:
    """The gate reads durable state at call time and rejects tampered versions."""

    cluster = _cluster(tmp_path, admitted=(arb03._PORTFOLIO,))
    with cluster:
        _assert_combo_wiring(cluster)

        fixtures.tamper_policy_document(
            cluster.policy_path, ref=fixtures._POLICY_REF, admitted=(fixtures._OTHER_PORTFOLIO,)
        )
        assert fixtures.policy_row_count(cluster.policy_path, ref=fixtures._POLICY_REF) == 1
        _assert_denied_without_dispatch(cluster, _GROUP_TAMPERED, "GROUP_POLICY_SCOPE_DENIED")

        with sqlite3.connect(cluster.policy_path) as connection:
            connection.execute(
                "UPDATE group_policy_versions SET policy_version=? WHERE group_policy_ref=?",
                ("0" * 64, fixtures._POLICY_REF),
            )
        _assert_denied_without_dispatch(cluster, _GROUP_DRIFTED, "GROUP_POLICY_VERSION_REJECTED")


def test_combo3_group_policy_role_alone_cannot_start_a_public_host(tmp_path) -> None:
    """The internal-only role yields no capability and never reaches resources."""

    raw = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "cfg06b-group-policy-service",
        "scope_ref": "cfg06b-group-policy-scope-v1",
        "transport_ref": "group-policy-listener",
        "registration_policy_ref": "cfg06b-group-policy-registration-v1",
        "service_requirements_ref": "cfg06b-group-policy-requirements-v1",
        "components": {"risk": fixtures._risk_policy_component()},
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "group-policy-listener": arb03._profile(
                    "tcp://127.0.0.1:9",
                    server=True,
                    security_ref="cfg06b-group-policy-server-v1",
                )
            },
        },
    }
    config = parse_service_config(raw)
    prepared = validate_components(component_specs(config.components))
    assert len(prepared) == 1
    assert dict(prepared[0].operations_by_port) == {}
    assert dict(prepared[0].write_operations_by_port) == {}

    authority = TrustedServiceAuthority(
        identity=WireIdentity(
            WireRole.SERVICE, "cfg06b-group-policy-principal", "cfg06b-group-policy-service"
        ),
        scope=WireScope(arb03._DOMAIN),
        scope_ref="cfg06b-group-policy-scope-v1",
        scope_version="cfg06b-scope-v1",
        security_ref="cfg06b-group-policy-server-v1",
        security_version="curve-key-v1",
        registration_policy_ref="cfg06b-group-policy-registration-v1",
        registration_policy_version="cfg06b-registration-v1",
        service_requirements_ref="cfg06b-group-policy-requirements-v1",
        service_requirements_version="cfg06b-requirements-v1",
        curve_public_key=b"a" * 40,
        curve_secret_key=b"b" * 40,
        principals={b"c" * 40: "group-policy-peer"},
        grants={"group-policy-peer": ()},
    )
    resource_requests: list[tuple[str, str]] = []

    def resources(component, instance_id, _options, _scope):
        resource_requests.append((component, instance_id))
        raise AssertionError("the internal-only role must never reach resources")

    host = ServiceHost(config, authority, str(tmp_path / "group-registry.sqlite3"), resources)
    try:
        with pytest.raises(ServiceHostError) as excinfo:
            host.start()
        assert excinfo.value.code == "NO_SUPPORTED_PUBLIC_PORTS"
    finally:
        with suppress(BaseException):
            host.close()
    assert resource_requests == []
