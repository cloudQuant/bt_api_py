"""Standalone authenticated Monitor ingress and Gateway heartbeat service.

The two listeners share a process but have independent authorities and registry
stores. Readiness only reports that both listeners and the local outbox started;
it says nothing about producers, alert processing or trading readiness.
"""

from __future__ import annotations

import argparse
import os
import signal
import stat
from collections.abc import Sequence
from pathlib import Path

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_storage_trust import assert_gateway_storage_isolated
from bt_api_py.monitor_service_config import (
    Listener,
    MonitorServiceConfig,
    load_monitor_service_config,
    read_bounded,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant


class MonitorServiceError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _storage(path: Path) -> None:
    fds = []
    chain = []
    try:
        if os.name != "posix" or not path.is_absolute():
            raise ValueError()
        fds.append(os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for name in path.parts[1:]:
            parent_info = os.fstat(fds[-1])
            if parent_info.st_uid not in (0, os.getuid()) or (
                parent_info.st_mode & 0o022 and not parent_info.st_mode & stat.S_ISVTX
            ):
                raise ValueError()
            try:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1])
            except FileNotFoundError:
                os.mkdir(name, 0o700, dir_fd=fds[-1])
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1])
            chain.append((fds[-1], name, fd))
            fds.append(fd)
        info = os.fstat(fds[-1])
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError()
        files = tuple(
            path / name
            for name in ("events.sqlite", "ingress-registry.sqlite", "heartbeat-registry.sqlite")
        )
        assert_gateway_storage_isolated((path,), files)
        for file in files:
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    entry = os.stat(file.name + suffix, dir_fd=fds[-1], follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if (
                    not stat.S_ISREG(entry.st_mode)
                    or entry.st_uid != os.getuid()
                    or entry.st_mode & 0o077
                    or entry.st_nlink != 1
                ):
                    raise ValueError()
        for parent, name, fd in chain:
            actual = os.fstat(fd)
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(entry.st_mode) or (
                actual.st_dev,
                actual.st_ino,
                actual.st_mode,
                actual.st_uid,
            ) != (entry.st_dev, entry.st_ino, entry.st_mode, entry.st_uid):
                raise ValueError()
    except Exception:
        raise MonitorServiceError("MONITOR_STORAGE_INVALID") from None
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _keys(config: MonitorServiceConfig):
    import zmq
    from zmq.utils import z85

    result = {}
    servers = set()
    client_identities = {}
    principal_identities = {}
    try:
        for role in ("ingress", "heartbeat"):
            listener = getattr(config, role)
            public = read_bounded(listener.public_key_file, 4096, protected=True).strip()
            secret = read_bounded(listener.secret_key_file, 4096, protected=True).strip()
            z85.decode(public)
            if zmq.curve_public(secret) != public or public in servers:
                raise ValueError()
            servers.add(public)
            principals = {}
            for principal in listener.principals:
                key = read_bounded(principal.public_key_file, 4096, protected=True).strip()
                z85.decode(key)
                identity = (principal.principal_ref, principal.actor_ref)
                if (
                    key in principals.values()
                    or (key in client_identities and client_identities[key] != identity)
                    or (
                        principal.principal_ref in principal_identities
                        and principal_identities[principal.principal_ref] != identity
                    )
                ):
                    raise ValueError()
                client_identities[key] = identity
                principal_identities[principal.principal_ref] = identity
                principals[principal.principal_ref] = key
            result[role] = (public, secret, principals)
        if servers.intersection(client_identities):
            raise ValueError()
    except Exception:
        raise MonitorServiceError("MONITOR_KEYS_INVALID") from None
    return result


def _host_config(listener: Listener, role: str):
    options = {"role": "durable_ingress" if role == "ingress" else "gateway_heartbeat"}
    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": listener.service_id,
            "scope_ref": "root-scope",
            "transport_ref": "listener",
            "registration_policy_ref": "policy",
            "service_requirements_ref": "requirements",
            "components": {
                "monitor": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": listener.instance_id,
                        "bind_ports": ["service.telemetry_ingress"],
                        "options": options,
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "listener": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": listener.rpc_bind},
                        "security_profile_ref": "server",
                    }
                },
            },
        }
    )


