"""Foreground launcher for code-registered Gateway operator profiles."""

from __future__ import annotations

import argparse
import json
import math
import signal
import threading
from collections.abc import Mapping
from pathlib import Path

from bt_api_py.gateway_operator_application import (
    GatewayOperatorApplication,
    GatewayOperatorApplicationError,
    GatewayOperatorProfileRegistry,
    load_gateway_operator_launch_config,
)


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be finite and in (0, 300]") from exc
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
        raise argparse.ArgumentTypeError("timeout must be finite and in (0, 300]")
    return timeout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--startup-timeout", type=_positive_timeout, default=30.0)
    parser.add_argument("--operation-timeout", type=_positive_timeout, default=30.0)
    args = parser.parse_args(argv)
    stop_requested = threading.Event()
    control_requested = threading.Event()
    previous_handlers: dict[int, object] = {}

    def request_stop(_signum: int, _frame: object) -> None:
        stop_requested.set()

    def request_control(_signum: int, _frame: object) -> None:
        control_requested.set()

    for sig in dict.fromkeys((getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None))):
        if isinstance(sig, int):
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, request_stop)
    recovery_signal = getattr(signal, "SIGBREAK", None) or getattr(signal, "SIGUSR1", None)
    if isinstance(recovery_signal, int):
        previous_handlers[recovery_signal] = signal.getsignal(recovery_signal)
        signal.signal(recovery_signal, request_control)

    exit_code = 0
    try:
        try:
            config = load_gateway_operator_launch_config(args.config)
            profiles = GatewayOperatorProfileRegistry.from_entry_points()
            application = GatewayOperatorApplication.from_launch_config(config, profiles)
        except GatewayOperatorApplicationError as exc:
            print(json.dumps({"state": "failed", "error_code": exc.code}, sort_keys=True))
            return 2

        try:
            application.start(timeout_seconds=args.startup_timeout)
        except GatewayOperatorApplicationError as exc:
            # start can time out while its non-daemon owner is still inside
            # Host/provider startup. Keep the application and its signal
            # controls alive until the same owner confirms close or cleanup.
            print(
                json.dumps(
                    {"error_code": exc.code, "status": _plain(application.status)},
                    sort_keys=True,
                )
            )
            exit_code = 1

        last_reported: object = None
        while True:
            status = application.status
            report = _plain(status)
            if report != last_reported:
                print(json.dumps(report, sort_keys=True))
                last_reported = report
            state = status.get("state")
            cleanup = status.get("cleanup")
            pending_cleanup = isinstance(cleanup, Mapping) and (
                cleanup.get("unclaimed_provider_count", 0) > 0
                or cleanup.get("last_cleanup_code") is not None
            )
            if state == "closed":
                break
            if state == "failed" and not pending_cleanup:
                exit_code = 1
                break
            if stop_requested.wait(0.1):
                stop_requested.clear()
                try:
                    application.stop(timeout_seconds=args.operation_timeout)
                except GatewayOperatorApplicationError as exc:
                    print(
                        json.dumps(
                            {"error_code": exc.code, "status": _plain(application.status)},
                            sort_keys=True,
                        )
                    )
                    exit_code = 1
                continue
            if control_requested.is_set():
                control_requested.clear()
                try:
                    if pending_cleanup or state == "failed":
                        application.retry_unclaimed_provider_cleanup(
                            timeout_seconds=args.operation_timeout
                        )
                    elif state == "uncertain":
                        application.stop(timeout_seconds=args.operation_timeout)
                    elif state == "running" and status.get("role") == "account_session_worker":
                        application.recover_account_session(timeout_seconds=args.operation_timeout)
                    else:
                        continue
                except GatewayOperatorApplicationError as exc:
                    print(
                        json.dumps(
                            {"error_code": exc.code, "status": _plain(application.status)},
                            sort_keys=True,
                        )
                    )
                    exit_code = 1
        if exit_code == 0 and application.status.get("state") != "closed":
            exit_code = 1
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    return exit_code


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
