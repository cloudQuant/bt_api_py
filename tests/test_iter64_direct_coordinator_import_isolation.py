"""Direct coordinator import isolation with synthetic in-memory endpoints.

The child hooks are defensive observations, not an OS sandbox: native code,
inherited file descriptors, and unsupported system calls can bypass them. This
exercises SDK coordinator construction and its calls, not BtApi or BT Broker
startup. No provider service, thread, filesystem journal or account is used.
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

    attempts = {"risk": 0, "network": 0, "write": 0, "lock": 0, "thread": 0}

    class RiskObserver:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "bt_api_risk" or fullname.startswith("bt_api_risk."):
                attempts["risk"] += 1
            return None

    sys.meta_path.insert(0, RiskObserver())
    assert not any(n == "bt_api_risk" or n.startswith("bt_api_risk.") for n in sys.modules)

    def audit(event, args):
        if event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
            attempts["network"] += 1
            raise PermissionError("network blocked")
        if event == "open":
            _, mode_arg, flags = args
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
        assert attempts == {"risk": 0, "network": 3, "write": 1, "lock": 2, "thread": 1}
    elif mode == "risk_control":
        import bt_api_risk  # noqa: F401 -- no business operations
        assert attempts["risk"] > 0 and "bt_api_risk" in sys.modules
        assert all(attempts[key] == 0 for key in ("network", "write", "lock", "thread"))
    elif mode == "direct":
        # SDK package initialization otherwise creates a disk FileHandler.
        # Keep logging in memory while exercising the actual coordinator.
        import logging
        import bt_api_base.logging_factory as logging_factory

        logger = logging.getLogger("iter64-isolation")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        logging_factory.get_logger = lambda *args, **kwargs: logger

        from bt_api_base.direct_write_protocol import (
            DirectExecutionEvent, DirectExecutionEventKind, DirectOrderReservation,
            DirectOrderStatus, DirectSubmitResult, DirectWriteErrorPolicy,
            NativeOrderIdentity, PreDispatchRecord, WriteAdmissionDenied, WriteOperation,
        )
        from bt_api_py.direct_write import DirectWriteCoordinator
        from bt_api_py.write_admission import WriteAdmission

        route = "CTP___FUTURE"
        calls = []
        identity = NativeOrderIdentity(
            exchange_name=route, client_order_id="000000000101", session_generation=3,
            fields={"order_ref": "000000000101", "front_id": 1, "session_id": 7,
                    "trading_day": "20261106", "exchange_id": "SHFE"},
        )

        class Provider:
            direct_write_error_policy = DirectWriteErrorPolicy(frozenset(), frozenset())

            def __init__(self):
                self.events = []

            def session_generation(self):
                return 3

            def reserve_order(self, request):
                calls.append("reserve")
                return DirectOrderReservation(identity=identity, symbol="rb2701", reservation_id="r1")

            def release_reservation(self, reservation):
                calls.append("release")

            def submit_order(self, reservation, request, grant, verifier):
                calls.append("submit")
                verifier.consume(grant, exchange_name=route, operation=WriteOperation.PLACE,
                                 session_generation=3)
                return DirectSubmitResult(sent=True, identity=reservation.identity)

            def drain_execution_events(self, max_events):
                result, self.events = self.events[:max_events], self.events[max_events:]
                return tuple(result)

            def close(self):
                calls.append("close")

        class Port:
            def __init__(self, marker):
                self.marker = marker

            def commit(self, record):
                assert isinstance(record, PreDispatchRecord)
                calls.append("commit")
                return self.marker

        provider = Provider()

        def factory(name):
            assert name == route
            calls.append("provider")
            return provider

        denied = DirectWriteCoordinator(WriteAdmission(), factory)
        try:
            denied.place(route, object(), commit_port=Port("marker"), host_scope="scope")
        except WriteAdmissionDenied as exc:
            assert exc.code == "write_admission_disabled"
        else:
            raise AssertionError("disabled admission did not deny")
        assert calls == []
        denied.close()

        coordinator = DirectWriteCoordinator(WriteAdmission({route: True}), factory)
        assert coordinator.provider(route) is provider
        failure = coordinator.place(route, object(), commit_port=Port(None), host_scope="scope")
        assert failure.sent is False and failure.error_code == "pre_dispatch_commit_failed"
        assert calls == ["provider", "reserve", "commit", "release"]
        outcome = coordinator.place(route, object(), commit_port=Port("marker"), host_scope="scope")
        assert outcome.sent is True and outcome.marker_id == "marker"
        accepted_identity = NativeOrderIdentity(
            exchange_name=route, client_order_id=identity.client_order_id,
            session_generation=3, fields={**identity.fields, "order_sys_id": "S1"},
        )
        trade_identity = NativeOrderIdentity(
            exchange_name=route, client_order_id=identity.client_order_id,
            fields={"order_ref": "000000000101", "exchange_id": "SHFE", "order_sys_id": "S1"},
        )
        provider.events = [
            DirectExecutionEvent(kind=DirectExecutionEventKind.ORDER, exchange_name=route,
                symbol="rb2701", identity=accepted_identity, exchange_order_id="S1",
                status=DirectOrderStatus.ACCEPTED, filled_qty=0.0, raw_status="accepted", sequence=1),
            DirectExecutionEvent(kind=DirectExecutionEventKind.TRADE, exchange_name=route,
                symbol="rb2701", identity=trade_identity, exchange_order_id="S1", side="buy",
                trade_id="T1", trade_price=3000.0, trade_qty=1.0),
        ]
        events = coordinator.poll_events(route)
        assert [e.event.kind for e in events] == [DirectExecutionEventKind.ORDER,
                                                  DirectExecutionEventKind.TRADE]
        assert all(e.matched and e.marker_id == "marker" and e.host_scope == "scope" for e in events)
        unmatched_identity = NativeOrderIdentity(
            exchange_name=route, client_order_id="other", fields={
                "exchange_id": "SHFE", "order_sys_id": "OTHER"},
        )
        provider.events = [DirectExecutionEvent(
            kind=DirectExecutionEventKind.TRADE, exchange_name=route, symbol="rb2701",
            identity=unmatched_identity, exchange_order_id="OTHER", side="buy",
            trade_id="T2", trade_price=3000.0, trade_qty=1.0,
        )]
        (unmatched,) = coordinator.poll_events(route)
        assert not unmatched.matched and unmatched.marker_id is None
        coordinator.close()
        assert calls == ["provider", "reserve", "commit", "release", "reserve", "commit",
                         "submit", "close"]
        assert attempts == {"risk": 0, "network": 0, "write": 0, "lock": 0, "thread": 0}
        assert not any(n == "bt_api_risk" or n.startswith("bt_api_risk.") for n in sys.modules)
    else:
        raise AssertionError("unknown child mode")
    print(json.dumps({"mode": mode, "attempts": attempts}))


def _run_child(mode):
    sdk = Path(__file__).resolve().parents[1]
    paths = [sdk, sdk / "bt_api/bt_api_base/src", sdk / "bt_api/bt_api_risk/src"]
    env = {"PYTHONPATH": os.pathsep.join(map(str, paths)), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONNOUSERSITE": "1"}
    result = subprocess.run([sys.executable, "-B", __file__, mode], cwd=sdk, env=env,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_direct_coordinator_never_imports_risk_or_performs_external_io():
    assert _run_child("direct")["attempts"] == {
        "risk": 0, "network": 0, "write": 0, "lock": 0, "thread": 0,
    }


def test_observers_detect_risk_and_block_side_effects():
    assert _run_child("risk_control")["attempts"]["risk"] > 0
    assert _run_child("guards")["attempts"] == {
        "risk": 0, "network": 3, "write": 1, "lock": 2, "thread": 1,
    }


if __name__ == "__main__":
    _child(sys.argv[1])
