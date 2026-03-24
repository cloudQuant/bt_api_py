"""Gateway process management — CLI start/stop/status with PID file.

Provides a thin entry-point layer that:
1. Reads a YAML/dict config
2. Writes a PID file on start
3. Handles SIGTERM/SIGINT for graceful shutdown
4. Exposes ``start / stop / status`` helpers

Usage (programmatic)::

    from bt_api_py.gateway.process import GatewayProcess
    proc = GatewayProcess(config_dict)
    proc.start()   # blocks until SIGTERM / SIGINT

Usage (CLI)::

    python -m bt_api_py.gateway.process start --config gateway.yaml
    python -m bt_api_py.gateway.process stop  --pid-file /tmp/btgw/gw.pid
    python -m bt_api_py.gateway.process status --pid-file /tmp/btgw/gw.pid
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

if sys.platform.startswith("win"):
    import ctypes
    from ctypes import wintypes

logger = logging.getLogger(__name__)

if sys.platform.startswith("win"):
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259


class GatewayProcess:
    """Lifecycle wrapper around GatewayRuntime with PID-file management.

    Args:
        config: A dict-like gateway configuration (same kwargs accepted
                by ``GatewayConfig.from_kwargs``).
        pid_dir: Directory for the PID file.  Defaults to the gateway
                 base dir from config.
    """

    def __init__(self, config: dict[str, Any], *, pid_dir: str | None = None) -> None:
        self._config = dict(config)
        self._pid_dir = Path(pid_dir or config.get("gateway_base_dir", "/tmp/bt_gateway"))
        self._runtime = None
        self._stopped = False

    # ------------------------------------------------------------------
    # PID helpers
    # ------------------------------------------------------------------

    @property
    def pid_file(self) -> Path:
        name = self._config.get(
            "gateway_runtime_name",
            f"gw-{self._config.get('exchange_type', 'unknown')}"
            f"-{self._config.get('account_id', 'default')}",
        )
        return self._pid_dir / f"{name}.pid"

    def _write_pid(self) -> None:
        try:
            self._pid_dir.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        except OSError as exc:
            logger.exception("Failed to write PID file %s: %s", self.pid_file, exc)
            raise
        logger.info("PID %d written to %s", os.getpid(), self.pid_file)

    def _remove_pid(self) -> None:
        try:
            self.pid_file.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove PID file %s: %s", self.pid_file, exc)

    @staticmethod
    def read_pid(pid_file: str | Path) -> int | None:
        """Read a PID from *pid_file*, returning ``None`` if absent or invalid."""
        p = Path(pid_file)
        if not p.exists():
            return None
        try:
            return int(p.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return None

    @staticmethod
    def is_running(pid: int) -> bool:
        """Check whether a process with *pid* is alive."""
        if sys.platform.startswith("win"):
            if pid <= 0:
                return False
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            exit_code = wintypes.DWORD()
            try:
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == _STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the gateway runtime (blocking).

        Installs signal handlers for graceful shutdown and blocks until
        the runtime is stopped.
        """
        from bt_api_py.gateway.config import GatewayConfig
        from bt_api_py.gateway.runtime import GatewayRuntime

        cfg = GatewayConfig.from_kwargs(**self._config)
        self._runtime = GatewayRuntime(cfg, **self._config)

        try:
            self._write_pid()
            self._install_signal_handlers()
        except Exception:
            self._runtime = None
            self._remove_pid()
            raise

        logger.info("Gateway process starting …")
        try:
            self._runtime.start()
        except KeyboardInterrupt:
            logger.info("Gateway process interrupted by KeyboardInterrupt")
        except Exception as exc:
            logger.exception("Gateway process start failed: %s", exc)
            raise
        finally:
            self._shutdown()

    def stop_remote(self) -> bool:
        """Send SIGTERM to the process recorded in the PID file.

        Returns:
            ``True`` if the signal was sent successfully.
        """
        pid = self.read_pid(self.pid_file)
        if pid is None:
            if self.pid_file.exists():
                logger.warning(
                    "PID file %s is invalid during stop request; cleaning it up",
                    self.pid_file,
                )
                self._remove_pid()
                return False
            logger.warning("No PID file found at %s", self.pid_file)
            return False
        if not self.is_running(pid):
            logger.warning("Process %d is not running; cleaning up PID file", pid)
            self._remove_pid()
            return False
        try:
            if sys.platform.startswith("win"):
                os.kill(pid, signal.CTRL_BREAK_EVENT)
                logger.info("Sent CTRL_BREAK_EVENT to PID %d", pid)
            else:
                os.kill(pid, signal.SIGTERM)
                logger.info("Sent SIGTERM to PID %d", pid)
            return True
        except ProcessLookupError as exc:
            logger.warning(
                "Process %d disappeared before signal delivery; cleaning up PID file: %s",
                pid,
                exc,
            )
            self._remove_pid()
            return False
        except OSError as exc:
            logger.error("Failed to send termination signal to %d: %s", pid, exc)
            return False

    def status(self) -> dict[str, Any]:
        """Return a status dict for the gateway process."""
        pid = self.read_pid(self.pid_file)
        if pid is None and self.pid_file.exists():
            logger.warning(
                "PID file %s is invalid during status check; cleaning it up", self.pid_file
            )
            self._remove_pid()
        running = pid is not None and self.is_running(pid)
        if pid is not None and not running:
            logger.warning(
                "Process %d is not running during status check; cleaning up PID file", pid
            )
            self._remove_pid()
        return {
            "pid_file": str(self.pid_file),
            "pid": pid,
            "running": running,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _shutdown(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        logger.info("Gateway process shutting down …")
        try:
            if self._runtime is not None:
                self._runtime.stop()
        except Exception as exc:
            logger.exception("Gateway process stop failed: %s", exc)
        finally:
            self._runtime = None
            self._remove_pid()
        logger.info("Gateway process stopped.")

    def _install_signal_handlers(self) -> None:
        def _handler(signum, frame):
            logger.info("Received signal %s, initiating shutdown", signum)
            self._shutdown()

        signal.signal(signal.SIGINT, _handler)
        if sys.platform.startswith("win"):
            signal.signal(signal.SIGBREAK, _handler)
        else:
            signal.signal(signal.SIGTERM, _handler)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _cli_main() -> None:  # pragma: no cover
    """Minimal CLI: ``python -m bt_api_py.gateway.process <start|stop|status>``."""
    import argparse

    parser = argparse.ArgumentParser(description="Gateway process manager")
    sub = parser.add_subparsers(dest="command")

    start_p = sub.add_parser("start")
    start_p.add_argument("--config", required=True, help="JSON or YAML config file")

    stop_p = sub.add_parser("stop")
    stop_p.add_argument("--pid-file", required=True)

    status_p = sub.add_parser("status")
    status_p.add_argument("--pid-file", required=True)

    args = parser.parse_args()

    if args.command == "start":
        config_path = Path(args.config)
        if config_path.suffix in (".yaml", ".yml"):
            try:
                import yaml

                config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            except ImportError:
                sys.exit("PyYAML required for YAML config files")
        else:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        GatewayProcess(config).start()

    elif args.command == "stop":
        pid = GatewayProcess.read_pid(args.pid_file)
        if pid and GatewayProcess.is_running(pid):
            if sys.platform.startswith("win"):
                os.kill(pid, signal.CTRL_BREAK_EVENT)
                print(f"Sent CTRL_BREAK_EVENT to {pid}")
            else:
                os.kill(pid, signal.SIGTERM)
                print(f"Sent SIGTERM to {pid}")
        else:
            print("Process not running")

    elif args.command == "status":
        pid = GatewayProcess.read_pid(args.pid_file)
        running = pid is not None and GatewayProcess.is_running(pid)
        print(json.dumps({"pid": pid, "running": running}, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    _cli_main()
