"""Tests for Gateway health aggregator and process management."""

from __future__ import annotations

import os
import time

import pytest

from bt_api_py.gateway.health import (
    ConnectionState,
    GatewayHealth,
    GatewayState,
)
from bt_api_py.gateway.process import GatewayProcess

# ---------------------------------------------------------------------------
# GatewayHealth
# ---------------------------------------------------------------------------


class TestGatewayHealth:
    def test_initial_state(self):
        h = GatewayHealth()
        assert h.state == GatewayState.STOPPED
        assert h.is_healthy is False

    def test_set_state(self):
        h = GatewayHealth()
        h.set_state(GatewayState.RUNNING)
        assert h.state == GatewayState.RUNNING

    def test_is_healthy_requires_running_and_connected(self):
        h = GatewayHealth()
        h.set_state(GatewayState.RUNNING)
        assert h.is_healthy is False  # market not connected yet

        h.update_market_connection(ConnectionState.CONNECTED)
        assert h.is_healthy is True

        h.update_market_connection(ConnectionState.RECONNECTING)
        assert h.is_healthy is False

    def test_connection_states(self):
        h = GatewayHealth()
        h.update_market_connection(ConnectionState.CONNECTED)
        h.update_trade_connection(ConnectionState.CONNECTED)
        snap = h.snapshot()
        assert snap["market_connection"] == "connected"
        assert snap["trade_connection"] == "connected"

    def test_record_heartbeat(self):
        h = GatewayHealth()
        h.set_state(GatewayState.RUNNING)
        h.record_heartbeat()
        snap = h.snapshot()
        assert snap["last_heartbeat"] is not None
        assert snap["heartbeat_age_sec"] is not None
        assert snap["heartbeat_age_sec"] < 1.0

    def test_record_tick_and_order(self):
        h = GatewayHealth()
        h.record_tick()
        h.record_tick()
        h.record_order()
        snap = h.snapshot()
        assert snap["tick_count"] == 2
        assert snap["order_count"] == 1
        assert snap["last_tick_time"] is not None
        assert snap["last_order_time"] is not None

    def test_update_counts(self):
        h = GatewayHealth()
        h.update_counts(strategy_count=3, symbol_count=10)
        snap = h.snapshot()
        assert snap["strategy_count"] == 3
        assert snap["symbol_count"] == 10

    def test_record_error(self):
        h = GatewayHealth()
        h.record_error("adapter", "connection lost")
        h.record_error("runtime", "zmq timeout")
        snap = h.snapshot()
        assert len(snap["recent_errors"]) == 2
        assert snap["recent_errors"][0]["source"] == "adapter"
        assert snap["recent_errors"][1]["message"] == "zmq timeout"

    def test_max_errors_limit(self):
        h = GatewayHealth(max_errors=3)
        for i in range(5):
            h.record_error("test", f"err-{i}")
        snap = h.snapshot()
        assert len(snap["recent_errors"]) == 3
        assert snap["recent_errors"][0]["message"] == "err-2"

    def test_set_extra(self):
        h = GatewayHealth()
        h.set_extra("exchange", "BINANCE")
        h.set_extra("account_id", "acc-1")
        snap = h.snapshot()
        assert snap["exchange"] == "BINANCE"
        assert snap["account_id"] == "acc-1"

    def test_uptime(self):
        h = GatewayHealth()
        h.set_state(GatewayState.RUNNING)
        time.sleep(0.1)
        snap = h.snapshot()
        assert snap["uptime_sec"] >= 0.1

    def test_snapshot_no_heartbeat(self):
        h = GatewayHealth()
        snap = h.snapshot()
        assert snap["last_heartbeat"] is None
        assert snap["heartbeat_age_sec"] is None

    def test_state_enum_values(self):
        assert GatewayState.STARTING.value == "starting"
        assert GatewayState.RUNNING.value == "running"
        assert GatewayState.STOPPING.value == "stopping"
        assert GatewayState.STOPPED.value == "stopped"
        assert GatewayState.ERROR.value == "error"

    def test_connection_enum_values(self):
        assert ConnectionState.DISCONNECTED.value == "disconnected"
        assert ConnectionState.CONNECTING.value == "connecting"
        assert ConnectionState.CONNECTED.value == "connected"
        assert ConnectionState.RECONNECTING.value == "reconnecting"
        assert ConnectionState.ERROR.value == "error"


# ---------------------------------------------------------------------------
# GatewayProcess
# ---------------------------------------------------------------------------


