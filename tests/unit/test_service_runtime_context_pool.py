from __future__ import annotations

import builtins
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType

import pytest

import bt_api_py.service_runtime as service_runtime
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_py.service_runtime import ClientCurveKeys, ServiceRuntimeError, ZmqTransportFactory


def _profile(security_ref: str, *, high_water_mark: object = 1000) -> dict[str, object]:
    return {
        "backend": "zmq",
        "security_profile_ref": security_ref,
        "endpoints": {"rpc_connect": "tcp://127.0.0.1:5555"},
        "options": {
            "high_water_mark": high_water_mark,
            "max_message_bytes": 65_536,
        },
    }


def _peer(actor_ref: str) -> WireIdentity:
    return WireIdentity(WireRole.SERVICE, "service:fixture", actor_ref)


def _install_transport_fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    records: dict[str, object] = {"pools": [], "clients": []}
    records_lock = threading.Lock()

    class Credentials:
        def __init__(self, server_key: bytes, public_key: bytes, secret_key: bytes) -> None:
            self.server_key = server_key
            self.public_key = public_key
            self.secret_key = secret_key

    class ContextPool:
        def __init__(self) -> None:
            with records_lock:
                records["pools"].append(self)  # type: ignore[union-attr]

    class RequestNotSentError(RuntimeError):
        pass

    class StartupCleanupError(RuntimeError):
        def __init__(self, transport: object) -> None:
            self.transport = transport

    class Client:
        def __init__(
            self,
            endpoint: str,
            expected_peer: WireIdentity,
            *,
            credentials: Credentials,
            high_water_mark: int,
            max_message_bytes: int,
            context_pool: ContextPool,
        ) -> None:
            self.endpoint = endpoint
            self.expected_peer = expected_peer
            self.credentials = credentials
            self.high_water_mark = high_water_mark
            self.max_message_bytes = max_message_bytes
            self.context_pool = context_pool
            with records_lock:
                records["clients"].append(self)  # type: ignore[union-attr]

        def close(self) -> None:
            return None

    package = ModuleType("bt_api_transport_zmq")
    package.__path__ = []  # type: ignore[attr-defined]
    server_module = ModuleType("bt_api_transport_zmq.server")
    server_module.CurveClientCredentials = Credentials
    service_module = ModuleType("bt_api_transport_zmq.service_v2")
    service_module.RequestNotSentError = RequestNotSentError
    service_module.ServiceTransportStartupCleanupError = StartupCleanupError
    service_module.ZmqServiceContextPool = ContextPool
    service_module.ZmqServiceClient = Client
    package.server = server_module  # type: ignore[attr-defined]
    package.service_v2 = service_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "bt_api_transport_zmq", package)
    monkeypatch.setitem(sys.modules, "bt_api_transport_zmq.server", server_module)
    monkeypatch.setitem(
        sys.modules,
        "bt_api_transport_zmq.service_v2",
        service_module,
    )
    return records


def test_factory_lazily_shares_one_pool_per_factory_and_keeps_client_keys_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _install_transport_fakes(monkeypatch)
    first_keys = ClientCurveKeys(b"server-a", b"public-a", b"secret-a")
    second_keys = ClientCurveKeys(b"server-b", b"public-b", b"secret-b")
    factory = ZmqTransportFactory({"security:a": first_keys, "security:b": second_keys})

    assert factory._context_pool is None
    first = factory(_profile("security:a", high_water_mark=100_001), _peer("peer:a"))
    second = factory(_profile("security:b"), _peer("peer:b"))

    pools = records["pools"]
    clients = records["clients"]
    assert len(pools) == 1  # type: ignore[arg-type]
    assert len(clients) == 2  # type: ignore[arg-type]
    assert clients[0].context_pool is pools[0]  # type: ignore[index,union-attr]
    assert clients[1].context_pool is pools[0]  # type: ignore[index,union-attr]
    assert clients[0].endpoint == "tcp://127.0.0.1:5555"  # type: ignore[index,union-attr]
    assert clients[0].expected_peer == _peer("peer:a")  # type: ignore[index,union-attr]
    assert clients[0].high_water_mark == 100_001  # type: ignore[index,union-attr]
    assert clients[0].max_message_bytes == 65_536  # type: ignore[index,union-attr]
    assert clients[0].credentials.server_key == b"server-a"  # type: ignore[index,union-attr]
    assert clients[0].credentials.public_key == b"public-a"  # type: ignore[index,union-attr]
    assert clients[0].credentials.secret_key == b"secret-a"  # type: ignore[index,union-attr]
    assert clients[1].credentials.secret_key == b"secret-b"  # type: ignore[index,union-attr]
    assert first._transport is clients[0]  # type: ignore[attr-defined,index]
    assert second._transport is clients[1]  # type: ignore[attr-defined,index]

    isolated = ZmqTransportFactory({"security:a": first_keys})
    isolated_transport = isolated(_profile("security:a"), _peer("peer:isolated"))
    assert len(pools) == 2  # type: ignore[arg-type]
    assert clients[2].context_pool is pools[1]  # type: ignore[index,union-attr]
    assert clients[2].context_pool is not clients[0].context_pool  # type: ignore[index,union-attr]
    assert isolated_transport._transport is clients[2]  # type: ignore[attr-defined,index]