def _authority(config, listener, scope, keys, operation):
    public, secret, principals = keys
    grants = {}
    for principal in listener.principals:
        identity = WireIdentity(WireRole.SERVICE, principal.principal_ref, principal.actor_ref)
        grant = ResourceGrant(
            listener.instance_id,
            "service.telemetry_ingress",
            listener.scope_ref,
            scope,
            frozenset({operation}),
        )
        grants[principal.principal_ref] = (PrincipalGrant(identity, (grant,)),)
    return TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, listener.principal_ref, listener.service_id),
        scope=scope,
        scope_ref=config.scope_ref,
        scope_version="v1",
        security_ref="server",
        security_version="v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="v1",
        curve_public_key=public,
        curve_secret_key=secret,
        principals={key: principal for principal, key in principals.items()},
        grants=grants,
    )


class MonitorServiceHost:
    """Own both listeners on one thread, including failure and shutdown cleanup."""

    def __init__(self, config: MonitorServiceConfig) -> None:
        self.config = config
        self._hosts: list[ServiceHost] = []
        self.state = "new"

    def start(self) -> None:
        if self.state != "new":
            raise MonitorServiceError("MONITOR_STATE_INVALID")
        from bt_api_monitor.durable import DurableOutbox
        from bt_api_monitor.service_gateway_heartbeat import GatewayHeartbeatService
        from bt_api_monitor.service_ingress import MonitorIngressTarget

        keys = _keys(self.config)
        _storage(self.config.storage_directory)
        scope = WireScope(self.config.execution_domain_ref, account_ref=self.config.account_ref)
        old_umask = os.umask(0o077)
        try:
            outbox = DurableOutbox(self.config.storage_directory / "events.sqlite")
            target = MonitorIngressTarget(self.config.outbox_scope, self.config.event_types)
            for role in ("ingress", "heartbeat"):
                listener = getattr(self.config, role)
                raw = _host_config(listener, role)
                operation = "events.append" if role == "ingress" else "monitor.heartbeat"
                if role == "ingress":
                    resources = {"outbox": outbox, "targets": {scope: target}}
                else:
                    identities = frozenset(
                        (WireIdentity(WireRole.SERVICE, p.principal_ref, p.actor_ref), scope)
                        for p in listener.principals
                    )
                    resources = {
                        "service": GatewayHeartbeatService(
                            monitor_principal=listener.principal_ref, targets=identities
                        )
                    }
                host = ServiceHost(
                    raw,
                    _authority(raw, listener, scope, keys[role], operation),
                    self.config.storage_directory / f"{role}-registry.sqlite",
                    lambda *_, values=resources: ComponentResources.borrowed(
                        values, ready=lambda: True
                    ),
                )
                self._hosts.append(host)
                host.start()
            self.state = "ready"
        except Exception:
            try:
                self.close()
            except Exception:
                raise MonitorServiceError("MONITOR_START_CLEANUP_FAILED") from None
            self.state = "failed"
            raise MonitorServiceError("MONITOR_START_FAILED") from None
        finally:
            os.umask(old_umask)

    def serve_once(self, timeout_ms: int = 20) -> None:
        if self.state != "ready":
            raise MonitorServiceError("MONITOR_STATE_INVALID")
        for host in self._hosts:
            host.serve_once(timeout_ms=timeout_ms)

    def close(self) -> None:
        failed = False
        for host in reversed(self._hosts):
            try:
                if host.state not in {"closed", "failed"}:
                    host.close()
            except Exception:
                failed = True
        self.state = "close_failed" if failed else "closed"
        if failed:
            raise MonitorServiceError("MONITOR_CLOSE_FAILED")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "validate"):
        sub.add_parser(command).add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_monitor_service_config(args.config)
        if args.command == "validate":
            print("MONITOR_CONFIG_VALID", flush=True)
            return 0
        host = MonitorServiceHost(config)
        stopping = False

        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True

        previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            host.start()
            print("MONITOR_LISTENERS_READY", flush=True)
            while not stopping:
                host.serve_once()
        finally:
            try:
                host.close()
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
        print("MONITOR_STOPPED", flush=True)
        return 0
    except Exception:
        print("MONITOR_FAILED", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
