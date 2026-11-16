"""Explicit protected configuration for the independent recovery Monitor relay."""

from __future__ import annotations

import math

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.ctp_gateway_monitor_config import _endpoint, _key, _text
from bt_api_py.service_config import ServiceConfig, parse_service_config
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory


class CtpGatewayRecoveryRelayConfigError(ValueError):
    def __init__(self):
        super().__init__("INVALID_RECOVERY_MONITOR_RELAY_CONFIG")


def build_recovery_monitor_settings(raw, *, config, authority, account_scope, observation):
    """Build a dedicated lazy runtime factory; no sockets or files are opened."""
    try:
        from bt_api_py.gateway_recovery_monitor_worker import GatewayRecoveryMonitorSettings
        from bt_api_py.gateway_recovery_observation import GatewayRecoveryObservationSettings

        fields = {
            "monitor_identity",
            "instance_id",
            "scope_ref",
            "rpc_connect",
            "server_curve_public_key",
            "client_curve_public_key",
            "client_curve_secret_key",
            "outbox_scope",
            "binding_ref",
            "checkpoint_path",
            "poll_interval_seconds",
            "start_timeout_seconds",
            "close_timeout_seconds",
            "request_timeout_seconds",
            "max_records",
            "max_bytes",
        }
        if (
            type(raw) is not dict
            or set(raw) != fields
            or type(config) is not ServiceConfig
            or type(authority) is not TrustedServiceAuthority
            or type(observation) is not GatewayRecoveryObservationSettings
            or observation.qualification_mode != "local_clock_v1"
            or observation.scope_ref != account_scope
        ):
            raise ValueError()
        identity = raw["monitor_identity"]
        if (
            type(identity) is not dict
            or set(identity) != {"role", "principal_ref", "actor_ref", "delegate_ref"}
            or identity["role"] != "service"
            or identity["delegate_ref"] is not None
        ):
            raise ValueError()
        peer = WireIdentity(
            WireRole.SERVICE, _text(identity["principal_ref"]), _text(identity["actor_ref"])
        )
        gateway = authority.identity
        if (
            type(gateway) is not WireIdentity
            or gateway.role is not WireRole.SERVICE
            or gateway.delegate_ref is not None
            or gateway.actor_ref != config.service_id
            or peer.actor_ref == gateway.actor_ref
            or peer.principal_ref == gateway.principal_ref
        ):
            raise ValueError()
        scope = WireScope(
            _text(authority.scope.execution_domain_ref), account_ref=_text(account_scope)
        )
        instance_id, scope_ref = _text(raw["instance_id"]), _text(raw["scope_ref"])
        endpoint = _endpoint(raw["rpc_connect"])
        outbox_scope, binding_ref = _text(raw["outbox_scope"]), _text(raw["binding_ref"])
        server, client, secret = (
            _key(raw[name])
            for name in (
                "server_curve_public_key",
                "client_curve_public_key",
                "client_curve_secret_key",
            )
        )
        import zmq

        if server == client or zmq.curve_public(secret) != client:
            raise ValueError()
        timeout = raw["request_timeout_seconds"]
        poll = raw["poll_interval_seconds"]
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0.001 <= timeout <= 120
            or type(poll) not in (int, float)
            or not math.isfinite(poll)
            or not 0 < poll <= 60
        ):
            raise ValueError()
        timeout_ms = max(1, min(120_000, int(timeout * 1000)))
        public = config.to_public_dict()
        public.pop("clients", None)
        profiles = public["communication"]["profiles"]
        transport_ref = "recovery-monitor-connect"
        while transport_ref in profiles:
            transport_ref += "-x"
        security_ref = "recovery-monitor-curve"
        profiles[transport_ref] = {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": endpoint},
            "security_profile_ref": security_ref,
            "options": {"request_timeout_ms": timeout_ms},
        }
        public["clients"] = {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "upstream.telemetry",
                            "service_id": peer.actor_ref,
                            "instance_id": instance_id,
                            "scope_ref": scope_ref,
                            "transport_ref": transport_ref,
                            "required": True,
                        }
                    ],
                    "options": {
                        "request_timeout_ms": timeout_ms,
                        "heartbeat_interval_seconds": max(1, math.ceil(poll)),
                    },
                },
            }
        }
        client_config = parse_service_config(public)
        keys = ClientCurveKeys(server, client, secret)

        def resolve_scope(ref):
            if ref != scope_ref:
                raise ValueError("INVALID_MONITOR_SCOPE")
            return scope

        def resolve_peer(service_id):
            if service_id != peer.actor_ref:
                raise ValueError("INVALID_MONITOR_PEER")
            return peer

        def factory(*, require_unregister_ack=True):
            if require_unregister_ack is not True:
                raise ValueError("INVALID_RELAY_RUNTIME")
            return ServiceClientRuntime(
                client_config,
                identity=gateway,
                scope_resolver=resolve_scope,
                peer_resolver=resolve_peer,
                transport_factory=ZmqTransportFactory({security_ref: keys}),
                requested_operations={"upstream.telemetry": frozenset({"events.append"})},
                require_unregister_ack=True,
            )

        return GatewayRecoveryMonitorSettings(
            runtime_factory=factory,
            expected_peer=peer,
            scope_ref=scope_ref,
            outbox_scope=outbox_scope,
            binding_ref=binding_ref,
            source_ref=observation.source_ref,
            journal_path=observation.journal_path,
            journal_scope_ref=observation.scope_ref,
            checkpoint_path=raw["checkpoint_path"],
            poll_interval_seconds=raw["poll_interval_seconds"],
            start_timeout_seconds=raw["start_timeout_seconds"],
            close_timeout_seconds=raw["close_timeout_seconds"],
            max_records=raw["max_records"],
            max_bytes=raw["max_bytes"],
        )
    except Exception:
        raise CtpGatewayRecoveryRelayConfigError() from None