class TestGatewayProcess:
    def test_pid_file_path(self, tmp_path):
        proc = GatewayProcess(
            {"exchange_type": "CTP", "account_id": "acc-1", "gateway_runtime_name": "gw-test"},
            pid_dir=str(tmp_path),
        )
        assert proc.pid_file == tmp_path / "gw-test.pid"

    def test_pid_file_default_name(self, tmp_path):
        proc = GatewayProcess(
            {"exchange_type": "BINANCE", "account_id": "bn-1"},
            pid_dir=str(tmp_path),
        )
        assert "BINANCE" in proc.pid_file.name
        assert "bn-1" in proc.pid_file.name

    def test_write_and_read_pid(self, tmp_path):
        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-pid-test"},
            pid_dir=str(tmp_path),
        )
        proc._write_pid()
        assert proc.pid_file.exists()
        pid = GatewayProcess.read_pid(proc.pid_file)
        assert pid == os.getpid()

    def test_write_pid_logs_failure(self, tmp_path, monkeypatch, caplog):
        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-pid-write-fail"},
            pid_dir=str(tmp_path),
        )

        def fail_write_text(self, data, encoding=None):
            raise OSError("write failed")

        monkeypatch.setattr(type(proc.pid_file), "write_text", fail_write_text)

        with pytest.raises(OSError, match="write failed"), caplog.at_level("ERROR"):
            proc._write_pid()

        assert "Failed to write PID file" in caplog.text
        assert "write failed" in caplog.text

    def test_remove_pid(self, tmp_path):
        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-rm"},
            pid_dir=str(tmp_path),
        )
        proc._write_pid()
        assert proc.pid_file.exists()
        proc._remove_pid()
        assert not proc.pid_file.exists()

    def test_remove_pid_logs_warning_when_unlink_fails(self, tmp_path, monkeypatch, caplog):
        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-rm-fail"},
            pid_dir=str(tmp_path),
        )
        proc._write_pid()

        def fail_unlink(self, missing_ok=False):
            raise OSError("unlink failed")

        monkeypatch.setattr(type(proc.pid_file), "unlink", fail_unlink)

        with caplog.at_level("WARNING"):
            proc._remove_pid()

        assert proc.pid_file.exists() is True
        assert "Failed to remove PID file" in caplog.text
        assert "unlink failed" in caplog.text

    def test_read_pid_missing(self, tmp_path):
        assert GatewayProcess.read_pid(tmp_path / "nonexistent.pid") is None

    def test_read_pid_invalid(self, tmp_path):
        bad = tmp_path / "bad.pid"
        bad.write_text("not-a-number", encoding="utf-8")
        assert GatewayProcess.read_pid(bad) is None

    def test_is_running_current_process(self):
        assert GatewayProcess.is_running(os.getpid()) is True

    def test_is_running_dead_pid(self):
        assert GatewayProcess.is_running(999999999) is False

    def test_status(self, tmp_path):
        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-status"},
            pid_dir=str(tmp_path),
        )
        proc._write_pid()
        st = proc.status()
        assert st["pid"] == os.getpid()
        assert st["running"] is True

    def test_status_no_pid(self, tmp_path):
        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-none"},
            pid_dir=str(tmp_path),
        )
        st = proc.status()
        assert st["pid"] is None
        assert st["running"] is False

    def test_status_cleans_invalid_pid_file(self, tmp_path, caplog):
        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-invalid-status"},
            pid_dir=str(tmp_path),
        )
        proc.pid_file.parent.mkdir(parents=True, exist_ok=True)
        proc.pid_file.write_text("not-a-pid", encoding="utf-8")

        with caplog.at_level("WARNING"):
            st = proc.status()

        assert st["pid"] is None
        assert st["running"] is False
        assert proc.pid_file.exists() is False
        assert "is invalid during status check" in caplog.text

    def test_status_cleans_stale_pid_file(self, tmp_path, monkeypatch, caplog):
        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-stale-status"},
            pid_dir=str(tmp_path),
        )
        proc._write_pid()
        monkeypatch.setattr(proc, "is_running", lambda pid: False)

        with caplog.at_level("WARNING"):
            st = proc.status()

        assert st["pid"] == os.getpid()
        assert st["running"] is False
        assert proc.pid_file.exists() is False
        assert "not running during status check" in caplog.text

    def test_shutdown_removes_pid_even_if_runtime_stop_fails(self, tmp_path):
        class BadRuntime:
            def stop(self) -> None:
                raise RuntimeError("stop failed")

        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-bad-stop"},
            pid_dir=str(tmp_path),
        )
        proc._runtime = BadRuntime()
        proc._write_pid()

        proc._shutdown()

        assert proc.pid_file.exists() is False
        assert proc._stopped is True
        assert proc._runtime is None

    def test_start_logs_keyboard_interrupt_and_shuts_down(self, tmp_path, monkeypatch, caplog):
        class FakeRuntime:
            def __init__(self, *_args, **_kwargs) -> None:
                return None

            def start(self) -> None:
                raise KeyboardInterrupt()

            def stop(self) -> None:
                return None

        monkeypatch.setattr("bt_api_py.gateway.config.GatewayConfig.from_kwargs", lambda **kwargs: object())
        monkeypatch.setattr("bt_api_py.gateway.runtime.GatewayRuntime", FakeRuntime)

        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-keyboard-interrupt"},
            pid_dir=str(tmp_path),
        )
        monkeypatch.setattr(proc, "_install_signal_handlers", lambda: None)

        with caplog.at_level("INFO"):
            proc.start()

        assert proc._stopped is True
        assert proc.pid_file.exists() is False
        assert "Gateway process interrupted by KeyboardInterrupt" in caplog.text

    def test_start_cleans_runtime_when_pid_write_fails(self, tmp_path, monkeypatch):
        class FakeRuntime:
            def __init__(self, *_args, **_kwargs) -> None:
                return None

            def start(self) -> None:
                return None

            def stop(self) -> None:
                return None

        monkeypatch.setattr("bt_api_py.gateway.config.GatewayConfig.from_kwargs", lambda **kwargs: object())
        monkeypatch.setattr("bt_api_py.gateway.runtime.GatewayRuntime", FakeRuntime)

        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-start-pid-fail"},
            pid_dir=str(tmp_path),
        )
        monkeypatch.setattr(proc, "_write_pid", lambda: (_ for _ in ()).throw(OSError("pid write failed")))

        with pytest.raises(OSError, match="pid write failed"):
            proc.start()

        assert proc._runtime is None
        assert proc.pid_file.exists() is False

    def test_start_cleans_pid_and_runtime_when_signal_setup_fails(self, tmp_path, monkeypatch):
        class FakeRuntime:
            def __init__(self, *_args, **_kwargs) -> None:
                return None

            def start(self) -> None:
                return None

            def stop(self) -> None:
                return None

        monkeypatch.setattr("bt_api_py.gateway.config.GatewayConfig.from_kwargs", lambda **kwargs: object())
        monkeypatch.setattr("bt_api_py.gateway.runtime.GatewayRuntime", FakeRuntime)

        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-start-signal-fail"},
            pid_dir=str(tmp_path),
        )
        monkeypatch.setattr(
            proc,
            "_install_signal_handlers",
            lambda: (_ for _ in ()).throw(RuntimeError("signal install failed")),
        )

        with pytest.raises(RuntimeError, match="signal install failed"):
            proc.start()

        assert proc._runtime is None
        assert proc.pid_file.exists() is False

    def test_start_logs_runtime_failure_and_shuts_down(self, tmp_path, monkeypatch, caplog):
        class FakeRuntime:
            def __init__(self, *_args, **_kwargs) -> None:
                return None

            def start(self) -> None:
                raise RuntimeError("start failed")

            def stop(self) -> None:
                return None

        monkeypatch.setattr("bt_api_py.gateway.config.GatewayConfig.from_kwargs", lambda **kwargs: object())
        monkeypatch.setattr("bt_api_py.gateway.runtime.GatewayRuntime", FakeRuntime)

        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-start-fail"},
            pid_dir=str(tmp_path),
        )
        monkeypatch.setattr(proc, "_install_signal_handlers", lambda: None)

        with pytest.raises(RuntimeError, match="start failed"), caplog.at_level("ERROR"):
            proc.start()

        assert proc._stopped is True
        assert proc.pid_file.exists() is False
        assert "Gateway process start failed" in caplog.text
        assert "start failed" in caplog.text

    def test_stop_remote_cleans_stale_pid_when_process_disappears(self, tmp_path, monkeypatch, caplog):
        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-stop-remote-race"},
            pid_dir=str(tmp_path),
        )
        proc._write_pid()

        monkeypatch.setattr(proc, "is_running", lambda pid: True)
        monkeypatch.setattr(
            "os.kill",
            lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError("no such process")),
        )

        with caplog.at_level("WARNING"):
            result = proc.stop_remote()

        assert result is False
        assert proc.pid_file.exists() is False
        assert "disappeared before signal delivery" in caplog.text

    def test_stop_remote_cleans_invalid_pid_file(self, tmp_path, caplog):
        proc = GatewayProcess(
            {"gateway_runtime_name": "gw-stop-invalid-pid"},
            pid_dir=str(tmp_path),
        )
        proc.pid_file.parent.mkdir(parents=True, exist_ok=True)
        proc.pid_file.write_text("not-a-pid", encoding="utf-8")

        with caplog.at_level("WARNING"):
            result = proc.stop_remote()

        assert result is False
        assert proc.pid_file.exists() is False
        assert "is invalid during stop request" in caplog.text
