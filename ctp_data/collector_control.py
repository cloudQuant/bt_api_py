#!/usr/bin/env python3
"""Start and manage the CTP collector supervisor on Windows.

The detached service is identified by both its PID and process creation time.
Stopping is cooperative: the controller writes the service's unique stop marker
and waits for the process to exit. It never terminates a process by PID.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXIT_RUNNING = 0
EXIT_STOPPED = 1
EXIT_BAD_STATE = 2
EXIT_STOP_TIMEOUT = 1

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
STATE_NAME = "state.json"
LOCK_NAME = "control.lock"
SERVICE_LOG = SCRIPT_DIR / "service.log"
CONSOLE_LOG = SCRIPT_DIR / "service-console.log"


class ControlError(RuntimeError):
    """A safe, user-facing collector-control error."""


class LockBusyError(ControlError):
    """Another controller command currently owns the state reservation."""


class ProcessIdentityError(ControlError):
    """The process exists but its creation identity could not be verified."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _process_creation_identity(pid: int) -> str | None:
    """Return a stable creation identity, or None when the PID no longer exists.

    Windows uses GetProcessTimes, so a later process reusing the same PID cannot
    be mistaken for the original service. Linux uses /proc's process start tick
    for offline lifecycle tests and local development.
    """
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        get_times = kernel32.GetProcessTimes
        get_times.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        )
        get_times.restype = wintypes.BOOL
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_exit_code.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        process = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: no process has this PID
                return None
            raise ProcessIdentityError(
                f"cannot verify process {pid} creation time (Windows error {error})"
            )
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code(process, ctypes.byref(exit_code)):
                error = ctypes.get_last_error()
                raise ProcessIdentityError(
                    f"cannot verify process {pid} state (Windows error {error})"
                )
            if exit_code.value != 259:  # STILL_ACTIVE
                return None
            created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
            if not get_times(
                process,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                error = ctypes.get_last_error()
                raise ProcessIdentityError(
                    f"cannot verify process {pid} creation time (Windows error {error})"
                )
            ticks = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
            return f"win-filetime:{ticks}"
        finally:
            close_handle(process)

    proc_stat = Path("/proc") / str(pid) / "stat"
    if proc_stat.exists():
        try:
            # comm is parenthesized and can contain spaces; fields after the
            # final ')' start at field 3, making starttime (field 22) index 19.
            fields = proc_stat.read_text(encoding="ascii").rsplit(")", 1)[1].split()
            return f"proc-start:{fields[19]}"
        except (OSError, IndexError) as exc:
            raise ProcessIdentityError(f"cannot verify process {pid} creation time") from exc

    if sys.platform.startswith("linux"):
        return None
    # Fail closed on platforms where this module has no race-safe identity API.
    raise ProcessIdentityError("process creation identity checks are unsupported on this platform")


def _process_matches(pid: int, identity: str) -> bool:
    current = _process_creation_identity(pid)
    return current is not None and current == identity


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlError(f"cannot read control state at {path}") from exc
    if not isinstance(payload, dict):
        raise ControlError(f"control state at {path} is not a JSON object")
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink()


def _valid_token(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 32:
        return False
    try:
        return uuid.UUID(hex=value).hex == value
    except ValueError:
        return False


def _state_paths(runtime_dir: Path, token: str) -> tuple[Path, Path]:
    return runtime_dir / f"{token}.stop", runtime_dir / f"{token}.ready"


def _validate_state(payload: dict[str, Any], runtime_dir: Path) -> dict[str, Any]:
    required = ("pid", "process_creation_identity", "token", "stopfile", "readyfile")
    if any(key not in payload for key in required):
        raise ControlError("control state is missing required fields")
    try:
        pid = int(payload["pid"])
    except (TypeError, ValueError) as exc:
        raise ControlError("control state contains an invalid PID") from exc
    identity = payload["process_creation_identity"]
    token = payload["token"]
    if pid <= 0 or not isinstance(identity, str) or not identity or not _valid_token(token):
        raise ControlError("control state contains an invalid process identity or token")
    expected_stop, expected_ready = _state_paths(runtime_dir.resolve(), token)
    try:
        stopfile = Path(str(payload["stopfile"])).resolve()
        readyfile = Path(str(payload["readyfile"])).resolve()
    except (OSError, ValueError) as exc:
        raise ControlError("control state contains invalid marker paths") from exc
    if stopfile != expected_stop.resolve() or readyfile != expected_ready.resolve():
        raise ControlError("control state marker paths do not belong to this runtime directory")
    payload = dict(payload)
    payload["pid"] = pid
    payload["stopfile"] = str(stopfile)
    payload["readyfile"] = str(readyfile)
    return payload


def _state_is_live(state: dict[str, Any]) -> bool:
    return _process_matches(state["pid"], state["process_creation_identity"])


def _remove_owned_state(runtime_dir: Path, state: dict[str, Any]) -> None:
    """Remove marker/state files only when they still carry our service token."""
    token = state.get("token")
    if not _valid_token(token):
        return
    state_path = runtime_dir / STATE_NAME
    with contextlib.suppress(OSError, ControlError):
        current = _read_json(state_path)
        if current and current.get("token") == token:
            state_path.unlink()
    stopfile, readyfile = _state_paths(runtime_dir.resolve(), token)
    with contextlib.suppress(OSError, ControlError):
        marker = _read_json(stopfile)
        if marker is None or marker.get("token") == token:
            stopfile.unlink()
    with contextlib.suppress(OSError, ControlError):
        receipt = _read_json(readyfile)
        if receipt is None or receipt.get("token") in (None, token):
            readyfile.unlink()


@contextlib.contextmanager
def _state_lock(runtime_dir: Path) -> Iterator[None]:
    """Reserve state changes with an atomic exclusive-create lock file."""
    runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_path = runtime_dir / LOCK_NAME
    owner = {
        "pid": os.getpid(),
        "process_creation_identity": _process_creation_identity(os.getpid()),
        "token": uuid.uuid4().hex,
    }
    try:
        stream = lock_path.open("x", encoding="utf-8")
    except FileExistsError:
        # A crashed controller may leave a lock behind. Recover it only when
        # its owner is demonstrably gone or the PID has been reused.
        old = _read_json(lock_path)
        if (
            not old
            or not isinstance(old.get("pid"), int)
            or not isinstance(old.get("process_creation_identity"), str)
        ):
            raise LockBusyError("another collector-control operation is in progress") from None
        try:
            old_is_live = _process_matches(old["pid"], old["process_creation_identity"])
        except ProcessIdentityError as exc:
            raise LockBusyError("cannot safely verify the active collector-control lock") from exc
        if old_is_live:
            raise LockBusyError("another collector-control operation is in progress") from None
        # Compare before removing so a lock replaced since the read is kept.
        if _read_json(lock_path) != old:
            raise LockBusyError("another collector-control operation is in progress") from None
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()
        try:
            stream = lock_path.open("x", encoding="utf-8")
        except FileExistsError as exc:
            raise LockBusyError("another collector-control operation is in progress") from exc

    try:
        with stream:
            json.dump(owner, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        with contextlib.suppress(OSError):
            current = _read_json(lock_path)
            if current and current.get("token") == owner["token"]:
                lock_path.unlink()


def prepare_runtime() -> None:
    """Prefer repository source and load the repo .env without overriding env."""
    source = REPO_ROOT / "bt_api" / "bt_api_ctp" / "src"
    if (source / "bt_api_ctp").is_dir():
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
        current = os.environ.get("PYTHONPATH", "")
        pieces = [piece for piece in current.split(os.pathsep) if piece]
        if str(source) not in pieces:
            os.environ["PYTHONPATH"] = os.pathsep.join([str(source), *pieces])
    for name, default in (
        ("OMP_NUM_THREADS", "4"),
        ("NUMEXPR_MAX_THREADS", "4"),
        ("NUMEXPR_NUM_THREADS", "4"),
    ):
        os.environ.setdefault(name, default)
    dotenv_path = REPO_ROOT / ".env"
    if dotenv_path.is_file():
        try:
            from dotenv import load_dotenv
        except ImportError as exc:  # pragma: no cover - declared project dependency
            raise ControlError("python-dotenv is required to load the repository .env") from exc
        load_dotenv(dotenv_path=dotenv_path, override=False, verbose=False)


def _preflight(config_path: Path) -> None:
    """Import and validate the configured collector, including its native CTP module."""
    prepare_runtime()
    if not config_path.is_file():
        raise ControlError(f"collector config not found: {config_path}")
    try:
        from bt_api_ctp.collector.cli import load_config, validate_config
        from bt_api_ctp.ctp import _ctp_base

        config = load_config(config_path)
        errors = validate_config(config)
        if errors:
            raise ControlError("invalid collector config: " + "; ".join(errors))
        ctp = config.get("ctp") or {}
        required = (
            ("md_front", "CTP_MD_FRONT"),
            ("td_front", "CTP_TD_FRONT"),
            ("broker_id", "CTP_BROKER_ID"),
            ("user_id", "CTP_USER_ID"),
            ("password", "CTP_PASSWORD"),
        )
        missing = [
            env_name
            for config_name, env_name in required
            if not str(ctp.get(config_name) or os.environ.get(env_name) or "").strip()
        ]
        if missing:
            raise ControlError(
                "required CTP settings are missing from config or environment: "
                + ", ".join(missing)
            )
        if not _ctp_base.is_ctp_native_loaded():
            raise ControlError("the bundled CTP native extension did not load for this Python")
    except ControlError:
        raise
    except Exception as exc:
        # Exception text from YAML parsers or native loaders can include source
        # excerpts. Keep startup output from exposing config or env contents.
        raise ControlError(f"collector preflight failed ({type(exc).__name__})") from exc


def _service_args_from_environment() -> list[str]:
    """Parse COLLECTOR_SERVICE_ARGS without passing it through a shell."""
    raw = os.environ.get("COLLECTOR_SERVICE_ARGS", "")
    if not raw.strip():
        return []
    if os.name != "nt":
        try:
            return shlex.split(raw)
        except ValueError as exc:
            raise ControlError("COLLECTOR_SERVICE_ARGS contains unmatched quoting") from exc

    parser = ctypes.windll.shell32.CommandLineToArgvW
    parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int()
    arguments = parser("collector " + raw, ctypes.byref(count))
    if not arguments:
        raise ControlError("COLLECTOR_SERVICE_ARGS could not be parsed")
    try:
        return [arguments[index] for index in range(1, count.value)]
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(ctypes.cast(arguments, ctypes.c_void_p))


def _effective_config_path(config_path: Path, service_args: list[str]) -> Path:
    """Honor a service-level --config in the same order argparse will see it."""
    effective = config_path
    index = 0
    while index < len(service_args):
        argument = service_args[index]
        if argument == "--config":
            if index + 1 >= len(service_args) or service_args[index + 1].startswith("-"):
                raise ControlError("service --config requires a path")
            effective = Path(service_args[index + 1]).expanduser()
            index += 2
            continue
        if argument.startswith("--config="):
            value = argument.partition("=")[2]
            if not value:
                raise ControlError("service --config requires a path")
            effective = Path(value).expanduser()
        if argument in ("--stop-file", "--ready-file", "--log-file") or argument.startswith(
            ("--stop-file=", "--ready-file=", "--log-file=")
        ):
            raise ControlError(f"{argument.split('=', 1)[0]} is managed by collector_control")
        index += 1
    return effective.resolve()


def _without_config_arguments(service_args: list[str]) -> list[str]:
    """Remove env config overrides when the controller CLI supplied --config."""
    filtered: list[str] = []
    index = 0
    while index < len(service_args):
        argument = service_args[index]
        if argument == "--config":
            index += 2
            continue
        if argument.startswith("--config="):
            index += 1
            continue
        filtered.append(argument)
        index += 1
    return filtered


def _build_service_command(
    *, config: Path, stopfile: Path, readyfile: Path, log_file: Path, extra_args: list[str]
) -> list[str]:
    return [
        sys.executable,
        "-u",
        str(SCRIPT_DIR / "service.py"),
        "--config",
        str(config),
        "--stop-file",
        str(stopfile),
        "--ready-file",
        str(readyfile),
        "--log-file",
        str(log_file),
        *extra_args,
    ]


def _popen_hidden(command: list[str], *, cwd: Path, console_log: Path) -> subprocess.Popen:
    console_log.parent.mkdir(parents=True, exist_ok=True)
    with console_log.open("ab") as output:
        kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "stdin": subprocess.DEVNULL,
            "stdout": output,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(command, **kwargs)  # noqa: S603 - fixed interpreter/script and validated args


def _read_ready_receipt(path: Path, *, pid: int, config: Path) -> dict[str, Any] | None:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(receipt, dict):
        return None
    try:
        receipt_pid = int(receipt.get("pid"))
    except (TypeError, ValueError):
        return None
    receipt_config = receipt.get("config")
    if receipt_pid != pid or not isinstance(receipt_config, str):
        raise ControlError("service startup receipt does not match its PID")
    if Path(receipt_config).resolve() != config.resolve():
        raise ControlError("service startup receipt does not match the requested config")
    if not isinstance(receipt.get("started_at"), str) or not receipt["started_at"]:
        return None
    return receipt


def _load_current_state(runtime_dir: Path) -> dict[str, Any] | None:
    raw = _read_json(runtime_dir / STATE_NAME)
    return _validate_state(raw, runtime_dir) if raw is not None else None


def _write_state(runtime_dir: Path, state: dict[str, Any]) -> None:
    _atomic_write_json(runtime_dir / STATE_NAME, state)


def _clear_if_owned(runtime_dir: Path, state: dict[str, Any]) -> None:
    current = _load_current_state(runtime_dir)
    if current is not None and current.get("token") == state.get("token"):
        _remove_owned_state(runtime_dir, current)


def _wait_for_exit(state: dict[str, Any], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if not _state_is_live(state):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.2, max(0.01, deadline - time.monotonic())))


def _start(
    *,
    config_path: Path,
    runtime_dir: Path,
    startup_timeout: float,
    stop_timeout: float,
    service_args: list[str],
) -> int:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = runtime_dir.resolve()
    with _state_lock(runtime_dir):
        existing = _load_current_state(runtime_dir)
        if existing is not None:
            try:
                if _state_is_live(existing):
                    print(f"collector service already running (PID {existing['pid']})")
                    return EXIT_RUNNING
            except ProcessIdentityError as exc:
                raise ControlError(f"cannot safely inspect managed service: {exc}") from exc
            _remove_owned_state(runtime_dir, existing)

        _preflight(config_path)
        token = uuid.uuid4().hex
        stopfile, readyfile = _state_paths(runtime_dir, token)
        config_path = config_path.resolve()
        log_file = SERVICE_LOG.resolve()
        command = _build_service_command(
            config=config_path,
            stopfile=stopfile,
            readyfile=readyfile,
            log_file=log_file,
            extra_args=service_args,
        )
        try:
            process = _popen_hidden(command, cwd=REPO_ROOT, console_log=CONSOLE_LOG)
        except OSError as exc:
            raise ControlError(
                f"could not launch collector service ({type(exc).__name__})"
            ) from exc

        try:
            identity = _process_creation_identity(process.pid)
        except ProcessIdentityError as exc:
            # The service is already launched. Preserve a record only if it
            # can be safely identified; never kill it by PID as recovery.
            raise ControlError(
                f"service started as PID {process.pid}, but its creation identity cannot be verified; "
                f"inspect {CONSOLE_LOG}"
            ) from exc
        if identity is None:
            raise ControlError(
                "collector service exited before its process identity could be recorded"
            )

        state: dict[str, Any] = {
            "pid": process.pid,
            "process_creation_identity": identity,
            "token": token,
            "stopfile": str(stopfile),
            "readyfile": str(readyfile),
            "config": str(config_path),
            "service_log": str(log_file),
            "console_log": str(CONSOLE_LOG.resolve()),
            "started_at": _utc_now(),
            "startup_receipt": None,
            "stop_requested": False,
        }
        _write_state(runtime_dir, state)

        deadline = time.monotonic() + startup_timeout
        receipt: dict[str, Any] | None = None
        receipt_error: ControlError | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                receipt = _read_ready_receipt(readyfile, pid=process.pid, config=config_path)
            except ControlError as exc:
                receipt_error = exc
                break
            if receipt is not None:
                receipt["token"] = token
                state["startup_receipt"] = receipt
                _write_state(runtime_dir, state)
                print(f"collector service started (PID {process.pid})")
                print(f"service log: {log_file}")
                print(f"console log: {CONSOLE_LOG.resolve()}")
                return EXIT_RUNNING
            time.sleep(0.05)

        state["stop_requested"] = True
        _write_state(runtime_dir, state)
        stopfile.write_text(
            json.dumps({"token": token, "requested_at": _utc_now()}) + "\n", encoding="utf-8"
        )
        if _wait_for_exit(state, stop_timeout):
            _clear_if_owned(runtime_dir, state)
            detail = f": {receipt_error}" if receipt_error else ""
            print(
                f"collector service did not complete startup handshake within {startup_timeout:g}s"
                f"{detail}; logs: {CONSOLE_LOG.resolve()}",
                file=sys.stderr,
            )
            return EXIT_BAD_STATE
        print(
            f"collector service did not complete startup handshake and is still running after "
            f"the {stop_timeout:g}s cooperative stop wait; stop marker: {stopfile}; "
            f"logs: {CONSOLE_LOG.resolve()}",
            file=sys.stderr,
        )
        return EXIT_BAD_STATE


def _status(*, runtime_dir: Path) -> int:
    runtime_dir = runtime_dir.resolve()
    try:
        state = _load_current_state(runtime_dir)
    except ControlError as exc:
        print(f"collector control state is invalid: {exc}", file=sys.stderr)
        return EXIT_BAD_STATE
    if state is None:
        if (runtime_dir / LOCK_NAME).exists():
            print("collector service startup or shutdown is being prepared")
            return EXIT_RUNNING
        print("collector service is stopped")
        return EXIT_STOPPED
    try:
        live = _state_is_live(state)
    except ProcessIdentityError as exc:
        print(f"cannot safely verify collector PID {state['pid']}: {exc}", file=sys.stderr)
        return EXIT_BAD_STATE
    if not live:
        try:
            with _state_lock(runtime_dir):
                _clear_if_owned(runtime_dir, state)
        except LockBusyError:
            pass
        print("collector service is stopped (stale state cleared)")
        return EXIT_STOPPED

    marker = Path(state["stopfile"])
    stop_requested = state.get("stop_requested") is True
    try:
        marker_payload = _read_json(marker)
        stop_requested = stop_requested or bool(
            marker_payload and marker_payload.get("token") == state["token"]
        )
    except ControlError:
        # An invalid marker cannot be used as a stop request, but the process
        # remains verifiably running and the status output remains useful.
        pass
    receipt = state.get("startup_receipt")
    if receipt is None:
        receipt = (
            _read_ready_receipt(
                Path(state["readyfile"]), pid=state["pid"], config=Path(state["config"])
            )
            if state.get("config")
            else None
        )
    startup = receipt.get("started_at", "ready") if isinstance(receipt, dict) else "waiting"
    print(f"collector service running (PID {state['pid']}; startup={startup})")
    print(f"stop requested: {'yes' if stop_requested else 'no'}")
    print(f"service log: {state.get('service_log', SERVICE_LOG)}")
    print(f"console log: {state.get('console_log', CONSOLE_LOG)}")
    return EXIT_RUNNING


def _stop(*, runtime_dir: Path, stop_timeout: float) -> int:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = runtime_dir.resolve()
    with _state_lock(runtime_dir):
        state = _load_current_state(runtime_dir)
        if state is None:
            print("collector service is stopped")
            return 0
        try:
            if not _state_is_live(state):
                _remove_owned_state(runtime_dir, state)
                print("collector service is stopped (stale state cleared)")
                return 0
        except ProcessIdentityError as exc:
            raise ControlError(f"cannot safely verify collector PID {state['pid']}: {exc}") from exc

        token = state["token"]
        stopfile = Path(state["stopfile"])
        state["stop_requested"] = True
        _write_state(runtime_dir, state)
        _atomic_write_json(stopfile, {"token": token, "requested_at": _utc_now()})

    try:
        print(
            f"stop requested (PID {state['pid']}); waiting for data finalization "
            f"up to {stop_timeout:g}s; logs: {state.get('console_log', CONSOLE_LOG)}",
            flush=True,
        )
        exited = _wait_for_exit(state, stop_timeout)
    except ProcessIdentityError as exc:
        raise ControlError(
            f"cannot safely confirm collector PID {state['pid']} exit: {exc}"
        ) from exc
    if exited:
        with _state_lock(runtime_dir):
            _clear_if_owned(runtime_dir, state)
        print("collector service stopped")
        return 0
    print(
        f"collector service is still running after {stop_timeout:g}s; no process was killed. "
        f"stop marker: {stopfile}; logs: {state.get('console_log', CONSOLE_LOG)}",
        file=sys.stderr,
    )
    return EXIT_STOP_TIMEOUT


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="collector_control.py",
        description="Manage the detached CTP collector supervisor.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("start", "status", "stop", "restart"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--config", default=None)
        sub.add_argument("--runtime-dir", default=str(SCRIPT_DIR / ".runtime"))
        if command in ("start", "restart"):
            sub.add_argument("--startup-timeout", type=float, default=10.0)
        if command in ("start", "stop", "restart"):
            sub.add_argument("--stop-timeout", type=float, default=300.0)
    return parser


def _positive_timeout(name: str, value: float) -> None:
    if value <= 0:
        raise ControlError(f"--{name} must be greater than zero")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args, service_args = parser.parse_known_args(argv)
    if service_args and args.command not in ("start", "restart"):
        parser.error(f"unrecognized arguments for {args.command}: {' '.join(service_args)}")
    if service_args and service_args[0] == "--":
        service_args = service_args[1:]
    try:
        runtime_dir = Path(args.runtime_dir).expanduser().resolve()
        configured_path = (
            Path(args.config).expanduser() if args.config else SCRIPT_DIR / "collector.yaml"
        )
        if args.command in ("start", "restart"):
            _positive_timeout("startup-timeout", args.startup_timeout)
        if args.command in ("start", "stop", "restart"):
            _positive_timeout("stop-timeout", args.stop_timeout)
        if args.command == "status":
            return _status(runtime_dir=runtime_dir)
        if args.command == "stop":
            return _stop(runtime_dir=runtime_dir, stop_timeout=args.stop_timeout)
        service_args = _service_args_from_environment() + service_args
        if args.config:
            service_args = _without_config_arguments(service_args)
            config_path = configured_path.resolve()
        else:
            config_path = _effective_config_path(configured_path, service_args)
        if args.command == "restart":
            stopped = _stop(runtime_dir=runtime_dir, stop_timeout=args.stop_timeout)
            if stopped != 0:
                return stopped
        return _start(
            config_path=config_path,
            runtime_dir=runtime_dir,
            startup_timeout=args.startup_timeout,
            stop_timeout=args.stop_timeout,
            service_args=service_args,
        )
    except (ControlError, OSError) as exc:
        print(f"collector control failed: {exc}", file=sys.stderr)
        return EXIT_BAD_STATE


if __name__ == "__main__":
    raise SystemExit(main())