def test_factory_first_context_pool_creation_is_thread_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _install_transport_fakes(monkeypatch)
    factory = ZmqTransportFactory(
        {"security:shared": ClientCurveKeys(b"server", b"public", b"secret")}
    )
    gate = threading.Barrier(6)

    def make_client(index: int) -> object:
        gate.wait(timeout=5)
        return factory(_profile("security:shared"), _peer(f"peer:{index}"))

    with ThreadPoolExecutor(max_workers=6) as executor:
        clients = list(executor.map(make_client, range(6)))

    pools = records["pools"]
    transports = records["clients"]
    assert len(pools) == 1  # type: ignore[arg-type]
    assert len(transports) == 6  # type: ignore[arg-type]
    assert all(client.context_pool is pools[0] for client in transports)  # type: ignore[union-attr,index]
    assert all(hasattr(client, "_transport") for client in clients)


def test_factory_rejects_forked_use_before_acquiring_inherited_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = ZmqTransportFactory(
        {"security:shared": ClientCurveKeys(b"server", b"public", b"secret")}
    )
    owner_pid = factory._owner_pid

    class InheritedLockedLock:
        def __enter__(self) -> None:
            raise AssertionError("forked factory entered its inherited initialization lock")

        def __exit__(self, *_args: object) -> None:
            return None

    factory._context_pool_init_lock = InheritedLockedLock()  # type: ignore[assignment]
    monkeypatch.setattr(service_runtime.os, "getpid", lambda: owner_pid + 1)

    with pytest.raises(ServiceRuntimeError) as error:
        factory(_profile("security:shared"), _peer("peer:child"))

    assert error.value.code == "PROCESS_BOUNDARY"
    assert factory._context_pool is None


@pytest.mark.parametrize(
    ("profile", "security_profiles", "error_code"),
    [
        (
            {**_profile("security:shared"), "backend": "other"},
            {"security:shared": ClientCurveKeys(b"server", b"public", b"secret")},
            "UNKNOWN_BACKEND",
        ),
        (
            _profile("security:missing"),
            {"security:shared": ClientCurveKeys(b"server", b"public", b"secret")},
            "SECURITY_PROFILE_UNAVAILABLE",
        ),
        (
            {**_profile("security:shared"), "endpoints": {}},
            {"security:shared": ClientCurveKeys(b"server", b"public", b"secret")},
            "RPC_ENDPOINT_REQUIRED",
        ),
    ],
    ids=("backend", "security-profile", "endpoint"),
)
def test_existing_profile_rejections_do_not_allocate_context_pool(
    monkeypatch: pytest.MonkeyPatch,
    profile: dict[str, object],
    security_profiles: dict[str, ClientCurveKeys],
    error_code: str,
) -> None:
    records = _install_transport_fakes(monkeypatch)
    factory = ZmqTransportFactory(security_profiles)

    with pytest.raises(ServiceRuntimeError) as error:
        factory(profile, _peer("peer:invalid"))

    assert error.value.code == error_code
    assert records["pools"] == []
    assert factory._context_pool is None


def test_factory_construction_does_not_import_optional_zmq_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def reject_zmq_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("bt_api_transport_zmq"):
            raise AssertionError("transport package imported before factory activation")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_zmq_import)
    factory = ZmqTransportFactory({})

    assert factory._context_pool is None
