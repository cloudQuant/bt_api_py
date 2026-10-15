from __future__ import annotations

from dataclasses import replace

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_config import (
    ServiceConfigError,
    parse_client_config,
    parse_service_config,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
)
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError
from tests.unit.test_service_config import (
    ACCOUNT_SERVICE_YAML,
    _client_binding,
    _strategy_config,
    _yaml,
)


def _scoped_writer_bindings() -> list[dict[str, object]]:
    return [
        _client_binding(
            "broker.orders",
            account_ref="account-a",
            scope_ref="strategy-a-orders",
            service_id="account-service",
            instance_id="execution-owner",
            transport_ref="rpc",
        ),
        _client_binding(
            "broker.orders",
            account_ref="account-a",
            scope_ref="strategy-b-orders",
            service_id="account-service",
            instance_id="execution-owner",
            transport_ref="rpc",
        ),
    ]


@pytest.mark.parametrize("required", [True, False])
def test_account_owner_approved_target_option_is_exact_and_preserved(required: bool) -> None:
    raw = _yaml(ACCOUNT_SERVICE_YAML)
    options = raw["components"]["execution"]["settings"]["options"]
    options["approved_target_required"] = required

    parsed = parse_service_config(raw)
    emitted = parsed.to_public_dict()["components"]["execution"]["settings"]["options"]
    assert emitted["approved_target_required"] is required


@pytest.mark.parametrize("invalid", [1, 0, None, "true"])
def test_account_owner_approved_target_option_rejects_non_boolean(invalid: object) -> None:
    raw = _yaml(ACCOUNT_SERVICE_YAML)
    raw["components"]["execution"]["settings"]["options"][
        "approved_target_required"
    ] = invalid

    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(raw)
    assert exc_info.value.code == "INVALID_VALUE"
    assert exc_info.value.path.endswith("approved_target_required")


def test_approved_target_option_has_no_default_and_is_not_client_configurable() -> None:
    service = parse_service_config(_yaml(ACCOUNT_SERVICE_YAML)).to_public_dict()
    assert "approved_target_required" not in service["components"]["execution"]["settings"][
        "options"
    ]

    client = _strategy_config([])
    client["clients"] = {
        "execution": {
            "config_schema_version": 1,
            "settings": {
                "bindings": [_scoped_writer_bindings()[0]],
                "options": {"approved_target_required": True},
            },
        }
    }
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(client)
    assert exc_info.value.code == "UNKNOWN_FIELD"


def test_approved_target_option_is_not_valid_for_other_execution_roles() -> None:
    raw = _yaml(ACCOUNT_SERVICE_YAML)
    execution = raw["components"]["execution"]["settings"]
    execution["bind_ports"] = ["service.order_group_entry"]
    execution["options"] = {
        "role": "group_coordinator",
        "portfolio_ref": "portfolio-a",
        "approved_target_required": True,
    }
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(raw)
    assert exc_info.value.code == "UNKNOWN_FIELD"


def test_nonwriter_account_routes_can_also_be_scope_specific() -> None:
    bindings = [
        _client_binding(
            "broker.order_query",
            account_ref="account-a",
            scope_ref="strategy-a-query",
            service_id="account-service",
            instance_id="execution-owner",
            transport_ref="rpc",
        ),
        _client_binding(
            "broker.order_query",
            account_ref="account-a",
            scope_ref="strategy-b-query",
            service_id="account-service",
            instance_id="execution-owner",
            transport_ref="rpc",
        ),
    ]
    parsed = parse_client_config(_strategy_config(bindings))
    rows = parsed.to_public_dict()["clients"]["execution"]["settings"]["bindings"]
    assert tuple(row["scope_ref"] for row in rows) == (
        "strategy-a-query",
        "strategy-b-query",
    )


def test_same_account_strategy_routes_share_only_the_exact_writer() -> None:
    bindings = _scoped_writer_bindings()
    parsed = parse_client_config(_strategy_config(bindings))
    rows = parsed.to_public_dict()["clients"]["execution"]["settings"]["bindings"]
    assert [row["scope_ref"] for row in rows] == [
        "strategy-a-orders",
        "strategy-b-orders",
    ]


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda rows: rows.__setitem__(1, dict(rows[0])), "DUPLICATE_BINDING"),
        (
            lambda rows: rows[1].__setitem__("instance_id", "other-writer"),
            "WRITER_CONFLICT",
        ),
        (
            lambda rows: rows[1].__setitem__("transport_ref", "other-transport"),
            "WRITER_CONFLICT",
        ),
    ],
)
def test_same_account_scoped_routes_reject_duplicate_or_different_writer(
    mutate, expected_code: str
) -> None:
    bindings = _scoped_writer_bindings()
    mutate(bindings)
    with pytest.raises(ServiceConfigError) as exc_info:
        parse_client_config(_strategy_config(bindings))
    assert exc_info.value.code == expected_code


