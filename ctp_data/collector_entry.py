"""Shared launch entry for background collection and console diagnostics."""

from __future__ import annotations

import ctypes
import os
import shlex
import sys
from pathlib import Path


def split_environment_args(value: str) -> list[str]:
    """Parse arguments without passing environment values to a shell."""
    if not value.strip():
        return []
    if os.name != "nt":
        return shlex.split(value)
    parser = ctypes.windll.shell32.CommandLineToArgvW
    parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int()
    arguments = parser("collector " + value, ctypes.byref(count))
    if not arguments:
        raise ctypes.WinError()
    try:
        return [arguments[index] for index in range(1, count.value)]
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(ctypes.cast(arguments, ctypes.c_void_p))


def prepare_runtime() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    source = repo_root / "bt_api" / "bt_api_ctp" / "src"
    if (source / "bt_api_ctp").is_dir():
        sys.path.insert(0, str(source))
        paths = os.environ.get("PYTHONPATH", "").split(os.pathsep)
        os.environ["PYTHONPATH"] = os.pathsep.join(
            [str(source)] + [path for path in paths if path and path != str(source)]
        )
    env_file = repo_root / ".env"
    if env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
    for name in ("OMP_NUM_THREADS", "NUMEXPR_MAX_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, "4")
    os.chdir(repo_root)
    return repo_root


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    service_mode = bool(arguments and arguments[0] == "--service")
    if service_mode:
        arguments.pop(0)
    if arguments == ["--help"]:
        print("start_collector.bat                  Start the background collector service")
        print("start_collector.bat --status         Show status and log paths")
        print("start_collector.bat --stop           Stop gracefully and save ticks")
        print("start_collector.bat --restart        Stop gracefully, then start again")
        print("start_collector.bat --foreground     Run one window in this console")
        print("service.bat --foreground             Run the supervisor in this console")
        print("start_collector.bat --check-calendar Check config without connecting")
        return 0
    try:
        repo_root = prepare_runtime()
        config = repo_root / "ctp_data" / "collector.yaml"
        if arguments and arguments[0] == "--foreground":
            arguments.pop(0)
            if service_mode:
                from service import main as service_main

                return service_main(
                    split_environment_args(os.environ.get("COLLECTOR_SERVICE_ARGS", "")) + arguments
                )
            from bt_api_ctp.collector.cli import main as collector_main

            defaults = split_environment_args(os.environ.get("COLLECTOR_ARGS", ""))
            if (
                not defaults
                and "--duration" not in arguments
                and not any(item.startswith("--duration=") for item in arguments)
            ):
                defaults = ["--until-close", "--wait-open"]
            return collector_main(["--config", str(config)] + defaults + arguments)
        if any(item in arguments for item in ("--check-calendar", "--validate-shards")):
            from bt_api_ctp.collector.cli import main as collector_main

            return collector_main(["--config", str(config)] + arguments)
        if "--dry-run" in arguments:
            from service import main as service_main

            return service_main(
                split_environment_args(os.environ.get("COLLECTOR_SERVICE_ARGS", "")) + arguments
            )
        command = "start"
        aliases = {"--status": "status", "--stop": "stop", "--restart": "restart"}
        if arguments and arguments[0] in aliases:
            command = aliases[arguments.pop(0)]
        elif arguments and arguments[0] in ("--background", "start"):
            arguments.pop(0)
        elif arguments and arguments[0] in ("status", "stop", "restart"):
            command = arguments.pop(0)
        from collector_control import main as control_main

        return control_main([command] + arguments)
    except (OSError, ValueError, ImportError) as exc:
        print(f"[start_collector] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
