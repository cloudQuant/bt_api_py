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
        self.pending_exit_at: float | None = None
        self.clock = None
        self.on_stop = None
        self.closed = False

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float = 0) -> None:
        exit_due = self.pending_exit_at is None or (
            self.clock is not None and self.clock() >= self.pending_exit_at
        )
        if self.alive and self.pending_exitcode is not None and exit_due:
            self.alive = False
            self.exitcode = self.pending_exitcode
            self.pending_exitcode = None
            self.events.append((self.name, "stopped"))
            if self.on_stop is not None:
                self.on_stop()

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
        elif trigger == "slow-owner-close":
            owner.pending_exitcode = 0
            owner.pending_exit_at = owner.clock() + 25.0
            owner_pipe.messages.append(
                ("owner-close-progress", 11, 0, "relay-drain", 0, 5)
            )
            owner.on_stop = lambda: owner_pipe.messages.extend(
                [
                    ("owner-close-progress", 11, 0, "relay-drain", 5, 5),
                    ("owner-close-progress", 11, 0, "execution-host-close", 5, 5),
                    ("owner-close-progress", 11, 0, "account-host-close", 5, 5),
                    ("owner-close-progress", 11, 0, "store-close", 5, 5),
                    ("owner-close-progress", 11, 0, "closed-ack-send", 5, 5),
                    (
                        "closed",
                        0,
                        11,
                        "closed",
                        (),
                        "closed",
                        (),
                        "closed",
                        0,
                        0,
                        0,
                        0,
                        5,
                        {},
                        (),
                        (),
                        None,
                        {"normal_drain_completed": True},
                    ),
                ]
            )

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
    assert fake_clock.now >= (
        runner._OWNER_CLOSE_GRACE_SECONDS
        + runner._EMERGENCY_TERMINATE_GRACE_SECONDS
        + runner._EMERGENCY_KILL_GRACE_SECONDS
    )
    assert fake_clock.now <= 160.1
    assert terminated == ["owner"]
    assert owner["normal_exit"] is False and owner["closed"] is False
    assert monitor["normal_exit"] is False and monitor["closed"] is False
    assert monitor["process"].alive and not monitor["pipe"].closed
    assert not any(event[:2] == ("monitor-pipe", "send:close") for event in events)
    assert "owner:still-alive" in cleanup_errors
    assert "monitor:close-withheld-owner-not-stopped" in cleanup_errors


def test_owner_close_can_finish_after_ordinary_grace_before_relay_deadline(
    monkeypatch,
) -> None:
    events: list[tuple[str, str]] = []
    records = _records("slow-owner-close", events=events)
    fake_clock = _FakeClock()
    records["owner"]["process"].clock = fake_clock.monotonic

    def receive(pipe: _FakePipe):
        if pipe.messages:
            return True, pipe.messages.popleft(), None
        if pipe.terminal is not None:
            terminal, pipe.terminal = pipe.terminal, None
            return False, None, terminal
        return False, None, None

    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(monotonic=fake_clock.monotonic, sleep=fake_clock.sleep),
    )
    monkeypatch.setattr(runner, "_recv_pipe_event_if_ready", receive)
    terminated: list[str] = []
    cleanup_errors: list[str] = []

    runner._emergency_cleanup(
        records,
        abort_event=_FakeEvent(),
        stop_event=_FakeEvent(),
        terminated=terminated,
        cleanup_errors=cleanup_errors,
    )

    owner = records["owner"]
    assert 20.0 < fake_clock.now < runner._OWNER_CLOSE_GRACE_SECONDS
    assert terminated == []
    assert owner["closed"] and owner["exit_code"] == 0
    assert owner["owner_emergency_close_ack_observed"] is True
    assert owner["normal_exit"] is False
    assert owner["owner_close_progress"][-1] == {
        "account_index": 0,
        "phase": "closed-ack-send",
        "relay_workers_completed": 5,
        "relay_workers_total": 5,
    }
    assert records["monitor"]["closed"]
    monitor_close = events.index(("monitor", "close-saw-owner-alive:False"))
    owner_stopped = events.index(("owner", "stopped"))
    assert monitor_close > owner_stopped
    assert cleanup_errors == []


