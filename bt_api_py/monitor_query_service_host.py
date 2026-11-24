"""Standalone read-only Monitor observation-query service.

The local operator configuration selects one event outbox and one exact wire
scope. The host exposes only the authenticated ``observation.query`` operation;
it never owns or modifies the Monitor ingress database.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import threading
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from .monitor_service_config import read_bounded
from .risk_service_config import RiskServiceConfigError, load_document, resolve_path
from .service_components import ComponentResources
from .service_config import ServiceConfig, parse_service_config
from .service_host import ServiceHost, TrustedServiceAuthority
from .service_registry import PrincipalGrant, ResourceGrant

SCHEMA = "bt_api_py.monitor_query_service.v1"
_MAX_CONFIG_BYTES = 1_048_576
_MAX_EVENT_TYPES = 16
_MAX_EVENT_TYPE_BYTES = 128
_MAX_SCOPE_BYTES = 256
_MONITOR_QUERY_PORT = "service.monitor_query"
_MONITOR_QUERY_OPERATION = "observation.query"
_RETAINED_HOSTS: set[ServiceHost] = set()


class MonitorQueryServiceError(ValueError):
    """Finite, value-free query-host configuration or startup error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class MonitorQueryServiceConfig:
    service: ServiceConfig
    service_identity: WireIdentity
    client_identity: WireIdentity
    scope: WireScope
    events_db: Path
    registry_path: Path
    outbox_scope: str
    event_types: tuple[str, ...]
    server_public_key_file: Path
    server_secret_key_file: Path
    client_public_key_file: Path
    hmac_key_hex_file: Path
    instance_id: str
    security_ref: str


def _map(value: object, keys: set[str]) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise MonitorQueryServiceError("CONFIG_INVALID")
    return value


def _text(value: object, *, maximum: int = _MAX_SCOPE_BYTES) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise MonitorQueryServiceError("CONFIG_INVALID")
    try:
        if len(value.encode("utf-8", errors="strict")) > maximum:
            raise MonitorQueryServiceError("CONFIG_INVALID")
    except UnicodeError:
        raise MonitorQueryServiceError("CONFIG_INVALID") from None
    return value


def _path(value: object, base: Path) -> Path:
    try:
        return resolve_path(value, base)
    except (RiskServiceConfigError, OSError, ValueError):
        raise MonitorQueryServiceError("CONFIG_INVALID") from None


