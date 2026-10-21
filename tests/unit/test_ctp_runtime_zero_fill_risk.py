"""Runtime-built CTP zero-fill tracking against the real durable Risk stack.

The Store, execution service adapter, ManagedExecutionFacade, TraderClient,
owner, callback verifier, zero-fill reader, Risk gate, journal, bridge, Host,
Registry, and SDK client are production objects. The native API callbacks and
in-process request/reply transport are synthetic leaves. This is source-fake
integration evidence, not exchange, credential, account-cutover, CURVE socket,
or real native acceptance.
"""

from __future__ import annotations

import hashlib
import json
import traceback
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
import test_ctp_execution_runtime as runtime_fixtures
import test_ctp_runtime_order_chain as chain_fixtures
from bt_api_execution import (
    ContractValidationError,
    ExecutionScope,
    ExecutionState,
    OrderIntent,
    PositionEffect,
    Side,
    TimeInForce,
)
from bt_api_execution.basic_admission import (
    RECOVERY_CLOSE_OPERATION,
    ExecutionAdmissionContextV1,
)
from bt_api_execution.ctp_single_worker_candidate import stable_managed_command_id
from bt_api_py.ctp_admission_authority import (
    _context_action_matches_order,
    _position_mode_matches_ctp_order,
)
from bt_api_py.ctp_zero_fill_tracked_order import (
    CtpZeroFillTrackedOrderEvidenceV1,
    CtpZeroFillTrackedOrderReader,
)
from bt_api_py.ctp_risk_dispatch_bridge import (
    _same_dispatch_resolution_receipt,
)
from bt_api_risk import (
    DispatchExposureResolutionReceiptStatus,
    DispatchResolutionReceiptStatus,
    IntentAction,
)
from bt_api_py.service_risk_order_journal import (
    _ctp_zero_fill_dispatch_history_valid,
)

_RECOVERY_VERTICAL_REASON_CODES = frozenset(
    {
        "admission_gate_error",
        "admission_validation_unavailable",
        "before_dispatch_claim_authorization_failed",
        "before_dispatch_claim_authorization_missing",
        "basic_admission_denied",
        "dispatch_outcome_unknown",
        "invalid_admission_permit",
        "invalid_admission_permit_reference",
        "admission_permit_reference_required",
        "RISK_INTENT_MAPPING_FAILED",
        "RISK_INTENT_SCOPE_MISMATCH",
        "RISK_INTENT_ECONOMIC_MISMATCH",
        "RECOVERY_PARENT_BINDING_INVALID",
        "RECOVERY_PARENT_NOT_TRACKED",
        "RECOVERY_PARENT_JOURNAL_MISMATCH",
        "RECOVERY_PARENT_READBACK_FAILED",
        "RECOVERY_PARENT_READBACK_MISMATCH",
        "RECOVERY_SOURCE_AUTHORITY_UNAVAILABLE",
        "RECOVERY_SOURCE_PROOF_REJECTED",
        "RECOVERY_SOURCE_PROOF_MISMATCH",
        "RECOVERY_RISK_ALLOCATION_INVALID",
        "RECOVERY_RISK_ALLOCATION_READBACK_MISMATCH",
        "RECOVERY_RESERVATION_OUTCOME_UNCERTAIN",
        "RECOVERY_RESERVATION_JOURNAL_FAILED",
        "RISK_PREPARE_FAILED",
        "RISK_RESERVATION_FAILED",
        "RISK_PERMIT_INVALID",
        "admission_activation_failed",
        "admission_claim_failed",
        "basic_admission_claim_failed",
    }
)
_RECOVERY_VERTICAL_SOURCE_BASENAMES = frozenset(
    {
        "ctp_execution_owner.py",
        "ctp_managed_submit_port.py",
        "ctp_single_worker_candidate.py",
        "store.py",
        "ctp_risk_dispatch_bridge.py",
        "client.py",
        "ctp_admission_authority.py",
        "facade.py",
        "basic_admission.py",
        "service_order_entry.py",
    }
)
_RECOVERY_VERTICAL_BOUNDARY_NAMES = frozenset(
    {
        "owner_dispatch",
        "store_stage",
        "store_queue_receipt",
        "risk_before_native",
        "store_claim",
        "native_lease",
        "native_submit",
    }
)


@pytest.mark.unit
def test_ctp_explicit_position_side_requires_exact_reduce_only_close():
    base = OrderIntent.limit(
        intent_id="position-mode-base",
        scope=ExecutionScope(
            provider="ctp",
            environment="simnow",
            account_ref="synthetic-account",
            strategy_id="position-mode-test",
            trading_day="20261021",
        ),
        signal_id="position-mode-signal",
        instrument="rb2710",
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        time_in_force=TimeInForce.DAY,
    )
    long_close = replace(
        base,
        side=Side.SELL,
        position_effect=PositionEffect.CLOSE_TODAY,
        position_bucket="TODAY",
        reduce_only=True,
        position_side="LONG",
    )
    assert _position_mode_matches_ctp_order(long_close)
    assert not _position_mode_matches_ctp_order(replace(long_close, side=Side.BUY))
    short_close = replace(
        long_close,
        side=Side.BUY,
        position_side="SHORT",
    )
    assert _position_mode_matches_ctp_order(short_close)
    assert not _position_mode_matches_ctp_order(replace(short_close, side=Side.SELL))
    assert not _position_mode_matches_ctp_order(
        replace(
            long_close,
            position_effect=PositionEffect.OPEN,
            position_bucket="ALL",
            reduce_only=False,
        )
    )
    assert _position_mode_matches_ctp_order(replace(base, position_side="NET"))