def test_collect_expected_accepts_owner_close_progress_before_ack(
    monkeypatch,
) -> None:
    events: list[tuple[str, str]] = []
    record = {
        "name": "owner",
        "kind": "owner",
        "pid": 11,
        "process": _FakeProcess("owner", events),
        "pipe": _FakePipe(
            "owner-pipe",
            events,
            messages=(
                ("owner-close-progress", 11, 0, "relay-drain", 2, 5),
                ("owner-close-progress", 11, 0, "execution-host-close", 5, 5),
                ("closed", 0, 11),
            ),
        ),
    }

    monkeypatch.setattr(
        runner,
        "_recv_pipe_event_if_ready",
        lambda pipe: (True, pipe.messages.popleft(), None)
        if pipe.messages
        else (False, None, None),
    )
    closed = runner._collect_expected(
        {0: record}, "closed", 1.0, "owner close", {"owner": {}}
    )

    assert closed[0] == ("closed", 0, 11)
    assert record["owner_close_progress"] == [
        {
            "account_index": 0,
            "phase": "relay-drain",
            "relay_workers_completed": 2,
            "relay_workers_total": 5,
        },
        {
            "account_index": 0,
            "phase": "execution-host-close",
            "relay_workers_completed": 5,
            "relay_workers_total": 5,
        },
    ]


@pytest.mark.parametrize(
    ("profile_id", "account_index", "worker_count"),
    [("N2", 0, 8), ("N3", 0, 6), ("N3", 1, 6)],
)
def test_owner_close_progress_accepts_n2_n3_worker_counts(
    profile_id: str,
    account_index: int,
    worker_count: int,
) -> None:
    record = {"kind": "owner", "pid": 17}
    message = (
        "owner-close-progress",
        17,
        account_index,
        "relay-drain",
        worker_count,
        worker_count,
    )

    assert runner._retain_owner_close_progress(record, message) is True
    assert record["owner_close_progress"] == [
        {
            "account_index": account_index,
            "phase": "relay-drain",
            "relay_workers_completed": worker_count,
            "relay_workers_total": worker_count,
        }
    ]


def test_owner_close_progress_rejects_out_of_range_account_and_worker_counts() -> None:
    record = {"kind": "owner", "pid": 17}
    invalid_messages = (
        ("owner-close-progress", 17, runner._ACCOUNTS, "relay-drain", 1, 1),
        ("owner-close-progress", 17, 0, "relay-drain", 11, 11),
        ("owner-close-progress", 18, 0, "relay-drain", 1, 1),
    )

    assert all(
        runner._retain_owner_close_progress(record, message) is False
        for message in invalid_messages
    )
    assert "owner_close_progress" not in record


def test_owner_grace_is_separate_from_other_dependent_close_grace(
    monkeypatch,
) -> None:
    events: list[tuple[str, str]] = []
    records = _records("slow-owner-close", events=events)
    fake_clock = _FakeClock()
    records["owner"]["process"].clock = fake_clock.monotonic
    coordinator_process = _FakeProcess(
        "coordinator",
        events,
        terminate_stops=False,
        kill_stops=False,
    )
    records["coordinator"] = _service_record(
        "coordinator",
        "coordinator",
        events,
        on_close=lambda: None,
        process=coordinator_process,
    )

    def receive(pipe: _FakePipe):
        if pipe.messages:
            return True, pipe.messages.popleft(), None
        if pipe.terminal is not None:
            terminal, pipe.terminal = pipe.terminal, None
            return False, None, terminal
        return False, None, None

    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(monotonic=fake_clock.monotonic, sleep=fake_clock.sleep),
    )
    monkeypatch.setattr(runner, "_recv_pipe_event_if_ready", receive)
    terminated: list[str] = []
    cleanup_errors: list[str] = []

    runner._emergency_cleanup(
        records,
        abort_event=_FakeEvent(),
        stop_event=_FakeEvent(),
        terminated=terminated,
        cleanup_errors=cleanup_errors,
    )

    assert 65.0 <= fake_clock.now <= 65.2
    assert terminated == ["coordinator"]
    assert records["owner"]["closed"] and records["owner"]["exit_code"] == 0
    assert records["coordinator"]["process"].alive
    assert not records["coordinator"]["closed"]
    assert records["monitor"]["process"].alive
    assert not records["monitor"]["closed"]
    assert not any(event == ("monitor-pipe", "send:close") for event in events)
    owner_stop = events.index(("owner", "stopped"))
    coordinator_close = events.index(("coordinator-pipe", "send:close"))
    assert coordinator_close > owner_stop
    assert "coordinator:still-alive" in cleanup_errors
    assert "monitor:close-withheld-dependent-not-stopped" in cleanup_errors


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
