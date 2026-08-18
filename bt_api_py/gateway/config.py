"""Gateway configuration with safe-by-default validation."""

from __future__ import annotations

from dataclasses import dataclass

from bt_api_py.gateway.safety import GatewaySafetyError, is_loopback_or_ipc


@dataclass(frozen=True)
class GatewayConfig:
    """Configuration for a forwarding gateway runtime.

    Safe-by-default policy:

    * ``enable_trading`` is ``False`` unless the operator also opts into an
      authenticated remote deployment via ``allow_remote=True``.
    * Non-loopback TCP endpoints are rejected unless ``allow_remote=True``.
    * The private event endpoint must not silently share the public market
      endpoint unless ``allow_shared_private_endpoint=True``.
    """

    command_endpoint: str
    market_endpoint: str = "tcp://127.0.0.1:7001"
    private_endpoint: str | None = None
    enable_trading: bool = False
    allow_remote: bool = False
    allow_shared_private_endpoint: bool = False

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if self.enable_trading and not self.allow_remote:
            raise GatewaySafetyError(
                "enable_trading=True requires an explicit safe policy "
                "(allow_remote=True for an authenticated remote deployment); "
                "otherwise keep enable_trading=False"
            )
        if not self.allow_remote:
            for name, endpoint in (
                ("command_endpoint", self.command_endpoint),
                ("market_endpoint", self.market_endpoint),
                ("private_endpoint", self.private_endpoint),
            ):
                if endpoint and not is_loopback_or_ipc(endpoint):
                    raise GatewaySafetyError(
                        f"{name}={endpoint!r} is not loopback/IPC; "
                        "set allow_remote=True only for an authenticated remote deployment"
                    )
        resolved_private = self.private_endpoint or self.market_endpoint
        if resolved_private == self.market_endpoint and not self.allow_shared_private_endpoint:
            raise GatewaySafetyError(
                "private_endpoint must not share the public market_endpoint; "
                "provide a distinct private_endpoint or set allow_shared_private_endpoint=True "
                "only for a single-user loopback deployment"
            )

    @property
    def is_loopback_or_ipc(self) -> bool:
        """Return True if every endpoint binds to loopback or IPC."""
        endpoints = [self.command_endpoint, self.market_endpoint]
        if self.private_endpoint:
            endpoints.append(self.private_endpoint)
        return all(is_loopback_or_ipc(endpoint) for endpoint in endpoints)

    @classmethod
    def local_defaults(cls) -> GatewayConfig:
        """Return a read-only loopback configuration with a distinct private endpoint."""
        return cls(
            command_endpoint="tcp://127.0.0.1:7002",
            market_endpoint="tcp://127.0.0.1:7001",
            private_endpoint="tcp://127.0.0.1:7003",
            enable_trading=False,
        )
