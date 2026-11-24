"""Standalone authenticated Monitor ingress and Gateway heartbeat service.

The two listeners share a process but have independent authorities and registry
stores. Readiness only reports that both listeners and the local outbox started;
it says nothing about producers, alert processing or trading readiness.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import stat
import threading
import time
from collections.abc import Sequence
from contextlib import suppress
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


def _close_owned_host(host: ServiceHost) -> None:
    # ServiceHost's general-purpose warning includes component instance names.
    # The standalone CLI exposes only its own finite cleanup diagnostic.
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


def _storage_windows(path: Path) -> None:
    """Windows parity of _storage: owner-only, reparse-free storage root."""
    from bt_api_py._win32_security import (
        Win32SecurityUnavailable,
        ensure_directory_chain,
        inspect_owner,
        is_reparse_point,
    )

    try:
        abs_path = Path(os.path.abspath(path))
        if not abs_path.is_absolute():
            raise ValueError()
        # ensure_directory_chain validates each level for reparse points and
        # owner/ACL as it walks; assert_gateway_storage_isolated then re-checks
        # the final directory and every SQLite side file.  Both operate on the
        # same path tree here, so a separate stamp re-walk would only report
        # legitimate mtime changes from creating the directory itself.
        ensure_directory_chain(abs_path, create=True)
        assert_gateway_storage_isolated((abs_path,), (
            abs_path / "events.sqlite",
            abs_path / "ingress-registry.sqlite",
            abs_path / "heartbeat-registry.sqlite",
        ))
    except (Win32SecurityUnavailable, OSError, ValueError):
        raise ValueError("MONITOR_STORAGE_INVALID") from None


def _storage(path: Path) -> None:
    if os.name == "nt":
        return _storage_windows(path)
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

    def __init__(
        self, config: MonitorServiceConfig, *, recovery_fact_coordinator_factory=None
    ) -> None:
        self.config = config
        self._recovery_fact_coordinator_factory = recovery_fact_coordinator_factory
        self._hosts: list[ServiceHost] = []
        self._closed_hosts: set[ServiceHost] = set()
        self._resources: list[object] = []
        self._recovery_worker = None
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
            database = self.config.storage_directory / "events.sqlite"
            outbox = (DurableOutbox(database, timeout_seconds=0.05)
                      if self.config.recovery_duration else DurableOutbox(database))
            self._resources.append(outbox)
            target = MonitorIngressTarget(self.config.outbox_scope, self.config.event_types)
            self._resources.append(target)
            producer_bindings = ()
            if self.config.producer_bindings:
                from bt_api_monitor.service_ingress import MonitorIngressProducerBinding

                producer_bindings = tuple(
                    MonitorIngressProducerBinding(
                        binding.binding_ref, binding.source_ref, binding.identity, binding.scope,
                        binding.outbox_scope, binding.event_types,
                    )
                    for binding in self.config.producer_bindings
                )
                self._resources.append(producer_bindings)
            for role in ("ingress", "heartbeat"):
                listener = getattr(self.config, role)
                raw = _host_config(listener, role)
                operation = "events.append" if role == "ingress" else "monitor.heartbeat"
                if role == "ingress":
                    resources = {"outbox": outbox, "targets": {scope: target}}
                    if producer_bindings:
                        resources["producer_bindings"] = producer_bindings
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
                self._resources.append(resources)
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
            if self.config.recovery_duration:
                from bt_api_py.monitor_recovery_duration_worker import RecoveryDurationWorker

                self._recovery_worker = RecoveryDurationWorker(
                    outbox,
                    self.config.recovery_duration,
                    producer_bindings,
                    fact_coordinator_factory=self._recovery_fact_coordinator_factory,
                )
                self._recovery_worker.start()
            self.state = "ready"
        except BaseException:
            try:
                self.close()
            except BaseException:
                raise MonitorServiceError("MONITOR_START_CLEANUP_FAILED") from None
            self.state = "failed"
            raise MonitorServiceError("MONITOR_START_FAILED") from None
        finally:
            os.umask(old_umask)

    def serve_once(self, timeout_ms: int = 20) -> None:
        if self.state != "ready":
            raise MonitorServiceError("MONITOR_STATE_INVALID")
        if self._recovery_worker is not None and self._recovery_worker.failed:
            raise MonitorServiceError(self._recovery_worker.failure_code or "MONITOR_RECOVERY_FAILED")
        for host in self._hosts:
            host.serve_once(timeout_ms=timeout_ms)

    def recovery_duration_snapshot(self, consumer_id: str) -> dict:
        if self.state != "ready" or self._recovery_worker is None:
            raise MonitorServiceError("MONITOR_RECOVERY_UNAVAILABLE")
        return self._recovery_worker.snapshot(consumer_id)

    def close(self) -> None:
        if self._recovery_worker is not None:
            try:
                stopped = (self._recovery_worker.stop() is True
                           and self._recovery_worker.stopped is True)
            except BaseException:
                stopped = False
            if not stopped:
                self.state = "close_failed"
                _RETAINED_HOSTS.add(self)
                raise MonitorServiceError("MONITOR_CLOSE_FAILED")
        failed = False
        for host in reversed(self._hosts):
            if host in self._closed_hosts:
                if (type(host) is ServiceHost and host._state == "closed"
                        and host._last_cleanup_failures == ()):
                    continue
                self._closed_hosts.discard(host)
            try:
                # Only the owned implementation's successful close, followed
                # by its explicit cleanup state, confirms this owner is closed.
                if type(host) is not ServiceHost or _close_owned_host(host) is not None:
                    failed = True
                    continue
                if host._state != "closed" or host._last_cleanup_failures != ():
                    failed = True
                    continue
                self._closed_hosts.add(host)
            except BaseException:
                failed = True
        self.state = "close_failed" if failed else "closed"
        if failed:
            _RETAINED_HOSTS.add(self)
            raise MonitorServiceError("MONITOR_CLOSE_FAILED")
        _RETAINED_HOSTS.discard(self)


# A caller can lose its reference after a failed start or close. The original
# owners remain reachable until that same MonitorServiceHost confirms cleanup.
_RETAINED_HOSTS: set[MonitorServiceHost] = set()


def _close_confirmed(host: MonitorServiceHost) -> bool:
    """Close the original aggregate and check every owner's confirmation."""
    try:
        if type(host) is not MonitorServiceHost or host.close() is not None:
            return False
        return (
            host.state == "closed"
            and (host._recovery_worker is None or host._recovery_worker.stopped is True)
            and len(host._closed_hosts) == len(host._hosts)
            and all(type(owner) is ServiceHost and owner in host._closed_hosts
                    and owner._state == "closed" and owner._last_cleanup_failures == ()
                    for owner in host._hosts)
        )
    except BaseException:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "validate"):
        sub.add_parser(command).add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_monitor_service_config(args.config)
    except Exception:
        print("MONITOR_FAILED", flush=True)
        return 1
    if args.command == "validate":
        print("MONITOR_CONFIG_VALID", flush=True)
        return 0
    host = MonitorServiceHost(config)
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    previous = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, stop)
    except Exception:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        print("MONITOR_FAILED", flush=True)
        return 1

    failed = False
    confirmed = False
    try:
        try:
            host.start()
            print("MONITOR_LISTENERS_READY", flush=True)
            while not stopping:
                host.serve_once()
        except BaseException:
            failed = True
        # A failed start may already have attempted rollback. Do not repeat an
        # uncertain close until the operator signals a retry.
        pending = host.state == "close_failed"
        if not pending:
            stopping = False
            pending = not _close_confirmed(host)
        if pending:
            _RETAINED_HOSTS.add(host)
            with suppress(Exception):
                print("MONITOR_CLEANUP_PENDING", flush=True)
            while pending:
                if not stopping:
                    with suppress(BaseException):
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
        print("MONITOR_FAILED", flush=True)
        return 1
    print("MONITOR_STOPPED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