def parse_config(raw: object, *, path: str | Path) -> MonitorQueryServiceConfig:
    """Parse all static fields without reading keys, databases, or registries."""

    top = _map(
        raw,
        {
            "schema",
            "service_config",
            "scope",
            "service_identity",
            "client_identity",
            "keys",
            "storage",
        },
    )
    if top["schema"] != SCHEMA:
        raise MonitorQueryServiceError("CONFIG_INVALID")

    service_raw = top["service_config"]
    try:
        service = parse_service_config(service_raw)
        service_document = service.to_public_dict()
    except Exception:
        raise MonitorQueryServiceError("CONFIG_INVALID") from None
    if set(service_document["components"]) != {"monitor"} or service.clients:
        raise MonitorQueryServiceError("CONFIG_INVALID")
    monitor = service_document["components"]["monitor"]["settings"]
    if (
        tuple(monitor["bind_ports"]) != (_MONITOR_QUERY_PORT,)
        or monitor["options"].get("role") != "observation_query"
    ):
        raise MonitorQueryServiceError("CONFIG_INVALID")
    instance_id = _text(monitor["instance_id"])

    scope_raw = _map(top["scope"], {"execution_domain_ref", "account_ref"})
    try:
        scope = WireScope(
            execution_domain_ref=_text(scope_raw["execution_domain_ref"]),
            account_ref=_text(scope_raw["account_ref"]),
        )
    except (TypeError, ValueError):
        raise MonitorQueryServiceError("CONFIG_INVALID") from None

    service_identity_raw = _map(top["service_identity"], {"principal_ref", "actor_ref"})
    client_identity_raw = _map(top["client_identity"], {"principal_ref", "actor_ref"})
    try:
        service_identity = WireIdentity(
            WireRole.SERVICE,
            _text(service_identity_raw["principal_ref"]),
            _text(service_identity_raw["actor_ref"]),
        )
        client_identity = WireIdentity(
            WireRole.APPLICATION_CLIENT,
            _text(client_identity_raw["principal_ref"]),
            _text(client_identity_raw["actor_ref"]),
        )
    except (TypeError, ValueError):
        raise MonitorQueryServiceError("CONFIG_INVALID") from None
    if (
        service_identity.actor_ref != service.service_id
        or service_identity.principal_ref == client_identity.principal_ref
    ):
        raise MonitorQueryServiceError("CONFIG_INVALID")

    keys_raw = _map(
        top["keys"],
        {
            "server_public_key_file",
            "server_secret_key_file",
            "client_public_key_file",
            "hmac_key_hex_file",
        },
    )
    storage_raw = _map(
        top["storage"], {"events_db", "registry_path", "outbox_scope", "event_types"}
    )
    event_types_raw = storage_raw["event_types"]
    if type(event_types_raw) is not list or not 1 <= len(event_types_raw) <= _MAX_EVENT_TYPES:
        raise MonitorQueryServiceError("CONFIG_INVALID")
    event_types = tuple(_text(value, maximum=_MAX_EVENT_TYPE_BYTES) for value in event_types_raw)
    if len(set(event_types)) != len(event_types):
        raise MonitorQueryServiceError("CONFIG_INVALID")
    outbox_scope = _text(storage_raw["outbox_scope"])

    config_path = Path(os.path.abspath(path))
    base = config_path.parent
    paths = {
        "config": config_path,
        "events_db": _path(storage_raw["events_db"], base),
        "registry_path": _path(storage_raw["registry_path"], base),
        "server_public": _path(keys_raw["server_public_key_file"], base),
        "server_secret": _path(keys_raw["server_secret_key_file"], base),
        "client_public": _path(keys_raw["client_public_key_file"], base),
        "hmac_key": _path(keys_raw["hmac_key_hex_file"], base),
    }
    normalized_paths = {os.path.normcase(os.path.abspath(value)) for value in paths.values()}
    if len(normalized_paths) != len(paths) or paths["events_db"] == paths["registry_path"]:
        raise MonitorQueryServiceError("CONFIG_INVALID")

    profiles = service_document["communication"]["profiles"]
    security_ref = profiles[service.transport_ref]["security_profile_ref"]
    return MonitorQueryServiceConfig(
        service=service,
        service_identity=service_identity,
        client_identity=client_identity,
        scope=scope,
        events_db=paths["events_db"],
        registry_path=paths["registry_path"],
        outbox_scope=outbox_scope,
        event_types=event_types,
        server_public_key_file=paths["server_public"],
        server_secret_key_file=paths["server_secret"],
        client_public_key_file=paths["client_public"],
        hmac_key_hex_file=paths["hmac_key"],
        instance_id=instance_id,
        security_ref=security_ref,
    )


def load_config(path: str | Path) -> MonitorQueryServiceConfig:
    """Read and validate the operator JSON/YAML document only."""

    try:
        config_path = Path(path)
        document = load_document(
            read_bounded(config_path, _MAX_CONFIG_BYTES, protected=False).decode("utf-8")
        )
        return parse_config(document, path=config_path)
    except MonitorQueryServiceError:
        raise
    except Exception:
        raise MonitorQueryServiceError("CONFIG_INVALID") from None


def _read_key_material(config: MonitorQueryServiceConfig) -> tuple[bytes, bytes, bytes, bytes]:
    try:
        import zmq
        from zmq.utils import z85

        server_public = read_bounded(config.server_public_key_file, 4096, protected=True).strip()
        server_secret = read_bounded(config.server_secret_key_file, 4096, protected=True).strip()
        client_public = read_bounded(config.client_public_key_file, 4096, protected=True).strip()
        encoded_hmac = read_bounded(config.hmac_key_hex_file, 66, protected=True)
        if encoded_hmac.endswith(b"\r\n"):
            encoded_hmac = encoded_hmac[:-2]
        elif encoded_hmac.endswith(b"\n"):
            encoded_hmac = encoded_hmac[:-1]
        if re.fullmatch(rb"[0-9a-fA-F]{64}", encoded_hmac) is None:
            raise ValueError()
        hmac_key = bytes.fromhex(encoded_hmac.decode("ascii"))
        z85.decode(server_public)
        z85.decode(client_public)
        z85.decode(server_secret)
        if (
            zmq.curve_public(server_secret) != server_public
            or server_public == client_public
            or len(hmac_key) != 32
        ):
            raise ValueError()
        return server_public, server_secret, client_public, hmac_key
    except Exception:
        raise MonitorQueryServiceError("KEYS_INVALID") from None


