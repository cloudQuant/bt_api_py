"""Strict, offline deployment configuration for the standalone Monitor host.

Validation resolves references but does not read keys, create storage or connect.
Only the local deployment operator supplies identities and grants.
"""

from __future__ import annotations

import ipaddress
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bt_api_py.risk_service_config import RiskServiceConfigError, load_document, resolve_path

SCHEMA = "bt_api_monitor.service.v1"
_MAX_CONFIG_BYTES = 1_048_576


def _stamp(info: os.stat_result) -> tuple:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def read_bounded(path: Path, limit: int, *, protected: bool) -> bytes:
    """Bounded FD read; key reads refuse symlinks throughout the path."""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("FILE_UNAVAILABLE")
    path = Path(os.path.abspath(path))
    fds = []
    chain = []
    fd = None
    try:
        if protected:
            fds.append(os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            for name in path.parts[1:-1]:
                parent = fds[-1]
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                fds.append(child)
                chain.append((parent, name, child, _stamp(os.fstat(child))))
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fds[-1])
        else:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > limit
            or (
                protected
                and (before.st_uid != os.getuid() or before.st_mode & 0o077 or before.st_nlink != 1)
            )
        ):
            raise ValueError()
        data = bytearray()
        while len(data) <= limit:
            block = os.read(fd, min(65536, limit + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(fd)
        entry = (
            os.stat(path.name, dir_fd=fds[-1], follow_symlinks=False)
            if protected
            else os.stat(path, follow_symlinks=False)
        )
        if (
            len(data) > limit
            or len(data) != before.st_size
            or _stamp(before) != _stamp(after)
            or _stamp(after) != _stamp(entry)
        ):
            raise ValueError()
        for parent, name, child, stamp in chain:
            if (
                _stamp(os.fstat(child)) != stamp
                or _stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) != stamp
            ):
                raise ValueError()
        return bytes(data)
    except (OSError, ValueError):
        raise ValueError("FILE_UNAVAILABLE") from None
    finally:
        if fd is not None:
            os.close(fd)
        for directory in reversed(fds):
            os.close(directory)


class MonitorServiceConfigError(ValueError):
    def __init__(self, code: str = "MONITOR_CONFIG_INVALID") -> None:
        self.code = code
        super().__init__(code)


def _map(value: object, keys: set[str]) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise MonitorServiceConfigError()
    return value


def _text(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise MonitorServiceConfigError()
    try:
        if len(value.encode("utf-8", errors="strict")) > 128:
            raise MonitorServiceConfigError()
    except UnicodeError:
        raise MonitorServiceConfigError() from None
    return value


def _endpoint(value: object) -> str:
    value = _text(value)
    try:
        prefix, port = value.rsplit(":", 1)
        if not prefix.startswith("tcp://") or not 1 <= int(port) <= 65535 or str(int(port)) != port:
            raise ValueError()
        address = ipaddress.ip_address(prefix[6:])
        if address.is_unspecified or address.version != 4:
            raise ValueError()
    except ValueError:
        raise MonitorServiceConfigError() from None
    return value


@dataclass(frozen=True)
class Principal:
    principal_ref: str
    actor_ref: str
    public_key_file: Path


@dataclass(frozen=True)
class Listener:
    service_id: str
    principal_ref: str
    instance_id: str
    scope_ref: str
    rpc_bind: str
    public_key_file: Path
    secret_key_file: Path
    principals: tuple[Principal, ...]


@dataclass(frozen=True)
class MonitorServiceConfig:
    storage_directory: Path
    execution_domain_ref: str
    account_ref: str
    outbox_scope: str
    event_types: tuple[str, ...]
    ingress: Listener
    heartbeat: Listener


def _path(value: object, base: Path) -> Path:
    try:
        return resolve_path(value, base)
    except RiskServiceConfigError:
        raise MonitorServiceConfigError() from None


def _listener(value: object, base: Path) -> Listener:
    item = _map(
        value,
        {
            "service_id",
            "principal_ref",
            "instance_id",
            "scope_ref",
            "rpc_bind",
            "public_key_file",
            "secret_key_file",
            "principals",
        },
    )
    entries = item["principals"]
    if type(entries) is not list or not 1 <= len(entries) <= 1024:
        raise MonitorServiceConfigError()
    principals = []
    for raw in entries:
        p = _map(raw, {"principal_ref", "actor_ref", "public_key_file"})
        principals.append(
            Principal(
                _text(p["principal_ref"]), _text(p["actor_ref"]), _path(p["public_key_file"], base)
            )
        )
    if len({p.principal_ref for p in principals}) != len(principals) or len(
        {p.actor_ref for p in principals}
    ) != len(principals):
        raise MonitorServiceConfigError()
    return Listener(
        _text(item["service_id"]),
        _text(item["principal_ref"]),
        _text(item["instance_id"]),
        _text(item["scope_ref"]),
        _endpoint(item["rpc_bind"]),
        _path(item["public_key_file"], base),
        _path(item["secret_key_file"], base),
        tuple(principals),
    )


def parse_monitor_service_config(document: object, *, path: Path) -> MonitorServiceConfig:
    top = _map(document, {"schema", "scope", "storage", "ingress", "heartbeat"})
    if top["schema"] != SCHEMA:
        raise MonitorServiceConfigError()
    base = Path(path).resolve().parent
    scope = _map(top["scope"], {"execution_domain_ref", "account_ref"})
    storage = _map(top["storage"], {"directory", "outbox_scope", "event_types"})
    event_types = storage["event_types"]
    if type(event_types) is not list or not 1 <= len(event_types) <= 128:
        raise MonitorServiceConfigError()
    types = tuple(_text(t) for t in event_types)
    if len(set(types)) != len(types):
        raise MonitorServiceConfigError()
    ingress, heartbeat = _listener(top["ingress"], base), _listener(top["heartbeat"], base)
    for attr in (
        "service_id",
        "principal_ref",
        "instance_id",
        "scope_ref",
        "rpc_bind",
        "public_key_file",
        "secret_key_file",
    ):
        if getattr(ingress, attr) == getattr(heartbeat, attr):
            raise MonitorServiceConfigError()
    return MonitorServiceConfig(
        _path(storage["directory"], base),
        _text(scope["execution_domain_ref"]),
        _text(scope["account_ref"]),
        _text(storage["outbox_scope"]),
        types,
        ingress,
        heartbeat,
    )


def load_monitor_service_config(path: str | Path) -> MonitorServiceConfig:
    path = Path(path)
    try:
        document: Any = load_document(
            read_bounded(path, _MAX_CONFIG_BYTES, protected=False).decode("utf-8")
        )
        return parse_monitor_service_config(document, path=path)
    except (OSError, UnicodeError, RiskServiceConfigError, ValueError):
        raise MonitorServiceConfigError() from None
