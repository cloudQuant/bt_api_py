"""Sealed-capability contract validation with no optional imports."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

CAPABILITY_EXECUTION = "execution"
CAPABILITY_RISK = "risk"
CAPABILITY_MONITOR = "monitor"
CAPABILITY_GATEWAY = "gateway"
CAPABILITY_TRANSPORT_ZMQ = "transport_zmq"
_ALL_CAPABILITIES = frozenset(
    {
        CAPABILITY_EXECUTION,
        CAPABILITY_RISK,
        CAPABILITY_MONITOR,
        CAPABILITY_GATEWAY,
        CAPABILITY_TRANSPORT_ZMQ,
    }
)
_MANAGED_DIRECT_ORDER = (CAPABILITY_EXECUTION, CAPABILITY_RISK, CAPABILITY_MONITOR)
_MANAGED_GATEWAY_ORDER = _MANAGED_DIRECT_ORDER + (CAPABILITY_GATEWAY, CAPABILITY_TRANSPORT_ZMQ)
_ALLOWED_ROUTES = frozenset({None, "read_only", "local_simulation", "managed_execution"})
_NON_MANAGED_SHAPES = {
    "local_backtest": ("backtest", "local", None),
    "replay": ("simulation", "offline", None),
    "shadow": ("simulation", "public_read", "read_only"),
    "paper": ("simulation", "public_read", "local_simulation"),
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class RuntimeContractError(ValueError):
    """A supplied effective contract cannot load a managed capability."""


def _id(value: object, name: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not _IDENTIFIER.fullmatch(value):
        raise RuntimeContractError("invalid " + name)
    return value


def _sha(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise RuntimeContractError("invalid " + name)
    return value


def _digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RuntimeCapabilityContract:
    """The small, redacted result of configuration-first policy resolution.

    The object is constructed by code after the schema-v4 configuration and its
    code-owned registry have been validated.  It deliberately cannot express a
    user-selectable plugin, module path, provider credential, or route upgrade.
    """

    strategy_id: str
    mode: str
    preset: str
    environment: str
    order_route: str | None
    required_capabilities: tuple[str, ...]
    effective_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_id", _id(self.strategy_id, "strategy_id"))
        if self.mode not in {"backtest", "simulation", "live"}:
            raise RuntimeContractError("invalid runtime mode")
        if self.preset not in {
            "local_backtest",
            "replay",
            "shadow",
            "paper",
            "sandbox",
            "managed_live_direct",
            "managed_live_gateway",
        }:
            raise RuntimeContractError("invalid runtime preset")
        if self.environment not in {"local", "offline", "public_read", "sandbox", "production"}:
            raise RuntimeContractError("invalid runtime environment")
        if self.order_route not in _ALLOWED_ROUTES:
            raise RuntimeContractError("invalid runtime order route")
        capabilities = tuple(self.required_capabilities)
        if len(set(capabilities)) != len(capabilities) or not set(capabilities).issubset(
            _ALL_CAPABILITIES
        ):
            raise RuntimeContractError("invalid required capabilities")
        object.__setattr__(self, "required_capabilities", capabilities)
        object.__setattr__(
            self, "effective_digest", _sha(self.effective_digest, "effective_digest")
        )
        self._validate_policy_shape()

    def _validate_policy_shape(self) -> None:
        actual = tuple(self.required_capabilities)
        if self.preset == "managed_live_direct":
            expected = _MANAGED_DIRECT_ORDER
            if (
                self.mode != "live"
                or self.environment != "production"
                or self.order_route != "managed_execution"
                or actual != expected
            ):
                raise RuntimeContractError("managed live contract does not match its sealed policy")
        elif self.preset == "managed_live_gateway":
            expected = _MANAGED_GATEWAY_ORDER
            if (
                self.mode != "live"
                or self.environment != "production"
                or self.order_route != "managed_execution"
                or actual != expected
            ):
                raise RuntimeContractError("managed live contract does not match its sealed policy")
        elif self.preset == "sandbox":
            sandbox_default = self.mode == "simulation" and self.environment == "sandbox"
            if not sandbox_default:
                raise RuntimeContractError("sandbox contract does not match its sealed policy")
            if self.order_route is None and not actual:
                return
            if self.order_route == "managed_execution" and actual == _MANAGED_DIRECT_ORDER:
                return
            raise RuntimeContractError("sandbox cannot select an unsealed execution route")
        elif self.preset == "replay":
            if (
                self.mode == "simulation"
                and self.environment == "offline"
                and self.order_route == "managed_execution"
                and actual == _MANAGED_DIRECT_ORDER
            ):
                # Backtrader only emits this shape for a reviewed
                # RegisteredRuntime.offline_managed_execution fixture.  The
                # contract accepts the sealed projection but cannot make it
                # user-selectable: registry resolution remains its authority.
                return
            expected_shape = _NON_MANAGED_SHAPES["replay"]
            if expected_shape != (self.mode, self.environment, self.order_route) or actual:
                raise RuntimeContractError("replay contract does not match its sealed policy")
        else:
            expected_shape = _NON_MANAGED_SHAPES.get(self.preset)
            if expected_shape != (self.mode, self.environment, self.order_route) or actual:
                raise RuntimeContractError("non-managed contract does not match its sealed policy")

    @property
    def is_managed_execution(self) -> bool:
        """Return whether this exact resolved policy may load execution packages."""

        return self.order_route == "managed_execution"

    @property
    def is_managed_live(self) -> bool:
        return self.preset.startswith("managed_live")

    @classmethod
    def from_effective_public_dict(cls, raw: Mapping[str, object]) -> RuntimeCapabilityContract:
        """Create a contract from a redacted Backtrader effective-config projection.

        Extra display fields are permitted because ``EffectiveRuntimeConfig``
        deliberately exposes diagnostics.  The security-significant subset is
        revalidated here and cannot be expanded by those fields.
        """
        capabilities = raw.get("required_capabilities")
        if not isinstance(capabilities, (list, tuple)):
            raise RuntimeContractError("required_capabilities must be an array")
        return cls(
            strategy_id=cast("str", raw.get("strategy_id")),
            mode=cast("str", raw.get("mode")),
            preset=cast("str", raw.get("preset")),
            environment=cast("str", raw.get("environment")),
            order_route=cast("str | None", raw.get("order_route")),
            required_capabilities=cast("tuple[str, ...]", tuple(capabilities)),
            effective_digest=cast("str", raw.get("effective_digest")),
        )

    def fingerprint(self) -> str:
        """Give the composition root a deterministic diagnostic identity."""
        return _digest(
            {
                "effective_digest": self.effective_digest,
                "environment": self.environment,
                "mode": self.mode,
                "order_route": self.order_route,
                "preset": self.preset,
                "required_capabilities": list(self.required_capabilities),
                "strategy_id": self.strategy_id,
            }
        )
