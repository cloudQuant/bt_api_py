"""Offline conversion of retired forwarding settings to Iteration 10 configs.

This module only transforms plain configuration data and validates it with the
public SDK parsers. It never loads a file, resolves a credential, starts a
service, or opens a network connection. The documented ``python -m`` command
sets ``BT_API_PY_LIGHT_IMPORT=1`` so package initialization also skips the
optional ``BtApi`` facade. A successful result is a reviewed migration draft,
not authorization or permission to start.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from typing import Any, Final

from bt_api_py.service_config import (
    ServiceConfig,
    ServiceConfigError,
    parse_client_config,
    parse_service_config,
)
from bt_api_py.service_deployment import validate_deployment

_REQUEST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "legacy_forwarding_config",
        "identity_mapping",
        "target_scope_refs",
        "recovery_plan_ref",
        "target_client_template",
        "target_service_configs",
    }
)
_LEGACY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "command_endpoint",
        "market_endpoint",
        "private_endpoint",
        "account_id",
        "strategy_id",
        "market_read_timeout_ms",
        "max_cache_age_ms",
    }
)
_IDENTITY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "legacy_account_id",
        "account_ref",
        "legacy_strategy_id",
        "strategy_ref",
    }
)
_ACCOUNT_REQUIRED_MOUNTS: Final[frozenset[str]] = frozenset(
    {
        "store.account",
        "broker.orders",
        "broker.order_query",
        "risk.read_model",
        "application.account",
        "application.orders",
        "application.paper_legacy_import",
        "application.order_query",
        "application.risk_read",
        "upstream.account",
        "upstream.orders",
        "upstream.order_query",
        "upstream.risk_read",
        "upstream.account_reservations",
        "upstream.group_compensation_orders",
        "upstream.group_compensation_order_query",
        "upstream.group_roll_exit_orders",
        "upstream.group_roll_exit_order_query",
    }
)
_LEGACY_TIMEOUT_DEFAULT = 250
_LEGACY_CACHE_AGE_DEFAULT = 5_000
_VERSIONED_REFERENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]*-v[1-9][0-9]*$"
)


class OfflineMigrationError(ValueError):
    """Finite migration error that never includes configuration values."""

    def __init__(self, code: str, path: str) -> None:
        self.code = code
        self.path = path
        super().__init__(f"{code} at {path}")


def convert_forwarding_config(request: object) -> dict[str, Any]:
    """Convert one legacy forwarding record into a validated target draft.

    ``request`` must provide the old record, explicit identity and scope
    mappings, a target client template, and the complete selected service
    configuration set. The converter fills only the target strategy/account
    references. It does not copy old endpoints or infer authorization,
    credentials, component recovery, service activation, or run identity.
    """

    root = _require_dict(request, "$")
    _check_exact_fields(root, _REQUEST_FIELDS, "INVALID_REQUEST", "$", require_all=True)

    legacy = _require_dict(root["legacy_forwarding_config"], "$.legacy_forwarding_config")
    _check_exact_fields(
        legacy,
        _LEGACY_FIELDS,
        "UNSUPPORTED_LEGACY_FIELD",
        "$.legacy_forwarding_config",
    )
    _validate_legacy_record(legacy)

    identity = _require_dict(root["identity_mapping"], "$.identity_mapping")
    _check_exact_fields(
        identity,
        _IDENTITY_FIELDS,
        "INVALID_IDENTITY_MAPPING",
        "$.identity_mapping",
        require_all=True,
    )
    account_ref, strategy_ref = _validate_identity_mapping(legacy, identity)
    target_scope_refs = _validate_scope_refs(root["target_scope_refs"])
    recovery_plan_ref = _validate_recovery_plan_ref(root["recovery_plan_ref"])

    client_raw = _copy_plain_tree(
        _require_dict(root["target_client_template"], "$.target_client_template"),
        "$.target_client_template",
    )
    _apply_client_identity_mapping(client_raw, account_ref, strategy_ref)
    try:
        client = parse_client_config(client_raw)
    except ServiceConfigError as exc:
        raise OfflineMigrationError(exc.code, f"$.target_client_template{exc.path[1:]}") from None
    if client.process_role != "strategy_client":
        raise OfflineMigrationError(
            "UNSUPPORTED_CLIENT_ROLE", "$.target_client_template.process_role"
        )

    service_raw = root["target_service_configs"]
    if type(service_raw) is not list or not service_raw:
        raise OfflineMigrationError("MISSING_TARGET_SERVICES", "$.target_service_configs")
    parsed_services: dict[str, ServiceConfig] = {}
    service_public: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(service_raw):
        path = f"$.target_service_configs[{index}]"
        try:
            service = parse_service_config(item)
        except ServiceConfigError as exc:
            raise OfflineMigrationError(exc.code, f"{path}{exc.path[1:]}") from None
        if service.service_id in parsed_services:
            raise OfflineMigrationError("DUPLICATE_SERVICE_ID", f"{path}.service_id")
        parsed_services[service.service_id] = service
        service_public[service.service_id] = service.to_public_dict()

    client_public = client.to_public_dict()
    _validate_explicit_scopes(client_public, service_public, target_scope_refs)
    try:
        plan = validate_deployment(parsed_services, (client,))
    except ServiceConfigError as exc:
        raise OfflineMigrationError(exc.code, exc.path) from None

    return {
        "migration_schema_version": 1,
        "status": "DRAFT_REQUIRES_OPERATOR_REVIEW",
        "client_config": client_public,
        "service_configs": [
            parsed_services[service_id].to_public_dict() for service_id in sorted(parsed_services)
        ],
        "deployment_plan": {
            "service_order": list(plan.service_order),
            "selected_components": [
                {
                    "service_id": row.service_id,
                    "component": row.component,
                    "instance_id": row.instance_id,
                    "bind_ports": list(row.bind_ports),
                }
                for row in plan.component_activations
            ],
            "services_activated": False,
            "remote_readiness_verified": False,
            "authorization_verified": False,
            "automatic_failover_supported": False,
            "external_fencing_supported": False,
        },
        "migration_review": {
            "legacy_endpoints_copied": False,
            "credential_material_copied": False,
            "authorization_granted": False,
            "legacy_recovery_state_transferred": False,
            "operator_recovery_plan_ref": recovery_plan_ref,
            "recovery_plan_resolved_or_executed": False,
            "legacy_cache_policy_transferred": False,
            "ready_to_start": False,
            "requires_operator_review_before_start": True,
            "unmapped_legacy_fields": [
                name for name in ("market_read_timeout_ms", "max_cache_age_ms") if name in legacy
            ],
            "notice": (
                "This draft does not carry legacy endpoint, credential, recovery, or authorization "
                "state. Resolve the referenced security profiles and recovery procedure out of band."
            ),
        },
    }


def _validate_legacy_record(legacy: dict[str, Any]) -> None:
    required = {
        "command_endpoint",
        "market_endpoint",
        "private_endpoint",
        "account_id",
        "strategy_id",
    }
    if not required.issubset(legacy):
        raise OfflineMigrationError("INVALID_LEGACY_CONFIG", "$.legacy_forwarding_config")
    for name in ("command_endpoint", "market_endpoint", "account_id", "strategy_id"):
        value = legacy[name]
        if type(value) is not str or not value.strip():
            raise OfflineMigrationError(
                "INVALID_LEGACY_CONFIG", f"$.legacy_forwarding_config.{name}"
            )
    private = legacy["private_endpoint"]
    if type(private) is not str:
        raise OfflineMigrationError(
            "INVALID_LEGACY_CONFIG", "$.legacy_forwarding_config.private_endpoint"
        )
    if private:
        raise OfflineMigrationError(
            "UNSUPPORTED_PRIVATE_CHANNEL",
            "$.legacy_forwarding_config.private_endpoint",
        )
    for name, default in (
        ("market_read_timeout_ms", _LEGACY_TIMEOUT_DEFAULT),
        ("max_cache_age_ms", _LEGACY_CACHE_AGE_DEFAULT),
    ):
        if name not in legacy:
            continue
        value = legacy[name]
        if type(value) is not int or value < 0:
            raise OfflineMigrationError(
                "INVALID_LEGACY_CONFIG", f"$.legacy_forwarding_config.{name}"
            )
        if value != default:
            raise OfflineMigrationError(
                "UNMAPPED_LEGACY_SETTING", f"$.legacy_forwarding_config.{name}"
            )


def _validate_identity_mapping(legacy: dict[str, Any], identity: dict[str, Any]) -> tuple[str, str]:
    if not _IDENTITY_FIELDS.issubset(identity):
        raise OfflineMigrationError("INCOMPLETE_IDENTITY_MAPPING", "$.identity_mapping")
    for mapping_field, legacy_field in (
        ("legacy_account_id", "account_id"),
        ("legacy_strategy_id", "strategy_id"),
    ):
        value = identity[mapping_field]
        if type(value) is not str or value != legacy[legacy_field]:
            raise OfflineMigrationError(
                "IDENTITY_MAPPING_MISMATCH", f"$.identity_mapping.{mapping_field}"
            )
    account_ref = identity["account_ref"]
    strategy_ref = identity["strategy_ref"]
    if (
        type(account_ref) is not str
        or not account_ref
        or type(strategy_ref) is not str
        or not strategy_ref
    ):
        raise OfflineMigrationError("INCOMPLETE_IDENTITY_MAPPING", "$.identity_mapping")
    return account_ref, strategy_ref


def _validate_scope_refs(value: object) -> frozenset[str]:
    if type(value) is not list or not value:
        raise OfflineMigrationError("MISSING_TARGET_SCOPE_MAPPING", "$.target_scope_refs")
    if any(type(item) is not str or not item for item in value) or len(set(value)) != len(value):
        raise OfflineMigrationError("INVALID_TARGET_SCOPE_MAPPING", "$.target_scope_refs")
    return frozenset(value)


def _validate_recovery_plan_ref(value: object) -> str:
    if type(value) is not str or not _VERSIONED_REFERENCE_RE.fullmatch(value):
        raise OfflineMigrationError("INVALID_RECOVERY_PLAN_REF", "$.recovery_plan_ref")
    return value


def _apply_client_identity_mapping(
    client: dict[str, Any], account_ref: str, strategy_ref: str
) -> None:
    if client.get("process_role") != "strategy_client":
        raise OfflineMigrationError(
            "UNSUPPORTED_CLIENT_ROLE", "$.target_client_template.process_role"
        )
    registration = client.get("registration")
    if registration is None:
        registration = {}
        client["registration"] = registration
    if type(registration) is not dict:
        raise OfflineMigrationError(
            "INVALID_TARGET_CLIENT", "$.target_client_template.registration"
        )
    existing_strategy = registration.get("strategy_ref")
    if existing_strategy is not None and existing_strategy != strategy_ref:
        raise OfflineMigrationError(
            "IDENTITY_MAPPING_MISMATCH",
            "$.target_client_template.registration.strategy_ref",
        )
    registration["strategy_ref"] = strategy_ref

    clients = client.get("clients")
    if type(clients) is not dict:
        raise OfflineMigrationError("INVALID_TARGET_CLIENT", "$.target_client_template.clients")
    account_binding_count = 0
    for alias_index, client_block in enumerate(clients.values()):
        if type(client_block) is not dict:
            continue  # The public parser supplies the precise schema error.
        settings = client_block.get("settings")
        if type(settings) is not dict:
            continue
        bindings = settings.get("bindings")
        if type(bindings) is not list:
            continue
        for binding_index, binding in enumerate(bindings):
            if type(binding) is not dict:
                continue
            mount = binding.get("mount")
            has_account = mount in _ACCOUNT_REQUIRED_MOUNTS or "account_ref" in binding
            if not has_account:
                continue
            account_binding_count += 1
            existing_account = binding.get("account_ref")
            if existing_account is not None and existing_account != account_ref:
                raise OfflineMigrationError(
                    "IDENTITY_MAPPING_MISMATCH",
                    "$.target_client_template.clients"
                    f"[{alias_index}].settings.bindings[{binding_index}].account_ref",
                )
            binding["account_ref"] = account_ref
    if account_binding_count == 0:
        raise OfflineMigrationError(
            "ACCOUNT_MAPPING_UNUSED",
            "$.target_client_template.clients",
        )


def _validate_explicit_scopes(
    client: dict[str, Any],
    services: dict[str, dict[str, Any]],
    target_scope_refs: frozenset[str],
) -> None:
    used: set[str] = set()
    for service in services.values():
        used.add(service["scope_ref"])
        for client_block in service.get("clients", {}).values():
            for binding in client_block["settings"]["bindings"]:
                used.add(binding["scope_ref"])
    for client_block in client.get("clients", {}).values():
        for binding in client_block["settings"]["bindings"]:
            used.add(binding["scope_ref"])
    if used != target_scope_refs:
        raise OfflineMigrationError("UNMAPPED_TARGET_SCOPE", "$.target_scope_refs")


def _require_dict(value: object, path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise OfflineMigrationError("INVALID_NODE", path)
    return value


def _check_exact_fields(
    value: dict[str, Any],
    expected: frozenset[str],
    code: str,
    path: str,
    *,
    require_all: bool = False,
) -> None:
    if any(type(key) is not str for key in value):
        raise OfflineMigrationError(code, path)
    keys = set(value)
    if not keys.issubset(expected) or (require_all and not expected.issubset(keys)):
        raise OfflineMigrationError(code, path)


def _copy_plain_tree(value: object, path: str) -> Any:
    """Copy only JSON-like builtins without invoking caller-defined hooks."""

    count = 0
    active: set[int] = set()

    def visit(node: object, node_path: str, depth: int) -> Any:
        nonlocal count
        count += 1
        if count > 20_000 or depth > 48:
            raise OfflineMigrationError("CONFIGURATION_LIMIT", node_path)
        if node is None or type(node) in {bool, int, float, str}:
            return node
        if type(node) not in {dict, list}:
            raise OfflineMigrationError("INVALID_NODE", node_path)
        identity = id(node)
        if identity in active:
            raise OfflineMigrationError("INVALID_NODE", node_path)
        active.add(identity)
        try:
            if type(node) is dict:
                copied: dict[str, Any] = {}
                for key, item in node.items():
                    if type(key) is not str:
                        raise OfflineMigrationError("INVALID_NODE", node_path)
                    copied[key] = visit(item, f"{node_path}.{key}", depth + 1)
                return copied
            return [
                visit(item, f"{node_path}[{index}]", depth + 1) for index, item in enumerate(node)
            ]
        finally:
            active.remove(identity)

    return visit(value, path, 0)


def _example_request() -> dict[str, Any]:
    """Return a public synthetic request for the documented ``--example``."""

    return {
        "legacy_forwarding_config": {
            "command_endpoint": "tcp://127.0.0.1:7401",
            "market_endpoint": "tcp://127.0.0.1:7402",
            "private_endpoint": "",
            "account_id": "legacy-account-01",
            "strategy_id": "legacy-strategy-01",
        },
        "identity_mapping": {
            "legacy_account_id": "legacy-account-01",
            "account_ref": "account-a01",
            "legacy_strategy_id": "legacy-strategy-01",
            "strategy_ref": "strategy-s01",
        },
        "target_scope_refs": ["account-a01"],
        "recovery_plan_ref": "account-a01-manual-reconcile-v1",
        "target_client_template": {
            "config_schema_version": 6,
            "process_role": "strategy_client",
            "clients": {
                "gateway": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "store.account",
                                "service_id": "account-a01-service",
                                "instance_id": "account-a01-gateway",
                                "scope_ref": "account-a01",
                                "transport_ref": "account-a01-client",
                            }
                        ]
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "account-a01-client": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7601"},
                        "security_profile_ref": "account-a01-client-curve",
                    }
                },
            },
        },
        "target_service_configs": [
            {
                "service_config_schema_version": 1,
                "process_role": "service",
                "service_id": "account-a01-service",
                "scope_ref": "account-a01",
                "transport_ref": "account-a01-listener",
                "registration_policy_ref": "account-a01-policy-v1",
                "service_requirements_ref": "account-a01-readiness-v1",
                "components": {
                    "gateway": {
                        "config_schema_version": 2,
                        "settings": {
                            "instance_id": "account-a01-gateway",
                            "bind_ports": ["service.account_read", "service.provider_session"],
                            "options": {
                                "role": "account_session_worker",
                                "account_session_ref": "account-a01-session",
                            },
                        },
                    }
                },
                "communication": {
                    "config_schema_version": 1,
                    "profiles": {
                        "account-a01-listener": {
                            "backend": "zmq",
                            "config_schema_version": 1,
                            "endpoints": {"rpc_bind": "tcp://127.0.0.1:7601"},
                            "security_profile_ref": "account-a01-server-curve",
                        }
                    },
                },
            }
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Read one JSON request from stdin and emit a draft; ``--example`` is synthetic."""

    parser = argparse.ArgumentParser(description=__doc__)
    examples = parser.add_mutually_exclusive_group()
    examples.add_argument(
        "--example",
        action="store_true",
        help="print a synthetic request (without identity fields in its client template) and result",
    )
    examples.add_argument(
        "--example-reject-private",
        action="store_true",
        help="show the finite rejection for a synthetic legacy private endpoint",
    )
    args = parser.parse_args(argv)
    if args.example:
        request = _example_request()
        result = convert_forwarding_config(request)
        json.dump({"request": request, "result": result}, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    if args.example_reject_private:
        request = _example_request()
        request["legacy_forwarding_config"]["private_endpoint"] = "tcp://127.0.0.1:7403"
        try:
            convert_forwarding_config(request)
        except OfflineMigrationError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        raise AssertionError("synthetic private endpoint must be rejected")

    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print("INVALID_JSON at $", file=sys.stderr)
        return 2
    try:
        result = convert_forwarding_config(request)
    except OfflineMigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
