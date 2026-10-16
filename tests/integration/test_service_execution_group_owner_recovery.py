"""ServiceHost owner recovery after the strategy process has exited."""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
import sys
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_execution import (
    ExecutionState,
    GroupBudgetReservationState,
    GroupLegOrderReceipt,
    GroupLegPhase,
    GroupOrderSnapshot,
    GroupOrderState,
    GroupReservationPhase,
    SqliteOrderGroupStore,
)
from bt_api_py.service_execution_group_client import GroupOriginRunBindingStore
from tests.integration import test_service_execution_group_local_process as base


_INCOMPLETE_FILLS_HOLD_TRIGGER = "owner_recovery_keep_incomplete_fills"


def _account_host_process_with_synthetic_plugins_only(*args: Any) -> None:
    """Start the Host and retain the armed direct-query handler failure."""

    bt_api_module = import_module("bt_api_py.bt_api")
    original_loader = bt_api_module._ensure_plugins_loaded
    bt_api_module._ensure_plugins_loaded = lambda: None
    try:
        group_module = import_module("bt_api_py.service_execution_group")
    except BaseException:
        bt_api_module._ensure_plugins_loaded = original_loader
        raise
    group_service = group_module.ExecutionGroupOrderEntryService
    original_handle = group_service.handle
    original_failure = group_module._failure
    paths = args[3]
    account_ref = args[4]
    diagnostic_path = Path(paths["owner_diagnostics"])
    operation_state = threading.local()
    diagnostic_lock = threading.Lock()

    def record_first_query_failure(code: str) -> None:
        if getattr(operation_state, "operation", None) != "orders.query_group_leg":
            return
        context = getattr(operation_state, "context", None)
        arguments = getattr(operation_state, "arguments", None)
        if context is None or not isinstance(arguments, Mapping):
            return
        registration_id = getattr(context, "registration_id", None)
        intent_id = arguments.get("intent_id")
        error = sys.exception()
        with diagnostic_lock:
            if not diagnostic_path.is_file():
                return
            armed = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            expected = armed.get("expected_query")
            expected_registration_ids = (
                expected.get("registration_ids")
                if isinstance(expected, Mapping)
                else None
            )
            if (
                not isinstance(expected_registration_ids, list)
                or registration_id not in expected_registration_ids
                or expected.get("intent_id") != intent_id
            ):
                return
            if armed.get("first_owner_failure") is not None:
                return
            chain = []
            seen: set[int] = set()
            current: BaseException | None = error
            while (
                isinstance(current, BaseException)
                and id(current) not in seen
                and len(chain) < 8
            ):
                seen.add(id(current))
                explicit_cause = current.__cause__
                next_exception = explicit_cause or current.__context__
                chain.append(
                    {
                        "type": f"{type(current).__module__}.{type(current).__qualname__}",
                        "args": repr(current.args)[:2000],
                        "message": str(current)[:2000],
                        "suppress_context": current.__suppress_context__,
                        "next_link": (
                            "cause" if explicit_cause is not None else "context"
                        )
                        if next_exception is not None
                        else None,
                    }
                )
                current = next_exception
            armed["first_owner_failure"] = {
                "account_ref": account_ref,
                "operation": "orders.query_group_leg",
                "registration_id": registration_id,
                "intent_id": intent_id,
                "failure_code": code,
                "active_exception_chain": chain or None,
            }
            temporary_path = diagnostic_path.with_name(diagnostic_path.name + ".tmp")
            temporary_path.write_text(
                json.dumps(armed, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary_path.replace(diagnostic_path)

    def handle_with_query_diagnostics(
        self: Any, operation: str, *, context: Any, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        previous = {
            name: getattr(operation_state, name, None)
            for name in ("operation", "context", "arguments")
        }
        operation_state.operation = operation
        operation_state.context = context
        operation_state.arguments = arguments
        try:
            return original_handle(self, operation, context=context, arguments=arguments)
        finally:
            for name, value in previous.items():
                setattr(operation_state, name, value)

    def failure_with_query_diagnostics(code: str) -> Mapping[str, object]:
        try:
            record_first_query_failure(code)
        except BaseException:
            # Diagnostics must never change the Host's business response.
            pass
        return original_failure(code)

    group_service.handle = handle_with_query_diagnostics
    group_module._failure = failure_with_query_diagnostics
    try:
        base._account_host_process(*args)
    finally:
        group_module._failure = original_failure
        group_service.handle = original_handle
        bt_api_module._ensure_plugins_loaded = original_loader


def _strategy_submit_and_exit(
    control,
    endpoint: str,
    server_public: bytes,
    client_public: bytes,
    client_secret: bytes,
    request: dict[str, Any],
) -> None:
    """Submit once, close the client runtime normally, then exit the process."""

    runtime = None
    try:
        strategy_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT, base._STRATEGY_PRINCIPAL, base._STRATEGY
        )
        coordinator_identity = WireIdentity(
            WireRole.SERVICE, base._COORDINATOR_PRINCIPAL, base._COORDINATOR_SERVICE_ID
        )
        runtime = base.ServiceClientRuntime(
            base.parse_client_config(
                base._strategy_client_config(endpoint, "strategy-client-security-v1")
            ),
            identity=strategy_identity,
            scope_resolver=lambda _scope_ref: base._stable_strategy_scope(),
            peer_resolver=lambda _service_id: coordinator_identity,
            transport_factory=base.ZmqTransportFactory(
                {
                    "strategy-client-security-v1": base.ClientCurveKeys(
                        server_key=server_public,
                        public_key=client_public,
                        secret_key=client_secret,
                    )
                }
            ),
            run_id=base._RUN,
            requested_operations={
                "broker.order_groups": frozenset({"groups.submit", "groups.query"})
            },
        )
        receipt = runtime.start()
        if receipt.degraded_count != 0 or len(receipt.bindings) != 1:
            raise AssertionError("strategy runtime did not register one healthy group route")
        reply = runtime.call(
            "broker.order_groups",
            "groups.submit",
            request,
            portfolio_ref=base._PORTFOLIO,
        )
        if reply.get("ok") is not True or reply.get("code") != "OK":
            raise AssertionError("group submit was not accepted")
        result = reply.get("result")
        if not isinstance(result, Mapping) or result.get("found") is not True:
            raise AssertionError("group submit omitted its accepted snapshot")
        snapshot = GroupOrderSnapshot.from_payload(
            base._plain_runtime_json(result.get("snapshot"))
        )
        runtime.close()
        if runtime.state != "closed" or runtime.unregister_uncertainties != 0:
            raise AssertionError("strategy runtime did not close its registration normally")
        control.send(("submitted-and-closed", snapshot.state.value, snapshot.revision))
    except BaseException as error:
        with suppress(Exception):
            control.send(("error", type(error).__name__))
        raise
    finally:
        if runtime is not None and runtime.state != "closed":
            with suppress(BaseException):
                runtime.close()
        control.close()


def _status(control) -> tuple[Any, ...]:
    control.send(("status",))
    result = base._receive(control)
    assert result[0] == "status", result
    return result


def _read_owner_handler_diagnostic(path: str) -> object:
    diagnostic_path = Path(path)
    if not diagnostic_path.is_file():
        return None
    try:
        return json.loads(diagnostic_path.read_text(encoding="utf-8"))
    except BaseException as error:
        return {
            "diagnostic_read_exception": {
                "type": f"{type(error).__module__}.{type(error).__qualname__}",
                "args": repr(error.args)[:2000],
            }
        }


def _arm_owner_handler_diagnostic(
    path: str, *, registration_id: str, intent_id: str
) -> None:
    diagnostic_path = Path(path)
    temporary_path = diagnostic_path.with_name(diagnostic_path.name + ".tmp")
    temporary_path.write_text(
        json.dumps(
            {
                "expected_query": {
                    "registration_ids": [registration_id],
                    "intent_id": intent_id,
                },
                "first_owner_failure": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary_path.replace(diagnostic_path)


def _include_owner_handler_diagnostic_registration(
    path: str,
    *,
    registration_id: str,
    owner_registration_id: str,
    intent_id: str,
) -> None:
    """Add one live read registration without clearing an earlier failure."""

    diagnostic_path = Path(path)
    current = _read_owner_handler_diagnostic(path)
    if not isinstance(current, Mapping):
        raise AssertionError("owner handler diagnostic was not armed before query")
    expected = current.get("expected_query")
    if (
        not isinstance(expected, Mapping)
        or expected.get("intent_id") != intent_id
        or not isinstance(expected.get("registration_ids"), list)
    ):
        raise AssertionError("owner handler diagnostic has a different query binding")
    registration_ids = list(expected["registration_ids"])
    if owner_registration_id not in registration_ids:
        raise AssertionError("owner read registration was not armed for diagnostics")
    if registration_id not in registration_ids:
        registration_ids.append(registration_id)
    current["expected_query"] = {
        "registration_ids": registration_ids,
        "intent_id": intent_id,
    }
    temporary_path = diagnostic_path.with_name(diagnostic_path.name + ".tmp")
    temporary_path.write_text(
        json.dumps(current, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary_path.replace(diagnostic_path)


def _query_existing_intent_through_real_read_route(
    *,
    coordinator_raw: dict[str, Any],
    account_configs: dict[str, dict[str, Any]],
    account_server_keys: dict[str, tuple[bytes, bytes]],
    coordinator_client_public: bytes,
    coordinator_client_secret: bytes,
    account_ref: str,
    intent_id: str,
    owner_query_registration_id: str,
    diagnostic_path: str,
) -> tuple[str, GroupLegOrderReceipt, object]:
    """Call the actual read-only Registry route with a distinct registration."""

    query_ref = base._route_refs(account_ref)[2]
    service_id = account_configs[account_ref]["service_id"]
    configured_execution = coordinator_raw["clients"]["execution"]
    query_binding = next(
        binding
        for binding in configured_execution["settings"]["bindings"]
        if binding["mount"] == "upstream.order_query"
        and binding["account_ref"] == account_ref
    )
    profile_ref = query_binding["transport_ref"]
    security_ref = coordinator_raw["communication"]["profiles"][profile_ref][
        "security_profile_ref"
    ]
    query_service_raw = dict(coordinator_raw)
    query_service_raw["clients"] = {
        "execution": {
            "config_schema_version": configured_execution["config_schema_version"],
            "settings": {
                "bindings": [dict(query_binding)],
                "options": dict(configured_execution["settings"]["options"]),
            },
        }
    }
    peer = WireIdentity(
        WireRole.SERVICE,
        f"group-owner-service-principal-{account_ref}",
        service_id,
    )
    scope = base._owner_scope(account_ref)
    security_profile = base.ClientCurveKeys(
        server_key=account_server_keys[account_ref][0],
        public_key=coordinator_client_public,
        secret_key=coordinator_client_secret,
    )
    runtime = base.ServiceClientRuntime(
        base.parse_service_config(query_service_raw),
        identity=WireIdentity(
            WireRole.SERVICE,
            base._COORDINATOR_PRINCIPAL,
            base._COORDINATOR_SERVICE_ID,
        ),
        scope_resolver=lambda scope_ref: scope if scope_ref == query_ref else None,
        peer_resolver=lambda requested_service: peer if requested_service == service_id else None,
        transport_factory=base.ZmqTransportFactory({security_ref: security_profile}),
        requested_operations={
            "upstream.order_query": frozenset({"orders.query_group_leg"})
        },
        run_id=base._RUN,
    )
    try:
        started = runtime.start()
        if started.degraded_count != 0:
            raise AssertionError("read-only route registration degraded")
        matches = tuple(
            binding
            for binding in runtime.bindings
            if binding.active
            and binding.mount == "upstream.order_query"
            and binding.scope.account_ref == account_ref
            and binding.scope_ref == query_ref
            and "orders.query_group_leg" in binding.operations
        )
        if len(matches) != 1 or len(runtime.bindings) != 1:
            raise AssertionError("one exact read-only query registration is required")
        _include_owner_handler_diagnostic_registration(
            diagnostic_path,
            registration_id=matches[0].registration_id,
            owner_registration_id=owner_query_registration_id,
            intent_id=intent_id,
        )
        try:
            reply = runtime.call(
                "upstream.order_query",
                "orders.query_group_leg",
                {"origin_run_id": base._RUN, "intent_id": intent_id},
                account_ref=account_ref,
                portfolio_ref=base._PORTFOLIO,
                scope_ref=query_ref,
                max_reply_message_bytes=base._MAX_MESSAGE_BYTES,
            )
        except BaseException as error:
            owner_diagnostic = _read_owner_handler_diagnostic(diagnostic_path)
            raise AssertionError(
                "read-only Registry route raised before returning a reply: "
                f"exception={type(error).__module__}.{type(error).__qualname__} "
                f"args={error.args!r}; first_owner_failure={owner_diagnostic!r}"
            ) from error
        owner_diagnostic = _read_owner_handler_diagnostic(diagnostic_path)
        try:
            complete_reply = (
                base._plain_runtime_json(reply)
                if isinstance(reply, Mapping)
                else {"unprojected_reply_repr": repr(reply)}
            )
        except BaseException as error:
            complete_reply = {
                "unprojected_reply_repr": repr(reply),
                "reply_projection_exception": {
                    "type": f"{type(error).__module__}.{type(error).__qualname__}",
                    "args": repr(error.args)[:2000],
                },
            }
        # ServiceClientRuntime.call strips the outer transport envelope. This
        # group-owner handler has its own business envelope, whose result is the
        # GroupLegOrderReceipt payload.
        if not isinstance(reply, Mapping) or set(reply) != {"ok", "code", "result"}:
            raise AssertionError(
                "read-only Registry route returned an invalid group reply: "
                f"reply={complete_reply!r}; first_owner_failure={owner_diagnostic!r}"
            )
        if reply["ok"] is not True or reply["code"] != "OK":
            raise AssertionError(
                "read-only Registry route returned a business failure: "
                f"code={reply['code']!r}; reply={complete_reply!r}; "
                f"first_owner_failure={owner_diagnostic!r}"
            )
        body = reply["result"]
        if not isinstance(body, Mapping):
            raise AssertionError(
                "read-only Registry route omitted its receipt payload: "
                f"reply={complete_reply!r}; first_owner_failure={owner_diagnostic!r}"
            )
        try:
            receipt = GroupLegOrderReceipt.from_payload(base._plain_runtime_json(body))
        except BaseException as error:
            raise AssertionError(
                "read-only Registry route returned an invalid GroupLegOrderReceipt: "
                f"reply={complete_reply!r}; first_owner_failure={owner_diagnostic!r}; "
                f"parse_exception={type(error).__module__}.{type(error).__qualname__} "
                f"args={error.args!r}"
            ) from error
        return matches[0].registration_id, receipt, complete_reply
    finally:
        active_error = sys.exc_info()[1]
        cleanup_failures: list[BaseException] = []
        if runtime.state != "closed":
            try:
                runtime.close()
            except BaseException as error:
                cleanup_failures.append(error)
        if runtime.state != "closed" or runtime.unregister_uncertainties != 0:
            cleanup_failures.append(
                AssertionError(
                    "read-only query route did not close normally: "
                    f"state={runtime.state!r}; "
                    f"unregister_uncertainties={runtime.unregister_uncertainties}"
                )
            )
        if cleanup_failures:
            cleanup_message = "read-only query route cleanup failed: " + "; ".join(
                f"{type(error).__module__}.{type(error).__qualname__}: {error}"
                for error in cleanup_failures
            )
            if active_error is not None:
                active_error.add_note(cleanup_message)
            else:
                raise AssertionError(cleanup_message) from cleanup_failures[0]


def _provider_client_order_id(
    proof_path: str,
    *,
    account_ref: str,
    group_id: str,
    leg_id: str,
    intent_id: str,
) -> str:
    """Read the pre-dispatch provider ID from its verified durable request."""

    account_scope_key = base.AccountScope(
        "fixture", f"provider-{account_ref}", "simulation"
    ).key
    journal = base.GroupProofJournal(
        proof_path, writer_id=f"dim09-owner-read-{account_ref}"
    )
    try:
        events = journal.events(account_scope_key, intent_id)
    finally:
        journal.close()

    started = tuple(event for event in events if event.event_type == "ATTEMPT_STARTED")
    assert len(started) == 1, "the durable group request must have one dispatch attempt"
    event = started[0]
    assert (event.account_scope_key, event.intent_id, event.group_id, event.leg_id) == (
        account_scope_key,
        intent_id,
        group_id,
        leg_id,
    )
    payload = event.payload
    assert payload.get("account_scope_key") == account_scope_key
    assert payload.get("intent_id") == intent_id
    assert payload.get("group_id") == group_id
    assert payload.get("leg_id") == leg_id
    request = payload.get("provider_request")
    assert isinstance(request, Mapping)
    client_order_id = request.get("client_order_id")
    assert type(client_order_id) is str
    assert payload.get("client_order_id") == client_order_id
    assert request.get("idempotency_key") == client_order_id
    assert request.get("account_id") == f"provider-{account_ref}"
    assert client_order_id.startswith("grp-") and len(client_order_id) == 44
    assert client_order_id != intent_id
    return client_order_id


def _mark_provider_order_terminal(
    provider_path: str, client_order_id: str, outcome: str
) -> None:
    """Publish one terminal or contradictory source fact while reads stay incomplete."""

    with sqlite3.connect(provider_path) as connection:
        found = connection.execute(
            "SELECT order_json FROM provider_orders WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
        assert found is not None, "the first-leg provider order was not recorded"
        order = json.loads(found[0])
        assert order["client_order_id"] == client_order_id
        assert order["status"] == "open"
        assert order["filled"] == "0"
        revision = order["order_revision"] + 1
        if outcome == "filled":
            order["status"] = "filled"
            order["filled"] = order["quantity"]
            order["avg_price"] = order["price"]
        elif outcome == "rejected":
            order["status"] = "rejected"
            order["filled"] = "0"
            order.pop("avg_price", None)
        elif outcome == "cancelled_no_fill":
            order["status"] = "canceled"
            order["filled"] = "0"
            order.pop("avg_price", None)
        elif outcome == "source_mismatch":
            # A filled order without fill rows must not become a Risk terminal
            # proof or release the original dispatch hold.
            order["status"] = "filled"
            order["filled"] = order["quantity"]
            order["avg_price"] = order["price"]
        elif outcome == "cancelled_full":
            # Matching full fill rows do not make a canceled full fill valid.
            order["status"] = "canceled"
            order["filled"] = order["quantity"]
            order["avg_price"] = order["price"]
        else:
            raise AssertionError(f"unsupported synthetic provider outcome: {outcome}")
        order["order_revision"] = revision
        fills = []
        if outcome in {"filled", "cancelled_full"}:
            fills.append(
                base._V2SyntheticBtApiFeed._fill(
                    account_id=order["account_id"],
                    client_order_id=client_order_id,
                    order_id=order["order_id"],
                    symbol=order["symbol"],
                    side=order["side"],
                    trade_id=f"fill-{client_order_id}-{revision}",
                    quantity=order["quantity"],
                    price=order["price"],
                    revision=revision,
                    fee="-0.05",
                )
            )
        connection.execute(
            "UPDATE provider_orders SET order_json=?, fills_json=? WHERE client_order_id=?",
            (
                json.dumps(order, sort_keys=True, separators=(",", ":")),
                json.dumps(fills, sort_keys=True, separators=(",", ":")),
                client_order_id,
            ),
        )
        connection.execute(
            """
            CREATE TRIGGER owner_recovery_keep_incomplete_fills
            AFTER DELETE ON provider_faults
            WHEN OLD.name='incomplete_next_fills'
            BEGIN
                INSERT OR IGNORE INTO provider_faults(name)
                VALUES ('incomplete_next_fills');
            END
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO provider_faults(name) VALUES ('incomplete_next_fills')"
        )


def _release_incomplete_fills(provider_path: str) -> None:
    """Release the test-local persistent UNKNOWN condition after parent checks."""

    with sqlite3.connect(provider_path) as connection:
        held = connection.execute(
            "SELECT 1 FROM provider_faults WHERE name='incomplete_next_fills'"
        ).fetchone()
        assert held is not None, "incomplete fill evidence was not held until parent release"
        connection.execute(
            f"DROP TRIGGER IF EXISTS {_INCOMPLETE_FILLS_HOLD_TRIGGER}"
        )
        connection.execute(
            "DELETE FROM provider_faults WHERE name='incomplete_next_fills'"
        )


def _verified_proof_events(proof_path: str, *, account_ref: str, intent_id: str):
    """Read events through the hash-verifying journal API, not raw SQLite rows."""

    scope = base.AccountScope("fixture", f"provider-{account_ref}", "simulation")
    journal = base.GroupProofJournal(
        proof_path, writer_id=f"dim09-owner-events-{account_ref}"
    )
    try:
        return journal.events(scope.key, intent_id)
    finally:
        journal.close()


def _run_owner_recovery_case(
    tmp_path: Path,
    provider_outcome: str,
    expected_execution_state: ExecutionState,
    expected_group_state: GroupOrderState,
    proof_kind: str | None,
    *,
    explicit_query_probe: bool = False,
) -> None:
    """The Host owner reads the original ID after deadline without new writes."""

    assert _account_host_process_with_synthetic_plugins_only.__module__ == (
        "tests.integration.test_service_execution_group_owner_recovery"
    )

    metadata_reference_ns = time.time_ns()
    account_paths = {
        account: {
            name: str(tmp_path / f"{account}-{name}.sqlite3")
            for name in (
                "registry",
                "provider",
                "execution",
                "risk_order_journal",
                "proof",
                "risk_gate",
                "accepted",
                "group_authority",
            )
        }
        for account in base._ACCOUNTS
    }
    for account in base._ACCOUNTS:
        account_paths[account]["owner_diagnostics"] = str(
            tmp_path / f"{account}-owner-diagnostics.json"
        )
    coordinator_paths = {
        "registry": str(tmp_path / "coordinator-registry.sqlite3"),
        "groups": str(tmp_path / "coordinator-groups.sqlite3"),
        "run_bindings": str(tmp_path / "coordinator-run-bindings.sqlite3"),
    }
    account_endpoints = {account: base._endpoint() for account in base._ACCOUNTS}
    account_server_keys = {account: base.zmq.curve_keypair() for account in base._ACCOUNTS}
    coordinator_server_public, coordinator_server_secret = base.zmq.curve_keypair()
    coordinator_client_public, coordinator_client_secret = base.zmq.curve_keypair()
    strategy_client_public, strategy_client_secret = base.zmq.curve_keypair()
    account_configs = {
        account: base._account_service_config(
            account,
            account_endpoints[account],
            f"account-server-security-{account}",
        )
        for account in base._ACCOUNTS
    }
    coordinator_endpoint = base._endpoint()
    coordinator_raw = base._coordinator_service_config(
        coordinator_endpoint,
        "coordinator-server-security-v1",
        account_configs,
        account_endpoints,
    )
    context = multiprocessing.get_context("spawn")
    account_processes: dict[str, Any] = {}
    account_controls: dict[str, Any] = {}
    coordinator_process = None
    coordinator_control = None
    strategy_process = None
    strategy_control = None
    group_store = None
    bindings_store = None

    request = base._group_request("group-owner-recovery-01", metadata_reference_ns)
    first_intent_id = request["legs"][0]["intent"]["intent_id"]
    second_intent_id = request["legs"][1]["intent"]["intent_id"]

    try:
        for account_ref in base._ACCOUNTS:
            parent_control, child_control = context.Pipe(duplex=True)
            process = context.Process(
                name=f"group-account-host-{account_ref}",
                target=_account_host_process_with_synthetic_plugins_only,
                args=(
                    child_control,
                    account_configs[account_ref],
                    account_paths[account_ref]["registry"],
                    account_paths[account_ref],
                    account_ref,
                    account_server_keys[account_ref][0],
                    account_server_keys[account_ref][1],
                    coordinator_client_public,
                    metadata_reference_ns,
                    ("open",) if account_ref == "account-a" else ("filled",),
                ),
            )
            process.start()
            child_control.close()
            account_processes[account_ref] = process
            account_controls[account_ref] = parent_control
            startup = base._receive(parent_control)
            assert startup[0] == "ready", startup
            assert startup[1] == account_endpoints[account_ref]

        coordinator_parent, coordinator_child = context.Pipe(duplex=True)
        coordinator_process = context.Process(
            name="group-coordinator-host",
            target=base._coordinator_host_process,
            args=(
                coordinator_child,
                coordinator_raw,
                account_configs,
                coordinator_paths["registry"],
                coordinator_paths["groups"],
                coordinator_paths["run_bindings"],
                account_endpoints,
                {account: account_server_keys[account][0] for account in base._ACCOUNTS},
                coordinator_server_public,
                coordinator_server_secret,
                coordinator_client_public,
                coordinator_client_secret,
                strategy_client_public,
                metadata_reference_ns,
            ),
        )
        coordinator_process.start()
        coordinator_child.close()
        coordinator_control = coordinator_parent
        coordinator_ready = base._receive(coordinator_control)
        assert coordinator_ready[0] == "ready", coordinator_ready
        assert coordinator_ready[1] == coordinator_endpoint
        upstream_bindings = coordinator_ready[2]
        order_registration_ids = {
            account: next(
                registration_id
                for mount, bound_account, _scope_ref, registration_id, _ops in upstream_bindings
                if mount == "upstream.orders" and bound_account == account
            )
            for account in base._ACCOUNTS
        }
        query_registration_ids = {
            account: next(
                registration_id
                for mount, bound_account, _scope_ref, registration_id, _ops in upstream_bindings
                if mount == "upstream.order_query" and bound_account == account
            )
            for account in base._ACCOUNTS
        }
        assert all(
            order_registration_ids[account] != query_registration_ids[account]
            for account in base._ACCOUNTS
        ), "Registry entry and read ports must use distinct registration identities"
        _arm_owner_handler_diagnostic(
            account_paths["account-a"]["owner_diagnostics"],
            registration_id=query_registration_ids["account-a"],
            intent_id=first_intent_id,
        )
        for account_ref in base._ACCOUNTS:
            control = account_controls[account_ref]
            control.send(("activate", order_registration_ids[account_ref]))
            activation = base._receive(control)
            assert activation[0] == "activated", activation
            assert activation[1] == order_registration_ids[account_ref]

        # Start the accepted intent with an otherwise valid short original
        # deadline, after all Hosts and registrations are ready. The owner will
        # retain the exact accepted request while the original fill read is
        # held UNKNOWN; recovery must still query that source ID after expiry,
        # while the coordinator's new-submit gate prevents the hedge.
        accepted_deadline_ns = time.time_ns() + 15_000_000_000
        request["deadline_ns"] = accepted_deadline_ns
        for leg in request["legs"]:
            leg["intent"]["deadline_ns"] = accepted_deadline_ns

        strategy_parent, strategy_child = context.Pipe(duplex=True)
        strategy_process = context.Process(
            name="group-strategy-client",
            target=_strategy_submit_and_exit,
            args=(
                strategy_child,
                coordinator_endpoint,
                coordinator_server_public,
                strategy_client_public,
                strategy_client_secret,
                request,
            ),
        )
        strategy_process.start()
        strategy_child.close()
        strategy_control = strategy_parent
        submitted = base._receive(strategy_control, timeout=30)
        strategy_process.join(15)
        assert not strategy_process.is_alive(), "strategy client did not exit after normal close"
        assert strategy_process.exitcode == 0
        assert submitted[0] == "submitted-and-closed", submitted
        assert submitted[1] in {
            GroupOrderState.FIRST_PENDING.value,
            GroupOrderState.RECOVERY_REQUIRED.value,
        }

        # No strategy process or runtime remains to replay submit/query. The
        # durable request and its original Registry-derived run binding are
        # the only continuation authority available to the Host owner.
        strategy_control.close()
        strategy_control = None
        strategy_process.close()
        strategy_process = None
        bindings_store = GroupOriginRunBindingStore(coordinator_paths["run_bindings"])
        stored_binding = bindings_store.get(base._PORTFOLIO, request["group_id"])
        assert stored_binding is not None
        assert stored_binding.origin_run_id == base._RUN
        assert stored_binding.request.deadline_ns == request["deadline_ns"]
        assert stored_binding.request_fingerprint == stored_binding.request.fingerprint
        assert stored_binding.strategy_principal_ref == base._STRATEGY_PRINCIPAL
        assert dict(stored_binding.strategy_identity) == {
            "role": WireRole.STRATEGY_CLIENT.value,
            "principal_ref": base._STRATEGY_PRINCIPAL,
            "actor_ref": base._STRATEGY,
            "delegate_ref": None,
        }
        assert stored_binding.instance_id == base._COORDINATOR_INSTANCE_ID
        assert stored_binding.scope_ref == base._COORDINATOR_SCOPE_REF
        assert stored_binding.registration_id
        assert stored_binding.service_epoch
        assert stored_binding.policy_version
        assert stored_binding.requirements_version
        bindings_store.close()
        bindings_store = None

        group_store = SqliteOrderGroupStore(coordinator_paths["groups"])
        accepted = group_store.get(base._PORTFOLIO, request["group_id"])
        assert accepted is not None
        assert accepted.state in {
            GroupOrderState.FIRST_PENDING,
            GroupOrderState.RECOVERY_REQUIRED,
        }
        assert accepted.request.deadline_ns == request["deadline_ns"]
        assert accepted.legs[0].submitted_intent is not None
        assert accepted.legs[0].submitted_intent.intent_id == first_intent_id
        assert accepted.legs[1].submitted_intent is None
        assert accepted.budget_reservation is not None
        assert accepted.budget_reservation.state not in {
            GroupBudgetReservationState.RELEASED,
            GroupBudgetReservationState.DENIED,
        }
        assert all(
            item.phase not in {GroupReservationPhase.RELEASED, GroupReservationPhase.DENIED}
            for item in accepted.reservations
        )

        first_provider_client_order_id = _provider_client_order_id(
            account_paths["account-a"]["proof"],
            account_ref="account-a",
            group_id=request["group_id"],
            leg_id=request["legs"][0]["leg_id"],
            intent_id=first_intent_id,
        )
        status_a = _status(account_controls["account-a"])
        status_b = _status(account_controls["account-b"])
        assert status_a[1] == (first_provider_client_order_id,)
        assert status_b[1] == ()
        assert tuple(item.intent_id for item in status_a[3]) == (first_intent_id,)
        assert status_b[3] == ()
        pre_fill_queries = status_a[2]["order_queries"]

        # Publish the selected source outcome and hold every fill read incomplete.
        # The provider fixture normally consumes this fault on its first read;
        # this test-local SQLite trigger restores it until the parent explicitly
        # releases it after checking UNKNOWN, the original hold, and no hedge.
        _mark_provider_order_terminal(
            account_paths["account-a"]["provider"],
            first_provider_client_order_id,
            provider_outcome,
        )
        if explicit_query_probe:
            probe_registration_id, probe_receipt, probe_reply = (
                _query_existing_intent_through_real_read_route(
                    coordinator_raw=coordinator_raw,
                    account_configs=account_configs,
                    account_server_keys=account_server_keys,
                    coordinator_client_public=coordinator_client_public,
                    coordinator_client_secret=coordinator_client_secret,
                    account_ref="account-a",
                    intent_id=first_intent_id,
                    owner_query_registration_id=query_registration_ids["account-a"],
                    diagnostic_path=account_paths["account-a"]["owner_diagnostics"],
                )
            )
            owner_diagnostic = _read_owner_handler_diagnostic(
                account_paths["account-a"]["owner_diagnostics"]
            )
            assert isinstance(owner_diagnostic, Mapping)
            assert owner_diagnostic.get("first_owner_failure") is None, (
                "owner query handler failed before the direct read returned: "
                f"{owner_diagnostic!r}"
            )
            assert probe_registration_id != query_registration_ids["account-a"], (
                f"distinct read registration reused the coordinator query ID; reply={probe_reply!r}"
            )
            assert probe_registration_id != order_registration_ids["account-a"], (
                f"distinct read registration reused the owner write ID; reply={probe_reply!r}"
            )
            assert probe_receipt.status.intent_id == first_intent_id, (
                f"query receipt changed the original intent; reply={probe_reply!r}"
            )
            assert probe_receipt.status.state is ExecutionState.UNKNOWN, (
                f"query receipt was not UNKNOWN before fill release; reply={probe_reply!r}"
            )
            assert probe_receipt.resolution is None, (
                f"query route resolved before complete source facts; reply={probe_reply!r}"
            )
        deadline = time.monotonic() + 30
        uncertain = None
        while time.monotonic() < deadline:
            uncertain = group_store.get(base._PORTFOLIO, request["group_id"])
            if (
                uncertain is not None
                and uncertain.state is GroupOrderState.RECOVERY_REQUIRED
                and uncertain.legs[0].phase is GroupLegPhase.UNKNOWN
            ):
                break
            time.sleep(0.05)
        assert uncertain is not None
        assert uncertain.state is GroupOrderState.RECOVERY_REQUIRED
        assert uncertain.legs[0].phase is GroupLegPhase.UNKNOWN
        assert uncertain.legs[0].submitted_intent is not None
        assert uncertain.legs[0].submitted_intent.intent_id == first_intent_id
        assert uncertain.budget_reservation == accepted.budget_reservation
        assert all(
            item.phase not in {GroupReservationPhase.RELEASED, GroupReservationPhase.DENIED}
            for item in uncertain.reservations
        )
        still_one_write = _status(account_controls["account-a"])
        assert still_one_write[1] == (first_provider_client_order_id,)
        assert tuple(item.intent_id for item in still_one_write[3]) == (first_intent_id,)
        still_no_hedge = _status(account_controls["account-b"])
        assert still_no_hedge[1] == ()
        with sqlite3.connect(account_paths["account-a"]["provider"]) as connection:
            held_fault = connection.execute(
                "SELECT 1 FROM provider_faults WHERE name='incomplete_next_fills'"
            ).fetchone()
        assert held_fault is not None, "owner consumed the persistent incomplete-fill hold"
        owner_diagnostic = _read_owner_handler_diagnostic(
            account_paths["account-a"]["owner_diagnostics"]
        )
        assert isinstance(owner_diagnostic, Mapping)
        assert owner_diagnostic.get("first_owner_failure") is None, (
            "owner query handler failed while incomplete fills were held: "
            f"{owner_diagnostic!r}"
        )
        before_release_queries = still_one_write[2]["order_queries"]
        before_release_fill_queries = still_one_write[2]["fill_queries"]

        # Let the immutable, already-accepted transaction deadline expire while
        # its source read remains UNKNOWN. This does not create a new group
        # authorization or extend the original deadline.
        remaining_ns = request["deadline_ns"] - time.time_ns()
        if remaining_ns > 0:
            time.sleep(remaining_ns / 1_000_000_000 + 0.05)
        assert time.time_ns() >= request["deadline_ns"]

        # Only after observing UNKNOWN, the original hold, no duplicate A
        # write, no B write, and deadline expiry does the parent allow complete
        # source facts. The owner may reconcile A by its exact source ID; it may not
        # dispatch B after the accepted deadline.
        _release_incomplete_fills(account_paths["account-a"]["provider"])

        deadline = time.monotonic() + 30
        completed = None
        while time.monotonic() < deadline:
            owner_diagnostic = _read_owner_handler_diagnostic(
                account_paths["account-a"]["owner_diagnostics"]
            )
            if (
                isinstance(owner_diagnostic, Mapping)
                and owner_diagnostic.get("first_owner_failure") is not None
            ):
                raise AssertionError(
                    "owner query handler failed during read reconciliation: "
                    f"{owner_diagnostic!r}"
                )
            completed = group_store.get(base._PORTFOLIO, request["group_id"])
            if (
                completed is not None
                and completed.state is expected_group_state
                and completed.legs[0].child_status is not None
                and completed.legs[0].child_status.state is expected_execution_state
            ):
                break
            if expected_execution_state is ExecutionState.UNKNOWN:
                observed_queries = _status(account_controls["account-a"])[2]
                if (
                    observed_queries["order_queries"] > before_release_queries
                    and observed_queries["fill_queries"] > before_release_fill_queries
                ):
                    # The status RPC is served after the owner tick returns;
                    # a malformed source must leave the prior UNKNOWN intact.
                    completed = group_store.get(base._PORTFOLIO, request["group_id"])
                    break
            time.sleep(0.05)
        assert completed is not None
        assert completed.state is expected_group_state, completed
        assert completed.request.deadline_ns == request["deadline_ns"]
        assert completed.legs[0].submitted_intent is not None
        assert completed.legs[0].submitted_intent.intent_id == first_intent_id
        assert completed.legs[0].child_status is not None
        assert completed.legs[0].child_status.state is expected_execution_state
        assert completed.legs[1].child_status is None
        if expected_execution_state is ExecutionState.FILLED:
            assert completed.residual_base_units != 0
        elif expected_execution_state is ExecutionState.UNKNOWN:
            assert completed.residual_base_units == uncertain.residual_base_units
            assert completed.legs[0].phase is GroupLegPhase.UNKNOWN
        else:
            assert completed.residual_base_units == 0
        assert completed.request.budget_quote == accepted.request.budget_quote
        group_store.close()
        group_store = None

        final_a = _status(account_controls["account-a"])
        final_b = _status(account_controls["account-b"])
        assert final_a[1] == (first_provider_client_order_id,)
        assert final_b[1] == ()
        assert final_a[2]["order_queries"] > pre_fill_queries
        assert final_a[2]["order_queries"] > before_release_queries
        assert final_a[2]["fill_queries"] > before_release_fill_queries
        owner_diagnostic = _read_owner_handler_diagnostic(
            account_paths["account-a"]["owner_diagnostics"]
        )
        assert isinstance(owner_diagnostic, Mapping)
        assert owner_diagnostic.get("first_owner_failure") is None, (
            "owner query handler recorded a failure before terminal assertions: "
            f"{owner_diagnostic!r}"
        )
        if expected_execution_state is ExecutionState.UNKNOWN:
            assert tuple(item.intent_id for item in final_a[3]) == (first_intent_id,)
        else:
            assert final_a[3] == ()
        assert final_b[3] == ()

        proof_events = _verified_proof_events(
            account_paths["account-a"]["proof"],
            account_ref="account-a",
            intent_id=first_intent_id,
        )
        proof_ready = tuple(item for item in proof_events if item.event_type == "PROOF_READY")
        risk_resolved = tuple(item for item in proof_events if item.event_type == "RISK_RESOLVED")
        if proof_kind is None:
            assert proof_ready == ()
            assert risk_resolved == ()
        else:
            assert len(proof_ready) == 1
            assert proof_ready[0].payload.get("proof_kind") == proof_kind
            assert len(risk_resolved) == 1

        execution_store = base.SqliteExecutionStore(
            account_paths["account-a"]["execution"]
        )
        try:
            record = execution_store.get(
                first_intent_id, scope=base._execution_scope("account-a")
            )
            assert record is not None and record.state is expected_execution_state
            assert record.dispatch_attempts == 1
        finally:
            execution_store.close()
        execution_store = base.SqliteExecutionStore(
            account_paths["account-b"]["execution"]
        )
        try:
            record = execution_store.get(
                second_intent_id, scope=base._execution_scope("account-b")
            )
            assert record is None
        finally:
            execution_store.close()
    finally:
        active_error = sys.exc_info()[1]
        cleanup_failures: list[BaseException] = []
        if strategy_control is not None:
            with suppress(Exception):
                strategy_control.close()
        if strategy_process is not None:
            strategy_process.join(10)
            if not strategy_process.is_alive():
                with suppress(Exception):
                    strategy_process.close()
            else:
                cleanup_failures.append(
                    AssertionError("strategy client process did not exit normally")
                )
        if bindings_store is not None:
            bindings_store.close()
        if group_store is not None:
            group_store.close()
        if coordinator_process is not None and coordinator_control is not None:
            try:
                base._stop_process(coordinator_process, coordinator_control)
            except BaseException as error:
                cleanup_failures.append(error)
        for account_ref in reversed(base._ACCOUNTS):
            process = account_processes.get(account_ref)
            control = account_controls.get(account_ref)
            if process is not None and control is not None:
                try:
                    base._stop_process(process, control)
                except BaseException as error:
                    cleanup_failures.append(error)
        if cleanup_failures:
            cleanup_message = (
                "one or more normal Host shutdowns failed: "
                + "; ".join(
                    f"{type(error).__name__}: {error}" for error in cleanup_failures
                )
            )
            if active_error is not None:
                active_error.add_note(cleanup_message)
            else:
                raise AssertionError(cleanup_message) from cleanup_failures[0]


@pytest.mark.parametrize(
    ("provider_outcome", "expected_execution_state", "expected_group_state", "proof_kind"),
    (
        ("filled", ExecutionState.FILLED, GroupOrderState.RECOVERY_REQUIRED, "FILLED"),
        (
            "rejected",
            ExecutionState.REJECTED,
            GroupOrderState.CLOSED,
            "REJECTED_NO_FILL",
        ),
        (
            "cancelled_no_fill",
            ExecutionState.CANCELLED,
            GroupOrderState.CLOSED,
            "CANCELED_NO_FILL",
        ),
        ("source_mismatch", ExecutionState.UNKNOWN, GroupOrderState.RECOVERY_REQUIRED, None),
        ("cancelled_full", ExecutionState.UNKNOWN, GroupOrderState.RECOVERY_REQUIRED, None),
    ),
)
def test_owner_reconciles_expired_accepted_unknown_without_new_dispatch(
    tmp_path: Path,
    provider_outcome: str,
    expected_execution_state: ExecutionState,
    expected_group_state: GroupOrderState,
    proof_kind: str | None,
) -> None:
    _run_owner_recovery_case(
        tmp_path,
        provider_outcome,
        expected_execution_state,
        expected_group_state,
        proof_kind,
    )


def test_owner_recovers_through_distinct_query_registration_after_strategy_exit(
    tmp_path: Path,
) -> None:
    """Exercise one terminal owner recovery through a distinct read registration."""

    _run_owner_recovery_case(
        tmp_path,
        "rejected",
        ExecutionState.REJECTED,
        GroupOrderState.CLOSED,
        "REJECTED_NO_FILL",
        explicit_query_probe=True,
    )