class _LocalRegistryTransport:
    def __init__(
        self,
        registry: ServiceRegistry,
        principal_ref: str,
        service_identity: WireIdentity,
    ) -> None:
        self.registry = registry
        self.principal_ref = principal_ref
        self.service_identity = service_identity
        self.closed = False

    def request(self, request, *, timeout):
        del timeout
        result = self.registry.handle(self.principal_ref, request)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=self.service_identity,
            ),
            result,
        )

    def close(self) -> None:
        self.closed = True


def test_runtime_still_requires_explicit_scope_route_for_same_account(tmp_path) -> None:
    coordinator_identity = WireIdentity(
        WireRole.SERVICE, "coordinator-principal", "coordinator-service"
    )
    account_service_identity = WireIdentity(
        WireRole.SERVICE, "account-service-principal", "account-service"
    )
    strategy_scopes = {
        "strategy-a-orders": WireScope(
            "paper:routes",
            account_ref="account-a",
            strategy_ref="strategy-a",
            portfolio_ref="portfolio-a",
        ),
        "strategy-b-orders": WireScope(
            "paper:routes",
            account_ref="account-a",
            strategy_ref="strategy-b",
            portfolio_ref="portfolio-a",
        ),
    }
    bindings = [
        _client_binding(
            "upstream.orders",
            account_ref="account-a",
            scope_ref="strategy-a-orders",
            service_id="account-service",
            instance_id="execution-owner",
            transport_ref="rpc",
        ),
        _client_binding(
            "upstream.orders",
            account_ref="account-a",
            scope_ref="strategy-b-orders",
            service_id="account-service",
            instance_id="execution-owner",
            transport_ref="rpc",
        ),
    ]
    coordinator_raw = _yaml(ACCOUNT_SERVICE_YAML)
    coordinator_raw["service_id"] = coordinator_identity.actor_ref
    coordinator_raw["scope_ref"] = "coordinator-scope"
    coordinator_raw["clients"] = {
        "execution": {
            "config_schema_version": 1,
            "settings": {"bindings": bindings},
        }
    }
    coordinator_raw["communication"]["profiles"]["rpc"] = {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7102"},
        "security_profile_ref": "route-security",
    }
    service_config = parse_service_config(coordinator_raw)
    group_operations = frozenset(
        {"orders.submit_group_leg", "orders.resolve_group_leg"}
    )
    capability = ServiceCapability(
        "execution-owner",
        "service.order_entry",
        group_operations,
        group_operations,
    )
    write_policies = tuple(
        TrustedGroupWritePolicy(
            capability.instance_id,
            coordinator_identity.principal_ref,
            coordinator_identity,
            scope_ref,
            scope,
            f"run-{scope.strategy_ref}",
        )
        for scope_ref, scope in strategy_scopes.items()
    )
    resolution_policies = tuple(
        TrustedGroupResolutionPolicy(
            capability.instance_id,
            coordinator_identity.principal_ref,
            coordinator_identity,
            scope_ref,
            scope,
            f"run-{scope.strategy_ref}",
            "account-a",
        )
        for scope_ref, scope in strategy_scopes.items()
    )
    registry = ServiceRegistry(
        tmp_path / "scoped-routes.sqlite3",
        service_id=account_service_identity.actor_ref,
        capabilities=(capability,),
        grants={
            coordinator_identity.principal_ref: (
                PrincipalGrant(
                    coordinator_identity,
                    tuple(
                        ResourceGrant(
                            capability.instance_id,
                            capability.port,
                            binding["scope_ref"],
                            strategy_scopes[binding["scope_ref"]],
                            group_operations,
                        )
                        for binding in bindings
                    ),
                ),
            )
        },
        policy_version="route-policy-v1",
        requirements_version="route-ready-v1",
        trusted_group_write_policies=write_policies,
        trusted_group_resolution_policies=resolution_policies,
    )
    calls: list[tuple[str, str]] = []
    registry.register_handler(
        capability.instance_id,
        "orders.submit_group_leg",
        lambda context, arguments: calls.append(
            (context.scope.strategy_ref, arguments["route"])
        )
        or {"accepted": True},
    )
    registry.register_handler(
        capability.instance_id,
        "orders.resolve_group_leg",
        lambda _context, _arguments: {"resolved": True},
    )
    transport = _LocalRegistryTransport(
        registry, coordinator_identity.principal_ref, account_service_identity
    )
    runtime = ServiceClientRuntime(
        service_config,
        identity=coordinator_identity,
        scope_resolver=lambda scope_ref: strategy_scopes[scope_ref],
        peer_resolver=lambda _service_id: account_service_identity,
        transport_factory=lambda *_args: transport,
        requested_operations={"upstream.orders": group_operations},
    )
    try:
        runtime.start()
        assert len(runtime.bindings) == 2
        for binding in runtime.bindings:
            registry.bind_trusted_group_run(
                binding.scope.execution_domain_ref,
                binding.scope.strategy_ref,
                binding.scope.account_ref,
                registration_id=binding.registration_id,
            )
        with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
            runtime.call(
                "upstream.orders",
                "orders.submit_group_leg",
                {"route": "implicit"},
                account_ref="account-a",
            )
        assert runtime.call(
            "upstream.orders",
            "orders.submit_group_leg",
            {"route": "strategy-a"},
            account_ref="account-a",
            scope_ref="strategy-a-orders",
        ) == {"accepted": True}
        assert runtime.call(
            "upstream.orders",
            "orders.submit_group_leg",
            {"route": "strategy-b"},
            account_ref="account-a",
            scope_ref="strategy-b-orders",
        ) == {"accepted": True}
        assert calls == [("strategy-a", "strategy-a"), ("strategy-b", "strategy-b")]
    finally:
        runtime.close()
        registry.close()


