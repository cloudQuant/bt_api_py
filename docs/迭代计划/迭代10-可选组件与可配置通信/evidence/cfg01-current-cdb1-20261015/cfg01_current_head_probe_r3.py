from __future__ import annotations

import _thread
import ctypes
import hashlib
import importlib
import importlib.metadata
import json
import multiprocessing.process
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
from collections import defaultdict
from contextlib import redirect_stdout
from io import StringIO

result_path = Path(sys.argv[1])
no_block_config = Path(sys.argv[2])
empty_clients_config = Path(sys.argv[3])
EXPECTED_SOURCE_REVISION = "cdb1b81e39b661afb145ff3d9e94695c97429d7e"
EXPECTED_SOURCE_TREE = "6943f04da92133ab23ace7fc0c3ceefdc676eb50"
EXPECTED_BUILD_MANIFEST_SHA256 = "fd8fd3b9477b1080bddb681c531b11880a58b586e1a51d7c8de6950a541956af"
EXPECTED_WHEEL_SHA256 = "f529664e95b4b489cf418b4fc78e81d7a77b72f5ba80c37ed3f5c4ce03c9033b"
EXPECTED_SDK_VERSION = "0.15.5"
EXPECTED_CONFIG_BYTES = {
    "no_new_block": b"# Ordinary SDK/backtest use: no optional clients or services.\n{}\n",
    "empty_clients": b"clients: {}",
}
wheel_path = Path(sys.argv[4])
build_manifest_path = Path(sys.argv[5])

phase = "imports"
counts = defaultdict(lambda: defaultdict(int))
audit_events = []
watched_names = (
    "bt_api_gateway",
    "bt_api_execution",
    "bt_api_risk",
    "bt_api_monitor",
    "bt_api_transport_zmq",
    "bt_api_py.service_host",
    "bt_api_py.service_runtime",
    "bt_api_py.service_components",
    "zmq",
    "spdlog",
)
import_attempts = []

class ImportAttemptProbe:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in watched_names):
            import_attempts.append(fullname)
        return None

sys.meta_path.insert(0, ImportAttemptProbe())

def audit(event, args):
    if event == "socket.__new__":
        key = "socket.audit_new"
    elif event == "subprocess.Popen":
        key = "subprocess.audit_popen"
    elif event == "os.system":
        key = "os.audit_system"
    elif event.startswith("os.spawn"):
        key = "os.audit_spawn"
    else:
        return
    counts[phase][key] += 1
    frames = []
    frame = sys._getframe(1)
    while frame is not None and len(frames) < 10:
        frames.append({
            "module": frame.f_globals.get("__name__", ""),
            "function": frame.f_code.co_name,
            "file": frame.f_code.co_filename,
            "line": frame.f_lineno,
        })
        frame = frame.f_back
    audit_events.append({
        "phase": phase,
        "event": event,
        "stack_nearest_first": frames,
    })

sys.addaudithook(audit)

def profile_service_host(frame, event, arg):
    if event == "call" and frame.f_code.co_name == "__init__":
        owner = frame.f_locals.get("self")
        if (
            owner is not None
            and type(owner).__name__ == "ServiceHost"
            and type(owner).__module__ == "bt_api_py.service_host"
        ):
            counts[phase]["ServiceHost.__init__"] += 1
    return profile_service_host

sys.setprofile(profile_service_host)

real_thread_start = threading.Thread.start
def observed_thread_start(self, *args, **kwargs):
    counts[phase]["threading.Thread.start"] += 1
    return real_thread_start(self, *args, **kwargs)
threading.Thread.start = observed_thread_start

real_lowlevel_thread_start = _thread.start_new_thread
def observed_lowlevel_thread_start(*args, **kwargs):
    counts[phase]["_thread.start_new_thread"] += 1
    return real_lowlevel_thread_start(*args, **kwargs)
_thread.start_new_thread = observed_lowlevel_thread_start

