"""A synchronous v2 Monitor sink borrowing one authenticated service runtime.

The binding and socket proof are local observations. They do not establish a
venue source, and an unknown RPC outcome is never treated as acknowledgement.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from dataclasses import fields

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_runtime import RegisteredBinding, ServiceClientRuntime, _bound_binding_values

_EVENT_ID = re.compile(r"service-event-v2:[0-9a-f]{64}\Z", re.ASCII)
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_FIELDS = frozenset(
    {"schema_version", "event_id", "event_type", "occurred_at", "data", "binding_ref", "source_ref"}
)


class AuthenticatedMonitorSinkError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _text(value, limit=256):
    try:
        return (
            type(value) is str
            and bool(value)
            and value == value.strip()
            and value.isprintable()
            and len(value.encode("utf-8", "strict")) <= limit
        )
    except UnicodeError:
        return False


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _bounded(value):
    """Match the receiver's depth, node and encoded request limits."""
    count = 0
    text_bytes = 0
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 4096 or depth > 32:
            raise ValueError
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str or len(key.encode("utf-8")) > 128:
                    raise ValueError
                count += 1
                text_bytes += len(key.encode("utf-8"))
                pending.append((child, depth + 1))
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            text_bytes += len(item.encode("utf-8"))
        elif type(item) is int:
            if item.bit_length() > 256:
                raise ValueError
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError
        elif type(item) not in (bool, type(None)):
            raise ValueError
        if count > 4096 or text_bytes > 65536:
            raise ValueError
    encoded = _canonical(value)
    if len(encoded) > 65536:
        raise ValueError
    return encoded