def test_group_host_cleanup_attempts_every_action_and_raises_first_failure() -> None:
    from tests.integration.test_arb02_group_budget_service import _cleanup_group_hosts

    first_error = RuntimeError("client close failed")
    stop_error = RuntimeError("account process close failed")
    calls: list[tuple[str, object]] = []

    class _Control:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            calls.append(("control", self.name))

    controls = {
        "coordinator": _Control("coordinator"),
        "account-a": _Control("account-a"),
        "account-b": _Control("account-b"),
    }
    processes = {name: f"process-{name}" for name in controls}

    class _Release:
        def set(self) -> None:
            calls.append(("release", "provider"))

    def close_clients(*_args) -> None:
        calls.append(("clients", "close"))
        raise first_error

    def stop_process(process: str, control: _Control) -> None:
        calls.append(("process", process))
        control.close()
        if process == "process-account-b":
            raise stop_error

    with pytest.raises(RuntimeError) as exc_info:
        _cleanup_group_hosts(
            provider_release=_Release(),
            client_threads={"client": object()},
            client_commands={},
            client_results={},
            coordinator_process=processes["coordinator"],
            coordinator_control=controls["coordinator"],
            account_processes={
                "account-a": processes["account-a"],
                "account-b": processes["account-b"],
            },
            account_controls={
                "account-a": controls["account-a"],
                "account-b": controls["account-b"],
            },
            close_client_threads=close_clients,
            stop_process=stop_process,
        )

    assert exc_info.value is first_error
    assert calls == [
        ("release", "provider"),
        ("clients", "close"),
        ("process", "process-coordinator"),
        ("control", "coordinator"),
        ("process", "process-account-b"),
        ("control", "account-b"),
        ("process", "process-account-a"),
        ("control", "account-a"),
    ]


def test_account_resource_cleanup_attempts_all_closers() -> None:
    from tests.integration.test_arb02_group_budget_service import _MultiAccountResources

    first_error = RuntimeError("authority close failed")
    calls: list[str] = []

    class _Resource:
        def __init__(self, name: str, error: BaseException | None = None) -> None:
            self.name = name
            self.error = error

        def close(self) -> None:
            calls.append(self.name)
            if self.error is not None:
                raise self.error

    resources = object.__new__(_MultiAccountResources)
    resources.authority = _Resource("authority", first_error)
    resources.accepted_store = _Resource("accepted-store")
    resources.risk_gate = _Resource("risk-gate")
    resources.proof_journal = _Resource("proof-journal")
    resources.execution_store = _Resource("execution-store")
    resources.bt_api = _Resource("bt-api")

    with pytest.raises(RuntimeError, match="failed to close") as exc_info:
        resources.close()
    assert exc_info.value.__cause__ is first_error
    assert calls == [
        "authority",
        "accepted-store",
        "risk-gate",
        "proof-journal",
        "execution-store",
        "bt-api",
    ]