def _build_host(config: MonitorQueryServiceConfig) -> ServiceHost:
    from bt_api_monitor.durable import DurableOutbox
    from bt_api_monitor.service_component import MonitorReadTarget

    server_public, server_secret, client_public, hmac_key = _read_key_material(config)
    try:
        outbox = DurableOutbox(config.events_db, read_only=True)
        target = MonitorReadTarget(config.outbox_scope, config.event_types)
    except Exception:
        raise MonitorQueryServiceError("OUTBOX_UNAVAILABLE") from None

    principal_grant = PrincipalGrant(
        config.client_identity,
        (
            ResourceGrant(
                config.instance_id,
                _MONITOR_QUERY_PORT,
                config.service.scope_ref,
                config.scope,
                frozenset({_MONITOR_QUERY_OPERATION}),
            ),
        ),
    )
    authority = TrustedServiceAuthority(
        identity=config.service_identity,
        scope=config.scope,
        scope_ref=config.service.scope_ref,
        scope_version="v1",
        security_ref=config.security_ref,
        security_version="v1",
        registration_policy_ref=config.service.registration_policy_ref,
        registration_policy_version="v1",
        service_requirements_ref=config.service.service_requirements_ref,
        service_requirements_version="v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: config.client_identity.principal_ref},
        grants={config.client_identity.principal_ref: (principal_grant,)},
    )
    resources = {
        "outbox": outbox,
        "targets": {config.scope: target},
        "principals": frozenset({config.client_identity.principal_ref}),
        "hmac_key": hmac_key,
    }
    try:
        return ServiceHost(
            config.service,
            authority,
            config.registry_path,
            lambda *_, values=resources: ComponentResources.borrowed(
                values, ready=lambda: values["outbox"].is_read_only is True
            ),
        )
    except Exception:
        raise MonitorQueryServiceError("HOST_CONFIGURATION_INVALID") from None


def _close_owned_host(host: ServiceHost) -> None:
    logger = logging.getLogger("bt_api_py.service_host")
    owner_thread = threading.get_ident()

    def private_warning(record):
        return not (
            record.thread == owner_thread
            and record.msg == "ServiceHost close incomplete: %s"
        )

    logger.addFilter(private_warning)
    try:
        return host.close()
    finally:
        logger.removeFilter(private_warning)


def _close_confirmed(host: ServiceHost) -> bool:
    try:
        if type(host) is not ServiceHost or _close_owned_host(host) is not None:
            return False
        return host.state == "closed" and host.summary["cleanup_failures"] == ()
    except BaseException:
        return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Monitor observation query host")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate configuration without opening resources")
    validate.add_argument("--config", required=True)
    run = sub.add_parser("run", help="run the authenticated read-only query listener")
    run.add_argument("--config", required=True)
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="validate configuration without reading keys or opening files, sockets, or SQLite",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except Exception:
        print("MONITOR_OBSERVATION_QUERY_CONFIG_INVALID", flush=True)
        return 1
    if args.command == "validate" or args.dry_run:
        print("MONITOR_OBSERVATION_QUERY_CONFIG_VALID", flush=True)
        return 0

    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    previous = {}
    try:
        for sig in signals:
            previous[sig] = signal.signal(sig, stop)
    except Exception:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        print("MONITOR_OBSERVATION_QUERY_FAILED", flush=True)
        return 1

    host = None
    failed = False
    confirmed = False
    try:
        try:
            host = _build_host(config)
            host.start()
            print("MONITOR_OBSERVATION_QUERY_READY", flush=True)
            while not stopping:
                host.serve_once(timeout_ms=50)
        except BaseException:
            failed = True
        if host is None:
            confirmed = True
        else:
            pending = host.state == "close_failed"
            if not pending:
                stopping = False
                pending = not _close_confirmed(host)
            if pending:
                _RETAINED_HOSTS.add(host)
                with suppress(Exception):
                    print("MONITOR_OBSERVATION_QUERY_CLEANUP_PENDING", flush=True)
                while pending:
                    if not stopping:
                        with suppress(Exception):
                            time.sleep(0.1)
                        continue
                    stopping = False
                    pending = not _close_confirmed(host)
                    if pending:
                        _RETAINED_HOSTS.add(host)
            _RETAINED_HOSTS.discard(host)
            confirmed = True
    finally:
        if confirmed:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    if failed:
        print("MONITOR_OBSERVATION_QUERY_FAILED", flush=True)
        return 1
    print("MONITOR_OBSERVATION_QUERY_STOPPED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
