"""Trusted local bundle composition for the Gateway required Monitor client.

The public Gateway manifest is copied before adding the dedicated client. Keys
remain in the private factory closure; no network or thread starts here.
"""

from __future__ import annotations

import ipaddress
import math

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.monitor_heartbeat_wire import MONITOR_HEARTBEAT_OPERATIONS

from bt_api_py.gateway_required_monitor import RequiredMonitorSettings
from bt_api_py.service_config import ServiceConfig, parse_service_config
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory


class CtpGatewayMonitorConfigError(ValueError):
    def __init__(self):
        super().__init__("INVALID_REQUIRED_MONITOR_CONFIG")


def _text(value):
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value.encode("utf-8", errors="strict")) > 128
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError()
    return value


def _key(value):
    from zmq.utils import z85

    if type(value) is not str:
        raise ValueError()
    result = value.encode("ascii", errors="strict")
    if len(result) != 40 or len(z85.decode(result)) != 32:
        raise ValueError()
    return result


def _endpoint(value):
    value = _text(value)
    prefix, port = value.rsplit(":", 1)
    if not prefix.startswith("tcp://"):
        raise ValueError()
    address = ipaddress.ip_address(prefix[6:])
    if (
        address.version != 4
        or not address.is_loopback
        or not port.isascii()
        or not port.isdigit()
        or str(int(port)) != port
        or not 1 <= int(port) <= 65535
    ):
        raise ValueError()
    return value


def build_required_monitor_settings(raw, *, config, authority, account_scope):
    """Validate a protected declaration and bind its exact identities/scope."""
    try:
        if (
            type(raw) is not dict
            or set(raw)
            != {
                "monitor_identity",
                "instance_id",
                "scope_ref",
                "rpc_connect",
                "server_curve_public_key",
                "client_curve_public_key",
                "client_curve_secret_key",
                "interval_seconds",
                "timeout_seconds",
                "automatic_resume",
            }
            or type(config) is not ServiceConfig
            or type(authority) is not TrustedServiceAuthority
        ):
            raise ValueError()
        identity = raw["monitor_identity"]
        if type(identity) is not dict or set(identity) != {
            "role",
            "principal_ref",
            "actor_ref",
            "delegate_ref",
        }:
            raise ValueError()
        if identity["role"] != "service" or identity["delegate_ref"] is not None:
            raise ValueError()
        monitor_identity = WireIdentity(
            WireRole.SERVICE, _text(identity["principal_ref"]), _text(identity["actor_ref"])
        )
        gateway_identity = authority.identity
        if (
            type(gateway_identity) is not WireIdentity
            or gateway_identity.role is not WireRole.SERVICE
            or gateway_identity.delegate_ref is not None
            or gateway_identity.actor_ref != config.service_id
            or monitor_identity.actor_ref == gateway_identity.actor_ref
            or monitor_identity.principal_ref == gateway_identity.principal_ref
        ):
            raise ValueError()
        scope = WireScope(
            _text(authority.scope.execution_domain_ref), account_ref=_text(account_scope)
        )
        instance_id, scope_ref = _text(raw["instance_id"]), _text(raw["scope_ref"])
        endpoint = _endpoint(raw["rpc_connect"])
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
        interval, timeout = raw["interval_seconds"], raw["timeout_seconds"]
        if (
            any(type(n) not in (int, float) or not math.isfinite(n) for n in (interval, timeout))
            or not 0 < interval < timeout <= 300
            or timeout < 0.001
            or type(raw["automatic_resume"]) is not bool
        ):
            raise ValueError()
        public = config.to_public_dict()
        public.pop("clients", None)
        profiles = public["communication"]["profiles"]
        transport_ref = "required-monitor-connect"
        while transport_ref in profiles:
            transport_ref += "-x"
        security_ref = "required-monitor-curve"
        timeout_ms = min(120_000, int(timeout * 1000))
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
                            "service_id": monitor_identity.actor_ref,
                            "instance_id": instance_id,
                            "scope_ref": scope_ref,
                            "transport_ref": transport_ref,
                            "required": True,
                        }
                    ],
                    "options": {
                        "request_timeout_ms": timeout_ms,
                        "heartbeat_interval_seconds": max(1, math.ceil(interval)),
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
            if service_id != monitor_identity.actor_ref:
                raise ValueError("INVALID_MONITOR_PEER")
            return monitor_identity

        def factory():
            return ServiceClientRuntime(
                client_config,
                identity=gateway_identity,
                scope_resolver=resolve_scope,
                peer_resolver=resolve_peer,
                transport_factory=ZmqTransportFactory({security_ref: keys}),
                requested_operations={"upstream.telemetry": MONITOR_HEARTBEAT_OPERATIONS},
            )

        return RequiredMonitorSettings(
            factory,
            gateway_identity,
            monitor_identity,
            scope,
            interval_seconds=interval,
            timeout_seconds=timeout,
            automatic_resume=raw["automatic_resume"],
        )
    except Exception:
        raise CtpGatewayMonitorConfigError() from None
