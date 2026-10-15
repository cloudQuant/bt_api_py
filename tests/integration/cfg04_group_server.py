"""Server-side launcher for the isolated CFG04 order-group proxy test.

This fixture deliberately lives in the test source tree. The strategy-side
process imports only the installed SDK, Base, and transport distributions.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


class _ServerRunError(RuntimeError):
    def __init__(self, primary_error: BaseException, cleanup_failures: tuple[str, ...]):
        super().__init__(str(primary_error))
        self.primary_error = primary_error
        self.cleanup_failures = cleanup_failures


def _decode(value: object) -> bytes:
    if type(value) is not str:
        raise ValueError("invalid synthetic key")
    return base64.b64decode(value.encode("ascii"), validate=True)


def _encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _receive(control, *, timeout: float = 20.0) -> tuple[Any, ...]:
    if not control.poll(timeout):
        raise TimeoutError("group host did not answer within its bound")
    value = control.recv()
    if not value or value[0] == "error":
        raise RuntimeError("group host startup or control failed")
    return value


def _stop(process, control) -> dict[str, Any]:
    if not process.is_alive():
        raise RuntimeError("group host process exited before its close request")
    control.send("close")
    reply = _receive(control, timeout=20.0)
    if reply[0] != "closed":
        raise RuntimeError("group host returned a nonterminal close result")
    process.join(20)
    if process.is_alive():
        raise TimeoutError("group host remained alive after normal close")
    exitcode = process.exitcode
    if exitcode != 0:
        raise RuntimeError("group host exited unsuccessfully")
    control.close()
    process.close()
    return {"normal_close": True, "exitcode": exitcode, "result": list(reply[1:])}


def _cleanup_host(process, control, *, label: str) -> list[str]:
    """Boundedly close one child Host and report every abnormal cleanup path."""

    failures: list[str] = []
    started = process.pid is not None
    if started and process.is_alive():
        try:
            control.send("close")
            reply = _receive(control, timeout=5.0)
            if reply[0] != "closed":
                failures.append(f"{label}:nonterminal_close_reply")
        except BaseException as error:
            failures.append(f"{label}:normal_close_{type(error).__name__}")
    elif started:
        failures.append(f"{label}:exited_before_normal_close")

    if started:
        try:
            process.join(5)
            if process.is_alive():
                failures.append(f"{label}:forced_terminate_required")
                process.terminate()
                process.join(2)
            if process.is_alive():
                failures.append(f"{label}:forced_kill_required")
                process.kill()
                process.join(2)
            if process.is_alive():
                failures.append(f"{label}:process_still_alive")
            elif process.exitcode != 0:
                failures.append(f"{label}:exit_{process.exitcode}")
        except BaseException as error:
            failures.append(f"{label}:join_{type(error).__name__}")

    try:
        control.close()
    except BaseException as error:
        failures.append(f"{label}:control_close_{type(error).__name__}")
    if not started or not process.is_alive():
        try:
            process.close()
        except BaseException as error:
            failures.append(f"{label}:process_close_{type(error).__name__}")
    return failures


def _account_status(group, controls: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for account_ref, control in controls.items():
        control.send(("status",))
        reply = _receive(control)
        if reply[0] != "status":
            raise RuntimeError("account owner returned an unexpected status")
        _tag, writes, counters, holds = reply
        result[account_ref] = {
            "provider_writes": list(writes),
            "provider_counters": dict(counters),
            "pending_holds": [
                {
                    "intent_id": str(getattr(item, "intent_id", "")),
                    "group_id": str(getattr(item, "group_id", "")),
                    "leg_id": str(getattr(item, "leg_id", "")),
                }
                for item in holds
            ],
        }
    return result


def _durable_summary(group, paths: dict[str, Any], paired_id: str, unknown_id: str):
    from bt_api_execution import (
        ExecutionState,
        SqliteExecutionStore,
        SqliteOrderGroupStore,
        group_leg_intent_id,
    )
    from bt_api_risk import AccountScope, DurableRiskGate

    persisted_groups = SqliteOrderGroupStore(paths["coordinator"]["groups"])
    try:
        paired = persisted_groups.get(group._PORTFOLIO, paired_id)
        unknown = persisted_groups.get(group._PORTFOLIO, unknown_id)
        paired_events = persisted_groups.events(group._PORTFOLIO, paired_id)
        unknown_events = persisted_groups.events(group._PORTFOLIO, unknown_id)
        if paired is None or unknown is None:
            raise RuntimeError("group store did not retain both submitted groups")
        paired_payload = paired.to_payload()
        unknown_payload = unknown.to_payload()
        paired_event_types = [item.event_type for item in paired_events]
        unknown_event_types = [item.event_type for item in unknown_events]
    finally:
        persisted_groups.close()

    accounts: dict[str, Any] = {}
    policy = group._risk_policy()
    for account_ref in group._ACCOUNTS:
        paths_for_account = paths["accounts"][account_ref]
        risk_scope = AccountScope("fixture", f"provider-{account_ref}", "simulation")
        gate = DurableRiskGate(paths_for_account["risk_gate"], policy)
        try:
            paired_risk = gate.read_group_vector_snapshot(risk_scope, paired_id)
            unknown_risk = gate.read_group_vector_snapshot(risk_scope, unknown_id)
            paired_legs = [
                {
                    "leg_id": item.leg_id,
                    "claimed": item.claimed,
                    "resolved": item.resolved,
                }
                for item in paired_risk.legs
            ]
            unknown_legs = [
                {
                    "leg_id": item.leg_id,
                    "claimed": item.claimed,
                    "resolved": item.resolved,
                }
                for item in unknown_risk.legs
            ]
        finally:
            gate.close()

        execution_scope = group._execution_scope(account_ref)
        execution_store = SqliteExecutionStore(paths_for_account["execution"])
        try:
            account_leg = "leg-a" if account_ref == "account-a" else "leg-b"
            paired_record = execution_store.get(
                group_leg_intent_id(paired_id, account_leg), scope=execution_scope
            )
            unknown_record = execution_store.get(
                group_leg_intent_id(unknown_id, account_leg), scope=execution_scope
            )
            if paired_record is None or unknown_record is None:
                raise RuntimeError("execution store did not retain both child orders")
            execution_states = {
                "paired": paired_record.state.value,
                "unknown": unknown_record.state.value,
                "paired_dispatch_attempts": paired_record.dispatch_attempts,
                "unknown_dispatch_attempts": unknown_record.dispatch_attempts,
            }
            if paired_record.state is not ExecutionState.FILLED:
                raise RuntimeError("paired child did not persist as filled")
        finally:
            execution_store.close()

        verifier_store = SqliteExecutionStore(paths_for_account["execution"])
        authority = group.ExecutionGroupAuthority(
            paths_for_account["group_authority"],
            completion_verifier=group.ExecutionStoreGroupCompletionVerifier(
                verifier_store, execution_scopes=(execution_scope,)
            ),
        )
        try:
            holds = authority.pending_holds()
            hold_ids = sorted(item.intent_id for item in holds)
        finally:
            authority.close()
            verifier_store.close()

        writes, counters = group._provider_snapshot(Path(paths_for_account["provider"]))
        accounts[account_ref] = {
            "paired_risk_legs": paired_legs,
            "unknown_risk_legs": unknown_legs,
            "execution": execution_states,
            "authority_pending_hold_intents": hold_ids,
            "provider_writes": list(writes),
            "provider_counters": dict(counters),
        }

    return {
        "paired": paired_payload,
        "unknown": unknown_payload,
        "paired_event_types": paired_event_types,
        "unknown_event_types": unknown_event_types,
        "accounts": accounts,
    }


def _verify_support_payloads(root: Path, rows: object) -> dict[str, dict[str, Any]]:
    expected = {
        "tests/__init__.py",
        "tests/integration/cfg04_group_server.py",
        "tests/integration/test_cfg04_installed_group_proxy.py",
        "tests/integration/test_service_execution_group_local_process.py",
        "tests/unit/_service_group_provider_fixtures.py",
        "tests/unit/test_iteration10_optional_install_matrix.py",
    }
    if not isinstance(rows, dict) or set(rows) != expected:
        raise ValueError("group test support payload map is incomplete")
    result: dict[str, dict[str, Any]] = {}
    for relative, record in rows.items():
        if type(relative) is not str or not isinstance(record, dict):
            raise ValueError("invalid support payload record")
        if set(record) != {"bytes", "sha256"}:
            raise ValueError("invalid support payload fields")
        candidate = root / relative
        if candidate.is_symlink():
            raise ValueError("support payload may not be a symlink")
        path = candidate.resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("support payload escaped its staged root")
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if type(record["bytes"]) is not int or len(payload) != record["bytes"]:
            raise ValueError("support payload byte count changed")
        if type(record["sha256"]) is not str or digest != record["sha256"]:
            raise ValueError("support payload digest changed")
        result[relative] = {"bytes": len(payload), "sha256": digest}
    return result


def _server_installation_report(group) -> dict[str, Any]:
    package_names = (
        "bt_api_py",
        "bt_api_base",
        "bt_api_transport_zmq",
        "bt_api_gateway",
        "bt_api_execution",
        "bt_api_risk",
        "bt_api_monitor",
    )
    distributions = {name: importlib.metadata.version(name) for name in package_names}
    origins = {}
    for module_name in (
        "bt_api_py.service_host",
        "bt_api_base.service_protocol",
        "bt_api_transport_zmq.client",
        "bt_api_gateway",
        "bt_api_execution",
        "bt_api_risk",
        "bt_api_monitor",
    ):
        module = importlib.import_module(module_name)
        origin = getattr(module, "__file__", None)
        if type(origin) is not str or not origin:
            raise RuntimeError("installed server module has no file origin")
        origins[module_name] = str(Path(origin).resolve())
    domain_presence = {}
    for name in ("bt_api_gateway", "bt_api_execution", "bt_api_risk", "bt_api_monitor"):
        spec = importlib.util.find_spec(name)
        domain_presence[name] = {
            "distribution_installed": distributions[name] is not None,
            "import_spec_available": spec is not None,
            "module_loaded": any(
                loaded == name or loaded.startswith(name + ".") for loaded in sys.modules
            ),
        }
    direct_url_text = importlib.metadata.distribution("bt_api_py").read_text("direct_url.json")
    direct_url = None if direct_url_text is None else json.loads(direct_url_text)
    return {
        "pid": os.getpid(),
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "working_directory": str(Path.cwd().resolve()),
        "sys_path": [str(Path(item or os.getcwd()).resolve()) for item in sys.path],
        "distributions": distributions,
        "origins": origins,
        "domain_presence": domain_presence,
        "direct_url_editable": bool((direct_url or {}).get("dir_info", {}).get("editable", False)),
        "support_origin": str(Path(group.__file__).resolve()),
    }


def _run(payload: dict[str, Any]) -> int:
    support_root = Path(payload["test_support_root"]).resolve(strict=True)
    if not support_root.is_dir():
        raise ValueError("staged group test support root is missing")
    if not support_root.is_relative_to(Path(sys.prefix).resolve().parent):
        raise ValueError("test support root is not in the fixed external artifact")
    if Path(__file__).resolve().parents[2] != support_root:
        raise ValueError("group server is outside its pinned test support root")
    support_payloads = _verify_support_payloads(support_root, payload.get("support_payloads"))
    if str(support_root) not in sys.path:
        sys.path.insert(0, str(support_root))

    from tests.integration import test_service_execution_group_local_process as group

    installation = _server_installation_report(group)

    data_root = Path(payload["root"]).resolve()
    if data_root.exists() and not data_root.is_dir():
        raise ValueError("server data root is not a directory")
    data_root.mkdir(parents=True, exist_ok=True)

    import multiprocessing

    accounts = group._ACCOUNTS
    paths = {
        "accounts": {
            account: {
                name: str(data_root / f"{account}-{name}.sqlite3")
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
            for account in accounts
        },
        "coordinator": {
            "registry": str(data_root / "coordinator-registry.sqlite3"),
            "groups": str(data_root / "coordinator-groups.sqlite3"),
            "run_bindings": str(data_root / "coordinator-run-bindings.sqlite3"),
        },
    }
    endpoints = {account: payload["account_endpoints"][account] for account in accounts}
    account_server_keys = {
        account: (
            _decode(payload["account_server_keys"][account]["public"]),
            _decode(payload["account_server_keys"][account]["secret"]),
        )
        for account in accounts
    }
    coordinator_server_public = _decode(payload["coordinator_server"]["public"])
    coordinator_server_secret = _decode(payload["coordinator_server"]["secret"])
    coordinator_client_public = _decode(payload["coordinator_client"]["public"])
    coordinator_client_secret = _decode(payload["coordinator_client"]["secret"])
    strategy_client_public = _decode(payload["strategy_client"]["public"])
    metadata_reference_ns = int(payload["metadata_reference_ns"])

    account_configs = {
        account: group._account_service_config(
            account, endpoints[account], f"account-server-security-{account}"
        )
        for account in accounts
    }
    coordinator_endpoint = payload["coordinator_endpoint"]
    coordinator_config = group._coordinator_service_config(
        coordinator_endpoint,
        "coordinator-server-security-v1",
        account_configs,
        endpoints,
    )

    context = multiprocessing.get_context("spawn")
    account_processes: dict[str, Any] = {}
    account_controls: dict[str, Any] = {}
    account_child_controls: dict[str, Any] = {}
    coordinator_process = None
    coordinator_control = None
    coordinator_child_control = None
    primary_error: BaseException | None = None
    shutdown_failures: list[str] = []

    try:
        for account in accounts:
            parent_control, child_control = context.Pipe(duplex=True)
            process = context.Process(
                name=f"cfg04-group-account-{account}",
                target=group._account_host_process,
                args=(
                    child_control,
                    account_configs[account],
                    paths["accounts"][account]["registry"],
                    paths["accounts"][account],
                    account,
                    account_server_keys[account][0],
                    account_server_keys[account][1],
                    coordinator_client_public,
                    metadata_reference_ns,
                    ("filled", "filled") if account == "account-a" else ("filled", "open"),
                ),
            )
            account_processes[account] = process
            account_controls[account] = parent_control
            account_child_controls[account] = child_control
            process.start()
            child_control.close()
            account_child_controls.pop(account, None)
            startup = _receive(parent_control)
            if startup[0] != "ready" or startup[1] != endpoints[account]:
                raise RuntimeError("account owner endpoint did not start as configured")

        coordinator_context = multiprocessing.get_context("spawn")
        parent_control, child_control = coordinator_context.Pipe(duplex=True)
        coordinator_process = coordinator_context.Process(
            name="cfg04-group-coordinator",
            target=group._coordinator_host_process,
            args=(
                child_control,
                coordinator_config,
                account_configs,
                paths["coordinator"]["registry"],
                paths["coordinator"]["groups"],
                paths["coordinator"]["run_bindings"],
                endpoints,
                {account: account_server_keys[account][0] for account in accounts},
                coordinator_server_public,
                coordinator_server_secret,
                coordinator_client_public,
                coordinator_client_secret,
                strategy_client_public,
                metadata_reference_ns,
            ),
        )
        coordinator_control = parent_control
        coordinator_child_control = child_control
        coordinator_process.start()
        child_control.close()
        coordinator_child_control = None
        startup = _receive(coordinator_control)
        if startup[0] != "ready" or startup[1] != coordinator_endpoint:
            raise RuntimeError("group coordinator endpoint did not start as configured")

        upstream_bindings = startup[2]
        expected_operations = {
            "upstream.orders": ("orders.resolve_group_leg", "orders.submit_group_leg"),
            "upstream.order_query": ("orders.query_group_leg",),
            "upstream.account_reservations": (
                "reservations.query",
                "reservations.release",
                "reservations.reserve",
            ),
        }
        expected = {
            (mount, account, group._route_refs(account)[route_index])
            for account in accounts
            for mount, route_index in (
                ("upstream.orders", 1),
                ("upstream.order_query", 2),
                ("upstream.account_reservations", 0),
            )
        }
        actual = {
            (mount, account, scope_ref) for mount, account, scope_ref, *_ in upstream_bindings
        }
        if len(upstream_bindings) != 6 or actual != expected:
            raise RuntimeError("group coordinator did not register the six exact account routes")
        for mount, _account, _scope_ref, _registration_id, operations in upstream_bindings:
            if tuple(operations) != expected_operations[mount]:
                raise RuntimeError("group coordinator registered an unexpected upstream operation")

        for account in accounts:
            order_registration_id = next(
                registration_id
                for mount, bound_account, _scope_ref, registration_id, _ops in upstream_bindings
                if mount == "upstream.orders" and bound_account == account
            )
            control = account_controls[account]
            control.send(("activate", order_registration_id))
            activated = _receive(control)
            if (
                activated[0] != "activated"
                or activated[1] != order_registration_id
                or type(activated[2]) is not int
                or activated[2] <= 0
            ):
                raise RuntimeError("account-owner authority generation did not activate")

        _emit(
            {
                "kind": "ready",
                **installation,
                "source_support": support_payloads,
                "account_host_pids": {
                    account: account_processes[account].pid for account in accounts
                },
                "coordinator_host_pid": coordinator_process.pid,
                "endpoint": coordinator_endpoint,
                "service_id": group._COORDINATOR_SERVICE_ID,
                "service_principal": group._COORDINATOR_PRINCIPAL,
                "instance_id": group._COORDINATOR_INSTANCE_ID,
                "scope_ref": group._COORDINATOR_SCOPE_REF,
                "domain": group._DOMAIN,
                "strategy_ref": group._STRATEGY,
                "strategy_principal": group._STRATEGY_PRINCIPAL,
                "portfolio_ref": group._PORTFOLIO,
                "run_id": group._RUN,
                "accounts": list(accounts),
                "upstream_bindings": [
                    {
                        "mount": mount,
                        "account_ref": account,
                        "scope_ref": scope_ref,
                        "registration_id": registration_id,
                        "operations": list(operations),
                    }
                    for mount, account, scope_ref, registration_id, operations in upstream_bindings
                ],
            }
        )

        for raw_line in sys.stdin:
            try:
                command = json.loads(raw_line)
            except (TypeError, ValueError) as error:
                raise RuntimeError("invalid server control command") from error
            action = command.get("action") if isinstance(command, dict) else None
            if action == "request":
                group_id = command.get("group_id")
                if group_id not in {group._GROUP_ONE, group._GROUP_TWO}:
                    raise RuntimeError("unsupported synthetic group request")
                _emit(
                    {
                        "kind": "request",
                        "group_id": group_id,
                        "request": group._group_request(group_id, metadata_reference_ns),
                    }
                )
            elif action == "arm_unknown":
                account_controls["account-b"].send(("arm-incomplete-fills",))
                armed = _receive(account_controls["account-b"])
                if armed != ("armed", "account-b"):
                    raise RuntimeError("unknown-order fixture did not arm")
                _emit({"kind": "armed", "account_ref": "account-b"})
            elif action == "status":
                _emit(
                    {
                        "kind": "status",
                        "accounts": _account_status(group, account_controls),
                    }
                )
            elif action == "close":
                normal_closes = []
                if coordinator_process is not None and coordinator_control is not None:
                    normal_closes.append(_stop(coordinator_process, coordinator_control))
                    coordinator_process = None
                    coordinator_control = None
                for account in reversed(accounts):
                    normal_closes.append(
                        _stop(account_processes[account], account_controls[account])
                    )
                    account_processes.pop(account, None)
                    account_controls.pop(account, None)
                durable = _durable_summary(group, paths, group._GROUP_ONE, group._GROUP_TWO)
                _emit(
                    {
                        "kind": "closed",
                        "normal_closes": normal_closes,
                        "durable": durable,
                    }
                )
                return 0
            else:
                raise RuntimeError("unknown server control action")
        raise RuntimeError("control channel closed before normal Host shutdown")
    except BaseException as error:
        primary_error = error
    finally:
        if coordinator_process is not None and coordinator_control is not None:
            try:
                shutdown_failures.extend(
                    _cleanup_host(coordinator_process, coordinator_control, label="coordinator")
                )
            except BaseException as error:
                shutdown_failures.append(f"coordinator:cleanup_{type(error).__name__}")
        elif coordinator_control is not None:
            try:
                coordinator_control.close()
            except BaseException as error:
                shutdown_failures.append(f"coordinator:control_close_{type(error).__name__}")
        if coordinator_child_control is not None:
            try:
                coordinator_child_control.close()
            except BaseException as error:
                shutdown_failures.append(f"coordinator_child:control_close_{type(error).__name__}")
        for account in reversed(accounts):
            process = account_processes.get(account)
            control = account_controls.get(account)
            if process is not None and control is not None:
                try:
                    shutdown_failures.extend(
                        _cleanup_host(process, control, label=f"account_{account}")
                    )
                except BaseException as error:
                    shutdown_failures.append(f"account_{account}:cleanup_{type(error).__name__}")
            elif control is not None:
                try:
                    control.close()
                except BaseException as error:
                    shutdown_failures.append(
                        f"account_{account}:control_close_{type(error).__name__}"
                    )
            child_control = account_child_controls.get(account)
            if child_control is not None:
                try:
                    child_control.close()
                except BaseException as error:
                    shutdown_failures.append(
                        f"account_{account}_child:control_close_{type(error).__name__}"
                    )

    if primary_error is not None:
        raise _ServerRunError(primary_error, tuple(shutdown_failures)) from primary_error
    if shutdown_failures:
        error = RuntimeError("group child Host cleanup did not complete normally")
        raise _ServerRunError(error, tuple(shutdown_failures)) from error
    return 0


def main() -> int:
    first_line = sys.stdin.readline()
    if not first_line:
        return 2
    try:
        payload = json.loads(first_line)
        if not isinstance(payload, dict):
            raise ValueError
        return _run(payload)
    except BaseException as error:
        primary_error = error.primary_error if isinstance(error, _ServerRunError) else error
        cleanup_failures = error.cleanup_failures if isinstance(error, _ServerRunError) else ()
        with suppress(BaseException):
            _emit(
                {
                    "kind": "server_error",
                    "error_type": type(primary_error).__name__,
                    "error_code": getattr(primary_error, "code", None),
                    "cleanup_failures": list(cleanup_failures),
                }
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
