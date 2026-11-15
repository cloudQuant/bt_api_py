"""Synthetic owner receipt for caller-only tests; never a source of authority."""
import re

from bt_api_base.service_protocol import WireIdentity, WireScope
from bt_api_py.service_runtime_thread import (
    BindingSnapshot, BoundCallObservation, BoundCallResult, ThreadedRuntimeError,
)


def _ref(value):
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= 256
    except UnicodeError:
        return False


def _observation(runtime, mount, operation, route):
    snapshot = runtime.status()
    if (snapshot.state != "running" or snapshot.thread_alive is not True or snapshot.healthy is not True
            or type(snapshot.identity) is not WireIdentity or snapshot.identity != route.identity
            or not _ref(snapshot.run_id)):
        raise ValueError
    matches = [b for b in snapshot.bindings if type(b) is BindingSnapshot and b.active is True
               and b.mount == mount and b.scope_ref == route.scope_ref
               and type(b.operations) is frozenset and operation in b.operations
               and type(b.scope) is WireScope and b.scope.account_ref == route.scope.account_ref]
    if len(matches) != 1:
        raise ValueError
    binding = matches[0]
    if (binding.required is not True or binding.scope != route.scope or binding.service_id != route.service_id
            or binding.instance_id != route.instance_id or binding.authenticated_peer != route.peer
            or not _ref(binding.registration_id) or not _ref(binding.service_epoch)
            or type(binding.server_key_fingerprint) is not str
            or re.fullmatch(r"[0-9a-f]{64}", binding.server_key_fingerprint) is None):
        raise ValueError
    return BoundCallObservation(
        WireIdentity(**vars(snapshot.identity)), snapshot.run_id, WireScope(**vars(binding.scope)),
        binding.registration_id, binding.service_epoch, binding.mount, binding.service_id,
        binding.instance_id, binding.scope_ref, binding.operations,
        WireIdentity(**vars(binding.authenticated_peer)), binding.server_key_fingerprint, 1000.0,
        binding.active, binding.required, operation,
    )


def fake_bound_call(runtime, mount, operation, arguments, *, route, timeout_seconds):
    """Model a bound owner for Web caller tests; real owner behavior has separate tests."""
    try:
        before = _observation(runtime, mount, operation, route)
    except Exception:
        raise ThreadedRuntimeError("BOUND_ROUTE_UNAVAILABLE") from None
    try:
        result = runtime.call(mount, operation, arguments, route=route, timeout_seconds=timeout_seconds)
    except Exception:
        try:
            if _observation(runtime, mount, operation, route) != before:
                raise ThreadedRuntimeError("CALL_OUTCOME_UNKNOWN")
        except ThreadedRuntimeError:
            raise
        except Exception:
            raise ThreadedRuntimeError("CALL_OUTCOME_UNKNOWN") from None
        raise
    try:
        after = _observation(runtime, mount, operation, route)
        if after != before:
            raise ValueError
    except Exception:
        raise ThreadedRuntimeError("CALL_OUTCOME_UNKNOWN") from None
    return BoundCallResult(result, before, after)