def _rehash_evidence_events(
    order_event, fill_event, alteration, *, mirror_fingerprint=False
):
    """Build detached DTO variants with matching evidence digests and copies."""

    order_payload = json.loads(json.dumps(order_event.payload))
    fill_payload = json.loads(json.dumps(fill_event.payload))
    evidence = order_payload["record"]["evidence"]
    intent = evidence["intent"]
    if alteration == "fingerprint":
        intent["fingerprint"] = (
            "e" if intent["fingerprint"] != "e" * 64 else "f"
        ) * 64
    elif alteration == "scope":
        evidence["scope"]["strategy_id"] = "foreign-strategy"
    elif alteration == "position_effect":
        intent["position_effect"] = "CLOSE"
    elif alteration == "reduce_only":
        intent["reduce_only"] = True
    else:
        raise AssertionError(f"unknown detached evidence alteration: {alteration}")

    digest_body = {
        key: item for key, item in evidence.items() if key != "evidence_sha256"
    }
    evidence_sha256 = hashlib.sha256(
        json.dumps(
            digest_body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    ).hexdigest()
    evidence["evidence_sha256"] = evidence_sha256

    for payload in (order_payload, fill_payload):
        record = payload["record"]
        record["evidence"] = json.loads(json.dumps(evidence))
        record["evidence_sha256"] = evidence_sha256
        if mirror_fingerprint:
            record["execution_intent_sha256"] = intent["fingerprint"]

    return replace(order_event, payload=order_payload), replace(
        fill_event, payload=fill_payload
    )


def _install_submit_query_rows(case, api, monkeypatch, callback_case, intent_id):
    """Install a fake native query leaf that emits the exact persisted target."""

    calls: list[int] = []
    response_info = SimpleNamespace(ErrorID=0, ErrorMsg="synthetic query response")
    query_intent_ids = [intent_id]
    case.synthetic_query_order_intent_ids = query_intent_ids

    def req_qry_order(field, request_id):
        api.calls.append(("ReqQryOrder", field, request_id))
        calls.append(request_id)
        store = case.resources.values["store"]
        row_case = SimpleNamespace(
            api=api,
            scope=case.execution_scope,
            session_binding=callback_case.session_binding,
        )
        for queried_intent_id in tuple(query_intent_ids):
            intent = store.get_intent(queried_intent_id, scope=case.execution_scope)
            reservation = store.read_ctp_order_identity(
                case.execution_scope, queried_intent_id
            )
            if intent is None or reservation is None:
                continue
            row = chain_fixtures.query_fixtures._native_order(
                row_case, intent=intent, reservation=reservation
            )
            # The production query evaluator compares the provider's CTP
            # direction/offset projection with the durable intent. Keep the
            # fixture faithful for a SELL/CLOSE_TODAY recovery child too.
            from bt_api_py.ctp_order_target_query import _order_offset

            row.Direction = "0" if intent.side.value == "BUY" else "1"
            row.CombOffsetFlag = _order_offset(intent, "SHFE")
            api.spi.OnRspQryOrder(row, response_info, request_id, False)
        api.spi.OnRspQryOrder(None, response_info, request_id, True)
        return 0

    def req_qry_trade(field, request_id):
        api.calls.append(("ReqQryTrade", field, request_id))
        api.spi.OnRspQryTrade(None, response_info, request_id, True)
        return 0

    monkeypatch.setattr(api, "ReqQryOrder", req_qry_order, raising=False)
    monkeypatch.setattr(api, "ReqQryTrade", req_qry_trade, raising=False)
    return calls


def _install_position_query_rows(case, api, monkeypatch):
    """Answer position queries through the real TraderClient SPI path."""

    rows: list[dict[str, object]] = []
    case.synthetic_position_rows = rows
    response_info = SimpleNamespace(ErrorID=0, ErrorMsg="synthetic position query")

    def req_qry_position(field, request_id):
        api.calls.append(("ReqQryInvestorPosition", field, request_id))
        for row in tuple(rows):
            api.spi.OnRspQryInvestorPosition(
                SimpleNamespace(**dict(row)), response_info, request_id, False
            )
        api.spi.OnRspQryInvestorPosition(None, response_info, request_id, True)
        return 0

    monkeypatch.setattr(api, "ReqQryInvestorPosition", req_qry_position, raising=False)
    return rows


def _prepare_runtime_order(case, monkeypatch, intent_id, *, owner_control=False):
    component = (
        chain_fixtures._build_owner_control_execution_component(case, monkeypatch)
        if owner_control
        else runtime_fixtures._build_execution_component(case, monkeypatch)
    )
    chain_fixtures._seed_synthetic_order_ref_cutover(case)
    reserve_errors = chain_fixtures._observe_authenticated_reserve(case, monkeypatch)
    chain_fixtures._observe_submit_authorized(case, monkeypatch)
    api = case.factory.instances[0]
    callback_case, session_binding, req_order_insert = (
        chain_fixtures._install_submit_callback(case, api, intent_id)
    )
    monkeypatch.setattr(api, "ReqOrderInsert", req_order_insert, raising=False)
    _install_position_query_rows(case, api, monkeypatch)
    query_calls = _install_submit_query_rows(
        case, api, monkeypatch, callback_case, intent_id
    )
    return component, api, callback_case, session_binding, reserve_errors, query_calls


def _prepare_host_runtime_order(case, monkeypatch, intent_id, tmp_path, request):
    """Start the real Host and route the SDK client into its actual Registry.

    The in-process transport preserves Registry, authorization, readiness,
    handler, and client code while replacing only the socket exchange.
    """

    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_execution import ServiceExecutionClient
    from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
    from bt_api_py.service_runtime import ServiceClientRuntime

    host_fixtures = runtime_fixtures.service_host_fixtures
    service_id = "ctp-recovery-vertical-service"
    instance_id = "ctp-recovery-vertical-owner"
    scope_ref = "ctp-recovery-vertical-scope-v1"
    client_scope_ref = "ctp-recovery-client-scope-v1"
    foreign_scope_ref = "ctp-recovery-foreign-scope-v1"
    client_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        chain_fixtures._PRINCIPAL,
        case.execution_scope.strategy_id,
    )
    owner_identity = chain_fixtures._owner_control_context(case).identity
    owner_scope = WireScope(
        case.service_scope.execution_domain_ref,
        account_ref=case.execution_scope.account_ref,
        strategy_ref=case.execution_scope.strategy_id,
        run_id=chain_fixtures._RUN_ID,
    )
    foreign_scope = replace(
        case.service_scope,
        execution_domain_ref=case.service_scope.execution_domain_ref + ":foreign",
    )

    original_factory = case.config.risk_admission_factory
    quota_calls = []
    quota_returns = []

    def risk_admission_factory(context):
        admission = dict(original_factory(context))

        def cancel_quota_admission(*args):
            quota_calls.append(args)
            quota_returns.append(True)
            return None

        admission["cancel_quota_admission"] = cancel_quota_admission
        return admission

    case.provider.config = replace(
        case.config, risk_admission_factory=risk_admission_factory
    )
    case.cancel_quota_calls = quota_calls
    case.cancel_quota_returns = quota_returns

    endpoint = host_fixtures._endpoint()
    service_config = host_fixtures._service_config(
        {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": instance_id,
                    "bind_ports": [
                        "service.order_entry",
                        "service.order_query",
                        "service.owner_control",
                    ],
                    "options": {"role": "account_owner"},
                },
            }
        },
        service_id=service_id,
        scope_ref=scope_ref,
        listener_endpoint=endpoint,
    )
    server_public, server_secret = host_fixtures.zmq.curve_keypair()
    client_public, _client_secret = host_fixtures.zmq.curve_keypair()
    owner_public, _owner_secret = host_fixtures.zmq.curve_keypair()
    service_identity = WireIdentity(
        WireRole.SERVICE, "ctp-recovery-vertical-host", service_id
    )
    service_scope_operations = frozenset({"orders.submit_recovery_close"})
    order_entry_grants = (
        ResourceGrant(
            instance_id,
            "service.order_entry",
            client_scope_ref,
            case.service_scope,
            service_scope_operations,
        ),
        ResourceGrant(
            instance_id,
            "service.order_entry",
            foreign_scope_ref,
            foreign_scope,
            service_scope_operations,
        ),
    )
    owner_grant = ResourceGrant(
        instance_id,
        "service.owner_control",
        "ctp-recovery-owner-control-v1",
        owner_scope,
        frozenset({"owner.cancel"}),
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=case.service_scope,
        scope_ref=scope_ref,
        scope_version="scope-v1",
        security_ref="listener-security-v1",
        security_version="curve-v1",
        registration_policy_ref=service_config.registration_policy_ref,
        registration_policy_version="policy-v1",
        service_requirements_ref=service_config.service_requirements_ref,
        service_requirements_version="requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={
            client_public: chain_fixtures._PRINCIPAL,
            owner_public: owner_identity.principal_ref,
        },
        grants={
            chain_fixtures._PRINCIPAL: (
                PrincipalGrant(client_identity, order_entry_grants),
            ),
            owner_identity.principal_ref: (
                PrincipalGrant(owner_identity, (owner_grant,)),
            ),
        },
    )
    def resource_provider(component_name, selected_instance, options, scope):
        resources = case.provider(component_name, selected_instance, options, scope)
        case.resources = resources
        return resources

    host = ServiceHost(
        service_config,
        authority,
        tmp_path / "recovery-vertical-registry.sqlite3",
        resource_provider,
    )
    clients = []

    def close_with_retry(resource, label):
        last_error = None
        for attempt in range(2):
            if resource._state == "closed":
                return
            try:
                resource.close()
            except BaseException as exc:
                last_error = exc
                if resource._state == "close_failed" and attempt == 0:
                    continue
                raise
            if resource._state == "closed":
                return
            if resource._state == "close_failed" and attempt == 0:
                continue
            raise AssertionError(f"{label} did not close: {resource._state}")
        if resource._state != "closed":
            if last_error is not None:
                raise last_error
            raise AssertionError(f"{label} did not close: {resource._state}")

    def close_host_path():
        close_errors = []
        for client_runtime in clients:
            try:
                close_with_retry(client_runtime, "client runtime")
            except BaseException as exc:
                close_errors.append(exc)
        try:
            close_with_retry(host, "service host")
        except BaseException as exc:
            close_errors.append(exc)
        case.component = None
        if close_errors:
            raise close_errors[0]

    request.addfinalizer(close_host_path)
    host.start()
    component = host._components[0]
    case.component = component

    class RecordingRegistryTransport(host_fixtures._RegistryTransport):
        def __init__(self, registry, principal_ref, peer, events):
            super().__init__(registry, principal_ref, peer, events)
            self.replies = []

        def request(self, wire_request, *, timeout):
            reply = super().request(wire_request, timeout=timeout)
            payload = reply.payload
            self.replies.append(
                payload.get("result") if payload.get("ok") is True else payload
            )
            return reply

    def build_client(identity, principal_ref, grant_scope_ref, resolved_scope):
        client_config = parse_client_config(
            {
                "client_config_schema_version": 1,
                "process_role": "strategy_client",
                "registration": {
                    "strategy_ref": case.execution_scope.strategy_id
                },
                "clients": {
                    "execution": {
                        "config_schema_version": 1,
                        "settings": {
                            "bindings": [
                                {
                                    "mount": "broker.orders",
                                    "service_id": service_id,
                                    "instance_id": instance_id,
                                    "scope_ref": grant_scope_ref,
                                    "account_ref": case.execution_scope.account_ref,
                                    "transport_ref": "recovery-registry",
                                }
                            ],
                            "options": {"request_timeout_ms": 4_000},
                        },
                    }
                },
                "communication": {
                    "config_schema_version": 1,
                    "profiles": {
                        "recovery-registry": {
                            "backend": "zmq",
                            "config_schema_version": 1,
                            "endpoints": {"rpc_connect": host.endpoint},
                            "security_profile_ref": "recovery-registry-security-v1",
                        }
                    },
                },
            }
        )
        events = []
        transport = RecordingRegistryTransport(
            host._registry, principal_ref, authority.identity, events
        )
        runtime = ServiceClientRuntime(
            client_config,
            identity=identity,
            scope_resolver=lambda _scope_ref: resolved_scope,
            peer_resolver=lambda _service_id: authority.identity,
            transport_factory=lambda _profile, _peer: transport,
            run_id=chain_fixtures._RUN_ID,
            requested_operations={
                "broker.orders": frozenset({"orders.submit_recovery_close"})
            },
        )
        clients.append(runtime)
        runtime.start()
        api = ServiceExecutionClient(
            runtime, "broker.orders", scope_ref=grant_scope_ref
        )
        return runtime, api, transport, events

    client_runtime, recovery_client, client_transport, client_events = build_client(
        client_identity,
        chain_fixtures._PRINCIPAL,
        client_scope_ref,
        case.service_scope,
    )
    scope_runtime, scope_client, scope_transport, scope_events = build_client(
        client_identity,
        chain_fixtures._PRINCIPAL,
        foreign_scope_ref,
        foreign_scope,
    )
    chain_fixtures._seed_synthetic_order_ref_cutover(case)
    reserve_errors = chain_fixtures._observe_authenticated_reserve(case, monkeypatch)
    chain_fixtures._observe_submit_authorized(case, monkeypatch)
    api = case.factory.instances[0]
    callback_case, session_binding, req_order_insert = (
        chain_fixtures._install_submit_callback(case, api, intent_id)
    )
    monkeypatch.setattr(api, "ReqOrderInsert", req_order_insert, raising=False)
    _install_position_query_rows(case, api, monkeypatch)
    query_calls = _install_submit_query_rows(
        case, api, monkeypatch, callback_case, intent_id
    )
    return (
        host,
        component,
        api,
        callback_case,
        session_binding,
        reserve_errors,
        query_calls,
        recovery_client,
        client_runtime,
        client_transport,
        client_events,
        scope_client,
        scope_transport,
        scope_events,
        close_host_path,
    )


def _observe_zero_fill_reader(monkeypatch):
    original_issue = CtpZeroFillTrackedOrderReader.issue
    original_readback = CtpZeroFillTrackedOrderReader.readback
    issued = []
    readbacks = []

    def observe_issue(reader, managed_intent_id, *, timeout=5.0):
        result = original_issue(reader, managed_intent_id, timeout=timeout)
        if type(result) is CtpZeroFillTrackedOrderEvidenceV1:
            issued.append(result)
        return result

    def observe_readback(reader, evidence):
        readbacks.append(evidence)
        return original_readback(reader, evidence)

    monkeypatch.setattr(CtpZeroFillTrackedOrderReader, "issue", observe_issue)
    monkeypatch.setattr(CtpZeroFillTrackedOrderReader, "readback", observe_readback)
    return issued, readbacks


def _install_hook_counter(bridge, monkeypatch):
    original = bridge.after_evidenced_observation
    calls = []
    errors = []

    def observe(record, event, *, writer_lease):
        calls.append(
            (record.intent_id, event.event_type, record.state, record, event, writer_lease)
        )
        try:
            return original(record, event, writer_lease=writer_lease)
        except Exception as error:
            frames = []
            trace = error.__traceback__
            while trace is not None:
                name = trace.tb_frame.f_code.co_filename.replace(chr(92), "/")
                if "/bt_api_py/" in name or "/bt_api_risk/" in name:
                    frames.append((name.rsplit("/", 1)[-1], trace.tb_frame.f_code.co_name, trace.tb_lineno))
                trace = trace.tb_next
            code = getattr(error, "code", None)
            errors.append({"type": type(error).__name__, "code": code if type(code) is str and code.replace("_", "").isalnum() else None, "frames": frames[-4:]})
            raise

    monkeypatch.setattr(bridge, "after_evidenced_observation", observe, raising=True)
    return calls, errors


