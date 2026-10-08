"""Windows process-group regression for cooperative collector shutdown."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(sys.platform != "win32", reason="CTRL_BREAK_EVENT is Windows-only")
def test_ctrl_break_cancels_pending_query_and_restores_handler(tmp_path: Path) -> None:
    """A console break should stop a pending offline query and exit cleanly."""
    ready_file = tmp_path / "query-ready"
    child_code = textwrap.dedent(
        """
        import signal
        import sys
        import threading
        import time
        from pathlib import Path
        from types import SimpleNamespace

        from bt_api_ctp.collector import cli


        class _Calendar:
            def is_trading_day(self, day):
                return True


        class _PendingNativeInstrumentQuery:
            def wait(self, *, timeout, should_stop):
                Path(sys.argv[1]).write_text("waiting", encoding="utf-8")
                pending_callback = threading.Event()
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if should_stop():
                        return SimpleNamespace(
                            complete=False, error_message="query_cancelled"
                        )
                    pending_callback.wait(0.1)
                raise RuntimeError("test timed out waiting for CTRL_BREAK_EVENT")


        class _FakeEngine:
            def __init__(self, stop_event):
                self.stop_event = stop_event

            def run_once(self, *, duration_sec, stop_event):
                assert stop_event is self.stop_event
                result = _PendingNativeInstrumentQuery().wait(
                    timeout=15.0, should_stop=stop_event.is_set
                )
                assert result.error_message == "query_cancelled"
                print("QUERY_CANCELLED", flush=True)
                return None


        def _build_fake_engine(config, *, env=None, stop_event=None):
            # Match production: native wrapper imports happen after handlers
            # are installed, but no front connection is started here.
            from bt_api_ctp.ctp.client import TraderClient

            assert stop_event is not None
            return _FakeEngine(stop_event)


        cli.load_config = lambda _path: {"data_root": "."}
        cli.validate_config = lambda _config: []
        cli._calendar_from_config = lambda _config: _Calendar()
        cli._configure_logging = lambda **_kwargs: None
        cli._resolve_log_file = lambda *_args, **_kwargs: None
        cli.build_engine = _build_fake_engine
        cli._print_report = lambda _report: print("CLI_FINISHED", flush=True)

        original_break_handler = signal.getsignal(signal.SIGBREAK)
        result = cli.main(
            ["--config", "offline.yaml", "--date", "20260921", "--duration", "30"]
        )
        restored = signal.getsignal(signal.SIGBREAK) is original_break_handler
        print(f"CLI_RESULT={result}", flush=True)
        print(f"SIGBREAK_RESTORED={restored}", flush=True)
        raise SystemExit(result)
        """
    )

    child_env = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if name in os.environ
    }
    child_env["PYTHONPATH"] = str(ROOT / "bt_api" / "bt_api_ctp" / "src")
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(ready_file)],
        cwd=ROOT,
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    try:
        ready_deadline = time.monotonic() + 10.0
        while not ready_file.exists():
            if process.poll() is not None:
                output, _ = process.communicate()
                pytest.fail(
                    "child exited before entering the pending query "
                    f"(exit={process.returncode}):\n{output}"
                )
            if time.monotonic() >= ready_deadline:
                pytest.fail("child did not enter the pending query within 10 seconds")
            time.sleep(0.02)

        stop_started = time.monotonic()
        process.send_signal(signal.CTRL_BREAK_EVENT)
        try:
            output, _ = process.communicate(timeout=8.0)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
            pytest.fail(f"child did not stop after CTRL_BREAK_EVENT:\n{output}")

        assert process.returncode == 0, output
        assert "QUERY_CANCELLED" in output
        assert "CLI_FINISHED" in output
        assert "CLI_RESULT=0" in output
        assert "SIGBREAK_RESTORED=True" in output
        assert time.monotonic() - stop_started < 5.0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