real_process_start = multiprocessing.process.BaseProcess.start
def observed_process_start(self, *args, **kwargs):
    counts[phase]["multiprocessing.BaseProcess.start"] += 1
    return real_process_start(self, *args, **kwargs)
multiprocessing.process.BaseProcess.start = observed_process_start

win32_names = (
    "CreateJobObjectW",
    "CreateProcessW",
    "CreateNamedPipeW",
    "CreateEventW",
    "CreateThread",
    "CreateIoCompletionPort",
    "CreateMutexW",
    "CreateSemaphoreW",
    "CreateServiceW",
    "StartServiceW",
)
kernel32 = ctypes.windll.kernel32
real_win32 = {}

class ObservedWin32Call:
    def __init__(self, name, function):
        self.name = name
        self.function = function
    def __call__(self, *args, **kwargs):
        counts[phase]["win32." + self.name] += 1
        return self.function(*args, **kwargs)
    @property
    def argtypes(self):
        return self.function.argtypes
    @argtypes.setter
    def argtypes(self, value):
        self.function.argtypes = value
    @property
    def restype(self):
        return self.function.restype
    @restype.setter
    def restype(self, value):
        self.function.restype = value
    @property
    def errcheck(self):
        return self.function.errcheck
    @errcheck.setter
    def errcheck(self, value):
        self.function.errcheck = value

for win32_name in win32_names:
    try:
        win32_func = getattr(kernel32, win32_name)
    except AttributeError:
        continue
    real_win32[win32_name] = win32_func
    setattr(kernel32, win32_name, ObservedWin32Call(win32_name, win32_func))

kernel32.GetCurrentProcess.restype = ctypes.c_void_p
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
kernel32.GetProcessHandleCount.restype = ctypes.c_int
current_process = kernel32.GetCurrentProcess()