def _journal_events(case, bridge, intent_id):
    command = case.journal.get_command(
        bridge.account_scope, chain_fixtures._PRINCIPAL, intent_id
    )
    assert command is not None
    events = tuple(
        case.journal.get_event(
            bridge.account_scope,
            chain_fixtures._PRINCIPAL,
            intent_id,
            revision,
        )
        for revision in range(1, command.latest_revision + 1)
    )
    return command, tuple(event for event in events if event is not None)


def _assert_current_order_evidence_events(events, evidence, *, expected_quantity):
    """Check exact current order state and its complete target trade window."""

    order_events = [event for event in events if event.event_type == "ORDER_OBSERVATION"]
    fill_events = [event for event in events if event.event_type == "FILL_OBSERVATION"]
    assert len(order_events) == 1
    assert len(fill_events) == 1

    order_payload = order_events[0].payload
    fill_payload = fill_events[0].payload
    order_record = order_payload["record"]
    fill_record = fill_payload["record"]
    assert type(order_record) is dict
    assert type(fill_record) is dict

    for record in (order_record, fill_record):
        assert record["schema"] == "ctp-risk-current-order-evidence-v2"
        assert record["evidence_kind"] == (
            "CTP_CALLBACK_QUERY_ORDER_AND_QUERY_TRADE_TARGET_ORDER_WINDOW"
        )
        assert record["evidence"] == json.loads(json.dumps(evidence.to_payload()))
        assert record["evidence_sha256"] == evidence.evidence_sha256
        assert record["observation_scope"] == "CURRENT_ORDER_AND_TRADE_QUERY_WINDOW"
        assert record["fill_history_completeness"] == "COMPLETE_FOR_TARGET_ORDER"
        assert record["trade_history_observed"] is True
        assert record["trade_fact_count"] == 0
        assert Decimal(record["trade_notional"]) == Decimal("0")
        assert record["cumulative_commission"] is None
        assert record["cumulative_commission_currency"] is None
        assert record["commission_quality"] == "INCOMPLETE"
        assert record["evidence"]["schema"] == "ctp_zero_fill_tracked_order_evidence.v2"
        trade_window = record["evidence"]["query_trade"]
        assert trade_window["complete"] is True
        assert trade_window["is_last_seen"] is True
        assert trade_window["record_count"] == 0
        assert trade_window["target_order_trade_count"] == 0
        assert "get_deals" not in record
        assert "complete" not in record
        assert "records" not in record

    assert order_record["provider_state"] == "OPEN"
    assert order_record["provider_status"] == "ACKED"
    assert fill_record["requested_quantity"] == format(expected_quantity, "f")
    assert "get_deals" not in order_payload
    assert "get_deals" not in fill_payload


def _risk_resolution(case, bridge, intent_id):
    binding = bridge.read_dispatch_binding(chain_fixtures._PRINCIPAL, intent_id)
    assert binding is not None
    receipt = case.risk_gate.dispatch_resolution_receipt(binding["permit_id"])
    return binding, receipt