def test_failed_coordinator_start_closes_pipes_when_process_start_raises() -> None:
    from tests.integration.test_arb02_group_budget_service import (
        _cleanup_failed_host_start,
    )

    calls: list[str] = []
    pipe_error = RuntimeError("parent pipe close failed")

    class _Process:
        pid = None

        def is_alive(self) -> bool:
            raise AssertionError("an unstarted process must not be inspected")

        def close(self) -> None:
            calls.append("process-close")

    class _Pipe:
        def __init__(self, name: str, error: BaseException | None = None) -> None:
            self.name = name
            self.error = error

        def close(self) -> None:
            calls.append(self.name)
            if self.error is not None:
                raise self.error

    parent = _Pipe("parent", pipe_error)
    child = _Pipe("child")
    failure = _cleanup_failed_host_start(
        _Process(), parent, child, process_started=False
    )

    assert failure is pipe_error
    assert calls == ["process-close", "parent", "child"]


def test_failed_host_process_construction_still_closes_both_pipe_ends() -> None:
    from tests.integration.test_arb02_group_budget_service import (
        _cleanup_failed_host_start,
    )

    calls: list[str] = []

    class _Pipe:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            calls.append(self.name)

    failure = _cleanup_failed_host_start(
        None, _Pipe("parent"), _Pipe("child"), process_started=False
    )

    assert failure is None
    assert calls == ["parent", "child"]


def test_failed_coordinator_readiness_terminates_only_after_bounded_join() -> None:
    from tests.integration.test_arb02_group_budget_service import (
        _cleanup_failed_host_start,
    )

    calls: list[str] = []

    class _Process:
        pid = 123

        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float) -> None:
            calls.append(f"join:{timeout}")

        def terminate(self) -> None:
            calls.append("terminate")
            self.alive = False

        def close(self) -> None:
            calls.append("process-close")

    class _Pipe:
        def __init__(self, name: str) -> None:
            self.name = name

        def send(self, message: object) -> None:
            calls.append(f"send:{message}")

        def poll(self, timeout: float) -> bool:
            calls.append(f"poll:{timeout}")
            return False

        def close(self) -> None:
            calls.append(f"{self.name}-close")

    process = _Process()
    failure = _cleanup_failed_host_start(
        process, _Pipe("parent"), _Pipe("child"), process_started=True
    )

    assert isinstance(failure, TimeoutError)
    assert calls == [
        "send:close",
        "poll:2",
        "join:2",
        "terminate",
        "join:5",
        "process-close",
        "parent-close",
        "child-close",
    ]


def test_failed_coordinator_close_signal_still_reaps_and_closes_everything() -> None:
    from tests.integration.test_arb02_group_budget_service import (
        _cleanup_failed_host_start,
    )

    calls: list[str] = []
    close_error = RuntimeError("first close signal failed")

    class _Process:
        pid = 456

        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float) -> None:
            calls.append(f"join:{timeout}")

        def terminate(self) -> None:
            calls.append("terminate")
            self.alive = False

        def close(self) -> None:
            calls.append("process-close")

    class _Pipe:
        def __init__(self, name: str) -> None:
            self.name = name

        def send(self, _message: object) -> None:
            calls.append("send")
            raise close_error

        def close(self) -> None:
            calls.append(f"{self.name}-close")

    failure = _cleanup_failed_host_start(
        _Process(), _Pipe("parent"), _Pipe("child"), process_started=True
    )

    assert failure is close_error
    assert calls == [
        "send",
        "join:2",
        "terminate",
        "join:5",
        "process-close",
        "parent-close",
        "child-close",
    ]


def test_failed_coordinator_start_uses_normal_close_without_forced_fallback() -> None:
    from tests.integration.test_arb02_group_budget_service import (
        _cleanup_failed_host_start,
    )

    calls: list[str] = []

    class _Process:
        pid = 789

        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float) -> None:
            calls.append(f"join:{timeout}")
            self.alive = False

        def terminate(self) -> None:
            calls.append("terminate")

        def close(self) -> None:
            calls.append("process-close")

    class _Pipe:
        def __init__(self, name: str) -> None:
            self.name = name

        def send(self, message: object) -> None:
            calls.append(f"send:{message}")

        def poll(self, timeout: float) -> bool:
            calls.append(f"poll:{timeout}")
            return True

        def recv(self) -> tuple[str, str]:
            return ("closed", "closed")

        def close(self) -> None:
            calls.append(f"{self.name}-close")

    failure = _cleanup_failed_host_start(
        _Process(), _Pipe("parent"), _Pipe("child"), process_started=True
    )

    assert failure is None
    assert calls == [
        "send:close",
        "poll:2",
        "join:2",
        "process-close",
        "parent-close",
        "child-close",
    ]