def process_handle_count():
    result = ctypes.c_ulong()
    if not kernel32.GetProcessHandleCount(current_process, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(result.value)

def package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None

def snapshot():
    return {
        "active_python_threads": len(threading.enumerate()),
        "process_handle_count": process_handle_count(),
        "resource_calls": dict(counts[phase]),
    }

def persist(result, status):
    result["status"] = status
    result["audit_events"] = list(audit_events)
    result["resource_counts_by_phase"] = {
        name: dict(values) for name, values in counts.items()
    }
    result["watched_import_attempts"] = sorted(import_attempts)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

result = {
    "status": "PROBE_NOT_STARTED",
    "expected_sdk_source_revision": EXPECTED_SOURCE_REVISION,
    "expected_sdk_source_tree": EXPECTED_SOURCE_TREE,
    "expected_build_manifest_sha256": EXPECTED_BUILD_MANIFEST_SHA256,
    "expected_sdk_wheel": str(wheel_path),
    "expected_sdk_wheel_sha256": EXPECTED_WHEEL_SHA256,
    "build_manifest_path": str(build_manifest_path),
    "working_directory": str(Path.cwd()),
    "phase_observations": {},
}
snapshots = {}
execution_error = None
initial = None
sdk = None

try:
    manifest_bytes = build_manifest_path.read_bytes()
    build_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    build_manifest = json.loads(manifest_bytes)
    result["build_manifest_provenance"] = {
        "sha256": build_manifest_sha256,
        "source_revision": build_manifest.get("source", {}).get("revision"),
        "source_tree": build_manifest.get("source", {}).get("tree"),
        "archive_path": build_manifest.get("source", {}).get("archive"),
        "archive_sha256_recorded": build_manifest.get("source", {}).get("archive_sha256"),
        "wheel_path_recorded": build_manifest.get("wheel", {}).get("path"),
        "wheel_sha256_recorded": build_manifest.get("wheel", {}).get("sha256"),
        "wheel_version_recorded": build_manifest.get("wheel", {}).get("version"),
        "build_tool_version_recorded": build_manifest.get("build_tool_version"),
    }
    initial = snapshot()
    snapshots["before_product_imports"] = initial

    from bt_api_py.service_config_io import load_client_config, main as config_cli_main
    from bt_api_py import BtApi
    import backtrader as bt
    import pandas as pd

    after_imports = snapshot()
    snapshots["after_product_imports"] = after_imports
    sdk_dist = importlib.metadata.distribution("bt-api-py")
    installed_direct_url_raw = sdk_dist.read_text("direct_url.json")
    installed_direct_url = (
        json.loads(installed_direct_url_raw) if installed_direct_url_raw else None
    )
    installed_wheel_sha256 = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    build_archive_path = Path(build_manifest["source"]["archive"])
    build_archive_sha256 = hashlib.sha256(build_archive_path.read_bytes()).hexdigest()
    archive_hash = (
        installed_direct_url.get("archive_info", {}).get("hash")
        if installed_direct_url
        else None
    )
    result["installed_sdk"] = {
        "version": sdk_dist.version,
        "direct_url": installed_direct_url,
        "wheel_path_sha256": installed_wheel_sha256,
        "direct_url_archive_hash": archive_hash,
        "wheel_matches_expected_sha256": installed_wheel_sha256 == EXPECTED_WHEEL_SHA256,
        "direct_url_matches_expected_sha256": archive_hash == "sha256=" + EXPECTED_WHEEL_SHA256,
        "build_manifest_sha256": build_manifest_sha256,
        "build_manifest_source_revision": build_manifest.get("source", {}).get("revision"),
        "build_manifest_source_tree": build_manifest.get("source", {}).get("tree"),
        "build_manifest_wheel_sha256": build_manifest.get("wheel", {}).get("sha256"),
        "build_manifest_archive_sha256": build_manifest.get("source", {}).get("archive_sha256"),
        "build_manifest_wheel_version": build_manifest.get("wheel", {}).get("version"),
        "build_manifest_build_tool_version": build_manifest.get("build_tool_version"),
        "build_archive_sha256_actual": build_archive_sha256,
    }

    phase = "config"
    config_results = []
    for label, path in (
        ("no_new_block", no_block_config),
        ("empty_clients", empty_clients_config),
    ):
        raw_config_bytes = path.read_bytes()
        parsed = load_client_config(path)
        output = StringIO()
        with redirect_stdout(output):
            cli_exit_code = config_cli_main(["--role", "client", str(path)])
        cli_stdout = output.getvalue()
        try:
            cli_summary = json.loads(cli_stdout)
            cli_parse_error = None
        except json.JSONDecodeError as exc:
            cli_summary = None
            cli_parse_error = type(exc).__name__
        config_results.append({
            "variant": label,
            "path": str(path),
            "raw_config_sha256": hashlib.sha256(raw_config_bytes).hexdigest(),
            "raw_config_byte_length": len(raw_config_bytes),
            "raw_config_bytes_match_expected": raw_config_bytes == EXPECTED_CONFIG_BYTES[label],
            "raw_config": parsed.to_public_dict(),
            "client_count": len(parsed.clients),
            "process_role": parsed.process_role,
            "cli_exit_code": cli_exit_code,
            "cli_summary": cli_summary,
            "cli_parse_error": cli_parse_error,
        })
    snapshots["after_config_variants"] = snapshot()
    result["phase_observations"]["configs"] = config_results

    phase = "default_sdk_direct"
    sdk = BtApi(debug=False)
    sdk_mode = sdk.transport_mode.value
    backend_type = f"{type(sdk._backend).__module__}.{type(sdk._backend).__qualname__}"
    sdk.close()
    snapshots["after_default_sdk"] = snapshot()
    result["phase_observations"]["ordinary_sdk"] = {
        "transport_mode": sdk_mode,
        "backend": backend_type,
        "constructed_and_closed": True,
    }
    sdk = None

    phase = "synthetic_backtest"
    frame = pd.DataFrame(
        {
            "open": [10.0],
            "high": [10.5],
            "low": [9.5],
            "close": [10.0],
            "volume": [100],
            "openinterest": [0],
        },
        index=pd.date_range("2026-01-01", periods=1, freq="D"),
    )
    class Smoke(bt.Strategy):
        def __init__(self):
            self.seen = 0
        def next(self):
            self.seen += 1
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.adddata(bt.feeds.PandasData(dataname=frame))
    cerebro.addstrategy(Smoke)
    backtest_result = cerebro.run()
    bars = backtest_result[0].seen
    snapshots["after_cerebro"] = snapshot()
    result["phase_observations"]["synthetic_backtest"] = {
        "bars_processed": bars,
        "completed": True,
    }

    absent_domains = (
        "bt_api_gateway",
        "bt_api_execution",
        "bt_api_risk",
        "bt_api_monitor",
    )
    absent_optional = (
        "bt_api_transport_zmq",
        "pyzmq",
        "spdlog",
    )
    package_state = {
        name: {
            "distribution_version": package_version(name),
            "module_loaded": any(
                module == name or module.startswith(name + ".")
                for module in sys.modules
            ),
        }
        for name in absent_domains + absent_optional
    }
    service_modules = {
        name: name in sys.modules
        for name in (
            "bt_api_py.service_host",
            "bt_api_py.service_runtime",
            "bt_api_py.service_components",
        )
    }
    filesystem_changes = sorted(
        str(path.relative_to(Path.cwd()))
        for path in Path.cwd().rglob("*")
        if path.is_file()
    )
    phase_counts = {
        name: dict(values) for name, values in counts.items()
    }
    result["optional_and_domain_packages"] = package_state
    result["sdk_service_modules_loaded"] = service_modules
    result["resource_snapshots"] = snapshots
    result["win32_resource_apis_instrumented"] = sorted(real_win32)
    result["filesystem_files_created_in_working_directory"] = filesystem_changes
    result["runtime_phase_names"] = ["config", "default_sdk_direct", "synthetic_backtest"]
except BaseException as exc:
    execution_error = {
        "type": type(exc).__name__,
        "message": str(exc)[:1000],
    }
    result["execution_error"] = execution_error
    raise
finally:
    restoration_errors = []
    try:
        for name, function in real_win32.items():
            try:
                setattr(kernel32, name, function)
            except BaseException as restore_exc:
                restoration_errors.append({
                    "api": name,
                    "type": type(restore_exc).__name__,
                })
    finally:
        result["observer_restoration_errors"] = restoration_errors
        result["resource_snapshots"] = snapshots
        result["execution_error"] = execution_error
        if initial is not None:
            final_snapshot = snapshots.get("after_cerebro")
            if final_snapshot is not None:
                result["full_run_process_handle_delta"] = (
                    final_snapshot["process_handle_count"] - initial["process_handle_count"]
                )
                result["full_run_active_thread_delta"] = (
                    final_snapshot["active_python_threads"] - initial["active_python_threads"]
                )
        persist(result, "OBSERVATION_CAPTURED_BEFORE_ASSERTIONS")

runtime_phases = result.get("runtime_phase_names", [])
phase_counts = result.get("resource_counts_by_phase", {})
package_state = result.get("optional_and_domain_packages", {})
configs = result.get("phase_observations", {}).get("configs", [])
sdk_observation = result.get("phase_observations", {}).get("ordinary_sdk", {})
backtest_observation = result.get("phase_observations", {}).get("synthetic_backtest", {})
installed_sdk = result.get("installed_sdk", {})
import_attempts = result.get("watched_import_attempts", [])

checks = {
    "build_manifest_is_exactly_pinned": (
        result.get("build_manifest_provenance", {}).get("sha256")
        == EXPECTED_BUILD_MANIFEST_SHA256
    ),
    "build_manifest_binds_fixed_current_commit_and_tree": (
        installed_sdk.get("build_manifest_source_revision") == EXPECTED_SOURCE_REVISION
        and installed_sdk.get("build_manifest_source_tree") == EXPECTED_SOURCE_TREE
    ),
    "build_manifest_archive_matches_actual_git_archive": (
        installed_sdk.get("build_manifest_archive_sha256")
        == installed_sdk.get("build_archive_sha256_actual")
    ),
    "build_manifest_binds_wheel_to_current_commit": (
        installed_sdk.get("build_manifest_wheel_sha256") == EXPECTED_WHEEL_SHA256
    ),
    "installed_sdk_direct_url_and_hash_match_expected_wheel": (
        installed_sdk.get("wheel_matches_expected_sha256") is True
        and installed_sdk.get("direct_url_matches_expected_sha256") is True
    ),
    "installed_sdk_version_matches_pinned_wheel_metadata": (
        installed_sdk.get("version") == EXPECTED_SDK_VERSION
        and installed_sdk.get("build_manifest_wheel_version") == EXPECTED_SDK_VERSION
        and installed_sdk.get("build_manifest_build_tool_version") == "1.6.1"
    ),
    "observer_restoration_completed": result.get("observer_restoration_errors") == [],
    "both_configs_valid_empty_and_unactivated": (
        len(configs) == 2
        and all(
            item["raw_config_bytes_match_expected"] is True
            and item["process_role"] is None
            and item["client_count"] == 0
            and item["cli_exit_code"] == 0
            and item["cli_parse_error"] is None
            and item["cli_summary"] is not None
            and item["cli_summary"].get("status") == "valid"
            and item["cli_summary"].get("process_role") is None
            and item["cli_summary"].get("client_count") == 0
            and item["cli_summary"].get("component_count") == 0
            and item["cli_summary"].get("activated") is False
            for item in configs
        )
    ),
    "default_sdk_is_direct_and_closed": (
        sdk_observation.get("transport_mode") == "direct"
        and sdk_observation.get("backend", "").endswith(".DirectBackend")
        and sdk_observation.get("constructed_and_closed") is True
    ),
    "cerebro_processed_one_synthetic_bar": (
        backtest_observation.get("bars_processed") == 1
        and backtest_observation.get("completed") is True
    ),
    "four_domain_packages_absent_and_unloaded": (
        all(
            package_state.get(name, {}).get("distribution_version") is None
            and package_state.get(name, {}).get("module_loaded") is False
            for name in (
                "bt_api_gateway",
                "bt_api_execution",
                "bt_api_risk",
                "bt_api_monitor",
            )
        )
    ),
    "optional_transport_and_spdlog_absent_and_unloaded": (
        all(
            package_state.get(name, {}).get("distribution_version") is None
            and package_state.get(name, {}).get("module_loaded") is False
            for name in ("bt_api_transport_zmq", "pyzmq", "spdlog")
        )
    ),
    "no_service_modules_or_watched_import_attempts": (
        not any(result.get("sdk_service_modules_loaded", {}).values())
        and not import_attempts
    ),
    "no_observed_runtime_optional_resource_events": (
        all(not phase_counts.get(name) for name in runtime_phases)
    ),
    "no_servicehost_constructor_in_direct_or_backtest": (
        phase_counts.get("default_sdk_direct", {}).get("ServiceHost.__init__", 0) == 0
        and phase_counts.get("synthetic_backtest", {}).get("ServiceHost.__init__", 0) == 0
    ),
    "no_files_created_in_probe_working_directory": (
        result.get("filesystem_files_created_in_working_directory", []) == []
    ),
}
result["checks"] = checks
failed_checks = [name for name, passed in checks.items() if not passed]
if failed_checks:
    result["status"] = "ASSERTION_FAILED"
    result["failed_checks"] = failed_checks
    persist(result, "ASSERTION_FAILED")
    raise SystemExit("CFG01 current-head probe failed checks: " + ", ".join(failed_checks))

persist(result, "PASS_CFG01_CURRENT_HEAD_DEFAULT_PATH_NODE")
print(json.dumps({
    "status": result["status"],
    "installed_sdk": result.get("installed_sdk"),
    "phase_observations": result.get("phase_observations"),
    "checks": checks,
    "resource_counts_by_phase": result.get("resource_counts_by_phase"),
    "audit_event_count": len(result.get("audit_events", [])),
}, sort_keys=True))