@pytest.mark.unit
def test_runtime_ack_tracks_exact_zero_fill_risk_proof_and_replay_is_read_only(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        intent_id = "ctp-runtime-zero-fill-risk-ack"
        component, api, _callback_case, _session_binding, reserve_errors, query_calls = (
            _prepare_runtime_order(case, monkeypatch, intent_id)
        )
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None
        bridge = resources.values["admission"]["order_gate"]
        hook_calls, hook_errors = _install_hook_counter(bridge, monkeypatch)
        issued_evidence, readback_evidence = _observe_zero_fill_reader(monkeypatch)
        context = chain_fixtures._service_context(case)
        arguments = chain_fixtures._order_arguments(intent_id)

        submitted = component.handlers["orders.submit"](context, arguments)
        assert reserve_errors == []
        assert case.authority_verify_errors == []
        assert case.native_lease_errors == []
        assert case.native_submit_errors == []
        assert case.submit_authorized_errors == []
        assert submitted["ok"] is True
        assert submitted["result"]["record"]["state"] == ExecutionState.ACKED.value
        assert hook_errors == [], hook_errors
        assert len(hook_calls) == 1
        assert hook_calls[0][0] == intent_id
        assert len(issued_evidence) == 1
        assert len(readback_evidence) == 2
        assert all(evidence is issued_evidence[0] for evidence in readback_evidence)
        evidence = issued_evidence[0]
        assert evidence.managed_intent_id == intent_id
        assert evidence.callback_snapshot.provider_state == "ACKNOWLEDGED"
        assert evidence.callback_snapshot.volume_traded == 0
        assert evidence.callback_snapshot.trade_fact_count == 0
        assert evidence.provider_observation.state is ExecutionState.ACKED
        assert evidence.provider_observation.filled_quantity == 0
        assert evidence.projection.provider_state == "OPEN"
        assert evidence.projection.traded_quantity == 0
        assert evidence.projection.query_complete is True
        assert evidence.trade_query.result.complete is True
        assert evidence.trade_query.result.records == ()
        assert len(query_calls) == 1
        assert [call[0] for call in api.calls].count("ReqQryTrade") == 1
        assert chain_fixtures._counts(api) == (1, 1, 0)

        binding, receipt = _risk_resolution(case, bridge, intent_id)
        assert receipt.status is DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        assert receipt.dispatch_latch_active is False
        assert receipt.claim is not None and receipt.proof is not None
        assert receipt.proof.permit_id == binding["permit_id"]
        assert receipt.proof.intent_id == intent_id
        assert receipt.proof.filled_quantity == 0
        assert receipt.proof.trade_count == 0
        assert receipt.proof.provider_order_id
        journal_command, journal_events = _journal_events(case, bridge, intent_id)
        assert journal_command.state == "ACKED_TRACKED"
        assert "PROOF_READY" in {event.event_type for event in journal_events}
        assert journal_events[-1].event_type == "ACKED_TRACKED"
        assert journal_events[-1].payload["filled_quantity"] == 0
        assert journal_events[-1].payload["trade_count"] == 0
        _assert_current_order_evidence_events(
            journal_events,
            evidence,
            expected_quantity=Decimal(arguments["quantity"]),
        )

        order_event = next(
            event for event in journal_events if event.event_type == "ORDER_OBSERVATION"
        )
        fill_event = next(
            event for event in journal_events if event.event_type == "FILL_OBSERVATION"
        )
        proof_ready_event = next(
            event for event in journal_events if event.event_type == "PROOF_READY"
        )
        proof_reference = proof_ready_event.payload["exposure_reference"]

        def history_valid(candidate_order, candidate_fill):
            return _ctp_zero_fill_dispatch_history_valid(
                binding,
                claim=receipt.claim,
                risk_scope_key=bridge.account_scope.key,
                command=journal_command,
                proof=receipt.proof,
                reference=proof_reference,
                order_event=candidate_order,
                fill_event=candidate_fill,
            )

        assert history_valid(order_event, fill_event) is True
        for alteration, mirror_fingerprint in (
            ("fingerprint", True),
            ("scope", False),
            ("position_effect", False),
            ("reduce_only", False),
        ):
            # These are detached event DTOs for the pure validator only. They
            # are not appended to or represented as accepted journal events.
            altered_order, altered_fill = _rehash_evidence_events(
                order_event,
                fill_event,
                alteration,
                mirror_fingerprint=mirror_fingerprint,
            )
            assert history_valid(altered_order, altered_fill) is False, alteration

        unchanged_command, unchanged_events = _journal_events(
            case, bridge, intent_id
        )
        assert unchanged_command == journal_command
        assert unchanged_events == journal_events
        assert case.risk_gate.dispatch_resolution_receipt(binding["permit_id"]) == receipt

        risk_snapshot = case.risk_gate.snapshot(bridge.account_scope)
        assert risk_snapshot["increase_count"] == 1
        assert Decimal(risk_snapshot["increase_notional"]) == Decimal("200")

        revision_before_replay = journal_command.latest_revision
        # The facade's exact record replay has no new event and therefore
        # skips the hook. Exercise the bridge's own exact-source-event replay
        # branch with the same persisted record, event, and lease captured on
        # its first invocation.
        original_record, original_event, original_lease = hook_calls[0][3:]
        assert bridge.after_evidenced_observation(
            original_record,
            original_event,
            writer_lease=original_lease,
        ) is None
        assert len(hook_calls) == 2
        assert len(issued_evidence) == 1
        assert len(readback_evidence) == 2
        assert all(evidence is issued_evidence[0] for evidence in readback_evidence)
        assert len(query_calls) == 1
        assert chain_fixtures._counts(api) == (1, 1, 0)
        exact_replay_command, exact_replay_events = _journal_events(
            case, bridge, intent_id
        )
        assert exact_replay_command.latest_revision == revision_before_replay
        assert exact_replay_events == journal_events
        assert case.risk_gate.dispatch_resolution_receipt(binding["permit_id"]) == receipt
        assert case.risk_gate.snapshot(bridge.account_scope) == risk_snapshot

        replay = component.handlers["orders.submit"](context, arguments)
        assert replay["ok"] is True
        assert replay["result"]["record"]["state"] == ExecutionState.ACKED.value
        assert len(hook_calls) == 2
        assert len(query_calls) == 1
        assert chain_fixtures._counts(api) == (1, 1, 0)
        replay_command, replay_events = _journal_events(case, bridge, intent_id)
        assert replay_command.latest_revision == revision_before_replay
        assert replay_events == journal_events
        assert case.risk_gate.dispatch_resolution_receipt(binding["permit_id"]) == receipt
        assert case.risk_gate.snapshot(bridge.account_scope) == risk_snapshot

        component.close()
        case.component = None
        assert runtime.status.closed is True


@pytest.mark.unit
def test_later_partial_fill_without_fee_keeps_zero_fill_risk_reservation_counted(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        intent_id = "ctp-runtime-zero-fill-risk-then-partial"
        component, api, callback_case, _session_binding, reserve_errors, query_calls = (
            _prepare_runtime_order(case, monkeypatch, intent_id)
        )
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None
        bridge = resources.values["admission"]["order_gate"]
        hook_calls, hook_errors = _install_hook_counter(bridge, monkeypatch)
        context = chain_fixtures._service_context(case)
        submitted = component.handlers["orders.submit"](
            context, chain_fixtures._order_arguments(intent_id)
        )
        assert reserve_errors == []
        assert submitted["ok"] is True
        assert submitted["result"]["record"]["state"] == ExecutionState.ACKED.value
        assert hook_errors == [], hook_errors
        assert len(hook_calls) == 1

        binding_before, receipt_before = _risk_resolution(case, bridge, intent_id)
        assert receipt_before.status is DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        journal_command_before, journal_events_before = _journal_events(
            case, bridge, intent_id
        )
        risk_snapshot_before = case.risk_gate.snapshot(bridge.account_scope)

        reservation, command_id, command = chain_fixtures._submit_binding(case, intent_id)
        intent = resources.values["store"].get_intent(
            intent_id, scope=case.execution_scope
        )
        assert intent is not None
        callback_fixtures = chain_fixtures.callback_fixtures
        callback_fixtures._emit_trade(
            callback_case,
            intent,
            reservation,
            trade_id="RUNTIME-ZERO-FILL-PARTIAL-001",
            sequence=2,
            volume=1,
        )
        callback_fixtures._emit_order_status(
            callback_case,
            intent,
            reservation,
            next(call[2] for call in api.calls if call[0] == "ReqOrderInsert"),
            status="1",
            submit_status="1",
            traded=1,
            sequence=3,
        )
        applied = runtime._execution_owner.pump_callbacks(32)
        assert len(applied) == 2
        trade_facts = resources.values["store"].read_ctp_dispatch_trade_facts(
            case.execution_scope, command_id
        )
        assert len(trade_facts.trades) == 1
        trade = trade_facts.trades[0]
        assert trade.trade_id == "RUNTIME-ZERO-FILL-PARTIAL-001"
        assert trade.trade_quantity == 1
        assert trade.cumulative_trade_quantity == 1
        assert trade.cumulative_commission is None
        assert trade.commission_quality == "INCOMPLETE"

        # The public service query remains a read of Execution state. Runtime
        # reconciliation is the existing callback-backed path that persists
        # the later observation and exercises the bridge hook.
        runtime._next_reconciliation_ns = 0
        with pytest.raises(runtime_fixtures.CtpExecutionRuntimeError) as caught:
            runtime.maintain(frozenset({case.instance_id}))
        assert caught.value.code == "EXECUTION_RECONCILIATION_REVIEW_REQUIRED"
        assert runtime.status.ready is False
        record = resources.values["store"].get(intent_id, scope=case.execution_scope)
        assert record is not None
        assert record.state is ExecutionState.PARTIALLY_FILLED
        assert record.review_required is True
        assert len(hook_calls) == 2
        assert len(query_calls) == 1
        assert chain_fixtures._counts(api) == (1, 1, 0)

        binding, receipt = _risk_resolution(case, bridge, intent_id)
        assert binding == binding_before
        assert receipt == receipt_before
        assert receipt.status is DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        assert receipt.dispatch_latch_active is False
        journal_command, journal_events = _journal_events(case, bridge, intent_id)
        assert journal_command.latest_revision == journal_command_before.latest_revision
        assert journal_events == journal_events_before
        assert journal_command.state == "ACKED_TRACKED"
        assert journal_events[-1].event_type == "ACKED_TRACKED"
        assert case.risk_gate.dispatch_resolution_receipt(binding["permit_id"]) == receipt
        risk_snapshot = case.risk_gate.snapshot(bridge.account_scope)
        assert risk_snapshot == risk_snapshot_before
        assert risk_snapshot["increase_count"] == 1
        assert Decimal(risk_snapshot["increase_notional"]) == Decimal("200")

        # The receipt's account-wide freeze summary is a live projection and
        # can change while the immutable tracked-order claim remains exact.
        # That summary must not break positive-fill review readback, while
        # any claim or proof change must still fail the comparison.
        other_freeze = "sourcefake-other-freeze-receipt-test"
        case.risk_gate.freeze(
            bridge.account_scope, other_freeze, "sourcefake receipt projection test"
        )
        try:
            current_receipt = case.risk_gate.dispatch_resolution_receipt(
                binding["permit_id"]
            )
            assert receipt.has_other_active_freezes is False
            assert current_receipt.has_other_active_freezes is True
            assert _same_dispatch_resolution_receipt(current_receipt, receipt)
            assert bridge.read_tracked_update_review(
                record, writer_lease=runtime._writer_lease
            ).event_type == "manual_review_required"

            changed_claim = replace(
                current_receipt.claim,
                claimed_at=current_receipt.claim.claimed_at + 0.001,
            )
            assert not _same_dispatch_resolution_receipt(
                replace(current_receipt, claim=changed_claim), receipt
            )
            changed_proof = replace(
                current_receipt.proof,
                journal_record_sha256=(
                    "0" * 64
                    if current_receipt.proof.journal_record_sha256 != "0" * 64
                    else "1" * 64
                ),
            )
            assert not _same_dispatch_resolution_receipt(
                replace(
                    current_receipt,
                    proof=changed_proof,
                    proof_sha256=changed_proof.fingerprint,
                ),
                receipt,
            )
        finally:
            case.risk_gate.resolve_freeze(bridge.account_scope, other_freeze)
        assert case.risk_gate.dispatch_resolution_receipt(binding["permit_id"]) == receipt

        component.close()
        case.component = None
        assert runtime.status.closed is True


@pytest.mark.unit
def test_recovery_close_reduces_only_store_proved_fill_and_retains_unknown_fee_risk(
    tmp_path, monkeypatch, request
):
    """Compose flat baseline, tracked OPEN fill, and the restricted CLOSE route.

    All service, Store, Risk, callback owner, and dispatch code is production
    code. Only the CTP API leaf emits deterministic SPI callbacks and query
    rows; this case makes no native/provider/account connection.
    """

    from bt_api_py.service_execution import ServiceExecutionError

    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        parent_id = "ctp-runtime-recovery-parent"
        child_id = "ctp-runtime-recovery-close"
        cancel_provider = chain_fixtures.install_synthetic_cancel_recovery_provider(
            case, monkeypatch
        )
        (
            host,
            component,
            api,
            callback_case,
            _session_binding,
            reserve_errors,
            query_calls,
            recovery_client,
            client_runtime,
            client_transport,
            client_events,
            scope_client,
            scope_transport,
            scope_events,
            close_host_path,
        ) = _prepare_host_runtime_order(
            case, monkeypatch, parent_id, tmp_path, request
        )
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None
        assert runtime._recovery_close_available is True, {
            "source_authority_bound": runtime._recovery_close_source_authority is not None,
            "risk_authority_matches": getattr(
                runtime._order_gate._gate,
                "_recovery_close_source_authority",
                None,
            ) is runtime._recovery_close_source_authority,
            "store_api": {
                name: callable(getattr(resources.values["store"], name, None))
                for name in (
                    "read_ctp_recovery_close_source_facts",
                    "issue_ctp_recovery_close_source_proof",
                    "authorize_ctp_recovery_close_dispatch",
                )
            },
        }
        assert "orders.submit_recovery_close" in component.handlers
        bridge = resources.values["admission"]["order_gate"]
        submit_dispatch_errors = []
        submit_port = resources.values["dispatch"]
        store = resources.values["store"]
        stage_state_observations = []
        original_stage_command = store.stage_ctp_dispatch_command

        def observe_stage_command(*args, **kwargs):
            managed_intent_id = kwargs.get("managed_intent_id")
            if managed_intent_id in {parent_id, child_id}:
                staged_record = store.get(
                    managed_intent_id, scope=case.execution_scope
                )
                stage_state_observations.append(
                    (
                        managed_intent_id,
                        None if staged_record is None else staged_record.state,
                        None
                        if staged_record is None
                        else staged_record.dispatch_attempts,
                        kwargs.get("position_baseline_authorization") is not None,
                        kwargs.get("recovery_close_authorization") is not None,
                    )
                )
            return original_stage_command(*args, **kwargs)

        monkeypatch.setattr(store, "stage_ctp_dispatch_command", observe_stage_command)
        original_submit_with_baseline = submit_port.submit_with_position_baseline_authorized

        def observe_submit_with_baseline(*args, **kwargs):
            try:
                return original_submit_with_baseline(*args, **kwargs)
            except Exception as error:
                code = getattr(error, "code", None)
                submit_dispatch_errors.append(
                    (type(error).__name__, code if type(code) is str else None)
                )
                raise

        monkeypatch.setattr(
            submit_port,
            "submit_with_position_baseline_authorized",
            observe_submit_with_baseline,
        )
        context = chain_fixtures._service_context(case)
        submitted = component.handlers["orders.submit"](
            context, chain_fixtures._order_arguments(parent_id)
        )
        assert reserve_errors == []
        assert submitted["ok"] is True
        assert stage_state_observations == [
            (parent_id, ExecutionState.DISPATCHING, 1, True, False)
        ]
        if submitted["result"]["record"]["state"] != ExecutionState.ACKED.value:
            failed_parent = resources.values["store"].get(
                parent_id, scope=case.execution_scope
            )
            failed_reservation = resources.values["store"].read_ctp_order_identity(
                case.execution_scope, parent_id
            )
            failed_command = None
            if failed_reservation is not None:
                failed_command = resources.values["store"].read_ctp_dispatch_command(
                    case.execution_scope,
                    stable_managed_command_id(
                        "submit", parent_id, failed_reservation.runtime_order_id
                    ),
                )
            raise AssertionError(
                {
                    "record_state": submitted["result"]["record"]["state"],
                    "unknown_reason": (
                        None if failed_parent is None else failed_parent.unknown_reason
                    ),
                    "review_required": (
                        None if failed_parent is None else failed_parent.review_required
                    ),
                    "runtime_phase": runtime.status.phase,
                    "api_call_names": tuple(call[0] for call in api.calls),
                    "dispatch_attempts": (
                        None if failed_parent is None else failed_parent.dispatch_attempts
                    ),
                    "command_status": (
                        None if failed_command is None else failed_command.status
                    ),
                    "command_queued": (
                        None
                        if failed_command is None
                        else failed_command.local_queue_receipt_queued
                    ),
                    "risk_states": tuple(
                        (state.intent.intent_id, state.state)
                        for state in bridge._permits.values()
                    ),
                    "dispatch_errors": tuple(submit_dispatch_errors),
                }
            )

        parent_binding, parent_receipt = _risk_resolution(case, bridge, parent_id)
        assert parent_receipt.status is DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        initial_snapshot = case.risk_gate.snapshot(bridge.account_scope)
        parent_reservation, parent_command_id, _parent_command = chain_fixtures._submit_binding(
            case, parent_id
        )
        parent_intent = resources.values["store"].get_intent(
            parent_id, scope=case.execution_scope
        )
        assert parent_intent is not None
        callback_fixtures = chain_fixtures.callback_fixtures
        callback_fixtures._emit_order_status(
            callback_case,
            parent_intent,
            parent_reservation,
            next(call[2] for call in api.calls if call[0] == "ReqOrderInsert"),
            status="1",
            submit_status="1",
            traded=1,
            sequence=2,
        )
        callback_fixtures._emit_trade(
            callback_case,
            parent_intent,
            parent_reservation,
            trade_id="RUNTIME-RECOVERY-PARENT-001",
            sequence=3,
            volume=1,
        )
        assert len(runtime._execution_owner.pump_callbacks(32)) == 2
        parent_trade_facts = resources.values["store"].read_ctp_dispatch_trade_facts(
            case.execution_scope, parent_command_id
        )
        assert len(parent_trade_facts.trades) == 1
        parent_trade = parent_trade_facts.trades[0]
        canonical_parent_trade = json.dumps(
            json.loads(parent_trade.fact_payload_json),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        parent_trade_source_checks = {
            "row_count": len(parent_trade_facts.trades),
            "source_sequence_positive": (
                type(parent_trade.source_sequence) is int
                and parent_trade.source_sequence > 0
            ),
            "source_sequences_unique": len(
                {
                    item.source_sequence for item in parent_trade_facts.trades
                }
            )
            == len(parent_trade_facts.trades),
            "trade_id_nonempty": bool(parent_trade.trade_id),
            "trade_ids_unique": len(
                {item.trade_id for item in parent_trade_facts.trades}
            )
            == len(parent_trade_facts.trades),
            "trade_volume_positive": (
                type(parent_trade.trade_quantity) is int
                and parent_trade.trade_quantity > 0
            ),
            "fact_json_canonical": (
                canonical_parent_trade == parent_trade.fact_payload_json
            ),
            "fact_digest_matches": hashlib.sha256(
                parent_trade.fact_payload_json.encode("utf-8", errors="strict")
            ).hexdigest()
            == parent_trade.fact_payload_sha256,
            "prefix_consistency_matched": (
                parent_trade.prefix_consistency == "MATCHED"
            ),
            "commission_missing": parent_trade.cumulative_commission is None,
            "commission_quality_incomplete": (
                parent_trade.commission_quality == "INCOMPLETE"
            ),
            "prefix_state": parent_trade.prefix_consistency,
        }
        assert parent_trade.commission_quality == "INCOMPLETE"
        assert parent_trade.cumulative_commission is None

        runtime._next_reconciliation_ns = 0
        with pytest.raises(runtime_fixtures.CtpExecutionRuntimeError) as caught:
            runtime.maintain(frozenset({case.instance_id}))
        assert caught.value.code == "EXECUTION_RECONCILIATION_REVIEW_REQUIRED"
        parent_record = resources.values["store"].get(
            parent_id, scope=case.execution_scope
        )
        assert parent_record is not None
        assert parent_record.state is ExecutionState.PARTIALLY_FILLED
        assert parent_record.review_required is True
        assert parent_record.cumulative_commission is None
        assert runtime.status.ready is False
        assert runtime.recovery_ready() is False  # the parent still has live remainder
        owner_cancel_readiness = component.operation_readiness["owner.cancel"]
        assert owner_cancel_readiness() is True
        assert case.risk_gate.dispatch_resolution_receipt(
            parent_binding["permit_id"]
        ) == parent_receipt
        after_parent_fill = case.risk_gate.snapshot(bridge.account_scope)
        assert after_parent_fill["increase_count"] == initial_snapshot["increase_count"] == 1
        assert Decimal(after_parent_fill["increase_notional"]) == Decimal("200")

        # Cancel only the unfilled remainder. The CTP callback is terminal
        # CANCELLED with the same one verified fill; the service cancellation
        # path must preserve its unknown-fee review latch and full Risk reserve.
        parent_record = resources.values["store"].get(
            parent_id, scope=case.execution_scope
        )
        parent_reservation = resources.values["store"].read_ctp_order_identity(
            case.execution_scope, parent_id
        )
        assert parent_record is not None and parent_reservation is not None
        assert parent_record.provider_order_id
        parent_request_id = next(
            call[2] for call in api.calls if call[0] == "ReqOrderInsert"
        )
        cancel_query_info = SimpleNamespace(
            ErrorID=0, ErrorMsg="synthetic partial-order cancel query"
        )
        cancel_terminal_observed = False

        def req_cancel_query_order(field, request_id):
            api.calls.append(("ReqQryOrder", field, request_id))
            native_case = SimpleNamespace(
                api=api,
                scope=case.execution_scope,
                session_binding=callback_case.session_binding,
            )
            for queried_intent_id in tuple(case.synthetic_query_order_intent_ids):
                queried_intent = resources.values["store"].get_intent(
                    queried_intent_id, scope=case.execution_scope
                )
                queried_reservation = resources.values["store"].read_ctp_order_identity(
                    case.execution_scope, queried_intent_id
                )
                assert queried_intent is not None and queried_reservation is not None
                if queried_intent_id == parent_id:
                    row = chain_fixtures.query_fixtures._native_order(
                        native_case,
                        intent=queried_intent,
                        reservation=queried_reservation,
                        order_status=("5" if cancel_terminal_observed else "1"),
                    )
                    row.VolumeTraded = 1
                    row.VolumeTotal = 1
                else:
                    row = chain_fixtures.query_fixtures._native_order(
                        native_case,
                        intent=queried_intent,
                        reservation=queried_reservation,
                    )
                from bt_api_py.ctp_order_target_query import _order_offset

                row.Direction = (
                    "0" if queried_intent.side.value == "BUY" else "1"
                )
                row.CombOffsetFlag = _order_offset(
                    queried_intent, "SHFE"
                )
                api.spi.OnRspQryOrder(row, cancel_query_info, request_id, False)
            api.spi.OnRspQryOrder(None, cancel_query_info, request_id, True)
            return 0

        def req_cancel_order_action(field, request_id):
            nonlocal cancel_terminal_observed
            api.calls.append(("ReqOrderAction", field, request_id))
            api.spi.OnRspOrderAction(
                field,
                SimpleNamespace(ErrorID=0, ErrorMsg="synthetic cancel ACK"),
                request_id,
                True,
            )
            cancel_terminal_observed = True
            callback_fixtures = chain_fixtures.callback_fixtures
            callback_fixtures._emit_order_status(
                callback_case,
                parent_intent,
                parent_reservation,
                parent_request_id,
                status="5",
                submit_status="3",
                traded=1,
                sequence=4,
            )
            return 0

        monkeypatch.setattr(
            api, "ReqQryOrder", req_cancel_query_order, raising=False
        )
        monkeypatch.setattr(
            api, "ReqOrderAction", req_cancel_order_action, raising=False
        )
        cancel_command_id = "ctp-runtime-recovery-parent-remainder-cancel"
        cancelled = component.handlers["owner.cancel"](
            chain_fixtures._owner_control_context(case),
            {"command_id": cancel_command_id},
        )
        assert cancelled["ok"] is True, cancelled
        assert cancelled["result"]["outcome"] == "APPLIED"
        assert cancelled["result"]["total_count"] == 1
        assert cancelled["result"]["items"][0]["intent_id"] == parent_id
        assert cancelled["result"]["items"][0]["decision"] == "READY"
        assert (
            cancelled["result"]["items"][0]["cancel_state"]
            == ExecutionState.CANCELLED.value
        )
        assert len(cancel_provider.requests) == 1
        parent_record = resources.values["store"].get(
            parent_id, scope=case.execution_scope
        )
        assert parent_record is not None
        assert parent_record.state is ExecutionState.CANCELLED
        assert parent_record.filled_quantity == Decimal("1")
        assert parent_record.review_required is True
        assert parent_record.cumulative_commission is None
        cancel_review_event = bridge.read_tracked_update_review(
            parent_record, writer_lease=runtime._writer_lease
        )
        # The terminal QueryOrder created a fresh exact source/review pair;
        # both rows now bind the current positive-fill CANCELLED projection.
        assert cancel_review_event.state is ExecutionState.CANCELLED
        assert cancel_review_event.sequence == runtime._tracked_fill_review_event_sequence
        assert runtime.status.ready is False
        assert component.operation_readiness["owner.cancel"]() is False

        assert runtime.recovery_ready() is True
        recovery_readiness = component.operation_readiness[
            "orders.submit_recovery_close"
        ]
        host._refresh_registry_readiness()
        assert host._registry.ready is False
        assert runtime.recovery_route_available() is True
        assert host._restricted_operation_ready(
            component,
            recovery_readiness,
            operation="orders.submit_recovery_close",
        ) is True
        after_cancel = case.risk_gate.snapshot(bridge.account_scope)
        assert after_cancel["increase_count"] == 1
        assert Decimal(after_cancel["increase_notional"]) == Decimal("200")

        # The pre-dispatch window now reports exactly the one attributable
        # same-day LONG lot created by the verified parent TradeID.
        position_rows = case.synthetic_position_rows
        position_rows[:] = [
            {
                "BrokerID": "9999",
                "InvestorID": "investor-1",
                "InstrumentID": "rb2610",
                "ExchangeID": "SHFE",
                "PosiDirection": "2",
                "HedgeFlag": "1",
                "PositionDate": "1",
                "TradingDay": "20260926",
                "Position": 1,
                "TodayPosition": 1,
                "YdPosition": 0,
                "LongFrozen": 0,
                "ShortFrozen": 0,
                "LongFrozenAmount": 0.0,
                "ShortFrozenAmount": 0.0,
                "FrozenMargin": 0.0,
                "FrozenCash": 0.0,
                "FrozenCommission": 0.0,
                "CombLongFrozen": 0,
                "CombShortFrozen": 0,
                "StrikeFrozen": 0,
                "StrikeFrozenAmount": 0.0,
                "YdStrikeFrozen": 0,
            }
        ]
        case.synthetic_query_order_intent_ids[:] = [child_id]

        child_insert_fields = []
        child_request_ids = []

        def req_recovery_order_insert(field, request_id):
            api.calls.append(("ReqOrderInsert", field, request_id))
            child_insert_fields.append(field)
            child_request_ids.append(request_id)
            store = resources.values["store"]
            child_intent = store.get_intent(child_id, scope=case.execution_scope)
            child_reservation = store.read_ctp_order_identity(
                case.execution_scope, child_id
            )
            assert child_intent is not None and child_reservation is not None
            assert field.OrderRef == child_reservation.order_ref
            assert field.Direction == "1"  # SELL closes the source-proved LONG lot.
            assert field.CombOffsetFlag == "3"  # CTP CloseToday.
            api.spi.OnRtnOrder(
                SimpleNamespace(
                    BrokerID="9999",
                    InvestorID="investor-1",
                    UserID="investor-1",
                    InstrumentID=child_intent.instrument,
                    RequestID=request_id,
                    OrderRef=child_reservation.order_ref,
                    ExchangeID="SHFE",
                    OrderSysID="SYS" + child_reservation.order_ref,
                    FrontID=callback_case.session_binding.dispatch_front_id,
                    SessionID=callback_case.session_binding.dispatch_session_id,
                    TradingDay=case.execution_scope.trading_day,
                    OrderStatus="3",
                    OrderSubmitStatus="3",
                    VolumeTraded=0,
                    VolumeTotal=1,
                    VolumeTotalOriginal=1,
                    NotifySequence=4,
                    SequenceNo=4,
                    Direction="1",
                    CombOffsetFlag="3",
                    CombHedgeFlag="1",
                    LimitPrice=float(child_intent.price),
                    TimeCondition="3",
                    VolumeCondition="1",
                )
            )
            return 0

        monkeypatch.setattr(
            api, "ReqOrderInsert", req_recovery_order_insert, raising=False
        )
        prepare_errors = []
        original_prepare_recovery_close = submit_port.prepare_recovery_close

        def observe_prepare_recovery_close(**kwargs):
            try:
                return original_prepare_recovery_close(**kwargs)
            except Exception as error:
                frames = traceback.extract_tb(error.__traceback__)
                frame = next(
                    (
                        item
                        for item in reversed(frames)
                        if item.filename.replace("\\", "/").rsplit("/", 1)[-1]
                        in _RECOVERY_VERTICAL_SOURCE_BASENAMES
                    ),
                    None,
                )
                code = getattr(error, "code", None)
                if type(code) is not str or code not in _RECOVERY_VERTICAL_REASON_CODES:
                    code = None
                filename = None if frame is None else frame.filename
                source_basename = (
                    None
                    if filename is None
                    else filename.replace("\\", "/").rsplit("/", 1)[-1]
                )
                prepare_errors.append(
                    {
                        "error_type": type(error).__name__,
                        "error_code": code,
                        "source_basename": source_basename,
                        "source_line": None if frame is None else frame.lineno,
                    }
                )
                raise

        monkeypatch.setattr(
            submit_port,
            "prepare_recovery_close",
            observe_prepare_recovery_close,
        )
        dispatch_boundary_events = []

        def observe_dispatch_boundary(boundary, target, method_name):
            original = getattr(target, method_name)

            def observed(*args, **kwargs):
                if boundary not in _RECOVERY_VERTICAL_BOUNDARY_NAMES:
                    raise AssertionError("unexpected recovery dispatch boundary")
                if any(
                    event["boundary"] == boundary
                    for event in dispatch_boundary_events
                ):
                    return original(*args, **kwargs)
                event = {
                    "boundary": boundary,
                    "entered": True,
                    "returned": False,
                }
                dispatch_boundary_events.append(event)
                try:
                    result = original(*args, **kwargs)
                except Exception as error:
                    frames = traceback.extract_tb(error.__traceback__)
                    frame = next(
                        (
                            item
                            for item in reversed(frames)
                            if item.filename.replace("\\", "/").rsplit("/", 1)[-1]
                            in _RECOVERY_VERTICAL_SOURCE_BASENAMES
                        ),
                        None,
                    )
                    code = getattr(error, "code", None)
                    if type(code) is not str or code not in _RECOVERY_VERTICAL_REASON_CODES:
                        code = None
                    event.update(
                        {
                            "error_type": type(error).__name__,
                            "error_code": code,
                            "source_basename": (
                                None
                                if frame is None
                                else frame.filename.replace("\\", "/").rsplit("/", 1)[-1]
                            ),
                            "source_line": None if frame is None else frame.lineno,
                        }
                    )
                    raise
                event["returned"] = True
                return result

            monkeypatch.setattr(target, method_name, observed)

        owner = submit_port.execution_owner
        for boundary, target, method_name in (
            ("owner_dispatch", owner, "dispatch_submit"),
            ("store_stage", store, "stage_ctp_dispatch_command"),
            ("store_queue_receipt", store, "record_ctp_dispatch_queue_receipt"),
            ("risk_before_native", bridge, "before_native_dispatch"),
            ("store_claim", store, "claim_ctp_dispatch_command_for_session"),
            ("native_lease", owner._trader, "acquire_managed_native_call_lease"),
            ("native_submit", owner._trader, "submit_order_insert_with_lease"),
        ):
            observe_dispatch_boundary(boundary, target, method_name)

        order_ref_reservations = []
        store_authorizations = []
        risk_reservations = []
        original_order_ref_reserve = store.reserve_ctp_order_identity
        original_store_authorize = store.authorize_ctp_recovery_close_dispatch
        original_risk_reserve = case.risk_gate.reserve_recovery_reduction

        def observe_order_ref_reserve(*args, **kwargs):
            order_ref_reservations.append((args, kwargs))
            return original_order_ref_reserve(*args, **kwargs)

        def observe_store_authorize(*args, **kwargs):
            store_authorizations.append((args, kwargs))
            return original_store_authorize(*args, **kwargs)

        def observe_risk_reserve(*args, **kwargs):
            risk_reservations.append((args, kwargs))
            return original_risk_reserve(*args, **kwargs)

        monkeypatch.setattr(store, "reserve_ctp_order_identity", observe_order_ref_reserve)
        monkeypatch.setattr(
            store,
            "authorize_ctp_recovery_close_dispatch",
            observe_store_authorize,
        )
        monkeypatch.setattr(
            case.risk_gate, "reserve_recovery_reduction", observe_risk_reserve
        )

        try:
            child_view = recovery_client.submit_recovery_close(
                target_intent_id=parent_id,
                recovery_intent_id=child_id,
            )
            reply = client_transport.replies[-1]
        finally:
            print(
                "CTP_RECOVERY_DISPATCH_BOUNDARIES "
                + json.dumps(dispatch_boundary_events, sort_keys=True),
                flush=True,
            )
        assert child_view.intent_id == child_id
        assert client_events[-1] == "orders.submit_recovery_close"
        assert host._registry.ready is False
        assert runtime.recovery_ready() is False
        assert runtime.recovery_route_available() is True
        assert host._restricted_operation_ready(
            component,
            recovery_readiness,
            operation="orders.submit_recovery_close",
        ) is True
        assert len(order_ref_reservations) == 1
        assert len(store_authorizations) == 1
        assert len(risk_reservations) == 1
        child_record_after_reply = store.get(
            child_id, scope=case.execution_scope
        )
        child_events_after_reply = tuple(
            event
            for event in store.read_outbox(limit=10_000, scope=case.execution_scope)
            if event.intent_id == child_id
        )
        child_reservation_after_reply = store.read_ctp_order_identity(
            case.execution_scope, child_id
        )
        child_command_after_reply = (
            None
            if child_reservation_after_reply is None
            else store.read_ctp_dispatch_command(
                case.execution_scope,
                stable_managed_command_id(
                    "submit", child_id, child_reservation_after_reply.runtime_order_id
                ),
            )
        )
        latest_child_event = (
            None if not child_events_after_reply else child_events_after_reply[-1]
        )
        latest_event_reason = (
            None
            if latest_child_event is None
            else latest_child_event.payload.get("reason_code")
        )
        if (
            type(latest_event_reason) is not str
            or latest_event_reason not in _RECOVERY_VERTICAL_REASON_CODES
        ):
            latest_event_reason = None
        record_reason = (
            None
            if child_record_after_reply is None
            else child_record_after_reply.unknown_reason
        )
        if (
            type(record_reason) is not str
            or record_reason not in _RECOVERY_VERTICAL_REASON_CODES
        ):
            record_reason = None
        latest_event_type = (
            None if latest_child_event is None else latest_child_event.event_type
        )
        if type(latest_event_type) is not str or latest_event_type not in {
            "intent_admitted",
            "intent_activated",
            "intent_blocked",
            "intent_rejected",
            "dispatch_blocked",
            "dispatch_claimed",
            "manual_review_required",
            "provider_observation",
            "reconciled_observation",
            "unknown",
        }:
            latest_event_type = "OTHER" if latest_child_event is not None else None
        stage_observation_projection = tuple(
            (
                "child" if item[0] == child_id else "parent" if item[0] == parent_id else "other",
                None if item[1] is None else item[1].value,
                item[2],
                item[3],
                item[4],
            )
            for item in stage_state_observations
        )
        child_stage_seen = any(item[0] == child_id for item in stage_state_observations)
        child_risk_states = tuple(
            sorted(
                {
                    state.state
                    for state in bridge._permits.values()
                    if state.intent.intent_id == child_id
                }
            )
        )
        child_insert_count = len(child_insert_fields)
        reply_result = reply.get("result")
        reply_record = (
            reply_result.get("record") if isinstance(reply_result, dict) else None
        )
        diagnostic_success = (
            reply.get("ok") is True
            and child_record_after_reply is not None
            and child_record_after_reply.state is ExecutionState.ACKED
            and child_record_after_reply.dispatch_attempts == 1
            and isinstance(reply_record, dict)
            and reply_record.get("state") == ExecutionState.ACKED.value
            and child_stage_seen
            and child_insert_count == 1
        )
        if not diagnostic_success:
            print(
                "CTP_RECOVERY_VERTICAL_FAILURE "
                + json.dumps(
                    {
                        "reply_ok": reply.get("ok") is True,
                        "reply_code": (
                            reply.get("code")
                            if type(reply.get("code")) is str
                            and reply.get("code") in _RECOVERY_VERTICAL_REASON_CODES
                            else None
                        ),
                        "reply_record_present": isinstance(reply_record, dict),
                        "reply_record_state": (
                            reply_record.get("state")
                            if isinstance(reply_record, dict)
                            and reply_record.get("state")
                            in {state.value for state in ExecutionState}
                            else None
                        ),
                        "reply_record_dispatch_attempts": (
                            reply_record.get("dispatch_attempts")
                            if isinstance(reply_record, dict)
                            and type(reply_record.get("dispatch_attempts")) is int
                            else None
                        ),
                        "child_record_present": child_record_after_reply is not None,
                        "child_state": (
                            None
                            if child_record_after_reply is None
                            else child_record_after_reply.state.value
                        ),
                        "child_dispatch_attempts": (
                            None
                            if child_record_after_reply is None
                            else child_record_after_reply.dispatch_attempts
                        ),
                        "child_review_required": (
                            None
                            if child_record_after_reply is None
                            else child_record_after_reply.review_required
                        ),
                        "child_unknown_reason": record_reason,
                        "child_provider_id_present": (
                            child_record_after_reply is not None
                            and child_record_after_reply.provider_order_id is not None
                        ),
                        "child_permit_reference_present": (
                            child_record_after_reply is not None
                            and child_record_after_reply.permit_reference is not None
                        ),
                        "child_basic_reference_present": (
                            child_record_after_reply is not None
                            and child_record_after_reply.basic_admission_reference is not None
                        ),
                        "child_admission_context_present": (
                            child_record_after_reply is not None
                            and child_record_after_reply.admission_context_sha256 is not None
                        ),
                        "latest_event_type": latest_event_type,
                        "latest_event_state": (
                            None
                            if latest_child_event is None
                            else latest_child_event.state.value
                        ),
                        "latest_event_reason_code": latest_event_reason,
                        "child_risk_states": child_risk_states,
                        "dispatch_boundary_events": tuple(dispatch_boundary_events),
                        "child_command_status": (
                            None
                            if child_command_after_reply is None
                            else child_command_after_reply.status
                        ),
                        "stage_observations": stage_observation_projection,
                        "child_stage_seen": child_stage_seen,
                        "child_insert_count": child_insert_count,
                        "api_call_names": tuple(
                            name for name, *_rest in api.calls if type(name) is str
                        ),
                        "prepare_errors": tuple(prepare_errors),
                        "parent_trade_source_checks": parent_trade_source_checks,
                    },
                    sort_keys=True,
                )
            )
        assert reply["ok"] is True, {
            "reply_code": reply.get("code"),
        }
        assert prepare_errors == []
        assert child_stage_seen, "recovery child did not reach Store dispatch staging"
        assert stage_observation_projection == (
            ("parent", ExecutionState.DISPATCHING.value, 1, True, False),
            ("child", ExecutionState.DISPATCHING.value, 1, False, True),
        ), stage_observation_projection
        assert len(child_insert_fields) == 1
        assert len(child_request_ids) == 1
        child_intent = resources.values["store"].get_intent(
            child_id, scope=case.execution_scope
        )
        assert child_intent is not None
        recovery_context = ExecutionAdmissionContextV1(
            principal_ref=context.principal_ref,
            identity=context.identity,
            wire_scope=context.scope,
            execution_scope=case.execution_scope,
            action=RECOVERY_CLOSE_OPERATION,
            recovery_target_intent_id=parent_id,
        )
        assert _context_action_matches_order(recovery_context, child_intent)
        assert _position_mode_matches_ctp_order(child_intent)
        assert not _position_mode_matches_ctp_order(
            replace(child_intent, side=Side.BUY)
        )
        short_close_intent = replace(
            child_intent,
            side=Side.BUY,
            position_side="SHORT",
        )
        assert _position_mode_matches_ctp_order(short_close_intent)
        assert not _position_mode_matches_ctp_order(
            replace(short_close_intent, side=Side.SELL)
        )
        assert not _context_action_matches_order(
            replace(recovery_context, recovery_target_intent_id=child_id),
            child_intent,
        )
        opening_intent = replace(
            child_intent,
            position_effect=PositionEffect.OPEN,
            position_bucket="ALL",
            reduce_only=False,
        )
        assert not _position_mode_matches_ctp_order(opening_intent)
        assert not _context_action_matches_order(recovery_context, opening_intent)
        approved_target_context = replace(
            recovery_context,
            action="orders.submit_approved_target",
            recovery_target_intent_id=None,
        )
        assert not _context_action_matches_order(approved_target_context, child_intent)
        with pytest.raises(ContractValidationError):
            replace(recovery_context, recovery_target_intent_id=None)
        with pytest.raises(ContractValidationError):
            replace(
                recovery_context,
                action="orders.submit",
                recovery_target_intent_id=parent_id,
            )
        assert child_intent.side.value == "SELL"
        assert child_intent.position_effect.value == "CLOSE_TODAY"
        assert child_intent.position_side == "LONG"
        assert child_intent.position_bucket == "TODAY"
        assert child_intent.quantity == Decimal("1")
        assert child_intent.reduce_only is True
        child_record = resources.values["store"].get(
            child_id, scope=case.execution_scope
        )
        assert child_record is not None
        assert child_record.state is ExecutionState.ACKED
        positions_queries_before_retry = sum(
            call[0] == "ReqQryInvestorPosition" for call in api.calls
        )
        inserts_before_retry = len(child_insert_fields)

        def recovery_side_effects():
            return (
                tuple(api.calls),
                len(order_ref_reservations),
                len(risk_reservations),
                len(store_authorizations),
                len(child_insert_fields),
            )

        dispatched_effects = recovery_side_effects()
        assert dispatched_effects[1:] == (1, 1, 1, 1)

        repeated_view = recovery_client.submit_recovery_close(
            target_intent_id=parent_id,
            recovery_intent_id=child_id,
        )
        repeated = client_transport.replies[-1]
        assert repeated["ok"] is True, repeated
        assert repeated["result"]["record"] == reply["result"]["record"]
        assert repeated_view.intent_id == child_id
        assert len(child_insert_fields) == inserts_before_retry == 1
        assert sum(call[0] == "ReqQryInvestorPosition" for call in api.calls) == (
            positions_queries_before_retry
        )
        assert recovery_side_effects() == dispatched_effects

        # With no in-memory prepared-window cache, the exact persisted
        # admission context still makes this a readback-only retry, with no
        # fresh query or dispatch.
        submit_port._prepared_recovery_sources.clear()
        durable_repeat_view = recovery_client.submit_recovery_close(
            target_intent_id=parent_id,
            recovery_intent_id=child_id,
        )
        durable_repeat = client_transport.replies[-1]
        assert durable_repeat["ok"] is True, durable_repeat
        assert durable_repeat["result"]["record"] == reply["result"]["record"]
        assert durable_repeat_view.intent_id == child_id
        assert len(child_insert_fields) == inserts_before_retry
        assert sum(call[0] == "ReqQryInvestorPosition" for call in api.calls) == (
            positions_queries_before_retry
        )
        assert recovery_side_effects() == dispatched_effects

        def assert_public_route_rejected(client, transport, events, recovery_id):
            before_replies = len(transport.replies)
            with pytest.raises(ServiceExecutionError):
                client.submit_recovery_close(
                    target_intent_id=parent_id,
                    recovery_intent_id=recovery_id,
                )
            assert len(transport.replies) == before_replies + 1
            assert events[-1] == "orders.submit_recovery_close"
            rejected = transport.replies[-1]
            assert rejected["ok"] is False, rejected
            assert rejected["code"] != "POLICY_REQUIREMENT_UNMET", rejected
            assert recovery_side_effects() == dispatched_effects
            return rejected

        # The same active review latch keeps the Registry route available for
        # exact durable replay, but a new child reaches the port's one-use
        # reservation fence before OrderRef allocation or position evidence.
        assert host._registry.ready is False
        assert runtime.recovery_ready() is False
        assert runtime.recovery_route_available() is True
        assert_public_route_rejected(
            recovery_client,
            client_transport,
            client_events,
            "ctp-runtime-recovery-close-different-child",
        )

        original_client_identity = client_runtime.identity
        client_runtime.identity = replace(
            original_client_identity,
            principal_ref="ctp-runtime-recovery-other-principal",
        )
        try:
            assert_public_route_rejected(
                recovery_client,
                client_transport,
                client_events,
                child_id,
            )
        finally:
            client_runtime.identity = original_client_identity

        assert_public_route_rejected(
            scope_client,
            scope_transport,
            scope_events,
            child_id,
        )

        wrong_parent = component.handlers["orders.submit_recovery_close"](
            context,
            {
                "target_intent_id": "ctp-runtime-recovery-other-parent",
                "recovery_intent_id": child_id,
            },
        )
        assert wrong_parent["ok"] is False
        wrong_scope_context = replace(
            context,
            scope=replace(context.scope, run_id=context.scope.run_id + "-other"),
        )
        wrong_scope = component.handlers["orders.submit_recovery_close"](
            wrong_scope_context,
            {
                "target_intent_id": parent_id,
                "recovery_intent_id": child_id,
            },
        )
        assert wrong_scope["ok"] is False
        alternate_principal = "ctp-runtime-recovery-other-principal"
        wrong_principal_identity = replace(
            context.identity, principal_ref=alternate_principal
        )
        wrong_principal_context = replace(
            context,
            identity=wrong_principal_identity,
            principal_ref=alternate_principal,
        )
        wrong_principal = component.handlers["orders.submit_recovery_close"](
            wrong_principal_context,
            {
                "target_intent_id": parent_id,
                "recovery_intent_id": child_id,
            },
        )
        assert wrong_principal["ok"] is False
        assert len(child_insert_fields) == inserts_before_retry
        assert sum(call[0] == "ReqQryInvestorPosition" for call in api.calls) == (
            positions_queries_before_retry
        )
        assert runtime.status.ready is False
        assert runtime.recovery_ready() is False  # the one-use parent window is consumed
        child_binding = bridge.read_dispatch_binding(
            chain_fixtures._PRINCIPAL, child_id
        )
        assert child_binding is not None
        child_receipt = case.risk_gate.dispatch_close_resolution_receipt(
            child_binding["permit_id"]
        )
        assert (
            child_receipt.status
            is DispatchExposureResolutionReceiptStatus.UNRESOLVED_ACTIVE
        )
        assert child_receipt.claim.permit_id == child_binding["permit_id"]
        assert child_receipt.claim.intent_id == child_id
        assert child_receipt.dispatch_latch_active is True
        assert child_receipt.proof_sha256 is None
        assert child_receipt.exposure_proof_sha256 is None
        assert child_receipt.position_effect == "CLOSE"
        assert child_receipt.position_side == "LONG"
        assert child_receipt.position_bucket == "TODAY"
        child_risk_states = tuple(
            state
            for state in bridge._permits.values()
            if state.intent.intent_id == child_id
        )
        assert len(child_risk_states) == 1
        child_risk_state = child_risk_states[0]
        allocation = child_risk_state.recovery_allocation
        source_proof = child_risk_state.recovery_source_proof
        child_risk_intent = child_risk_state.risk_intent
        assert child_risk_state.state == "SETTLED"
        assert child_risk_state.zero_fill_reader is None
        assert child_risk_state.tracked_proof is None
        assert child_risk_state.tracked_receipt is None
        assert allocation is not None
        assert source_proof is not None
        assert allocation.permit == child_risk_state.permit
        assert allocation.recovery_intent_id == child_id
        assert allocation.recovery_intent_hash == child_risk_intent.fingerprint
        assert allocation.parent_intent_id == parent_id
        assert allocation.parent_permit_id == parent_binding["permit_id"]
        assert allocation.source_proof_sha256 == source_proof.fingerprint
        assert allocation.source_evidence_sha256 == source_proof.source_evidence_sha256
        assert allocation.source_revision == source_proof.source_revision
        assert source_proof.parent_intent_id == parent_id
        assert source_proof.parent_permit_id == parent_binding["permit_id"]
        assert source_proof.recovery_intent_id == child_id
        assert source_proof.recovery_intent_hash == child_risk_intent.fingerprint
        assert source_proof.scope == bridge.account_scope
        assert source_proof.book_id == case.execution_scope.key
        assert allocation.quantity == Decimal("1")
        assert allocation.position_side == "LONG"
        assert allocation.position_bucket == "TODAY"
        assert child_risk_intent.action is IntentAction.REDUCE
        assert child_risk_intent.notional == Decimal("0")

        # Complete the reduce-only close with one exact child TradeID. The
        # provider callback has no fee, so both orders remain review-latched
        # and the original full OPEN Risk reservation remains counted.
        child_reservation, child_command_id, _child_command = chain_fixtures._submit_binding(
            case, child_id
        )
        callback_case.trader._spi.OnRtnOrder(
            SimpleNamespace(
                BrokerID="9999",
                InvestorID="investor-1",
                UserID="investor-1",
                InstrumentID=child_intent.instrument,
                RequestID=child_request_ids[0],
                OrderRef=child_reservation.order_ref,
                ExchangeID="SHFE",
                OrderSysID="SYS" + child_reservation.order_ref,
                FrontID=callback_case.session_binding.dispatch_front_id,
                SessionID=callback_case.session_binding.dispatch_session_id,
                TradingDay=case.execution_scope.trading_day,
                OrderStatus="0",
                OrderSubmitStatus="3",
                VolumeTraded=1,
                VolumeTotal=0,
                VolumeTotalOriginal=1,
                NotifySequence=5,
                SequenceNo=5,
                Direction="1",
                CombOffsetFlag="3",
                CombHedgeFlag="1",
                LimitPrice=float(child_intent.price),
                TimeCondition="3",
                VolumeCondition="1",
            )
        )
        callback_case.trader._spi.OnRtnTrade(
            SimpleNamespace(
                BrokerID="9999",
                InvestorID="investor-1",
                UserID="investor-1",
                InstrumentID=child_intent.instrument,
                OrderRef=child_reservation.order_ref,
                ExchangeID="SHFE",
                TradeID="RUNTIME-RECOVERY-CLOSE-001",
                OrderSysID="SYS" + child_reservation.order_ref,
                TradingDay=case.execution_scope.trading_day,
                Direction="1",
                OffsetFlag="3",
                HedgeFlag="1",
                Price=float(child_intent.price),
                Volume=1,
                TradeDate=case.execution_scope.trading_day,
                TradeTime="09:31:01",
                SequenceNo=6,
                BrokerOrderSeq=1,
                TradeSource="0",
            )
        )
        assert len(runtime._execution_owner.pump_callbacks(32)) == 2
        child_observation = runtime._submit_port.query(child_intent)
        assert child_observation is not None
        assert child_observation.filled_quantity == Decimal("1")
        assert child_observation.cumulative_commission is None
        child_record = runtime._reconciliation_facade.record_provider_observation(
            child_observation
        )
        assert child_record.state is ExecutionState.FILLED
        assert child_record.review_required is True
        assert child_record.cumulative_commission is None
        child_trade_facts = resources.values["store"].read_ctp_dispatch_trade_facts(
            case.execution_scope, child_command_id
        )
        assert len(child_trade_facts.trades) == 1
        assert child_trade_facts.trades[0].trade_id == "RUNTIME-RECOVERY-CLOSE-001"
        assert child_trade_facts.trades[0].cumulative_commission is None
        assert child_trade_facts.trades[0].commission_quality == "INCOMPLETE"
        parent_receipt_after_child = case.risk_gate.dispatch_resolution_receipt(
            parent_binding["permit_id"]
        )
        assert parent_receipt_after_child.has_other_active_freezes is True
        assert _same_dispatch_resolution_receipt(
            parent_receipt_after_child, parent_receipt
        )
        final_snapshot = case.risk_gate.snapshot(bridge.account_scope)
        assert final_snapshot["increase_count"] == 1
        assert Decimal(final_snapshot["increase_notional"]) == Decimal("200")
        assert resources.values["store"].get(
            parent_id, scope=case.execution_scope
        ).review_required is True
        assert runtime.status.ready is False

        # A fresh terminal QueryInvestorPosition window confirms that the
        # exact same-day position is flat after this child fill. The source
        # window is immutable and remains linked to the child OrderRef.
        position_rows.clear()
        runtime._submit_port._query_position_evidence_window(
            child_id, runtime._execution_owner
        )
        windows = resources.values["store"].read_ctp_position_evidence_windows(
            case.execution_scope,
            child_id,
            owner_handle=runtime._execution_owner._owner_handle,
            writer_lease=runtime._writer_lease,
        )
        assert len(windows) == 2
        initial_position = windows[0].positions[0]["fields"]["Position"]
        assert initial_position["state"] == "value"
        assert type(initial_position["value"]) is int
        assert initial_position["value"] == 1
        assert windows[1].positions == ()
        assert windows[1].last_callback_sequence > windows[0].last_callback_sequence

        # No retry is authorized: the original OPEN permit is still full and
        # both fills remain fee-incomplete review facts.
        child_receipt_after_fill = case.risk_gate.dispatch_close_resolution_receipt(
            child_binding["permit_id"]
        )
        assert (
            child_receipt_after_fill.status
            is DispatchExposureResolutionReceiptStatus.UNRESOLVED_ACTIVE
        )
        assert child_receipt_after_fill.claim == child_receipt.claim
        assert child_receipt_after_fill.dispatch_latch_active is True
        assert child_receipt_after_fill.proof_sha256 is None
        assert child_receipt_after_fill.exposure_proof_sha256 is None
        assert child_receipt_after_fill.position_effect == "CLOSE"
        assert child_receipt_after_fill.position_side == "LONG"
        assert child_receipt_after_fill.position_bucket == "TODAY"
        assert child_risk_state.recovery_allocation is allocation
        assert child_risk_state.recovery_source_proof is source_proof
        assert child_risk_state.state == "SETTLED"
        assert runtime.recovery_ready() is False
        before_fee_incomplete_replay = recovery_side_effects()
        host._refresh_registry_readiness()
        assert host._registry.ready is False
        assert runtime.recovery_route_available() is True
        assert host._restricted_operation_ready(
            component,
            recovery_readiness,
            operation="orders.submit_recovery_close",
        ) is True
        filled_replay_view = recovery_client.submit_recovery_close(
            target_intent_id=parent_id,
            recovery_intent_id=child_id,
        )
        filled_replay = client_transport.replies[-1]
        assert filled_replay["ok"] is True, filled_replay
        assert filled_replay_view.intent_id == child_id
        assert filled_replay_view.state == ExecutionState.FILLED.value
        assert filled_replay_view.review_required is True
        assert recovery_side_effects() == before_fee_incomplete_replay
        assert [call[0] for call in api.calls].count("ReqOrderInsert") == 2
        # The parent OPEN proof queries order/trade once; cancel preparation
        # queries the parent twice (prepare then refresh). Recovery CLOSE ACK
        # skips the OPEN-only reader, and later readbacks are Store/callback-only.
        assert [call[0] for call in api.calls].count("ReqQryOrder") == 3
        assert [call[0] for call in api.calls].count("ReqOrderAction") == 1
        assert [call[0] for call in api.calls].count("ReqQryTrade") == 1

        close_host_path()
        assert runtime.status.closed is True


@pytest.mark.unit
def test_zero_fill_reader_rejects_equal_copy_before_risk_resolution(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        intent_id = "ctp-runtime-zero-fill-risk-copy-rejected"
        component, api, _callback_case, _session_binding, reserve_errors, query_calls = (
            _prepare_runtime_order(case, monkeypatch, intent_id)
        )
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None
        bridge = resources.values["admission"]["order_gate"]
        hook_calls, hook_errors = _install_hook_counter(bridge, monkeypatch)
        original_issue = CtpZeroFillTrackedOrderReader.issue
        original_readback = CtpZeroFillTrackedOrderReader.readback
        issued = []
        readback_attempts = []

        def issue_equal_copy(reader, managed_intent_id, *, timeout=5.0):
            result = original_issue(reader, managed_intent_id, timeout=timeout)
            if type(result) is CtpZeroFillTrackedOrderEvidenceV1:
                copied = replace(result)
                assert copied == result and copied is not result
                issued.append((result, copied))
                return copied
            return result

        def observe_readback(reader, evidence):
            readback_attempts.append(evidence)
            return original_readback(reader, evidence)

        monkeypatch.setattr(
            CtpZeroFillTrackedOrderReader, "issue", issue_equal_copy, raising=True
        )
        monkeypatch.setattr(
            CtpZeroFillTrackedOrderReader, "readback", observe_readback, raising=True
        )
        submitted = component.handlers["orders.submit"](
            chain_fixtures._service_context(case),
            chain_fixtures._order_arguments(intent_id),
        )
        assert reserve_errors == []
        assert submitted["ok"] is True
        assert issued and len(issued) == 1
        assert len(readback_attempts) == 1
        assert readback_attempts[0] is issued[0][1]
        assert len(hook_calls) == 1
        assert hook_calls[0][0] == intent_id
        assert len(query_calls) == 1
        assert chain_fixtures._counts(api) == (1, 1, 0)

        binding, receipt = _risk_resolution(case, bridge, intent_id)
        assert receipt.status is DispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE
        assert receipt.dispatch_latch_active is True
        journal_command, journal_events = _journal_events(case, bridge, intent_id)
        assert journal_command.state == "DISPATCHING"
        assert "PROOF_READY" not in {event.event_type for event in journal_events}
        assert "ACKED_TRACKED" not in {event.event_type for event in journal_events}
        record = resources.values["store"].get(intent_id, scope=case.execution_scope)
        assert record is not None and record.review_required is True
        assert case.risk_gate.snapshot(bridge.account_scope)["increase_count"] == 1

        component.close()
        case.component = None
        assert runtime.status.closed is True
