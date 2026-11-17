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

from bt_api_base.market_protocol import MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.risk_service_config import RiskServiceConfigError, load_document, resolve_path

SCHEMA = "bt_api_monitor.service.v1"
SCHEMA_V2 = "bt_api_monitor.service.v2"
_MAX_CONFIG_BYTES = 1_048_576
# MonitorIngressTarget aliases MonitorReadTarget, whose event allowlist is
# bounded to 16 entries. Keep offline validation independent of that package.
_MAX_EVENT_TYPES = 16


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
class MonitorProducerBinding:
    binding_ref: str
    source_ref: str
    identity: WireIdentity
    scope: WireScope
    outbox_scope: str
    event_types: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryDurationConfig:
    consumer_id: str
    binding_ref: str
    source_ref: str
    scope_ref: str
    config_version: str
    member_key: MarketSubscriptionKey


@dataclass(frozen=True)
class MonitorServiceConfig:
    storage_directory: Path
    execution_domain_ref: str
    account_ref: str
    outbox_scope: str
    event_types: tuple[str, ...]
    ingress: Listener
    heartbeat: Listener
    producer_bindings: tuple[MonitorProducerBinding, ...] = ()
    recovery_duration: tuple[RecoveryDurationConfig, ...] = ()


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


def _producer_bindings(value, ingress, domain, account, outbox_scope, allowed_types):
    if type(value) is not list or not 1 <= len(value) <= 1024:
        raise MonitorServiceConfigError()
    bindings = []
    refs = set()
    authorizations = set()
    identities = {(p.principal_ref, p.actor_ref) for p in ingress.principals}
    try:
        host_scope = WireScope(domain, account_ref=account)
    except (TypeError, ValueError):
        raise MonitorServiceConfigError() from None
    scope_keys = {
        "execution_domain_ref",
        "account_ref",
        "account_refs",
        "strategy_ref",
        "run_id",
        "manual_book_ref",
        "portfolio_ref",
        "group_id",
        "leg_id",
    }
    for raw in value:
        item = _map(
            raw, {"binding_ref", "source_ref", "identity", "scope", "outbox_scope", "event_types"}
        )
        ident = _map(item["identity"], {"principal_ref", "actor_ref"})
        principal, actor = _text(ident["principal_ref"]), _text(ident["actor_ref"])
        if (principal, actor) not in identities:
            raise MonitorServiceConfigError()
        raw_scope = _map(item["scope"], scope_keys)
        if type(raw_scope["account_refs"]) is not list or raw_scope["account_refs"]:
            raise MonitorServiceConfigError()
        normalized = {
            key: (_text(v) if v is not None else None)
            for key, v in raw_scope.items()
            if key != "account_refs"
        }
        try:
            scope = WireScope(**normalized, account_refs=())
            identity = WireIdentity(WireRole.SERVICE, principal, actor)
        except (TypeError, ValueError):
            raise MonitorServiceConfigError() from None
        if scope != host_scope:
            raise MonitorServiceConfigError()
        binding_ref, source_ref = _text(item["binding_ref"]), _text(item["source_ref"])
        target_scope = _text(item["outbox_scope"])
        event_types = item["event_types"]
        if type(event_types) is not list or not 1 <= len(event_types) <= _MAX_EVENT_TYPES:
            raise MonitorServiceConfigError()
        types = tuple(_text(t) for t in event_types)
        if (
            binding_ref in refs
            or target_scope != outbox_scope
            or len(set(types)) != len(types)
            or not set(types) <= set(allowed_types)
        ):
            raise MonitorServiceConfigError()
        if any(
            any(0x80 <= ord(char) <= 0x9F for char in text)
            for text in (binding_ref, source_ref, target_scope, *types)
        ):
            raise MonitorServiceConfigError()
        entries = {(identity, scope, event_type) for event_type in types}
        if entries & authorizations:
            raise MonitorServiceConfigError()
        refs.add(binding_ref)
        authorizations.update(entries)
        bindings.append(
            MonitorProducerBinding(binding_ref, source_ref, identity, scope, target_scope, types)
        )
    return tuple(bindings)


def _recovery_duration(value, bindings, outbox_scope):
    event_type = "gateway.recovery.journal.v1"
    producers = tuple(
        binding for binding in bindings
        if binding.outbox_scope == outbox_scope and event_type in binding.event_types
    )
    if type(value) is not list or not 1 <= len(value) <= 1024 or len(producers) != 1:
        raise MonitorServiceConfigError()
    result = []
    consumers, members = set(), set()
    for raw in value:
        item = _map(raw, {
            "consumer_id", "binding_ref", "source_ref", "scope_ref", "config_version", "member_key"
        })
        texts = tuple(_text(item[key]) for key in (
            "consumer_id", "binding_ref", "source_ref", "scope_ref", "config_version"
        ))
        if not all(text.isprintable() for text in texts):
            raise MonitorServiceConfigError()
        try:
            member = MarketSubscriptionKey.from_dict(item["member_key"])
        except (TypeError, ValueError, UnicodeError):
            raise MonitorServiceConfigError() from None
        consumer, binding_ref, source_ref, scope_ref, config_version = texts
        producer = producers[0]
        member_binding = (producer.identity, producer.scope, binding_ref, source_ref, member)
        if (consumer in consumers or member_binding in members or binding_ref != producer.binding_ref
                or source_ref != producer.source_ref):
            raise MonitorServiceConfigError()
        consumers.add(consumer)
        members.add(member_binding)
        result.append(RecoveryDurationConfig(
            consumer, binding_ref, source_ref, scope_ref, config_version, member
        ))
    return tuple(result)


def parse_monitor_service_config(document: object, *, path: Path) -> MonitorServiceConfig:
    if type(document) is not dict or document.get("schema") not in (SCHEMA, SCHEMA_V2):
        raise MonitorServiceConfigError()
    keys = {"schema", "scope", "storage", "ingress", "heartbeat"}
    if document["schema"] == SCHEMA_V2:
        keys.add("producer_bindings")
        if "recovery_duration" in document:
            keys.add("recovery_duration")
    top = _map(document, keys)
    base = Path(path).resolve().parent
    scope = _map(top["scope"], {"execution_domain_ref", "account_ref"})
    storage = _map(top["storage"], {"directory", "outbox_scope", "event_types"})
    event_types = storage["event_types"]
    if type(event_types) is not list or not 1 <= len(event_types) <= _MAX_EVENT_TYPES:
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
    domain = _text(scope["execution_domain_ref"])
    account = _text(scope["account_ref"])
    outbox_scope = _text(storage["outbox_scope"])
    bindings = ()
    if top["schema"] == SCHEMA_V2:
        bindings = _producer_bindings(
            top["producer_bindings"], ingress, domain, account, outbox_scope, types
        )
    recovery = ()
    if "recovery_duration" in top:
        recovery = _recovery_duration(top["recovery_duration"], bindings, outbox_scope)
    return MonitorServiceConfig(
        _path(storage["directory"], base),
        domain,
        account,
        outbox_scope,
        types,
        ingress,
        heartbeat,
        bindings,
        recovery,
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
