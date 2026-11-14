"""Direct facade import isolation with synthetic in-memory endpoints.

The child hooks are defensive observations, not an OS sandbox: native code,
inherited file descriptors, and unsupported system calls can bypass them. This
exercises the actual BtApi facade with a fake feed and fake provider, not BT
Broker startup or provider discovery. No provider service, background thread,
filesystem journal or account is used.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

if __name__ != "__main__":
    import pytest

    pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX fcntl guards")


def _child(mode):
    # Install all observers before importing any product package. The parent
    # supplies -B/PYTHONDONTWRITEBYTECODE to prevent bytecode writes as well.
    import fcntl
    import socket
    import threading
    import _thread

    attempts = {"risk": 0, "network": 0, "bind": 0, "private_read": 0, "write": 0, "lock": 0, "thread": 0}

    bind_origins = []

    class RiskObserver:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "bt_api_risk" or fullname.startswith("bt_api_risk."):
                attempts["risk"] += 1
                raise ImportError("Risk import blocked")
            return None

    sys.meta_path.insert(0, RiskObserver())
    assert not any(n == "bt_api_risk" or n.startswith("bt_api_risk.") for n in sys.modules)

    def audit(event, args):
        if event == "socket.bind":
            attempts["bind"] += 1
            bind_origins.append(sys._getframe(1).f_globals.get("__name__"))
            raise PermissionError("socket bind blocked")
        if event in {"socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo"}:
            attempts["network"] += 1
            raise PermissionError("network blocked")
        if event == "open":
            path, mode_arg, flags = args
            if isinstance(path, (str, bytes, os.PathLike)):
                name = os.fsdecode(path).lower()
                if os.path.basename(name).startswith(".env") or os.path.basename(name) in {"config.yaml", "config.yml"} or name.endswith((".sqlite", ".sqlite3", ".db", ".sqlite-wal", ".db-wal")):
                    attempts["private_read"] += 1
                    raise PermissionError("private config and database reads blocked")
            write_mode = isinstance(mode_arg, str) and any(c in mode_arg for c in "wax+")
            write_flags = isinstance(flags, int) and flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            )
            if write_mode or write_flags:
                attempts["write"] += 1
                raise PermissionError("file writes blocked")
        if event in {"os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.link",
                     "os.symlink", "os.chmod", "os.chown", "os.truncate"}:
            attempts["write"] += 1
            raise PermissionError("file mutations blocked")
        if event in {"subprocess.Popen", "os.system", "os.fork", "os.posix_spawn"}:
            raise PermissionError("process creation blocked")

    sys.addaudithook(audit)

    def deny_lock(*args, **kwargs):
        attempts["lock"] += 1
        raise PermissionError("file locks blocked")

    def deny_thread(*args, **kwargs):
        attempts["thread"] += 1
        raise RuntimeError("thread start blocked")

    fcntl.flock = deny_lock
    fcntl.lockf = deny_lock
    original_fcntl = fcntl.fcntl
    lock_commands = {getattr(fcntl, name) for name in (
        "F_SETLK", "F_SETLKW", "F_OFD_SETLK", "F_OFD_SETLKW"
    ) if hasattr(fcntl, name)}

    def guarded_fcntl(fd, command, *args):
        if command in lock_commands:
            return deny_lock(fd, command, *args)
        return original_fcntl(fd, command, *args)

    fcntl.fcntl = guarded_fcntl
    if hasattr(os, "lockf"):
        os.lockf = deny_lock
    threading.Thread.start = deny_thread
    threading._start_new_thread = deny_thread
    _thread.start_new_thread = deny_thread

    if mode == "guards":
        try:
            with socket.socket() as sock:
                sock.connect(("127.0.0.1", 1))
        except PermissionError:
            pass
        else:
            raise AssertionError("connect guard failed")
        try:
            with socket.socket() as sock:
                sock.connect_ex(("127.0.0.1", 1))
        except PermissionError:
            pass
        else:
            raise AssertionError("connect_ex guard failed")
        try:
            with socket.socket(type=socket.SOCK_DGRAM) as sock:
                sock.sendto(b"synthetic", ("127.0.0.1", 1))
        except PermissionError:
            pass
        else:
            raise AssertionError("sendto guard failed")
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
        except PermissionError:
            pass
        else:
            raise AssertionError("bind guard failed")
        for name in ("/__iter64__/.env", "/__iter64__/config.yaml", "/__iter64__/facts.sqlite"):
            try:
                open(name, "r")
            except PermissionError:
                pass
            else:
                raise AssertionError("private read guard failed")
        try:
            open("/__iter64_write_blocked__", "w")
        except PermissionError:
            pass
        else:
            raise AssertionError("write guard failed")
        try:
            fcntl.flock(-1, fcntl.LOCK_EX)
        except PermissionError:
            pass
        else:
            raise AssertionError("lock guard failed")
        try:
            fcntl.fcntl(-1, fcntl.F_SETLK, b"")
        except PermissionError:
            pass
        else:
            raise AssertionError("fcntl lock guard failed")
        try:
            threading.Thread(target=lambda: None).start()
        except RuntimeError:
            pass
        else:
            raise AssertionError("thread guard failed")
        assert attempts == {"risk": 0, "network": 3, "bind": 1, "private_read": 3, "write": 1, "lock": 2, "thread": 1}
    elif mode == "risk_control":
        from importlib.machinery import PathFinder
        assert PathFinder.find_spec("bt_api_risk") is not None
        try:
            import bt_api_risk  # noqa: F401 -- positive observer control
        except ImportError as exc:
            assert str(exc) == "Risk import blocked"
        else:
            raise AssertionError("Risk import guard failed")
        assert attempts["risk"] > 0 and "bt_api_risk" not in sys.modules
        assert all(attempts[key] == 0 for key in ("network", "bind", "private_read", "write", "lock", "thread"))
    elif mode == "direct":
        import logging
        from decimal import Decimal
        import bt_api_base.logging_factory as logging_factory

        logger = logging.getLogger("iter64-facade-isolation")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        logging_factory.get_logger = lambda *args, **kwargs: logger

        from bt_api_base.direct_write_protocol import (
            DirectExecutionEvent, DirectExecutionEventKind, DirectOrderReservation,
            DirectOrderStatus, DirectSubmitResult, DirectWriteErrorPolicy,
            NativeOrderIdentity, PreDispatchRecord, WriteOperation,
        )
        from bt_api_py.bt_api import BtApi
        import bt_api_py.bt_api as facade_module
        import bt_api_py.provider_entrypoints as entrypoints
        from bt_api_py._contracts import OrderRequest, OrderType, Side

        route = "FAKE___FUTURE"
        calls = []
        identity = NativeOrderIdentity(
            exchange_name=route, client_order_id="fake-101", session_generation=3,
            fields={"order_ref": "fake-101", "front_id": 1, "session_id": 7,
                    "trading_day": "20261106", "exchange_id": "FAKE"},
        )

        class Feed:
            def disconnect(self):
                calls.append("feed_disconnect")

        feed = Feed()

        class Provider:
            direct_write_error_policy = DirectWriteErrorPolicy(frozenset(), frozenset())

            def __init__(self):
                self.events = []

            def session_generation(self):
                return 3

            def reserve_order(self, request):
                assert request is order
                calls.append("reserve")
                return DirectOrderReservation(identity=identity, symbol="fake-symbol", reservation_id="r1")

            def release_reservation(self, reservation):
                calls.append("release")

            def submit_order(self, reservation, request, grant, verifier):
                assert request is order and reservation.identity == identity
                calls.append("submit")
                verifier.consume(grant, exchange_name=route, operation=WriteOperation.PLACE,
                                 session_generation=3)
                return DirectSubmitResult(sent=True, identity=reservation.identity)

            def drain_execution_events(self, max_events):
                calls.append("poll")
                result, self.events = self.events[:max_events], self.events[max_events:]
                return tuple(result)

            def close(self):
                calls.append("provider_close")

        class Port:
            def commit(self, record):
                assert isinstance(record, PreDispatchRecord)
                calls.append("commit")
                return "marker"

        provider = Provider()

        def create_feed(name, queue, **settings):
            assert name == route and settings == {"synthetic": True}
            calls.append("feed_create")
            return feed

        def load_entrypoint(group, name, *, optional=False):
            assert group == "bt_api.direct_write_providers" and name == route and optional
            calls.append("discovery")
            def factory(selected_feed):
                assert selected_feed is feed
                calls.append("provider_create")
                return provider
            return factory

        facade_module._ensure_plugins_loaded = lambda: calls.append("plugin_discovery_fake")
        facade_module.ExchangeRegistry.create_feed = create_feed
        entrypoints.load_route_entrypoint = load_entrypoint
        api = BtApi({route: {"synthetic": True}}, debug=False, write_admission={route: True})
        assert api.get_request_api(route) is feed
        assert calls == ["plugin_discovery_fake", "feed_create"]
        order = OrderRequest(symbol="fake-symbol", side=Side.BUY, order_type=OrderType.LIMIT,
                             quantity=Decimal("1"), price=Decimal("10"),
                             account_id="synthetic", client_order_id="fake-101")
        outcome = api.make_order(route, order, pre_dispatch_commit=Port(), host_scope="fake-scope")
        assert outcome.sent is True and outcome.marker_id == "marker"
        provider.events = [DirectExecutionEvent(
            kind=DirectExecutionEventKind.ORDER, exchange_name=route, symbol="fake-symbol",
            identity=identity, status=DirectOrderStatus.ACCEPTED, filled_qty=0.0,
            raw_status="accepted", sequence=1,
        )]
        (event,) = api.poll_direct_execution_events(route)
        assert event.matched and event.marker_id == "marker" and event.host_scope == "fake-scope"
        api.close()
        assert calls == ["plugin_discovery_fake", "feed_create", "discovery", "provider_create",
                         "reserve", "commit", "submit", "poll", "feed_disconnect", "provider_close"]
        # urllib3 tests local IPv6 support at import; its bind is denied before
        # reaching the OS. It is unrelated to Risk and may vary by version.
        assert bind_origins in ([], ["urllib3.util.connection"]), bind_origins
        assert all(attempts[key] == 0 for key in
                   ("risk", "network", "private_read", "write", "lock", "thread")), attempts
        assert not any(n == "bt_api_risk" or n.startswith("bt_api_risk.") for n in sys.modules)
    else:
        raise AssertionError("unknown child mode")
    print(json.dumps({"mode": mode, "attempts": attempts}))


def _run_child(mode):
    sdk = Path(__file__).resolve().parents[2]
    paths = [sdk, sdk / "bt_api/bt_api_base/src", sdk / "bt_api/bt_api_risk/src"]
    env = {"PYTHONPATH": os.pathsep.join(map(str, paths)), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONNOUSERSITE": "1"}
    result = subprocess.run([sys.executable, "-B", __file__, mode], cwd=sdk, env=env,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_direct_facade_never_imports_risk_or_performs_external_io():
    attempts = _run_child("direct")["attempts"]
    assert attempts["bind"] in (0, 1)
    assert all(attempts[key] == 0 for key in
               ("risk", "network", "private_read", "write", "lock", "thread"))


def test_observers_detect_risk_and_block_side_effects():
    assert _run_child("risk_control")["attempts"]["risk"] > 0
    assert _run_child("guards")["attempts"] == {
        "risk": 0, "network": 3, "bind": 1, "private_read": 3, "write": 1, "lock": 2, "thread": 1,
    }


if __name__ == "__main__":
    _child(sys.argv[1])
