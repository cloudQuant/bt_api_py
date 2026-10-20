"""Regression tests for LOAD01 owner/monitor emergency cleanup ordering."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import pytest

from tests.unit import test_load01_hundred_strategy_30min as runner


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _FakeEvent:
    def __init__(self) -> None:
        self.was_set = False

    def set(self) -> None:
        self.was_set = True


class _FakeProcess:
    def __init__(
        self,
        name: str,
        events: list[tuple[str, str]],
        *,
        alive: bool = True,
        exitcode: int | None = None,
        terminate_stops: bool = True,
        kill_stops: bool = True,
    ) -> None:
        self.name = name
        self.events = events
        self.alive = alive
        self.exitcode = exitcode
        self.terminate_stops = terminate_stops
        self.kill_stops = kill_stops
        self.pending_exitcode: int | None = None
        self.closed = False

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float = 0) -> None:
        if self.alive and self.pending_exitcode is not None:
            self.alive = False
            self.exitcode = self.pending_exitcode
            self.pending_exitcode = None
            self.events.append((self.name, "stopped"))

    def terminate(self) -> None:
        self.events.append((self.name, "terminate"))
        if self.terminate_stops:
            self.alive = False
            self.exitcode = -15
            self.events.append((self.name, "stopped"))

    def kill(self) -> None:
        self.events.append((self.name, "kill"))
        if self.kill_stops:
            self.alive = False
            self.exitcode = -9
            self.events.append((self.name, "stopped"))

    def close(self) -> None:
        self.closed = True


class _FakePipe:
    def __init__(
        self,
        name: str,
        events: list[tuple[str, str]],
        *,
        on_send=None,
        messages=(),
        terminal=None,
    ) -> None:
        self.name = name
        self.events = events
        self.on_send = on_send
        self.messages = deque(messages)
        self.terminal = terminal
        self.closed = False

    def send(self, message) -> None:
        self.events.append((self.name, f"send:{message[0]}"))
        if self.on_send is not None:
            self.on_send(message)

    def close(self) -> None:
        self.closed = True


def _records(
    trigger: str,
    *,
    events: list[tuple[str, str]],
    owner_never_stops: bool = False,
) -> dict[str, dict]:
    owner = _FakeProcess(
        "owner",
        events,
        alive=trigger != "pipe-eof",
        exitcode=1 if trigger == "pipe-eof" else None,
        terminate_stops=not owner_never_stops,
        kill_stops=not owner_never_stops,
    )

    def owner_send(message) -> None:
        if message[0] != "close":
            return
        if trigger == "owner-failed":
            owner_pipe.messages.append(
                ("error", "RelayFailed", 1, None, {"rpc_timing_at_failure": {"failed": True}})
            )
            owner.pending_exitcode = 1
        elif trigger == "parent-exception" and not owner_never_stops:
            owner.pending_exitcode = 0

    owner_pipe = _FakePipe(
        "owner-pipe",
        events,
        on_send=owner_send,
        terminal={"type": "EOFError"} if trigger == "pipe-eof" else None,
    )
    owner_record = {
        "name": "owner",
        "kind": "owner",
        "process": owner,
        "pipe": owner_pipe,
        "pid": 11,
        "closed": False,
        "rpc_diagnostic_ready": False,
    }

    monitor = _FakeProcess("monitor", events)

    def monitor_send(message) -> None:
        if message[0] == "close":
            events.append(("monitor", f"close-saw-owner-alive:{owner.is_alive()}"))
            monitor.pending_exitcode = 0

    monitor_pipe = _FakePipe("monitor-pipe", events, on_send=monitor_send)
    monitor_record = {
        "name": "monitor",
        "kind": "monitor",
        "process": monitor,
        "pipe": monitor_pipe,
        "pid": 22,
        "closed": False,
        "ready": True,
    }
    return {"owner": owner_record, "monitor": monitor_record}


@pytest.mark.parametrize(
    ("trigger", "owner_exitcode", "expected_terminal"),
    [
        ("parent-exception", 0, None),
        ("pipe-eof", 1, "EOFError"),
        ("owner-failed", 1, None),
    ],
)
def test_monitor_closes_only_after_owner_is_stopped_for_emergency_paths(
    monkeypatch,
    trigger: str,
    owner_exitcode: int,
    expected_terminal: str | None,
) -> None:
    events: list[tuple[str, str]] = []
    records = _records(trigger, events=events)
    fake_clock = _FakeClock()

    def receive(pipe: _FakePipe):
        if pipe.messages:
            return True, pipe.messages.popleft(), None
        if pipe.terminal is not None:
            terminal, pipe.terminal = pipe.terminal, None
            return False, None, terminal
        return False, None, None

    monkeypatch.setattr(runner, "time", SimpleNamespace(
        monotonic=fake_clock.monotonic,
        sleep=fake_clock.sleep,
    ))
    monkeypatch.setattr(runner, "_recv_pipe_event_if_ready", receive)
    abort_event = _FakeEvent()
    stop_event = _FakeEvent()
    terminated: list[str] = []
    cleanup_errors: list[str] = []

    runner._emergency_cleanup(
        records,
        abort_event=abort_event,
        stop_event=stop_event,
        terminated=terminated,
        cleanup_errors=cleanup_errors,
    )

    owner = records["owner"]
    monitor = records["monitor"]
    assert abort_event.was_set and stop_event.was_set
    assert owner["closed"] and owner["exit_code"] == owner_exitcode
    assert owner["normal_exit"] is False
    assert monitor["closed"] and monitor["normal_exit"] is False
    monitor_close = events.index(("monitor", "close-saw-owner-alive:False"))
    owner_stopped = max(
        index for index, event in enumerate(events) if event == ("owner", "stopped")
    ) if trigger != "pipe-eof" else -1
    assert monitor_close > owner_stopped
    assert owner.get("rpc_timing_collection_error") == expected_terminal
    if trigger == "owner-failed":
        assert owner["rpc_timing_snapshots"]["at_failure"] == {"failed": True}
    assert cleanup_errors == []


def test_monitor_is_retained_when_owner_stop_cannot_be_confirmed(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    records = _records(
        "parent-exception",
        events=events,
        owner_never_stops=True,
    )
    fake_clock = _FakeClock()

    monkeypatch.setattr(runner, "time", SimpleNamespace(
        monotonic=fake_clock.monotonic,
        sleep=fake_clock.sleep,
    ))
    monkeypatch.setattr(
        runner,
        "_recv_pipe_event_if_ready",
        lambda pipe: (False, None, None),
    )
    abort_event = _FakeEvent()
    stop_event = _FakeEvent()
    terminated: list[str] = []
    cleanup_errors: list[str] = []

    runner._emergency_cleanup(
        records,
        abort_event=abort_event,
        stop_event=stop_event,
        terminated=terminated,
        cleanup_errors=cleanup_errors,
    )

    owner = records["owner"]
    monitor = records["monitor"]
    assert 40.0 <= fake_clock.now <= 40.1
    assert terminated == ["owner"]
    assert owner["normal_exit"] is False and owner["closed"] is False
    assert monitor["normal_exit"] is False and monitor["closed"] is False
    assert monitor["process"].alive and not monitor["pipe"].closed
    assert not any(event[:2] == ("monitor-pipe", "send:close") for event in events)
    assert "owner:still-alive" in cleanup_errors
    assert "monitor:close-withheld-owner-not-stopped" in cleanup_errors


def _service_record(
    name: str,
    kind: str,
    events: list[tuple[str, str]],
    *,
    on_close=None,
    process: _FakeProcess | None = None,
    ready: bool = True,
) -> dict:
    child = process or _FakeProcess(name, events)

    def send(message) -> None:
        if message[0] == "close":
            if on_close is not None:
                on_close()
            else:
                child.pending_exitcode = 0

    return {
        "name": name,
        "kind": kind,
        "process": child,
        "pipe": _FakePipe(f"{name}-pipe", events, on_send=send),
        "pid": 100,
        "closed": False,
        "ready": ready,
        "rpc_diagnostic_ready": False,
    }


def test_strategies_stop_before_market_and_owner_closes_before_monitor(
    monkeypatch,
) -> None:
    events: list[tuple[str, str]] = []
    market = _service_record("market", "market", events)
    owner = _service_record("owner", "owner", events)
    strategy_process = _FakeProcess("strategy", events)

    def close_strategy() -> None:
        events.append(
            (
                "strategy",
                "services-alive:"
                f"{market['process'].is_alive()}:"
                f"{owner['process'].is_alive()}:"
                f"{monitor_process.is_alive()}",
            )
        )
        strategy_process.pending_exitcode = 0

    strategy = _service_record(
        "strategy",
        "strategy",
        events,
        on_close=close_strategy,
        process=strategy_process,
    )
    monitor_process = _FakeProcess("monitor", events)

    def close_monitor() -> None:
        events.append(("monitor", f"owner-alive:{owner['process'].is_alive()}"))
        monitor_process.pending_exitcode = 0

    monitor = _service_record(
        "monitor",
        "monitor",
        events,
        on_close=close_monitor,
        process=monitor_process,
    )
    # Match the production insertion order, where the market host precedes
    # owners and strategies and the monitor is filtered into its own phase.
    records = {
        "market": market,
        "owner": owner,
        "monitor": monitor,
        "strategy": strategy,
    }
    fake_clock = _FakeClock()

    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(monotonic=fake_clock.monotonic, sleep=fake_clock.sleep),
    )
    monkeypatch.setattr(
        runner,
        "_recv_pipe_event_if_ready",
        lambda _pipe: (False, None, None),
    )

    cleanup_errors: list[str] = []
    runner._emergency_cleanup(
        records,
        abort_event=_FakeEvent(),
        stop_event=_FakeEvent(),
        terminated=[],
        cleanup_errors=cleanup_errors,
    )

    strategy_stopped = events.index(("strategy", "stopped"))
    market_close = events.index(("market-pipe", "send:close"))
    market_stopped = events.index(("market", "stopped"))
    owner_stopped = events.index(("owner", "stopped"))
    monitor_close = next(
        index for index, event in enumerate(events) if event[0] == "monitor"
    )
    assert strategy_stopped < market_close < market_stopped < owner_stopped < monitor_close
    assert ("strategy", "services-alive:True:True:True") in events
    assert ("monitor", "owner-alive:False") in events
    assert all(record["closed"] for record in records.values())
    assert cleanup_errors == []


def test_uncertain_strategy_retains_market_but_preserves_owner_monitor_gate(
    monkeypatch,
) -> None:
    events: list[tuple[str, str]] = []
    market = _service_record("market", "market", events)
    owner = _service_record("owner", "owner", events)
    coordinator = _service_record("coordinator", "coordinator", events)
    strategy_process = _FakeProcess(
        "strategy",
        events,
        terminate_stops=False,
        kill_stops=False,
    )
    strategy = _service_record(
        "strategy",
        "strategy",
        events,
        on_close=lambda: None,
        process=strategy_process,
    )
    monitor_process = _FakeProcess("monitor", events)
    monitor = _service_record(
        "monitor",
        "monitor",
        events,
        process=monitor_process,
        on_close=lambda: (
            events.append(("monitor", f"owner-alive:{owner['process'].is_alive()}")),
            setattr(monitor_process, "pending_exitcode", 0),
        ),
    )
    records = {
        "market": market,
        "owner": owner,
        "coordinator": coordinator,
        "monitor": monitor,
        "strategy": strategy,
    }
    fake_clock = _FakeClock()

    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(monotonic=fake_clock.monotonic, sleep=fake_clock.sleep),
    )
    monkeypatch.setattr(
        runner,
        "_recv_pipe_event_if_ready",
        lambda _pipe: (False, None, None),
    )
    terminated: list[str] = []
    cleanup_errors: list[str] = []

    runner._emergency_cleanup(
        records,
        abort_event=_FakeEvent(),
        stop_event=_FakeEvent(),
        terminated=terminated,
        cleanup_errors=cleanup_errors,
    )

    assert 40.0 <= fake_clock.now <= 40.1
    assert terminated == ["strategy"]
    assert strategy["process"].alive and not strategy["closed"]
    assert market["process"].alive and not market["closed"]
    assert market["normal_exit"] is False and not market["pipe"].closed
    assert not any(event == ("market-pipe", "send:close") for event in events)
    for service in (market, owner, coordinator, monitor):
        assert service["process"].alive and not service["closed"]
        assert service["normal_exit"] is False and not service["pipe"].closed
        assert not any(
            event == (f"{service['name']}-pipe", "send:close")
            for event in events
        )
    assert "strategy:still-alive" in cleanup_errors
    assert "market:close-withheld-strategy-not-stopped" in cleanup_errors
    assert "owner:close-withheld-strategy-not-stopped" in cleanup_errors
    assert "coordinator:close-withheld-strategy-not-stopped" in cleanup_errors
    assert "monitor:close-withheld-strategy-not-stopped" in cleanup_errors
    assert "monitor:close-withheld-owner-not-stopped" not in cleanup_errors