class AuthenticatedMonitorV2Sink:
    """One fixed registration, authenticated peer and scoped v2 destination."""

    def __init__(self, runtime, *, scope_ref, outbox_scope, binding_ref, source_ref, expected_peer):
        if (
            type(runtime) is not ServiceClientRuntime
            or type(expected_peer) is not WireIdentity
            or expected_peer.role is not WireRole.SERVICE
            or expected_peer.delegate_ref is not None
            or any(not _text(item) for item in (scope_ref, outbox_scope, binding_ref, source_ref))
        ):
            raise AuthenticatedMonitorSinkError("INVALID_CONFIGURATION")
        self._runtime = self._original_runtime = runtime
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._scope_ref, self._outbox_scope = scope_ref, outbox_scope
        self._binding_ref, self._source_ref = binding_ref, source_ref
        self._peer = WireIdentity(**vars(expected_peer))
        self._configuration = (
            scope_ref,
            outbox_scope,
            binding_ref,
            source_ref,
            tuple(vars(self._peer).items()),
        )
        try:
            binding, signature, peer_fingerprint, lineage = self._observe()
            self._binding, self._signature, self._origin_lineage = binding, signature, lineage
            scope = {f.name: getattr(binding.scope, f.name) for f in fields(WireScope)}
            scope["account_refs"] = list(scope["account_refs"])
            identity = runtime.identity
            manifest = {
                "schema": "sdk.monitor-v2-destination/1",
                "mount": "upstream.telemetry",
                "service_id": binding.service_id,
                "instance_id": binding.instance_id,
                "scope_ref": scope_ref,
                "scope": scope,
                "producer": {
                    "role": identity.role.value,
                    "principal_ref": identity.principal_ref,
                    "actor_ref": identity.actor_ref,
                    "delegate_ref": identity.delegate_ref,
                },
                "peer": {
                    "role": self._peer.role.value,
                    "principal_ref": self._peer.principal_ref,
                    "actor_ref": self._peer.actor_ref,
                    "delegate_ref": self._peer.delegate_ref,
                },
                "server_key_fingerprint": peer_fingerprint,
                "outbox_scope": outbox_scope,
                "binding_ref": binding_ref,
                "source_ref": source_ref,
            }
            self._manifest = _bounded(manifest)
            self._original_manifest = self._manifest
        except Exception:
            raise AuthenticatedMonitorSinkError("INGRESS_BINDING_UNAVAILABLE") from None

    def _assert_owner(self):
        if os.getpid() != self._pid:
            raise AuthenticatedMonitorSinkError("WRONG_PROCESS")
        if threading.current_thread() is not self._thread:
            raise AuthenticatedMonitorSinkError("WRONG_OWNER_THREAD")
        if (
            self._runtime is not self._original_runtime
            or (
                self._scope_ref,
                self._outbox_scope,
                self._binding_ref,
                self._source_ref,
                tuple(vars(self._peer).items()),
            )
            != self._configuration
            or (hasattr(self, "_original_manifest") and self._manifest != self._original_manifest)
        ):
            raise AuthenticatedMonitorSinkError("INGRESS_BINDING_CHANGED")

    def _observe(self):
        self._assert_owner()
        runtime = self._runtime
        if (
            runtime.state != "registered"
            or runtime.healthy is not True
            or type(runtime.identity) is not WireIdentity
            or runtime.identity.role is not WireRole.SERVICE
            or runtime.identity.delegate_ref is not None
        ):
            raise ValueError
        matches = [
            b
            for b in runtime.bindings
            if type(b) is RegisteredBinding
            and b.mount == "upstream.telemetry"
            and b.scope_ref == self._scope_ref
            and b.active
        ]
        if len(matches) != 1:
            raise ValueError
        b = matches[0]
        now = runtime._monotonic_now()
        if (
            type(b.scope) is not WireScope
            or type(b.operations) is not frozenset
            or "events.append" not in b.operations
            or b.remote_lease_inactive
            or type(now) not in (int, float)
            or not math.isfinite(now)
            or type(b.lease_deadline) not in (int, float)
            or not math.isfinite(b.lease_deadline)
            or now >= b.lease_deadline
            or not runtime._has_current_registration_observation(b)
        ):
            raise ValueError
        registration = runtime._registration_lineage.get(id(b))
        if type(registration) is not tuple or len(registration) not in (9, 10):
            raise ValueError
        proof = runtime.authenticated_peer_for_binding(b)
        if (
            type(proof) is not tuple
            or len(proof) != 2
            or proof[0] != self._peer
            or type(proof[1]) is not str
            or _FINGERPRINT.fullmatch(proof[1]) is None
        ):
            raise ValueError
        signature = (
            tuple(
                (name, value)
                for name, value in _bound_binding_values(b)
                if name not in {"expires_at", "lease_deadline", "renewal_due_at"}
            ),
            tuple(vars(WireIdentity(**vars(runtime.identity))).items()),
            tuple(vars(WireIdentity(**vars(proof[0]))).items()),
            proof[1],
            runtime.run_id,
        )
        self._assert_owner()
        return b, signature, proof[1], registration[2:9]

    def assert_current(self):
        try:
            _, signature, _, lineage = self._observe()
            if (
                signature != self._signature
                or len(lineage) != len(self._origin_lineage)
                or any(
                    current is not original
                    for current, original in zip(lineage, self._origin_lineage)  # noqa: B905
                )
            ):
                raise ValueError
        except AuthenticatedMonitorSinkError:
            raise
        except Exception:
            raise AuthenticatedMonitorSinkError("INGRESS_BINDING_CHANGED") from None

    @property
    def destination_manifest(self):
        self.assert_current()
        return json.loads(self._manifest)

    def _expected_event_id(self, source_event_id):
        manifest = json.loads(self._manifest)
        identity = {
            "identity": manifest["producer"],
            "scope": manifest["scope"],
            "outbox_scope": self._outbox_scope,
            "binding_ref": self._binding_ref,
            "source_ref": self._source_ref,
            "source_event_id": source_event_id,
        }
        return "service-event-v2:" + hashlib.sha256(_canonical(identity)).hexdigest()

    def validate_envelope(self, envelope):
        """Check both v2 request and receiver's fixed 64 KiB stored shape."""
        self.assert_current()
        try:
            if (
                type(envelope) is not dict
                or set(envelope) != _FIELDS
                or type(envelope["schema_version"]) is not int
                or envelope["schema_version"] != 2
                or not _text(envelope["event_id"])
                or not _text(envelope["event_type"], 128)
                or envelope["binding_ref"] != self._binding_ref
                or envelope["source_ref"] != self._source_ref
                or type(envelope["occurred_at"]) not in (int, float)
                or not math.isfinite(envelope["occurred_at"])
                or not -62135596800 <= envelope["occurred_at"] <= 253402300799
                or type(envelope["data"]) is not dict
            ):
                raise ValueError
        except Exception:
            raise AuthenticatedMonitorSinkError("INVALID_EVENT") from None
        try:
            encoded = _bounded(envelope)
            arguments = json.loads(encoded)
            manifest = json.loads(self._manifest)
            durable_data = {
                "schema_version": 2,
                "producer": manifest["producer"],
                "scope": manifest["scope"],
                "binding_ref": self._binding_ref,
                "source_ref": self._source_ref,
                "source_event_id": arguments["event_id"],
                "payload": arguments["data"],
            }
            event_id = self._expected_event_id(arguments["event_id"])
            logical_bytes = (
                len(event_id.encode("utf-8"))
                + len(self._outbox_scope.encode("utf-8"))
                + len(arguments["event_type"].encode("utf-8"))
                + len(_canonical(durable_data))
            )
            if logical_bytes > 65536:
                raise ValueError
            return arguments
        except Exception:
            raise AuthenticatedMonitorSinkError("EVENT_TOO_LARGE") from None

    def append(self, envelope):
        arguments = self.validate_envelope(envelope)
        try:
            reply = self._runtime.call(
                "upstream.telemetry", "events.append", arguments, scope_ref=self._scope_ref
            )
            self.assert_current()
            if (
                not hasattr(reply, "keys")
                or set(reply) != {"event_id", "source_event_id", "sequence"}
                or type(reply["event_id"]) is not str
                or _EVENT_ID.fullmatch(reply["event_id"]) is None
                or reply["event_id"] != self._expected_event_id(arguments["event_id"])
                or type(reply["source_event_id"]) is not str
                or reply["source_event_id"] != arguments["event_id"]
                or type(reply["sequence"]) is not int
                or not 1 <= reply["sequence"] < 2**63
            ):
                raise ValueError
        except Exception:
            raise AuthenticatedMonitorSinkError("INGRESS_OUTCOME_UNKNOWN") from None
