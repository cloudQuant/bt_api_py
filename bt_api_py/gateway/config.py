from __future__ import annotations

import os
import socket
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TCP_PORT_ASSIGNMENTS: dict[str, int] = {}
_TCP_RESERVED_BASE_PORTS: set[int] = set()


def _tcp_port_triplet_available(base_port: int, host: str = "127.0.0.1") -> bool:
    sockets: list[socket.socket] = []
    try:
        for port in range(base_port, base_port + 3):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(sock)
            sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        for sock in sockets:
            sock.close()


@dataclass(slots=True)
class GatewayConfig:
    exchange_type: str
    asset_type: str
    account_id: str
    transport: str = "ipc"
    base_dir: str = ""
    runtime_name: str = ""
    command_endpoint: str = ""
    event_endpoint: str = ""
    market_endpoint: str = ""
    command_timeout_sec: float = 5.0
    startup_timeout_sec: float = 30.0
    poll_timeout_ms: int = 100

    def __post_init__(self) -> None:
        self.exchange_type = str(self.exchange_type or "CTP").upper()
        self.asset_type = str(self.asset_type or "FUTURE").upper()
        self.account_id = str(self.account_id or "default")
        self.transport = str(self.transport or "ipc").lower()
        if self.transport == "ipc" and sys.platform.startswith("win"):
            self.transport = "tcp"
        if not self.base_dir:
            self.base_dir = str(Path(tempfile.gettempdir()) / "btgw")
        if not self.runtime_name:
            self.runtime_name = self._build_runtime_name()
        if not self.command_endpoint or not self.event_endpoint or not self.market_endpoint:
            command_endpoint, event_endpoint, market_endpoint = self._build_endpoints()
            self.command_endpoint = self.command_endpoint or command_endpoint
            self.event_endpoint = self.event_endpoint or event_endpoint
            self.market_endpoint = self.market_endpoint or market_endpoint

    @classmethod
    def from_kwargs(cls, **kwargs: Any) -> GatewayConfig:
        return cls(
            exchange_type=kwargs.get("exchange_type") or kwargs.get("provider_exchange") or "CTP",
            asset_type=kwargs.get("asset_type") or "FUTURE",
            account_id=kwargs.get("account_id")
            or kwargs.get("investor_id")
            or kwargs.get("user_id")
            or "default",
            transport=kwargs.get("transport") or kwargs.get("zmq_transport") or "ipc",
            base_dir=kwargs.get("gateway_base_dir") or "",
            runtime_name=kwargs.get("gateway_runtime_name") or "",
            command_endpoint=kwargs.get("gateway_command_endpoint") or "",
            event_endpoint=kwargs.get("gateway_event_endpoint") or "",
            market_endpoint=kwargs.get("gateway_market_endpoint") or "",
            command_timeout_sec=float(kwargs.get("gateway_command_timeout_sec", 5.0) or 5.0),
            startup_timeout_sec=float(kwargs.get("gateway_startup_timeout_sec", 10.0) or 10.0),
            poll_timeout_ms=int(kwargs.get("gateway_poll_timeout_ms", 100) or 100),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange_type": self.exchange_type,
            "asset_type": self.asset_type,
            "account_id": self.account_id,
            "transport": self.transport,
            "base_dir": self.base_dir,
            "runtime_name": self.runtime_name,
            "command_endpoint": self.command_endpoint,
            "event_endpoint": self.event_endpoint,
            "market_endpoint": self.market_endpoint,
            "command_timeout_sec": self.command_timeout_sec,
            "startup_timeout_sec": self.startup_timeout_sec,
            "poll_timeout_ms": self.poll_timeout_ms,
        }

    def _build_runtime_name(self) -> str:
        safe_account = "".join(ch if ch.isalnum() else "-" for ch in self.account_id.lower()).strip(
            "-"
        )
        safe_account = safe_account or "default"
        return f"{self.exchange_type.lower()}-{self.asset_type.lower()}-{safe_account}"

    def _build_endpoints(self) -> tuple[str, str, str]:
        if self.transport == "tcp":
            base_port = self._tcp_base_port()
            host = "127.0.0.1"
            return (
                f"tcp://{host}:{base_port}",
                f"tcp://{host}:{base_port + 1}",
                f"tcp://{host}:{base_port + 2}",
            )

        root = Path(self.base_dir)
        root.mkdir(parents=True, exist_ok=True)
        runtime_root = root / self.runtime_name
        runtime_root.mkdir(parents=True, exist_ok=True)
        return (
            f"ipc://{runtime_root / 'command.sock'}",
            f"ipc://{runtime_root / 'event.sock'}",
            f"ipc://{runtime_root / 'market.sock'}",
        )

    def _tcp_base_port(self) -> int:
        # Use a stable per-process allocation keyed by runtime name. Under
        # pytest-xdist, include the worker id so parallel workers do not share
        # the same deterministic port space. If two runtime names hash to the
        # same base port, probe forward to the next free triplet.
        seed_input = self.runtime_name
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
        if worker_id:
            seed_input = f"{worker_id}:{seed_input}"
        assigned = _TCP_PORT_ASSIGNMENTS.get(seed_input)
        if assigned is not None:
            return assigned

        seed = zlib.crc32(seed_input.encode("utf-8")) % 10000
        for offset in range(10000):
            slot = (seed + offset) % 10000
            candidate = 32000 + slot * 3
            if candidate not in _TCP_RESERVED_BASE_PORTS and _tcp_port_triplet_available(candidate):
                _TCP_RESERVED_BASE_PORTS.add(candidate)
                _TCP_PORT_ASSIGNMENTS[seed_input] = candidate
                return candidate

        raise RuntimeError("no available TCP gateway ports")
