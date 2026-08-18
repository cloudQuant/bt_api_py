"""Safe-by-default gateway security primitives.

The forwarding gateway is not a trusted transport until Iteration 4 lands
CurveZMQ + principal ACL. Until then, the runtime must refuse anything but
read-only loopback/IPC unless the operator explicitly opts into a remote
deployment.
"""

from __future__ import annotations


class GatewaySafetyError(RuntimeError):
    """Raised when a gateway configuration violates the safe-by-default policy."""


def is_loopback_or_ipc(endpoint: str) -> bool:
    """Return True if ``endpoint`` binds to loopback or a local IPC path."""
    if not isinstance(endpoint, str) or not endpoint:
        return False
    if endpoint.startswith("ipc://"):
        return True
    if endpoint.startswith("tcp://"):
        host_port = endpoint[len("tcp://") :]
        host = host_port.split(":", 1)[0] if ":" in host_port else host_port
        return host in {"127.0.0.1", "localhost", "::1"}
    return False
