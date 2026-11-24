"""Single-step MIG04 installed-consumer supplement; inert without --execute."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import socket
import subprocess
import sys
import zipfile
from pathlib import Path


ORIGINAL_ROOT = Path(r"D:\temp\it10-mig04-certlib-consumer-r1-20261016")
ORIGINAL_RESULT = ORIGINAL_ROOT / "result.json"
ORIGINAL_RESULT_SHA256 = "b72809d9b2eb45b68e34323caf55eabdd66b444a49eb211bec31a0b28c56972a"
ORIGINAL_OUTER = Path(r"D:\temp\iteration10-certlib-consumer-root-execution-20261016")
ORIGINAL_OUTER_EXIT_SHA256 = "df4e26a04a444901b95afef44e4a96cfae34690fff2ad2c66389c70079cdff2b"
WHEELHOUSE_MANIFEST = ORIGINAL_ROOT / "wheelhouse-sha256.json"
WHEELHOUSE_MANIFEST_SHA256 = "d806e5961c794f077167312f191855ebe1319d169190fd28b1c97c06cbccd7d3"
DEPENDENCY_LOCK = ORIGINAL_ROOT / "consumer-dependencies.lock"
DEPENDENCY_LOCK_SHA256 = "29b79469307509553d26842d69c2539460d663d3bcbcfba8bcf9529d7b69ac51"
CONSUMER_VENV = ORIGINAL_ROOT / "containers" / "consumer" / "env"
CONSUMER_PYTHON = CONSUMER_VENV / "Scripts" / "python.exe"
OUTPUT = Path(r"D:\temp\it10-mig04-certlib-consumer-step06-supplement-r9-20261016")
CHILD_SOURCE = OUTPUT / "step06_installed_crossrepo_child.py"
EXPECTED_ORIGINAL_STEP06_BODY_SHA256 = "5b001b99ba240647a69cf73769d2a140aabba431cef0fc2a9e7941957b3df052"
R1_SUPPLEMENT_ROOT = Path(r"D:\temp\it10-mig04-certlib-consumer-step06-supplement-r1-20261016")
R1_SUPPLEMENT_RESULT = R1_SUPPLEMENT_ROOT / "result.json"
R1_SUPPLEMENT_RESULT_SHA256 = "3fc13df54a08d5e47febf3c934f6379ad7efdafaa48288b1a526f5d44e92344e"
R1_SUPPLEMENT_SCRIPT = Path(__file__).resolve().with_name("it10_mig04_certlib_consumer_step06_supplement_r1_20261016.py")
R1_SUPPLEMENT_SCRIPT_SHA256 = "f59b44c1c4d854c5599893b62577c4a85fa09a893ff27d610b011f29dd3260bd"
R1_CLEANUP_RECEIPT = Path(r"D:\temp\it10-mig04-certlib-consumer-step06-owned-cleanup-final.json")
R1_CLEANUP_RECEIPT_SHA256 = "fbf0dd18d47f67e90300c003e37e159e6658b118565c639866ab0080a40e5f46"
R1_CLEANUP_LOG = Path(r"D:\temp\it10-mig04-certlib-consumer-step06-r1-owned-cleanup.log")
R1_CLEANUP_LOG_SHA256 = "6283740de8e33e2f0f873eb81b7792e6054c5db169f8f68bcb319528178acf05"
PYTHON_PLATFORM_SOURCE = Path(r"C:\Anaconda3\Lib\platform.py")
PYTHON_PLATFORM_SOURCE_SHA256 = "0b41720e16faa3101996864c28f1c85fe61fc19608a0187068d5e1716bdf883e"
PYTHON_SUBPROCESS_SOURCE = Path(r"C:\Anaconda3\Lib\subprocess.py")
PYTHON_SUBPROCESS_SOURCE_SHA256 = "2ff641d58f8696496e8718538cd96cd7afcd503ae271fb23da22422b3aee9fc7"
PLATFORM_VER_TIMEOUT_SECONDS = 10
NUMPY_LOCAL_PROBE_TIMEOUT_SECONDS = 10
STEP06_PARENT_TIMEOUT_SECONDS = 120
R3_SUPPLEMENT_ROOT = Path(r"D:\temp\it10-mig04-certlib-consumer-step06-supplement-r3-20261016")
R3_SUPPLEMENT_RESULT = R3_SUPPLEMENT_ROOT / "result.json"
R3_SUPPLEMENT_RESULT_SHA256 = "4def695595f4aa3ac9dd9f05291a8911e8d44e8801e4de86b7507bc4980f0d20"
R3_SUPPLEMENT_RESULT_BYTES = 23607
R3_SUPPLEMENT_SCRIPT = Path(__file__).resolve().with_name("it10_mig04_certlib_consumer_step06_supplement_r3_20261016.py")
R3_SUPPLEMENT_SCRIPT_SHA256 = "c793a8fe760cce94844721e3e8f666da50119a6c4f345de2d963eb9e10c0bd1c"
R3_SUPPLEMENT_SCRIPT_BYTES = 36074
R3_STDOUT_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
R3_STDERR_SHA256 = "5012521c82db90e6c72b92e366a9294570676ce9e15fe1f30eb9673dafa9f409"
R3_STDERR_BYTES = 5418
NUMPY_UTILS_SOURCE = CONSUMER_VENV / "Lib" / "site-packages" / "numpy" / "testing" / "_private" / "utils.py"
NUMPY_UTILS_SOURCE_SHA256 = "15a67bea0d5acd4f22d345e44d065512b06d1456639de5c2d100a25276a67b8c"
NUMPY_UTILS_SOURCE_BYTES = 87751
NUMPY_SVE_CALLER_FIRST_LINE = 1239
R7_SUPPLEMENT_ROOT = Path(r"D:\temp\it10-mig04-certlib-consumer-step06-supplement-r7-20261016")
R7_SUPPLEMENT_RESULT = R7_SUPPLEMENT_ROOT / "result.json"
R7_SUPPLEMENT_RESULT_SHA256 = "f3d86415dc2114a789cc476324bfe4ca352bb84c1f6e3aa9e4389cc9662066e6"
R7_SUPPLEMENT_RESULT_BYTES = 43110
R7_SUPPLEMENT_SCRIPT = Path(__file__).resolve().with_name("it10_mig04_certlib_consumer_step06_supplement_r7_20261016.py")
R7_SUPPLEMENT_SCRIPT_SHA256 = "4ef3c282640f69d697318f505dbbe8148d32b3010c2a4c460db635aff70ff2c7"
R7_SUPPLEMENT_SCRIPT_BYTES = 64542
R7_STDOUT_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
R7_STDERR_SHA256 = "fe2a4f089974a73ff7a282fbd4c44881010088a6c9a44cbbf31cbb2638c296e1"
R7_STDERR_BYTES = 114
R8_SUPPLEMENT_ROOT = Path(r"D:\temp\it10-mig04-certlib-consumer-step06-supplement-r8-20261016")
R8_SUPPLEMENT_PREFLIGHT = R8_SUPPLEMENT_ROOT / "preflight.json"
R8_SUPPLEMENT_PREFLIGHT_SHA256 = "38ce23020a55e2c56d59ecbe26e9a22545dbdfa128345f9135480adb6ed4d08a"
R8_SUPPLEMENT_PREFLIGHT_BYTES = 19534
R8_SUPPLEMENT_SCRIPT = Path(__file__).resolve().with_name("it10_mig04_certlib_consumer_step06_supplement_r8_20261016.py")
R8_SUPPLEMENT_SCRIPT_SHA256 = "cfa7fad48c8406de483263669891abbc816c5ff3875aefeb16fbea4a1e2de06d"
R8_SUPPLEMENT_SCRIPT_BYTES = 97923
R8_CHILD_BYTES = 35539
R8_CHILD_SHA256 = "420becd8db1cc6bd0985b493308227460a083eb156a40e2d87d961f30b186245"
R8_COMMAND_LINE_CHARS = 36776
WINDOWS_CREATEPROCESS_COMMAND_LINE_LIMIT = 32767
R8_ROOT_TOOL_ID = "8b22c0"
R8_ROOT_REPORTED_ERROR = "FileNotFoundError: [WinError 206] 文件名或扩展名太长。"
URLLIB3_WHEEL = ORIGINAL_ROOT / "wheelhouse" / "urllib3-2.8.0-py3-none-any.whl"
URLLIB3_WHEEL_SHA256 = "0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3"
URLLIB3_WHEEL_MEMBER = "urllib3/util/connection.py"
URLLIB3_SOURCE = CONSUMER_VENV / "Lib" / "site-packages" / "urllib3" / "util" / "connection.py"
URLLIB3_SOURCE_SHA256 = "2633bbdb69731e5ccb5cf4e4afd65605d86c7979cc5633126f50c92d5ad74a74"
URLLIB3_SOURCE_BYTES = 4444
PYTHON_SOCKET_SOURCE = Path(r"C:\Anaconda3\Lib\socket.py")
PYTHON_SOCKET_SOURCE_SHA256 = "07f2010f98b7b90c237879c12f738e7305f786d7af4bc06606c4d77fd7fa9e1b"
PYTHON_SOCKET_SOURCE_BYTES = 37320
PLATFORM_METADATA_ENV_KEYS = (
    "PROCESSOR_ARCHITEW6432",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
)
EXPECTED_CERTLIB_WHEEL_SHA256 = "164ca44a402ba8153cf55890b9b954ce29becf57ae9bbbc8feefc283a007a2c3"
EXPECTED_CERTLIB_SOURCES = {
    "__init__.py": "d00e80e6c9129ece4d6f10bbe6c582addf6576d58e19ce012a8c551602097baa",
    "binance_client.py": "95ff4186b3642ed9b6c4df2934e1f1aac740a0f302b8b882670f61ac78b90db6",
    "context.py": "cc95002367f0c6241240e431f3b79c155793d260aa0897bb0e124c8a27c58ba9",
    "envfile.py": "de3613ea601c87d2dcbd88b068375c270dc75c98aedd29e0f8fce132a6ecf30d",
    "monitor.py": "7e3be314e2b24e11247207d62c6e31c9c1449a1a2804e66fde7f12fe3bede5f7",
    "runner.py": "0c7754790655bfb4c0ece0dfb2787854cc27d8807b3d59b758dd5a1db2c2811d",
    "validation.py": "8996f4c4e5a1e841d16ff60cc7b68e848d6da10933793855d709fe389c8fa78f",
}
DIRECT_WHEELS = {
    "ai_for_investor_backend-0.1.0-py3-none-any.whl": "287ee6fcef1ff50f0d51600e951285741596cc3a1d365906dbb0d7afcb0fa234",
    "backtrader-1.4.0-py3-none-any.whl": "d825efaeb82245fe91dbfcce213e85f6bacf43a1ef27f5c5955463f20ab0749d",
    "bt_api_base-0.15.8-py3-none-any.whl": "840090fbc13e7b1fefdc686805ec8685a5e5a7dfd39ace3e7a5763922fd5dbb0",
    "bt_api_binance-2.0.1-py3-none-any.whl": "7cac597539bfa6a2a6fa6249e9983df1494d30c4f427898346f22940c9b89a23",
    "bt_api_py-0.15.5-py3-none-any.whl": "6ed0ccc39991ef715f33a8a816c41ee6244b4490b911c2204eba4486a04377b8",
    "bt_api_binance_certlib-0.1.0-py3-none-any.whl": EXPECTED_CERTLIB_WHEEL_SHA256,
}
ORIGINAL_LABELS = [
    "01-download-public-lock",
    "02-install-exact-combo",
    "03-pip-check",
    "04-normal-installed-entry",
    "05-installed-certlib-provenance",
    "06-installed-crossrepo-origin",
]
MODULE_PREWARM_OLD = "import importlib, importlib.metadata as md, json, socket, sys\n"
MODULE_PREWARM_NEW = "import importlib, importlib.metadata as md, json, hashlib, os, platform, socket, subprocess, sys, threading\n"
PREWARM_INSERT_OLD = "blocked = []\ndef deny(name, *args, **kwargs):\n"
PREWARM_INSERT_NEW = r'''_harness_blocked = []
_harness_prewarm = {
    "active": True,
    "inside_platform_machine": False,
    "inside_platform_ver": False,
    "check_output_calls": 0,
    "popen_events": 0,
    "hostname_events": 0,
}
_hostname_metadata = {
    "first_hook_events": 0,
    "second_hook_confirmations": 0,
    "pending_frame": None,
    "pending_thread_id": None,
    "caller_file": None,
    "module_spec_origin": None,
    "call_return_observed": False,
}
_platform_path = Path(platform.__file__).resolve()
_subprocess_path = Path(subprocess.__file__).resolve()
assert _platform_path == Path(r"C:\Anaconda3\Lib\platform.py").resolve()
assert _subprocess_path == Path(r"C:\Anaconda3\Lib\subprocess.py").resolve()
assert hashlib.sha256(_platform_path.read_bytes()).hexdigest() == "0b41720e16faa3101996864c28f1c85fe61fc19608a0187068d5e1716bdf883e"
assert hashlib.sha256(_subprocess_path.read_bytes()).hexdigest() == "2ff641d58f8696496e8718538cd96cd7afcd503ae271fb23da22422b3aee9fc7"
_system_root = os.environ.get("SYSTEMROOT")
assert _system_root and Path(_system_root).is_absolute()
_ver_cmd = str((Path(_system_root) / "System32" / "cmd.exe").resolve())
assert Path(_ver_cmd).is_file()
_ver_argv = [_ver_cmd, "/d", "/c", "ver"]
_ver_audit_cmdline = subprocess.list2cmdline(_ver_argv)
_real_check_output = subprocess.check_output
_original_syscmd_ver = platform._syscmd_ver
_real_gethostname = socket.gethostname
_platform_metadata_env = {
    key: os.environ[key]
    for key in ("PROCESSOR_ARCHITEW6432", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER")
    if key in os.environ
}
_numpy_utils_path = Path(r"D:\temp\it10-mig04-certlib-consumer-r1-20261016\containers\consumer\env\Lib\site-packages\numpy\testing\_private\utils.py").resolve()
assert _numpy_utils_path.is_file()
assert _numpy_utils_path.stat().st_size == 87751
_numpy_utils_source = _numpy_utils_path.read_bytes()
_numpy_utils_sha256 = hashlib.sha256(_numpy_utils_source).hexdigest()
assert _numpy_utils_sha256 == "15a67bea0d5acd4f22d345e44d065512b06d1456639de5c2d100a25276a67b8c"
_numpy_utils_text = _numpy_utils_source.decode("utf-8").replace("\r\n", "\n")
assert "def check_support_sve():" in _numpy_utils_text
assert "cmd = 'lscpu'" in _numpy_utils_text
assert "output = subprocess.run(cmd, capture_output=True, text=True)" in _numpy_utils_text
assert "except OSError:\n        return False" in _numpy_utils_text
_numpy_probe = {
    "active": False,
    "thread_id": None,
    "run_calls": 0,
    "popen_events": 0,
    "popen_event": None,
    "outcome": None,
    "exception_type": None,
    "process_created": None,
    "return_code": None,
    "stdout_character_count": None,
    "stdout_sha256": None,
    "stderr_character_count": None,
    "stderr_sha256": None,
    "stdout_contains_sve": None,
    "supports_sve": None,
    "source_sha256": _numpy_utils_sha256,
}
_real_subprocess_run = subprocess.run

def _process_audit_event(name):
    return (
        name == "subprocess.Popen"
        or name in {"os.system", "os.startfile", "os.fork", "os.forkpty", "os.posix_spawn"}
        or name.startswith(("os.exec", "os.spawn", "os.posix_spawn"))
    )

def _prewarm_deny(name):
    _harness_blocked.append(name)
    raise RuntimeError("blocked external operation: " + name)

def _local_hostname_source(frame):
    module = sys.modules.get("bt_api_base.event_clock")
    if module is None or module.__name__ != "bt_api_base.event_clock":
        _prewarm_deny("socket.gethostname caller was not bt_api_base.event_clock")
    spec = getattr(module, "__spec__", None)
    if spec is None or not spec.origin:
        _prewarm_deny("bt_api_base.event_clock has no source origin")
    if (
        frame.f_globals is not module.__dict__
        or frame.f_code.co_name != "<module>"
        or frame.f_lineno != 10
        or Path(spec.origin).resolve() != Path(module.__file__).resolve()
        or Path(frame.f_code.co_filename).resolve() != Path(module.__file__).resolve()
    ):
        _prewarm_deny("socket.gethostname source binding changed")
    return str(Path(module.__file__).resolve()), str(Path(spec.origin).resolve())

def _record_local_hostname(frame):
    if (
        _harness_prewarm["active"]
        or _harness_prewarm["hostname_events"] != 1
        or _hostname_metadata["first_hook_events"] != 0
        or _hostname_metadata["pending_frame"] is not None
    ):
        _prewarm_deny("unexpected or duplicate local socket.gethostname")
    caller_file, module_origin = _local_hostname_source(frame)
    _hostname_metadata.update({
        "first_hook_events": 1,
        "pending_frame": frame,
        "pending_thread_id": threading.get_ident(),
        "caller_file": caller_file,
        "module_spec_origin": module_origin,
    })

def _confirm_local_hostname(frame):
    if (
        _harness_prewarm["active"]
        or _harness_prewarm["inside_platform_machine"]
        or _harness_prewarm["hostname_events"] != 1
        or _hostname_metadata["first_hook_events"] != 1
        or _hostname_metadata["second_hook_confirmations"] != 0
        or _hostname_metadata["pending_frame"] is not frame
        or _hostname_metadata["pending_thread_id"] != threading.get_ident()
    ):
        _prewarm_deny("socket.gethostname was not the same source-bound audit event")
    _hostname_metadata["second_hook_confirmations"] = 1
    _hostname_metadata["pending_frame"] = None
    _hostname_metadata["pending_thread_id"] = None

def _prewarm_audit(event, args):
    if event.startswith("socket."):
        if event == "socket.gethostname" and args == ():
            if (
                _harness_prewarm["active"]
                and _harness_prewarm["inside_platform_machine"]
                and _harness_prewarm["hostname_events"] == 0
            ):
                _harness_prewarm["hostname_events"] += 1
                return
            if (
                not _harness_prewarm["active"]
                and not _harness_prewarm["inside_platform_machine"]
            ):
                _record_local_hostname(sys._getframe(1))
                return
        _prewarm_deny(event)
    if _process_audit_event(event):
        if (
            event == "subprocess.Popen"
            and _harness_prewarm["active"]
            and _harness_prewarm["inside_platform_ver"]
            and _harness_prewarm["popen_events"] == 0
            and len(args) == 4
            and args[0] is None
            and args[1] == _ver_audit_cmdline
            and args[2] is None
            and args[3] is None
        ):
            _harness_prewarm["popen_events"] += 1
            return
        if (
            event == "subprocess.Popen"
            and _numpy_probe["active"]
            and threading.get_ident() == _numpy_probe["thread_id"]
            and _numpy_probe["popen_events"] == 0
            and len(args) == 4
            and args[0] is None
            and args[1] == "lscpu"
            and args[2] is None
            and args[3] is None
        ):
            _event_frame = sys._getframe(1)
            assert _event_frame.f_code is subprocess.Popen._execute_child.__code__
            assert _event_frame.f_globals is subprocess.__dict__
            assert _event_frame.f_back.f_code is subprocess.Popen.__init__.__code__
            assert _event_frame.f_back.f_back.f_code is _real_subprocess_run.__code__
            assert _event_frame.f_back.f_back.f_back.f_code is _numpy_probe_run.__code__
            _caller_frame = _event_frame.f_back.f_back.f_back.f_back
            assert _caller_frame.f_code.co_name == "check_support_sve"
            assert _caller_frame.f_globals is sys.modules["numpy.testing._private.utils"].__dict__
            _numpy_probe["popen_events"] += 1
            _numpy_probe["popen_event"] = {
                "executable": args[0],
                "command_line": args[1],
                "cwd": args[2],
                "env_is_none": args[3] is None,
            }
            return
        _prewarm_deny(event)

def _platform_safe_check_output(command, *args, **kwargs):
    expected = {
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "text": True,
        "encoding": "locale",
        "shell": True,
    }
    if (
        not _harness_prewarm["active"]
        or _harness_prewarm["inside_platform_ver"]
        or _harness_prewarm["check_output_calls"] != 0
        or command != "ver"
        or args
        or kwargs != expected
        or sys._getframe(1).f_code is not _original_syscmd_ver.__code__
        or sys._getframe(1).f_globals is not platform.__dict__
    ):
        _prewarm_deny("unexpected platform subprocess.check_output")
    _harness_prewarm["check_output_calls"] += 1
    _harness_prewarm["inside_platform_ver"] = True
    try:
        output = _real_check_output(
            _ver_argv,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="locale",
            shell=False,
            timeout=10,
        )
    finally:
        _harness_prewarm["inside_platform_ver"] = False
    if not isinstance(output, str):
        _prewarm_deny("platform ver output was not decoded text")
    _harness_prewarm["completed"] = True
    _harness_prewarm["return_code"] = 0
    _harness_prewarm["stdout_character_count"] = len(output)
    _harness_prewarm["stdout_utf8_sha256"] = hashlib.sha256(output.encode("utf-8")).hexdigest()
    return output

assert platform._uname_cache is None
sys.addaudithook(_prewarm_audit)
subprocess.check_output = _platform_safe_check_output
try:
    _harness_prewarm["inside_platform_machine"] = True
    _prewarmed_machine = platform.machine()
finally:
    _harness_prewarm["inside_platform_machine"] = False
    subprocess.check_output = _real_check_output
    _harness_prewarm["active"] = False
    _harness_prewarm["inside_platform_ver"] = False
assert subprocess.check_output is _real_check_output
assert platform._syscmd_ver is _original_syscmd_ver
assert isinstance(_prewarmed_machine, str) and _prewarmed_machine
assert _harness_prewarm["check_output_calls"] == 1
assert _harness_prewarm["popen_events"] == 1
assert _harness_prewarm["hostname_events"] == 1
assert _harness_blocked == []
assert _prewarmed_machine == (
    os.environ.get("PROCESSOR_ARCHITEW6432", "")
    or os.environ.get("PROCESSOR_ARCHITECTURE", "")
)
_harness_platform_prewarm = {
    "machine": _prewarmed_machine,
    "platform_source_sha256": "0b41720e16faa3101996864c28f1c85fe61fc19608a0187068d5e1716bdf883e",
    "subprocess_source_sha256": "2ff641d58f8696496e8718538cd96cd7afcd503ae271fb23da22422b3aee9fc7",
    "command": _ver_cmd,
    "argv": _ver_argv,
    "audit_event": {
        "executable": None,
        "command_line": _ver_audit_cmdline,
        "cwd": None,
        "env": None,
    },
    "shell": False,
    "timeout_seconds": 10,
    "check_output_calls": _harness_prewarm["check_output_calls"],
    "popen_events": _harness_prewarm["popen_events"],
    "hostname_events": _harness_prewarm["hostname_events"],
    "completion_observed": _harness_prewarm["completed"],
    "completion_source": "pinned subprocess.check_output returned normally with its default check=True behavior",
    "return_code": _harness_prewarm["return_code"],
    "stdout_character_count": _harness_prewarm["stdout_character_count"],
    "stdout_utf8_sha256": _harness_prewarm["stdout_utf8_sha256"],
    "platform_metadata_environment": _platform_metadata_env,
    "candidate_provider_action": False,
    "classification": "one harness-only local platform version probe; /d disables cmd AutoRun",
}

def _numpy_probe_run(*popenargs, **kwargs):
    caller = sys._getframe(1)
    module = sys.modules.get("numpy.testing._private.utils")
    if (
        _numpy_probe["run_calls"] != 0
        or _numpy_probe["active"]
        or module is None
        or Path(module.__file__).resolve() != _numpy_utils_path
        or module.__spec__ is None
        or module.__spec__.origin != str(_numpy_utils_path)
        or hashlib.sha256(_numpy_utils_path.read_bytes()).hexdigest() != _numpy_probe["source_sha256"]
        or caller.f_code.co_name != "check_support_sve"
        or caller.f_code.co_firstlineno != 1239
        or Path(caller.f_code.co_filename).resolve() != _numpy_utils_path
        or caller.f_globals is not module.__dict__
        or module.check_support_sve.__code__ is not caller.f_code
        or caller.f_locals.get("subprocess") is not subprocess
        or caller.f_locals.get("cmd") != "lscpu"
        or popenargs != ("lscpu",)
        or kwargs != {"capture_output": True, "text": True}
    ):
        _prewarm_deny("unexpected subprocess.run during installed imports")
    _numpy_probe["run_calls"] += 1
    _numpy_probe["active"] = True
    _numpy_probe["thread_id"] = threading.get_ident()
    try:
        completed = _real_subprocess_run(
            "lscpu",
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        _numpy_probe["outcome"] = "file_not_found"
        _numpy_probe["exception_type"] = type(exc).__name__
        _numpy_probe["process_created"] = False
        raise
    except subprocess.TimeoutExpired as exc:
        _numpy_probe["outcome"] = "timeout"
        _numpy_probe["exception_type"] = type(exc).__name__
        _numpy_probe["process_created"] = True
        raise
    except OSError as exc:
        _numpy_probe["outcome"] = "unexpected_oserror"
        _numpy_probe["exception_type"] = type(exc).__name__
        _numpy_probe["process_created"] = None
        raise RuntimeError("unexpected OSError from the exact NumPy local CPU probe") from None
    except Exception as exc:
        _numpy_probe["outcome"] = "unexpected_exception"
        _numpy_probe["exception_type"] = type(exc).__name__
        _numpy_probe["process_created"] = None
        raise
    else:
        if not isinstance(completed.stdout, str) or not isinstance(completed.stderr, str):
            _numpy_probe["outcome"] = "unexpected_result_type"
            _numpy_probe["exception_type"] = "CompletedProcessOutputNotText"
            _numpy_probe["process_created"] = True
            raise RuntimeError("NumPy local CPU probe did not return captured text")
        _numpy_probe["outcome"] = "completed"
        _numpy_probe["process_created"] = True
        _numpy_probe["return_code"] = completed.returncode
        _numpy_probe["stdout_character_count"] = len(completed.stdout)
        _numpy_probe["stdout_sha256"] = hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest()
        _numpy_probe["stderr_character_count"] = len(completed.stderr)
        _numpy_probe["stderr_sha256"] = hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest()
        _numpy_probe["stdout_contains_sve"] = "sve" in completed.stdout
        return completed
    finally:
        _numpy_probe["active"] = False
        _numpy_probe["thread_id"] = None

blocked = _harness_blocked
def deny(name, *args, **kwargs):
'''
PLATFORM_PREWARM_OUTPUT_OLD = '"versions": versions, "network_or_process_attempts": blocked}, sort_keys=True))'
PLATFORM_PREWARM_OUTPUT_NEW = '"versions": versions, "network_or_process_attempts": blocked, "harness_platform_prewarm": _harness_platform_prewarm}, sort_keys=True))'
CANDIDATE_SOCKET_GUARD_OLD = 'def audit(event, args):\n    if event.startswith("socket."):\n        deny(event)\n'
CANDIDATE_SOCKET_GUARD_NEW = '''def audit(event, args):
    if event.startswith("socket."):
        if _observe_local_ipv6_probe("second", event, args, sys._getframe(1)):
            return
        if event == "socket.gethostname" and args == ():
            _confirm_local_hostname(sys._getframe(1))
            return
        deny(event)
'''
PREWARM_SOCKET_GUARD_OLD = '''def _prewarm_audit(event, args):
    if event.startswith("socket."):
        if event == "socket.gethostname" and args == ():
'''
PREWARM_SOCKET_GUARD_NEW = '''def _prewarm_audit(event, args):
    if event.startswith("socket."):
        if _observe_local_ipv6_probe("first", event, args, sys._getframe(1)):
            return
        if event == "socket.gethostname" and args == ():
'''
LOCAL_IPV6_PROBE_HELPERS = r'''_socket_module_path = Path(socket.__file__).resolve()
assert _socket_module_path == Path(r"C:\Anaconda3\Lib\socket.py").resolve()
assert socket.__spec__ is not None and Path(socket.__spec__.origin).resolve() == _socket_module_path
assert _socket_module_path.stat().st_size == 37320
_socket_module_source = _socket_module_path.read_bytes()
_socket_module_sha256 = hashlib.sha256(_socket_module_source).hexdigest()
assert _socket_module_sha256 == "07f2010f98b7b90c237879c12f738e7305f786d7af4bc06606c4d77fd7fa9e1b"
_socket_module_lines = _socket_module_source.decode("utf-8").splitlines()
assert _socket_module_lines[219].strip() == "def __init__(self, family=-1, type=-1, proto=-1, fileno=None):"
assert _socket_module_lines[231].strip() == "_socket.socket.__init__(self, family, type, proto, fileno)"
assert type(socket.has_ipv6) is bool
_urllib3_connection_path = Path(r"D:\temp\it10-mig04-certlib-consumer-r1-20261016\containers\consumer\env\Lib\site-packages\urllib3\util\connection.py").resolve()
assert _urllib3_connection_path.is_file()
assert _urllib3_connection_path.stat().st_size == 4444
_urllib3_connection_source = _urllib3_connection_path.read_bytes()
_urllib3_connection_sha256 = hashlib.sha256(_urllib3_connection_source).hexdigest()
assert _urllib3_connection_sha256 == "2633bbdb69731e5ccb5cf4e4afd65605d86c7979cc5633126f50c92d5ad74a74"
_urllib3_connection_text = _urllib3_connection_source.decode("utf-8").replace("\r\n", "\n")
_urllib3_connection_lines = _urllib3_connection_text.splitlines()
assert _urllib3_connection_lines[113].strip() == "def _has_ipv6(host: str) -> bool:"
assert _urllib3_connection_lines[125].strip() == "sock = socket.socket(socket.AF_INET6)"
assert _urllib3_connection_lines[126].strip() == "sock.bind((host, 0))"
assert _urllib3_connection_lines[127].strip() == "has_ipv6 = True"
assert _urllib3_connection_lines[128].strip() == "except Exception:"
assert _urllib3_connection_lines[132].strip() == "sock.close()"
assert _urllib3_connection_lines[133].strip() == "return has_ipv6"
assert _urllib3_connection_lines[136].strip() == 'HAS_IPV6 = _has_ipv6("::1")'
_local_ipv6_probe = {
    "active": False,
    "phase": "installed_crossrepo_import",
    "socket_has_ipv6": socket.has_ipv6,
    "first_hook": {"socket_new_events": 0, "socket_bind_events": 0, "callchains": {}},
    "second_hook": {"socket_new_confirmations": 0, "socket_bind_confirmations": 0, "callchains": {}},
    "pending_event": None,
    "pending_frame": None,
    "pending_args": None,
    "pending_thread_id": None,
    "socket_object": None,
    "constructor_arguments": None,
    "same_object_for_bind": None,
}

def _ipv6_source_chain(event, args, caller_frame):
    module = sys.modules.get("urllib3.util.connection")
    function = None if module is None else getattr(module, "_has_ipv6", None)
    if (
        module is None
        or module.__name__ != "urllib3.util.connection"
        or module.__spec__ is None
        or Path(module.__spec__.origin).resolve() != _urllib3_connection_path
        or Path(module.__file__).resolve() != _urllib3_connection_path
        or function is None
        or function.__globals__ is not module.__dict__
        or function.__code__.co_name != "_has_ipv6"
        or function.__code__.co_firstlineno != 114
        or Path(function.__code__.co_filename).resolve() != _urllib3_connection_path
        or hashlib.sha256(_urllib3_connection_path.read_bytes()).hexdigest() != _urllib3_connection_sha256
    ):
        _prewarm_deny("installed urllib3 IPv6 probe source binding changed")
    if event == "socket.__new__":
        if len(args) != 4:
            _prewarm_deny("socket.__new__ audit argument shape changed")
        sock, family, sock_type, proto = args
        if (
            type(sock) is not socket.socket
            or type(family) not in (int, type(socket.AF_INET6))
            or family != socket.AF_INET6
            or type(sock_type) not in (int, type(socket.SOCK_STREAM))
            or sock_type != socket.SOCK_STREAM
            or type(proto) is not int
            or proto != 0
        ):
            _prewarm_deny("socket.__new__ was not the exact AF_INET6 stream probe")
        if caller_frame.f_code is not socket.socket.__init__.__code__:
            _prewarm_deny("socket.__new__ caller did not pass through the pinned stdlib socket.__init__")
        if (
            caller_frame.f_globals is not socket.__dict__
            or Path(caller_frame.f_code.co_filename).resolve() != _socket_module_path
            or caller_frame.f_lineno != 232
            or caller_frame.f_locals.get("self") is not sock
            or caller_frame.f_locals.get("family") != socket.AF_INET6
            or caller_frame.f_locals.get("type") != socket.SOCK_STREAM
            or caller_frame.f_locals.get("proto") != 0
            or caller_frame.f_locals.get("fileno") is not None
        ):
            _prewarm_deny("stdlib socket.__init__ arguments differ from the pinned constructor")
        urllib3_frame = caller_frame.f_back
        chain = "stdlib_socket_init_to_urllib3_has_ipv6"
        if (
            urllib3_frame is None
            or urllib3_frame.f_code is not function.__code__
            or urllib3_frame.f_globals is not module.__dict__
            or urllib3_frame.f_lineno != 126
            or urllib3_frame.f_locals.get("host") != "::1"
            or urllib3_frame.f_globals.get("socket") is not socket
        ):
            _prewarm_deny("AF_INET6 constructor was not the pinned urllib3 ::1 probe line")
        return chain
    if event == "socket.bind":
        if (
            len(args) != 2
            or args[0] is not _local_ipv6_probe["socket_object"]
            or type(args[1]) is not tuple
            or args[1] != ("::1", 0)
            or caller_frame.f_code is not function.__code__
            or caller_frame.f_globals is not module.__dict__
            or caller_frame.f_lineno != 127
            or caller_frame.f_locals.get("sock") is not args[0]
            or caller_frame.f_locals.get("host") != "::1"
            or caller_frame.f_globals.get("socket") is not socket
        ):
            _prewarm_deny("socket.bind was not the pinned urllib3 ::1 port-zero probe")
        return "urllib3_has_ipv6_loopback_bind"
    return None

def _observe_local_ipv6_probe(layer, event, args, caller_frame):
    if event not in {"socket.__new__", "socket.bind"} or not _local_ipv6_probe["active"]:
        return False
    chain = _ipv6_source_chain(event, args, caller_frame)
    thread_id = threading.get_ident()
    if layer == "first":
        if _local_ipv6_probe["pending_event"] is not None:
            _prewarm_deny("local IPv6 probe audit events overlapped")
        if event == "socket.__new__":
            if _local_ipv6_probe["first_hook"]["socket_new_events"] != 0 or _local_ipv6_probe["socket_object"] is not None:
                _prewarm_deny("duplicate or unexpected IPv6 socket construction")
            _local_ipv6_probe["first_hook"]["socket_new_events"] += 1
            _local_ipv6_probe["socket_object"] = args[0]
            _local_ipv6_probe["constructor_arguments"] = {
                "family": int(args[1]),
                "type": int(args[2]),
                "protocol": args[3],
            }
        else:
            if (
                _local_ipv6_probe["first_hook"]["socket_bind_events"] != 0
                or _local_ipv6_probe["socket_object"] is None
                or _local_ipv6_probe["second_hook"]["socket_new_confirmations"] != 1
            ):
                _prewarm_deny("IPv6 bind did not follow the confirmed exact socket object")
            _local_ipv6_probe["first_hook"]["socket_bind_events"] += 1
            _local_ipv6_probe["same_object_for_bind"] = args[0] is _local_ipv6_probe["socket_object"]
        _local_ipv6_probe["first_hook"]["callchains"][event] = chain
        _local_ipv6_probe["pending_event"] = event
        _local_ipv6_probe["pending_frame"] = caller_frame
        _local_ipv6_probe["pending_args"] = args
        _local_ipv6_probe["pending_thread_id"] = thread_id
        return True
    if layer != "second":
        _prewarm_deny("unknown IPv6 probe audit layer")
    pending_args = _local_ipv6_probe["pending_args"]
    if (
        _local_ipv6_probe["pending_event"] != event
        or _local_ipv6_probe["pending_frame"] is not caller_frame
        or _local_ipv6_probe["pending_thread_id"] != thread_id
        or _local_ipv6_probe["first_hook"]["callchains"].get(event) != chain
        or pending_args is None
        or args[0] is not pending_args[0]
        or args[1:] != pending_args[1:]
    ):
        _prewarm_deny("second audit hook did not confirm the same IPv6 probe event")
    if event == "socket.__new__":
        if _local_ipv6_probe["second_hook"]["socket_new_confirmations"] != 0 or args[0] is not _local_ipv6_probe["socket_object"]:
            _prewarm_deny("second audit hook changed the IPv6 socket object")
        _local_ipv6_probe["second_hook"]["socket_new_confirmations"] += 1
    else:
        if (
            _local_ipv6_probe["second_hook"]["socket_bind_confirmations"] != 0
            or args[0] is not _local_ipv6_probe["socket_object"]
            or _local_ipv6_probe["same_object_for_bind"] is not True
        ):
            _prewarm_deny("second audit hook changed the IPv6 bind object")
        _local_ipv6_probe["second_hook"]["socket_bind_confirmations"] += 1
    _local_ipv6_probe["second_hook"]["callchains"][event] = chain
    _local_ipv6_probe["pending_event"] = None
    _local_ipv6_probe["pending_frame"] = None
    _local_ipv6_probe["pending_args"] = None
    _local_ipv6_probe["pending_thread_id"] = None
    return True

def _finish_local_ipv6_probe(module):
    function = getattr(module, "_has_ipv6", None)
    if (
        function is None
        or function.__globals__ is not module.__dict__
        or function.__code__.co_name != "_has_ipv6"
        or Path(module.__spec__.origin).resolve() != _urllib3_connection_path
        or hashlib.sha256(_urllib3_connection_path.read_bytes()).hexdigest() != _urllib3_connection_sha256
    ):
        _prewarm_deny("urllib3 IPv6 probe return source changed")
    _local_ipv6_probe["active"] = False
    first = _local_ipv6_probe["first_hook"]
    second = _local_ipv6_probe["second_hook"]
    expected_new = 1 if _local_ipv6_probe["socket_has_ipv6"] else 0
    if (
        _local_ipv6_probe["pending_event"] is not None
        or _local_ipv6_probe["pending_frame"] is not None
        or _local_ipv6_probe["pending_args"] is not None
        or _local_ipv6_probe["pending_thread_id"] is not None
        or first["socket_new_events"] != expected_new
        or second["socket_new_confirmations"] != expected_new
        or first["socket_bind_events"] != second["socket_bind_confirmations"]
        or first["socket_bind_events"] not in (0, 1)
        or first["socket_bind_events"] > expected_new
        or second["callchains"] != first["callchains"]
    ):
        _prewarm_deny("two audit hooks disagree on the bounded local IPv6 probe")
    result = getattr(module, "HAS_IPV6", None)
    if type(result) is not bool or function.__code__.co_firstlineno != 114:
        _prewarm_deny("urllib3 HAS_IPV6 is not the original boolean probe result")
    sock = _local_ipv6_probe["socket_object"]
    fileno = None if sock is None else sock.fileno()
    if sock is not None and fileno != -1:
        _prewarm_deny("urllib3 local IPv6 probe left an open socket")
    if result and first["socket_bind_events"] != 1:
        _prewarm_deny("urllib3 reported IPv6 support without a real loopback bind attempt")
    if not _local_ipv6_probe["socket_has_ipv6"] and (first["socket_new_events"] or first["socket_bind_events"] or result):
        _prewarm_deny("urllib3 probe diverged from the real socket.has_ipv6 false path")
    return {
        "phase": _local_ipv6_probe["phase"],
        "caller_module": "urllib3.util.connection",
        "source_path": str(_urllib3_connection_path),
        "source_bytes": 4444,
        "source_sha256": _urllib3_connection_sha256,
        "wheel_name": "urllib3-2.8.0-py3-none-any.whl",
        "wheel_path": str(Path(r"D:\temp\it10-mig04-certlib-consumer-r1-20261016\wheelhouse\urllib3-2.8.0-py3-none-any.whl").resolve()),
        "wheel_sha256": "0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3",
        "wheel_member": "urllib3/util/connection.py",
        "wheel_member_bytes": 4444,
        "wheel_member_sha256": _urllib3_connection_sha256,
        "socket_source_path": str(_socket_module_path),
        "socket_source_bytes": 37320,
        "socket_source_sha256": _socket_module_sha256,
        "probe_host": "::1",
        "socket_has_ipv6": _local_ipv6_probe["socket_has_ipv6"],
        "constructor_arguments": _local_ipv6_probe["constructor_arguments"],
        "first_hook": first,
        "second_hook": second,
        "same_event_frame_and_thread_confirmed": first["socket_new_events"] == 1,
        "socket_object_created_event": first["socket_new_events"] == 1,
        "same_socket_object_for_bind": _local_ipv6_probe["same_object_for_bind"],
        "bind_event_count": first["socket_bind_events"],
        "bind_address": ["::1", 0] if first["socket_bind_events"] else None,
        "fileno_after_original_probe_return": fileno,
        "no_open_socket_after_probe": sock is None or fileno == -1,
        "close_verified_after_bind_attempt": bool(first["socket_bind_events"] == 1 and fileno == -1),
        "HAS_IPV6": result,
        "HAS_IPV6_type": type(result).__name__,
        "candidate_provider_action": False,
        "external_network_action": False,
        "network_absence_claim": False,
        "classification": "real installed urllib3 local IPv6 loopback capability probe; socket creation and optional ::1 port-zero bind are local OS activity, not provider traffic or proof of network absence",
    }
'''
NUMPY_PROBE_IMPORT_OLD = "import app, backtrader, bt_api_binance, certlib\n"
MODULES_ORIGIN_OLD = 'modules = {"app": app, "backtrader": backtrader,\n    "bt_api_binance": bt_api_binance, "certlib": certlib}\n'
MODULES_ORIGIN_NEW = 'modules = {"app": app, "backtrader": backtrader,\n    "bt_api_base": bt_api_base,\n    "bt_api_base.event_clock": _bt_api_base_event_clock,\n    "bt_api_binance": bt_api_binance, "certlib": certlib}\n'
NUMPY_PROBE_IMPORT_NEW = r'''assert "urllib3.util.connection" not in sys.modules
_local_ipv6_probe["active"] = True
import app
subprocess.run = _numpy_probe_run
try:
    import backtrader
finally:
    subprocess.run = _real_subprocess_run
assert subprocess.run is _real_subprocess_run
assert socket.gethostname is _real_gethostname
import bt_api_binance, certlib, bt_api_base
import bt_api_base.event_clock as _bt_api_base_event_clock
assert socket.gethostname is _real_gethostname
assert _hostname_metadata["first_hook_events"] == 1
assert _hostname_metadata["second_hook_confirmations"] == 1
assert _hostname_metadata["pending_frame"] is None
assert _hostname_metadata["pending_thread_id"] is None
_hostname_metadata["call_return_observed"] = hasattr(_bt_api_base_event_clock, "CLOCK_DOMAIN_ID")
assert _hostname_metadata["call_return_observed"] is True
_harness_local_socket_metadata = {
    "phase": "installed_crossrepo_import",
    "event": "socket.gethostname",
    "event_argument_count": 0,
    "first_hook_events": _hostname_metadata["first_hook_events"],
    "second_hook_confirmations": _hostname_metadata["second_hook_confirmations"],
    "caller_module": "bt_api_base.event_clock",
    "caller_file": _hostname_metadata["caller_file"],
    "caller_line": 10,
    "module_spec_origin": _hostname_metadata["module_spec_origin"],
    "call_return_observed": _hostname_metadata["call_return_observed"],
    "hostname_function_unpatched": socket.gethostname is _real_gethostname,
    "hostname_value_recorded": False,
    "candidate_provider_action": False,
    "network_absence_claim": False,
    "classification": "one source-bound process-local hostname metadata read during installed import; not a network operation and not evidence that no network activity occurred",
}
_numpy_utils_module_name = "numpy.testing._private.utils"
_numpy_utils_module = sys.modules.get(_numpy_utils_module_name)
assert _numpy_utils_module is not None
assert Path(_numpy_utils_module.__file__).resolve() == _numpy_utils_path
assert _numpy_utils_module.__spec__.origin == str(_numpy_utils_path)
assert hashlib.sha256(_numpy_utils_path.read_bytes()).hexdigest() == _numpy_probe["source_sha256"]
assert _numpy_probe["run_calls"] == 1 and _numpy_probe["popen_events"] == 1
assert not _numpy_probe["active"] and _numpy_probe["thread_id"] is None
assert _numpy_probe["outcome"] in {"file_not_found", "completed"}
_numpy_probe["supports_sve"] = _numpy_utils_module._SUPPORTS_SVE
if _numpy_probe["outcome"] == "file_not_found":
    assert _numpy_probe["exception_type"] == "FileNotFoundError"
    assert _numpy_probe["process_created"] is False
    assert _numpy_probe["supports_sve"] is False
else:
    assert _numpy_probe["process_created"] is True
    assert isinstance(_numpy_probe["return_code"], int)
    assert isinstance(_numpy_probe["stdout_contains_sve"], bool)
    assert _numpy_probe["supports_sve"] is _numpy_probe["stdout_contains_sve"]
_harness_local_process_probes = [{
    "caller_module": _numpy_utils_module_name,
    "caller_name": "check_support_sve",
    "caller_first_line": 1239,
    "source_path": str(_numpy_utils_path),
    "source_sha256": _numpy_probe["source_sha256"],
    "command_argument": "lscpu",
    "shell": False,
    "timeout_seconds": 10,
    "run_calls": _numpy_probe["run_calls"],
    "popen_audit_events": _numpy_probe["popen_events"],
    "popen_audit_event": _numpy_probe["popen_event"],
    "outcome": _numpy_probe["outcome"],
    "exception_type": _numpy_probe["exception_type"],
    "process_created": _numpy_probe["process_created"],
    "return_code": _numpy_probe["return_code"],
    "stdout_character_count": _numpy_probe["stdout_character_count"],
    "stdout_sha256": _numpy_probe["stdout_sha256"],
    "stderr_character_count": _numpy_probe["stderr_character_count"],
    "stderr_sha256": _numpy_probe["stderr_sha256"],
    "stdout_contains_sve": _numpy_probe["stdout_contains_sve"],
    "supports_sve": _numpy_probe["supports_sve"],
    "candidate_provider_action": False,
    "classification": "one harness-observed optional local CPU-feature probe during NumPy import; not a provider or account action",
}]
assert len(_harness_local_process_probes) == 1
'''
NUMPY_PROBE_OUTPUT_OLD = '"network_or_process_attempts": blocked, "harness_platform_prewarm": _harness_platform_prewarm'
NUMPY_PROBE_OUTPUT_NEW = '"blocked_network_or_process_attempts": blocked, "harness_platform_prewarm": _harness_platform_prewarm, "harness_local_process_probes": _harness_local_process_probes, "harness_local_socket_metadata": _harness_local_socket_metadata, "localIPv6probe": _harness_local_ipv6_probe'
IPV6_PROBE_FINISH_OLD = 'provider_path = Path(provider_module.__file__).resolve()\nassert any(provider_path.is_relative_to(root) for root in roots)\nassert not blocked, blocked\n'
IPV6_PROBE_FINISH_NEW = '''provider_path = Path(provider_module.__file__).resolve()
assert any(provider_path.is_relative_to(root) for root in roots)
_urllib3_connection_module = sys.modules.get("urllib3.util.connection")
assert _urllib3_connection_module is not None
_harness_local_ipv6_probe = _finish_local_ipv6_probe(_urllib3_connection_module)
assert not blocked, blocked
'''


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_pinned_json(path: Path, expected_sha256: str) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    require(sha256_bytes(raw) == expected_sha256, f"pin mismatch: {path}")
    value = json.loads(raw.decode("utf-8"))
    require(isinstance(value, dict), f"expected object: {path}")
    return value, raw


def verify_logged_command(command: dict, base: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for field in ("stdout", "stderr"):
        path = Path(command[field]).resolve()
        require(path.is_relative_to(base.resolve()), f"log escaped original root: {path}")
        digest = sha256_file(path)
        require(digest == command[f"{field}_sha256"], f"logged {field} changed: {path}")
        pins[field] = digest
    return pins


def preflight() -> tuple[dict, list[str], dict[str, object], dict[str, object], str]:
    require(sys.platform == "win32" and sys.version_info[:3] == (3, 11, 5), "expected original Windows CPython 3.11.5 runner")
    require(not OUTPUT.exists(), f"refusing to reuse supplement output: {OUTPUT}")
    require(sha256_file(PYTHON_PLATFORM_SOURCE) == PYTHON_PLATFORM_SOURCE_SHA256, "CPython platform.py source pin changed")
    require(sha256_file(PYTHON_SUBPROCESS_SOURCE) == PYTHON_SUBPROCESS_SOURCE_SHA256, "CPython subprocess.py source pin changed")
    require(PYTHON_SOCKET_SOURCE.stat().st_size == PYTHON_SOCKET_SOURCE_BYTES, "CPython socket.py source size changed")
    require(sha256_file(PYTHON_SOCKET_SOURCE) == PYTHON_SOCKET_SOURCE_SHA256, "CPython socket.py source pin changed")
    socket_source_lines = PYTHON_SOCKET_SOURCE.read_bytes().decode("utf-8").splitlines()
    require(socket_source_lines[219].strip() == "def __init__(self, family=-1, type=-1, proto=-1, fileno=None):", "stdlib socket constructor source changed")
    require(socket_source_lines[231].strip() == "_socket.socket.__init__(self, family, type, proto, fileno)", "stdlib socket constructor call changed")
    require(URLLIB3_SOURCE.stat().st_size == URLLIB3_SOURCE_BYTES, "installed urllib3 connection source size changed")
    installed_urllib3_source = URLLIB3_SOURCE.read_bytes()
    require(sha256_bytes(installed_urllib3_source) == URLLIB3_SOURCE_SHA256, "installed urllib3 connection source pin changed")
    urllib3_lines = installed_urllib3_source.decode("utf-8").splitlines()
    require(urllib3_lines[113].strip() == "def _has_ipv6(host: str) -> bool:", "urllib3 IPv6 probe function changed")
    require(urllib3_lines[125].strip() == "sock = socket.socket(socket.AF_INET6)", "urllib3 IPv6 constructor call changed")
    require(urllib3_lines[126].strip() == "sock.bind((host, 0))", "urllib3 IPv6 bind call changed")
    require(urllib3_lines[127].strip() == "has_ipv6 = True", "urllib3 IPv6 success projection changed")
    require(urllib3_lines[128].strip() == "except Exception:", "urllib3 IPv6 fallback changed")
    require(urllib3_lines[132].strip() == "sock.close()", "urllib3 IPv6 cleanup changed")
    require(urllib3_lines[133].strip() == "return has_ipv6", "urllib3 IPv6 return changed")
    require(urllib3_lines[136].strip() == 'HAS_IPV6 = _has_ipv6("::1")', "urllib3 import-time IPv6 probe changed")
    require(R3_SUPPLEMENT_SCRIPT.stat().st_size == R3_SUPPLEMENT_SCRIPT_BYTES, "R3 supplement source size changed")
    require(sha256_file(R3_SUPPLEMENT_SCRIPT) == R3_SUPPLEMENT_SCRIPT_SHA256, "R3 supplement source pin changed")
    r3_failure, r3_result_raw = load_pinned_json(R3_SUPPLEMENT_RESULT, R3_SUPPLEMENT_RESULT_SHA256)
    require(len(r3_result_raw) == R3_SUPPLEMENT_RESULT_BYTES, "R3 result size changed")
    require(r3_failure.get("status") == "HOLD_STEP06_FAILED", "unexpected R3 supplement status")
    require(r3_failure.get("formal_mig04_acceptance") is False, "R3 evidence must remain nonformal")
    require(r3_failure.get("script_sha256") == R3_SUPPLEMENT_SCRIPT_SHA256, "R3 result does not bind its script")
    r3_command = r3_failure.get("command", {})
    require(r3_command.get("exit_code") == 1 and r3_command.get("timed_out") is False, "R3 step-06 completion facts changed")
    require(Path(r3_command.get("argv", [""])[0]).resolve() == CONSUMER_PYTHON.resolve(), "R3 did not use the accepted consumer interpreter")
    r3_stdout = R3_SUPPLEMENT_ROOT / "logs" / "06-installed-crossrepo-origin.stdout.log"
    r3_stderr = R3_SUPPLEMENT_ROOT / "logs" / "06-installed-crossrepo-origin.stderr.log"
    require(Path(r3_command.get("stdout", "")).resolve() == r3_stdout.resolve(), "R3 stdout path binding changed")
    require(Path(r3_command.get("stderr", "")).resolve() == r3_stderr.resolve(), "R3 stderr path binding changed")
    require(r3_command.get("stdout_sha256") == R3_STDOUT_SHA256 and sha256_file(r3_stdout) == R3_STDOUT_SHA256, "R3 stdout pin changed")
    require(r3_command.get("stderr_sha256") == R3_STDERR_SHA256 and sha256_file(r3_stderr) == R3_STDERR_SHA256, "R3 stderr pin changed")
    require(r3_stderr.stat().st_size == R3_STDERR_BYTES, "R3 stderr size changed")
    r3_stderr_text = r3_stderr.read_text(encoding="utf-8").replace("\\", "/")
    require("numpy/testing/_private/utils.py\", line 1247, in check_support_sve" in r3_stderr_text, "R3 did not fail at the NumPy SVE local probe")
    require("subprocess.run(cmd, capture_output=True, text=True)" in r3_stderr_text, "R3 NumPy probe callsite changed")
    require("blocked external operation: subprocess.Popen" in r3_stderr_text, "R3 process denial changed")
    require(R7_SUPPLEMENT_SCRIPT.stat().st_size == R7_SUPPLEMENT_SCRIPT_BYTES, "R7 supplement source size changed")
    require(sha256_file(R7_SUPPLEMENT_SCRIPT) == R7_SUPPLEMENT_SCRIPT_SHA256, "R7 supplement source pin changed")
    r7_failure, r7_result_raw = load_pinned_json(R7_SUPPLEMENT_RESULT, R7_SUPPLEMENT_RESULT_SHA256)
    require(len(r7_result_raw) == R7_SUPPLEMENT_RESULT_BYTES, "R7 result size changed")
    require(r7_failure.get("status") == "HOLD_STEP06_FAILED", "unexpected R7 supplement status")
    require(r7_failure.get("formal_mig04_acceptance") is False, "R7 evidence must remain nonformal")
    require(r7_failure.get("script_sha256") == R7_SUPPLEMENT_SCRIPT_SHA256, "R7 result does not bind its script")
    r7_command = r7_failure.get("command", {})
    require(r7_command.get("exit_code") == 1 and r7_command.get("timed_out") is False, "R7 step-06 completion facts changed")
    r7_argv = r7_command.get("argv", [])
    require(len(r7_argv) == 4 and r7_argv[1:3] == ["-I", "-c"], "R7 step-06 argv shape changed")
    require(Path(r7_argv[0]).resolve() == CONSUMER_PYTHON.resolve(), "R7 did not use the accepted consumer interpreter")
    require(sha256_bytes(r7_argv[3].encode("utf-8")) == r7_command.get("supplement_code_sha256"), "R7 generated child source pin changed")
    r7_stdout = R7_SUPPLEMENT_ROOT / "logs" / "06-installed-crossrepo-origin.stdout.log"
    r7_stderr = R7_SUPPLEMENT_ROOT / "logs" / "06-installed-crossrepo-origin.stderr.log"
    require(Path(r7_command.get("stdout", "")).resolve() == r7_stdout.resolve(), "R7 stdout path binding changed")
    require(Path(r7_command.get("stderr", "")).resolve() == r7_stderr.resolve(), "R7 stderr path binding changed")
    require(r7_command.get("stdout_sha256") == R7_STDOUT_SHA256 and sha256_file(r7_stdout) == R7_STDOUT_SHA256, "R7 stdout pin changed")
    require(r7_command.get("stderr_sha256") == R7_STDERR_SHA256 and sha256_file(r7_stderr) == R7_STDERR_SHA256, "R7 stderr pin changed")
    require(r7_stderr.stat().st_size == R7_STDERR_BYTES, "R7 stderr size changed")
    r7_stderr_text = r7_stderr.read_text(encoding="utf-8").replace("\\", "/")
    require("<string>\", line 462, in <module>" in r7_stderr_text, "R7 did not reach its final blocked-event assertion")
    require("AssertionError: ['socket.__new__']" in r7_stderr_text, "R7 did not record the exact denied IPv6 constructor event")
    require(R8_SUPPLEMENT_SCRIPT.stat().st_size == R8_SUPPLEMENT_SCRIPT_BYTES, "R8 supplement source size changed")
    require(sha256_file(R8_SUPPLEMENT_SCRIPT) == R8_SUPPLEMENT_SCRIPT_SHA256, "R8 supplement source pin changed")
    r8_preflight, r8_preflight_raw = load_pinned_json(R8_SUPPLEMENT_PREFLIGHT, R8_SUPPLEMENT_PREFLIGHT_SHA256)
    require(len(r8_preflight_raw) == R8_SUPPLEMENT_PREFLIGHT_BYTES, "R8 preflight record size changed")
    require(r8_preflight.get("status") == "PREFLIGHT_OK_STEP06_ONLY", "R8 did not complete its own preflight")
    require(r8_preflight.get("formal_mig04_acceptance") is False, "R8 record must remain nonformal")
    require(r8_preflight.get("script_sha256") == R8_SUPPLEMENT_SCRIPT_SHA256, "R8 preflight record does not bind its script")
    r8_binding = r8_preflight.get("command_binding", {})
    require(r8_binding.get("supplement_code_sha256") == R8_CHILD_SHA256, "R8 preflight child-source pin changed")
    require(
        r8_binding.get("argv_prefix") == [str(CONSUMER_PYTHON.resolve()), "-I", "-c"],
        "R8 did not use the pinned long -c launch form",
    )
    require(R8_SUPPLEMENT_ROOT.is_dir(), "R8 output root is missing")
    r8_logs = R8_SUPPLEMENT_ROOT / "logs"
    require(r8_logs.is_dir() and list(r8_logs.iterdir()) == [], "R8 created child stdout/stderr logs despite the reported launch failure")
    require(not (R8_SUPPLEMENT_ROOT / "result.json").exists(), "R8 unexpectedly recorded a child result")
    require(not (R8_SUPPLEMENT_ROOT / "step06_installed_crossrepo_child.py").exists(), "R8 unexpectedly used a fixed child file")
    numpy_source = NUMPY_UTILS_SOURCE.read_bytes()
    require(len(numpy_source) == NUMPY_UTILS_SOURCE_BYTES, "installed NumPy utils source size changed")
    require(sha256_bytes(numpy_source) == NUMPY_UTILS_SOURCE_SHA256, "installed NumPy utils source pin changed")
    numpy_lines = numpy_source.decode("utf-8").splitlines()
    require(numpy_lines[NUMPY_SVE_CALLER_FIRST_LINE - 1].strip() == "def check_support_sve():", "NumPy probe function line changed")
    require(numpy_lines[1246].strip() == "output = subprocess.run(cmd, capture_output=True, text=True)", "NumPy probe command callsite changed")
    require(numpy_lines[1244].strip() == "cmd = 'lscpu'", "NumPy probe command changed")
    require(numpy_lines[1247].strip() == "return 'sve' in output.stdout", "NumPy probe result projection changed")
    require(numpy_lines[1248].strip() == "except OSError:", "NumPy missing-command behavior changed")
    require(numpy_lines[1249].strip() == "return False", "NumPy missing-command result changed")
    require(sha256_file(R1_SUPPLEMENT_SCRIPT) == R1_SUPPLEMENT_SCRIPT_SHA256, "R1 supplement source pin changed")
    r1_failure, _ = load_pinned_json(R1_SUPPLEMENT_RESULT, R1_SUPPLEMENT_RESULT_SHA256)
    require(r1_failure.get("status") == "HOLD_STEP06_FAILED", "unexpected R1 supplement status")
    require(r1_failure.get("formal_mig04_acceptance") is False, "R1 evidence must remain nonformal")
    require(r1_failure.get("script_sha256") == R1_SUPPLEMENT_SCRIPT_SHA256, "R1 result does not bind its script")
    require(r1_failure.get("command", {}).get("exit_code") == 1, "R1 step-06 exit changed")
    r1_stdout = R1_SUPPLEMENT_ROOT / "logs" / "06-installed-crossrepo-origin.stdout.log"
    r1_stderr = R1_SUPPLEMENT_ROOT / "logs" / "06-installed-crossrepo-origin.stderr.log"
    r1_command = r1_failure["command"]
    empty_sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    require(Path(r1_command["stdout"]).resolve() == r1_stdout.resolve(), "R1 stdout path binding changed")
    require(Path(r1_command["stderr"]).resolve() == r1_stderr.resolve(), "R1 stderr path binding changed")
    require(r1_command.get("stdout_sha256") == empty_sha256 and sha256_file(r1_stdout) == empty_sha256, "R1 stdout log changed")
    require(r1_command.get("stderr_sha256") == empty_sha256 and sha256_file(r1_stderr) == empty_sha256, "R1 stderr log changed")
    cleanup, _ = load_pinned_json(R1_CLEANUP_RECEIPT, R1_CLEANUP_RECEIPT_SHA256)
    require(cleanup.get("owned_remaining") == 0 and cleanup.get("remaining") == [], "R1 owned-process cleanup is not complete")
    require(cleanup.get("observed") == "2026-10-16T15:49:02.1487250+08:00" and cleanup.get("free_kib") == 18212152, "R1 final cleanup receipt changed")
    require(sha256_file(R1_CLEANUP_LOG) == R1_CLEANUP_LOG_SHA256, "R1 owned cleanup log changed")
    result, _ = load_pinned_json(ORIGINAL_RESULT, ORIGINAL_RESULT_SHA256)
    require(result.get("status") == "HOLD_COMBO_CHECK", "unexpected original combo status")
    require(result.get("formal_mig04_acceptance") is False, "original evidence must remain nonformal")
    commands = result.get("commands")
    require(isinstance(commands, list) and len(commands) == 6, "original command inventory changed")
    require([item.get("label") for item in commands] == ORIGINAL_LABELS, "original command labels changed")
    for index, command in enumerate(commands):
        expected_exit = 0 if index < 5 else 1
        require(command.get("exit_code") == expected_exit, f"unexpected original step {index + 1} exit")
        verify_logged_command(command, ORIGINAL_ROOT)

    outer_exit = ORIGINAL_OUTER / "exit.txt"
    outer_stderr = ORIGINAL_OUTER / "stderr.log"
    outer_stdout = ORIGINAL_OUTER / "stdout.log"
    require(sha256_file(outer_exit) == ORIGINAL_OUTER_EXIT_SHA256, "original outer exit pin changed")
    require(outer_exit.read_text(encoding="ascii").strip() == "2", "original outer exit was not 2")
    require(outer_stderr.read_text(encoding="utf-8") == "HOLD_COMBO_CHECK RuntimeError: 06-installed-crossrepo-origin exited 1\n", "original outer failure changed")
    require(outer_stdout.read_bytes() == b"", "original outer stdout changed")

    require(sha256_file(WHEELHOUSE_MANIFEST) == WHEELHOUSE_MANIFEST_SHA256, "121-wheel manifest pin changed")
    manifest = json.loads(WHEELHOUSE_MANIFEST.read_text(encoding="utf-8"))
    require(isinstance(manifest, dict) and len(manifest) == 121, "expected exact 121-wheel manifest")
    wheelhouse = ORIGINAL_ROOT / "wheelhouse"
    present_wheels = {path.name for path in wheelhouse.glob("*.whl")}
    require(present_wheels == set(manifest), "wheelhouse members differ from the accepted manifest")
    require(set(manifest).issuperset(DIRECT_WHEELS), "direct consumer wheels are absent from the manifest")
    for filename, expected in manifest.items():
        require(sha256_file(wheelhouse / filename) == expected, f"wheel pin changed: {filename}")
    for filename, expected in DIRECT_WHEELS.items():
        require(manifest[filename] == expected, f"direct wheel manifest pin changed: {filename}")
    require(manifest.get(URLLIB3_WHEEL.name) == URLLIB3_WHEEL_SHA256, "urllib3 wheel manifest pin changed")
    require(sha256_file(URLLIB3_WHEEL) == URLLIB3_WHEEL_SHA256, "urllib3 wheel source changed")
    with zipfile.ZipFile(URLLIB3_WHEEL) as urllib3_archive:
        require(URLLIB3_WHEEL_MEMBER in urllib3_archive.namelist(), "urllib3 wheel member is missing")
        urllib3_wheel_source = urllib3_archive.read(URLLIB3_WHEEL_MEMBER)
    require(len(urllib3_wheel_source) == URLLIB3_SOURCE_BYTES, "urllib3 wheel member size changed")
    require(sha256_bytes(urllib3_wheel_source) == URLLIB3_SOURCE_SHA256, "urllib3 wheel member source pin changed")
    require(urllib3_wheel_source == installed_urllib3_source, "installed urllib3 source differs from the pinned wheel member")
    require(sha256_file(DEPENDENCY_LOCK) == DEPENDENCY_LOCK_SHA256, "consumer dependency lock changed")

    dry = json.loads(Path(commands[3]["stdout"]).read_text(encoding="utf-8"))
    require(
        dry.get("case") == "MIG04"
        and dry.get("venue") == "binance"
        and dry.get("status") == "DRY"
        and dry.get("live") is False
        and dry.get("env_keys_missing") == []
        and dry.get("env_keys_optional_missing") == ["MIG04_DRY_OPTIONAL_KEY"],
        "original normal-entry dry result changed",
    )
    installed = json.loads(Path(commands[4]["stdout"]).read_text(encoding="utf-8"))
    require(installed == result.get("installed_certlib"), "step-05 result differs from the original result object")
    require(
        installed.get("distribution") == "bt-api-binance-certlib"
        and installed.get("version") == "0.1.0"
        and installed.get("PyYAML") == "6.0.3"
        and installed.get("owners") == ["bt-api-binance-certlib"]
        and installed.get("source_hashes") == EXPECTED_CERTLIB_SOURCES,
        "installed certlib provenance pins changed",
    )
    require(installed.get("default_env_exists") is False, "controlled .env was not absent in step 05")
    envfile = Path(installed["default_env_path"]).resolve()
    require(envfile == (ORIGINAL_ROOT / "containers" / ".env").resolve(), "default env path changed")
    require(not envfile.exists(), "controlled default .env appeared before step 06")
    site_roots = [Path(value).resolve() for value in installed["site_roots"]]
    require(CONSUMER_VENV.resolve() in site_roots, "step-05 venv root changed")
    for filename, expected in EXPECTED_CERTLIB_SOURCES.items():
        path = Path(installed["origins"]["certlib" if filename == "__init__.py" else "certlib." + filename[:-3]]).resolve()
        require(any(path.is_relative_to(root) for root in site_roots), f"installed source escaped venv: {path}")
        require(path.name == filename and sha256_file(path) == expected, f"installed source pin changed: {filename}")
    require(installed["origins"]["certlib"] == str((CONSUMER_VENV / "Lib" / "site-packages" / "certlib" / "__init__.py").resolve()), "installed certlib origin changed")

    expected_direct = {str((wheelhouse / name).resolve()) for name in DIRECT_WHEELS}
    install_wheels = {
        str(Path(value).resolve())
        for value in commands[1]["argv"]
        if isinstance(value, str) and value.lower().endswith(".whl")
    }
    require(install_wheels == expected_direct, "original install command does not bind the six accepted wheels")
    original_step = commands[5]
    argv = original_step["argv"]
    require(len(argv) == 4 and argv[1:3] == ["-I", "-c"], "original step-06 invocation shape changed")
    require(Path(argv[0]).resolve() == CONSUMER_PYTHON.resolve() and CONSUMER_PYTHON.is_file(), "fresh consumer interpreter changed")
    require(Path(original_step["cwd"]).resolve() == ORIGINAL_ROOT.resolve(), "original step-06 cwd changed")
    old_body = argv[3]
    require(sha256_bytes(old_body.encode("utf-8")) == EXPECTED_ORIGINAL_STEP06_BODY_SHA256, "original step-06 code pin changed")
    require(old_body.count(MODULE_PREWARM_OLD) == 1, "expected one exact import line for platform prewarm")
    require(old_body.count(PREWARM_INSERT_OLD) == 1, "expected one exact insertion point before the original guards")
    require(old_body.count(PLATFORM_PREWARM_OUTPUT_OLD) == 1, "expected one exact child-output projection")
    require(old_body.count(NUMPY_PROBE_IMPORT_OLD) == 1, "expected one exact candidate import insertion point")
    require(old_body.count(CANDIDATE_SOCKET_GUARD_OLD) == 1, "expected one exact candidate socket audit guard")
    require(old_body.count(MODULES_ORIGIN_OLD) == 1, "expected one exact installed-module origin map")
    amended_body = old_body.replace(MODULE_PREWARM_OLD, MODULE_PREWARM_NEW, 1)
    amended_body = amended_body.replace(PREWARM_INSERT_OLD, PREWARM_INSERT_NEW, 1)
    amended_body = amended_body.replace(CANDIDATE_SOCKET_GUARD_OLD, CANDIDATE_SOCKET_GUARD_NEW, 1)
    amended_body = amended_body.replace(PLATFORM_PREWARM_OUTPUT_OLD, PLATFORM_PREWARM_OUTPUT_NEW, 1)
    amended_body = amended_body.replace(MODULES_ORIGIN_OLD, MODULES_ORIGIN_NEW, 1)
    require(amended_body.count(NUMPY_PROBE_OUTPUT_OLD) == 1, "expected one platform-probe output projection after prewarm binding")
    amended_body = amended_body.replace(NUMPY_PROBE_OUTPUT_OLD, NUMPY_PROBE_OUTPUT_NEW, 1)
    amended_body = amended_body.replace(NUMPY_PROBE_IMPORT_OLD, NUMPY_PROBE_IMPORT_NEW, 1)
    require(amended_body.count(PREWARM_SOCKET_GUARD_OLD) == 1, "expected one exact first socket audit hook")
    amended_body = amended_body.replace(PREWARM_SOCKET_GUARD_OLD, PREWARM_SOCKET_GUARD_NEW, 1)
    ipv6_helper_marker = "def _process_audit_event(name):\n"
    require(amended_body.count(ipv6_helper_marker) == 1, "expected one exact shared probe helper insertion point")
    amended_body = amended_body.replace(ipv6_helper_marker, LOCAL_IPV6_PROBE_HELPERS + "\n" + ipv6_helper_marker, 1)
    require(amended_body.count(IPV6_PROBE_FINISH_OLD) == 1, "expected one exact final local probe validation point")
    amended_body = amended_body.replace(IPV6_PROBE_FINISH_OLD, IPV6_PROBE_FINISH_NEW, 1)
    require(amended_body != old_body, "local platform metadata prewarm was not applied")
    require(amended_body.count("def _observe_local_ipv6_probe(") == 1, "generated child omitted the single shared IPv6 audit classifier")
    require(amended_body.count("_observe_local_ipv6_probe(\"first\", event, args, sys._getframe(1))") == 1, "first audit hook does not use the IPv6 classifier")
    require(amended_body.count("_observe_local_ipv6_probe(\"second\", event, args, sys._getframe(1))") == 1, "second audit hook does not use the IPv6 classifier")
    require(amended_body.count('\"localIPv6probe\": _harness_local_ipv6_probe') == 1, "generated child did not emit its separate localIPv6probe record")
    compile(amended_body, "<static-MIG04-step06-R8-child>", "exec")
    amended_bytes = amended_body.encode("utf-8")
    r8_argv = [str(CONSUMER_PYTHON.resolve()), "-I", "-c", amended_body]
    r8_command_line = subprocess.list2cmdline(r8_argv)
    require(len(amended_bytes) == R8_CHILD_BYTES, "R8 generated child size changed")
    require(sha256_bytes(amended_bytes) == R8_CHILD_SHA256, "R8 generated child pin changed")
    require(len(r8_command_line) == R8_COMMAND_LINE_CHARS, "R8 serialized Windows command-line length changed")
    require(len(r8_command_line) > WINDOWS_CREATEPROCESS_COMMAND_LINE_LIMIT, "R8 long-command-line diagnosis no longer reproduces statically")
    candidate_argv = [str(CONSUMER_PYTHON.resolve()), "-I", str(CHILD_SOURCE.resolve())]
    candidate_command_line_chars = len(subprocess.list2cmdline(candidate_argv))
    require(candidate_command_line_chars < WINDOWS_CREATEPROCESS_COMMAND_LINE_LIMIT, "fixed child-file launch remains over the Windows command-line ceiling")
    child_tree = ast.parse(amended_body, filename="<static-R9-child-file-check>")
    require(
        not any(isinstance(node, ast.Name) and node.id == "__file__" for node in ast.walk(child_tree)),
        "generated child relies on -c __file__ semantics",
    )
    require(
        not any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
            and node.attr in {"argv", "path"}
            for node in ast.walk(child_tree)
        ),
        "generated child relies on -c sys.argv/sys.path semantics",
    )

    entry = ORIGINAL_ROOT / "consumer" / "binance_penetration" / "cases" / "MIG04" / "run.py"
    require(sha256_file(entry) == "4d8604f87996f566777c97c91c53b3aca1292a44271b3f1fc94b712a4f392920", "installed-entry fixture pin changed")
    lock_argv = commands[0]["argv"]
    require(Path(lock_argv[lock_argv.index("--requirement") + 1]).resolve() == DEPENDENCY_LOCK.resolve(), "download lock binding changed")
    require(not Path(ORIGINAL_ROOT / "containers" / ".env").exists(), "controlled .env appeared")

    command_binding = {
        "label": original_step["label"],
        "launch_mode": "isolated fixed child source file",
        "argv_prefix": candidate_argv[:2],
        "candidate_argv": candidate_argv,
        "child_source_path": str(CHILD_SOURCE.resolve()),
        "child_source_bytes": len(amended_bytes),
        "child_source_sha256": sha256_bytes(amended_bytes),
        "candidate_command_line_chars": candidate_command_line_chars,
        "r8_long_command_diagnosis": {
            "root_reported_tool_id": R8_ROOT_TOOL_ID,
            "root_reported_exit_code": 1,
            "root_reported_error": R8_ROOT_REPORTED_ERROR,
            "preflight_record_path": str(R8_SUPPLEMENT_PREFLIGHT.resolve()),
            "preflight_record_sha256": R8_SUPPLEMENT_PREFLIGHT_SHA256,
            "child_source_bytes": len(amended_bytes),
            "serialized_windows_command_line_chars": len(r8_command_line),
            "windows_createprocess_command_line_limit_chars": WINDOWS_CREATEPROCESS_COMMAND_LINE_LIMIT,
        },
        "original_code_sha256": EXPECTED_ORIGINAL_STEP06_BODY_SHA256,
        "supplement_code_sha256": sha256_bytes(amended_body.encode("utf-8")),
        "change": "preserve the accepted R6 platform, R4 NumPy, R7 installed event_clock and R8 source-bound urllib3 local IPv6 allowances; launch the exact pinned step-06 source from one fixed file because its -c command exceeded the Windows command-line ceiling",
        "prewarm_process_policy": "allow only the exact one Windows subprocess audit event (None, list2cmdline([absolute cmd.exe, /d, /c, ver]), None, None) inside the pinned platform ver call; the persistent hook rejects all other subprocess.Popen, os.system, os.startfile, os.exec, os.spawn and os.posix_spawn events; platform.machine's one hostname event remains harness-only and separately counted, while the installed-import hostname event is bound by candidate_socket_metadata_policy",
        "candidate_process_policy": "during installed imports, allow only the exact once-only NumPy SVE caller from the hash-pinned installed numpy/testing/_private/utils.py, with literal lscpu arguments, shell=False and ten-second timeout; audit-stack/thread/source checks bind its one Popen attempt; all other process starts remain blocked",
        "candidate_socket_metadata_policy": "allow the one exact source-bound bt_api_base.event_clock hostname metadata read, plus only the pinned urllib3 _has_ipv6 socket.__new__ and same-object bind(('::1', 0)) local capability sequence, each confirmed by both audit hooks on the same frame/thread; all connect, DNS, send, listen and other socket/process events remain blocked",
        "parent_timeout_seconds": STEP06_PARENT_TIMEOUT_SECONDS,
        "platform_ver_timeout_seconds": PLATFORM_VER_TIMEOUT_SECONDS,
        "numpy_local_probe_timeout_seconds": NUMPY_LOCAL_PROBE_TIMEOUT_SECONDS,
        "numpy_local_probe": {
            "source_path": str(NUMPY_UTILS_SOURCE.resolve()),
            "source_bytes": NUMPY_UTILS_SOURCE_BYTES,
            "source_sha256": NUMPY_UTILS_SOURCE_SHA256,
            "caller_name": "check_support_sve",
            "caller_first_line": NUMPY_SVE_CALLER_FIRST_LINE,
            "command_argument": "lscpu",
            "popen_audit_event": [None, "lscpu", None, None],
            "expected_keyword_arguments": {"capture_output": True, "text": True},
        },
        "local_ipv6_probe": {
            "installed_source_path": str(URLLIB3_SOURCE.resolve()),
            "installed_source_bytes": URLLIB3_SOURCE_BYTES,
            "installed_source_sha256": URLLIB3_SOURCE_SHA256,
            "wheel_name": URLLIB3_WHEEL.name,
            "wheel_path": str(URLLIB3_WHEEL.resolve()),
            "wheel_sha256": URLLIB3_WHEEL_SHA256,
            "wheel_member": URLLIB3_WHEEL_MEMBER,
            "wheel_member_sha256": URLLIB3_SOURCE_SHA256,
            "standard_library_socket_source_path": str(PYTHON_SOCKET_SOURCE.resolve()),
            "standard_library_socket_source_sha256": PYTHON_SOCKET_SOURCE_SHA256,
            "allowed_socket_events": ["socket.__new__", "socket.bind"],
            "family": "AF_INET6",
            "type": "SOCK_STREAM",
            "protocol": 0,
            "bind_address": ["::1", 0],
            "source_function": "urllib3.util.connection._has_ipv6",
            "source_function_first_line": 114,
        },
        "platform_source_sha256": PYTHON_PLATFORM_SOURCE_SHA256,
        "subprocess_source_sha256": PYTHON_SUBPROCESS_SOURCE_SHA256,
        "windows_cmd_path": str((Path(os.environ["SYSTEMROOT"]) / "System32" / "cmd.exe").resolve()),
        "platform_metadata_environment": {
            key: os.environ[key]
            for key in PLATFORM_METADATA_ENV_KEYS
            if key in os.environ
        },
        "platform_metadata_environment_keys_added": sorted(
            key for key in PLATFORM_METADATA_ENV_KEYS if key in os.environ
        ),
        "sanitized_environment_base_keys": sorted(
            {"PATH", "TEMP", "TMP"}
            | {key for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        ),
        "wheelhouse_manifest_sha256": WHEELHOUSE_MANIFEST_SHA256,
        "wheel_count": len(manifest),
        "installed_certlib_source_hashes": installed["source_hashes"],
        "consumer_venv_python": str(CONSUMER_PYTHON.resolve()),
        "cwd": str(Path(original_step["cwd"]).resolve()),
    }
    evidence = {
        "original_result": {
            "path": str(ORIGINAL_RESULT),
            "sha256": ORIGINAL_RESULT_SHA256,
            "status": result["status"],
            "formal_mig04_acceptance": result["formal_mig04_acceptance"],
        },
        "original_outer_invocation": {
            "path": str(ORIGINAL_OUTER),
            "exit_code": 2,
            "exit_sha256": ORIGINAL_OUTER_EXIT_SHA256,
            "stderr": outer_stderr.read_text(encoding="utf-8").strip(),
            "stdout_sha256": sha256_file(outer_stdout),
            "stderr_sha256": sha256_file(outer_stderr),
        },
        "prior_steps": [
            {
                "label": command["label"],
                "exit_code": command["exit_code"],
                "stdout_sha256": command["stdout_sha256"],
                "stderr_sha256": command["stderr_sha256"],
            }
            for command in commands[:5]
        ],
        "wheelhouse": {
            "manifest_sha256": WHEELHOUSE_MANIFEST_SHA256,
            "wheel_count": len(manifest),
            "consumer_dependency_lock_sha256": DEPENDENCY_LOCK_SHA256,
            "direct_wheels": DIRECT_WHEELS,
            "urllib3_probe_wheel": {
                "path": str(URLLIB3_WHEEL.resolve()),
                "sha256": URLLIB3_WHEEL_SHA256,
                "member": URLLIB3_WHEEL_MEMBER,
                "member_bytes": len(urllib3_wheel_source),
                "member_sha256": sha256_bytes(urllib3_wheel_source),
                "installed_path": str(URLLIB3_SOURCE.resolve()),
                "installed_sha256": sha256_bytes(installed_urllib3_source),
                "wheel_member_equals_installed_bytes": urllib3_wheel_source == installed_urllib3_source,
            },
        },
        "installed_certlib": {
            "distribution": installed["distribution"],
            "version": installed["version"],
            "origins": installed["origins"],
            "source_hashes": installed["source_hashes"],
            "default_env_path": installed["default_env_path"],
            "default_env_exists": installed["default_env_exists"],
        },
        "r1_diagnostic_and_cleanup": {
            "result_path": str(R1_SUPPLEMENT_RESULT),
            "result_sha256": R1_SUPPLEMENT_RESULT_SHA256,
            "result_status": r1_failure["status"],
            "step06_exit_code": r1_failure["command"]["exit_code"],
            "stdout_path": str(r1_stdout),
            "stdout_sha256": sha256_file(r1_stdout),
            "stderr_path": str(r1_stderr),
            "stderr_sha256": sha256_file(r1_stderr),
            "cleanup_receipt_path": str(R1_CLEANUP_RECEIPT),
            "cleanup_receipt_sha256": R1_CLEANUP_RECEIPT_SHA256,
            "owned_remaining": cleanup["owned_remaining"],
            "cleanup_log_path": str(R1_CLEANUP_LOG),
            "cleanup_log_sha256": R1_CLEANUP_LOG_SHA256,
            "r1_process_chain_observed_by_root": "cmd.exe /c ver -> conda.exe shell.cmd.exe activate C:\\anaconda3 -> python",
            "cleanup_scope_from_root_observation": "only this QA-owned process chain; unrelated PID 81428 and historical hosts were untouched",
        },
        "r3_step06_diagnostic": {
            "script_path": str(R3_SUPPLEMENT_SCRIPT),
            "script_sha256": R3_SUPPLEMENT_SCRIPT_SHA256,
            "result_path": str(R3_SUPPLEMENT_RESULT),
            "result_sha256": R3_SUPPLEMENT_RESULT_SHA256,
            "status": r3_failure["status"],
            "exit_code": r3_command["exit_code"],
            "timed_out": r3_command["timed_out"],
            "stdout_path": str(r3_stdout),
            "stdout_sha256": R3_STDOUT_SHA256,
            "stderr_path": str(r3_stderr),
            "stderr_sha256": R3_STDERR_SHA256,
            "first_denied_process_caller": "numpy.testing._private.utils.check_support_sve",
            "source_path": str(NUMPY_UTILS_SOURCE.resolve()),
            "source_sha256": NUMPY_UTILS_SOURCE_SHA256,
            "command": "lscpu",
            "static_disposition": "the installed pinned NumPy helper catches OSError and returns False; R4 only preserves genuine FileNotFoundError or genuine completed output and adds a ten-second timeout",
        },
        "r7_step06_diagnostic": {
            "script_path": str(R7_SUPPLEMENT_SCRIPT),
            "script_sha256": R7_SUPPLEMENT_SCRIPT_SHA256,
            "result_path": str(R7_SUPPLEMENT_RESULT),
            "result_sha256": R7_SUPPLEMENT_RESULT_SHA256,
            "status": r7_failure["status"],
            "exit_code": r7_command["exit_code"],
            "timed_out": r7_command["timed_out"],
            "stdout_path": str(r7_stdout),
            "stdout_sha256": R7_STDOUT_SHA256,
            "stderr_path": str(r7_stderr),
            "stderr_sha256": R7_STDERR_SHA256,
            "stderr_bytes": R7_STDERR_BYTES,
            "last_failure": "the final assertion reported only the unclassified socket.__new__ audit event",
            "source_disposition": "R8 adds a finite source-bound allowance for the installed urllib3 _has_ipv6 local IPv6 capability probe; R7 remains preserved as a failed consumed attempt",
        },
        "r8_step06_launch_failure": {
            "script_path": str(R8_SUPPLEMENT_SCRIPT),
            "script_sha256": R8_SUPPLEMENT_SCRIPT_SHA256,
            "preflight_path": str(R8_SUPPLEMENT_PREFLIGHT),
            "preflight_sha256": R8_SUPPLEMENT_PREFLIGHT_SHA256,
            "preflight_status": r8_preflight["status"],
            "root_reported_tool_id": R8_ROOT_TOOL_ID,
            "root_reported_exit_code": 1,
            "root_reported_error": R8_ROOT_REPORTED_ERROR,
            "result_json_present": False,
            "child_stdout_stderr_logs_present": False,
            "generated_child_bytes": R8_CHILD_BYTES,
            "serialized_command_line_chars": R8_COMMAND_LINE_CHARS,
            "windows_createprocess_command_line_limit_chars": WINDOWS_CREATEPROCESS_COMMAND_LINE_LIMIT,
            "static_disposition": "the R8 fixed -c launch serialized beyond the Windows CreateProcess command-line limit and failed before producing child logs; R9 keeps the exact child bytes and starts them from a fixed file",
        },
        "static_import_probe_inventory": {
            "scope": ["app", "backtrader", "bt_api_base", "bt_api_binance", "bt_api_py", "certlib", "numpy", "requests", "scipy", "statsmodels", "urllib3"],
            "import_time_process_probe": "the pinned NumPy check_support_sve lscpu invocation is allowed as before; this R8 change adds one source-pinned urllib3 local IPv6 socket capability probe, not a process invocation",
            "other_process_call_sites": "static rg hits in app Docker helpers, NumPy build/compiler/test helpers, SciPy build/test helpers, statsmodels X13 helpers and Backtrader notification helpers are function or test paths; R3 reaches the app import and then fails at the NumPy probe before later candidate imports",
            "local_socket_metadata_call_sites": "bt_api_base.event_clock and bt_api_py._normalization contain module-level socket.gethostname metadata reads; only the event_clock call is permitted. The installed urllib3 wheel also performs the pinned module-level _has_ipv6('::1') capability probe; R8 permits only that exact socket construction and optional loopback bind, leaving normalization and all other socket events blocked",
            "runtime_limit": "the installed import chain is source-bound to urllib3.util.connection._has_ipv6 and the standard-library socket constructor; both audit hooks independently confirm event count, call chain, same object, loopback address, closed/no-descriptor state and the unmodified real bool result",
            "no_allowance_for_other_processes": True,
            "remote_network_audit_remains_blocking": True,
            "local_loopback_socket_activity_is_reported": True,
            "network_absence_claim": False,
        },
    }
    return result, candidate_argv, command_binding, evidence, amended_body


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def execute() -> int:
    original, argv, binding, evidence, child_body = preflight()
    OUTPUT.mkdir()
    log_dir = OUTPUT / "logs"
    log_dir.mkdir()
    child_bytes = child_body.encode("utf-8")
    require(len(child_bytes) == binding["child_source_bytes"], "fixed child source byte count differs from preflight")
    require(sha256_bytes(child_bytes) == binding["child_source_sha256"], "fixed child source bytes differ from preflight pin")
    CHILD_SOURCE.write_bytes(child_bytes)
    require(CHILD_SOURCE.read_bytes() == child_bytes, "fixed child file readback differs from generated source")
    require(sha256_file(CHILD_SOURCE) == binding["child_source_sha256"], "fixed child file hash differs from generated source pin")
    child_source_sha256_at_launch = sha256_file(CHILD_SOURCE)
    preflight_record = {
        "status": "PREFLIGHT_OK_STEP06_ONLY",
        "formal_mig04_acceptance": False,
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "original_evidence": evidence,
        "command_binding": binding,
        "candidate_child_file": {
            "path": str(CHILD_SOURCE.resolve()),
            "bytes": len(child_bytes),
            "sha256": sha256_bytes(child_bytes),
            "encoding": "UTF-8 exact bytes; no newline translation",
        },
        "environment_policy": "sanitized PATH, TEMP, TMP and present SYSTEMROOT/WINDIR, plus only present parent PROCESSOR_ARCHITEW6432/PROCESSOR_ARCHITECTURE/PROCESSOR_IDENTIFIER values; no other inherited environment keys",
        "environment_binding": {
            "base_keys": binding["sanitized_environment_base_keys"],
            "added_platform_metadata_keys": binding["platform_metadata_environment_keys_added"],
            "platform_metadata_values": binding["platform_metadata_environment"],
            "final_keys": sorted(
                set(binding["sanitized_environment_base_keys"])
                | set(binding["platform_metadata_environment_keys_added"])
            ),
        },
        "execution_bounds": {
            "parent_step06_timeout_seconds": STEP06_PARENT_TIMEOUT_SECONDS,
            "platform_ver_child_timeout_seconds": PLATFORM_VER_TIMEOUT_SECONDS,
            "numpy_local_probe_child_timeout_seconds": NUMPY_LOCAL_PROBE_TIMEOUT_SECONDS,
            "child_tree_bound": "the consumer interpreter runs one fixed absolute child file whose exact bytes/hash are checked before launch; the prewarm permits only fixed /d cmd.exe built-in ver; installed imports permit the pinned shell=False NumPy lscpu probe and separately recorded source-bound urllib3 local IPv6 socket probe; all other process-start and socket events remain rejected",
            "timeout_cleanup": "each authorized subprocess.run uses its pinned timeout behavior, which kills and waits for its direct child on timeout; the parent step has a 120-second timeout",
        },
    }
    save_json(OUTPUT / "preflight.json", preflight_record)
    child_env = {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        "TEMP": str(OUTPUT),
        "TMP": str(OUTPUT),
    }
    for key in ("SYSTEMROOT", "WINDIR"):
        if key in os.environ:
            child_env[key] = os.environ[key]
    child_env.update(binding["platform_metadata_environment"])
    require(
        sorted(child_env)
        == preflight_record["environment_binding"]["final_keys"],
        "constructed child environment keys differ from the frozen environment binding",
    )
    stdout_path = log_dir / "06-installed-crossrepo-origin.stdout.log"
    stderr_path = log_dir / "06-installed-crossrepo-origin.stderr.log"
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            cwd=original["commands"][5]["cwd"],
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=STEP06_PARENT_TIMEOUT_SECONDS,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        exit_code: int | None = completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        raw_stdout = exc.stdout or b""
        raw_stderr = exc.stderr or b""
        stdout = raw_stdout.decode("utf-8", "replace") if isinstance(raw_stdout, bytes) else raw_stdout
        stderr = raw_stderr.decode("utf-8", "replace") if isinstance(raw_stderr, bytes) else raw_stderr
        exit_code = None
    child_source_sha256_after_completion = sha256_file(CHILD_SOURCE)
    child_source_matches_pin_after_completion = child_source_sha256_after_completion == binding["child_source_sha256"]
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    outcome: dict[str, object] = {
        **preflight_record,
        "command": {
            **binding,
            "argv": argv,
            "child_source_sha256_at_launch": child_source_sha256_at_launch,
            "child_source_sha256_after_completion": child_source_sha256_after_completion,
            "child_source_matches_pin_after_completion": child_source_matches_pin_after_completion,
            "cwd": original["commands"][5]["cwd"],
            "exit_code": exit_code,
            "timed_out": timed_out,
            "timeout_seconds": STEP06_PARENT_TIMEOUT_SECONDS,
            "timeout_cleanup": "subprocess.run killed and waited for the direct child" if timed_out else None,
            "stdout": str(stdout_path),
            "stdout_sha256": sha256_file(stdout_path),
            "stderr": str(stderr_path),
            "stderr_sha256": sha256_file(stderr_path),
        },
        "crossrepo": None,
        "status": (
            "HOLD_STEP06_CHILD_SOURCE_CHANGED"
            if not child_source_matches_pin_after_completion
            else "HOLD_STEP06_TIMEOUT"
            if timed_out
            else "HOLD_STEP06_FAILED"
        ),
    }
    if not child_source_matches_pin_after_completion:
        outcome["postcheck_error"] = "fixed child source hash changed during the bounded step-06 invocation"
    if child_source_matches_pin_after_completion and not timed_out and exit_code == 0:
        try:
            crossrepo = json.loads(stdout)
            expected_versions = {
                "ai-for-investor-backend": "0.1.0",
                "backtrader": "1.4.0",
                "bt_api_base": "0.15.8",
                "bt_api_binance": "2.0.1",
                "bt_api_py": "0.15.5",
                "bt-api-binance-certlib": "0.1.0",
            }
            require(crossrepo.get("versions") == expected_versions, "installed distribution versions differ from the six-wheel combo")
            require(crossrepo.get("blocked_network_or_process_attempts") == [], "step 06 observed a blocked socket or process operation")
            roots = [Path(value).resolve() for value in original["installed_certlib"]["site_roots"]]
            socket_metadata = crossrepo.get("harness_local_socket_metadata")
            require(isinstance(socket_metadata, dict), "step 06 omitted the local hostname metadata record")
            event_clock_origin = crossrepo.get("origins", {}).get("bt_api_base.event_clock")
            require(isinstance(event_clock_origin, str), "step 06 omitted the installed event_clock origin")
            require(
                socket_metadata
                == {
                    "phase": "installed_crossrepo_import",
                    "event": "socket.gethostname",
                    "event_argument_count": 0,
                    "first_hook_events": 1,
                    "second_hook_confirmations": 1,
                    "caller_module": "bt_api_base.event_clock",
                    "caller_file": event_clock_origin,
                    "caller_line": 10,
                    "module_spec_origin": event_clock_origin,
                    "call_return_observed": True,
                    "hostname_function_unpatched": True,
                    "hostname_value_recorded": False,
                    "candidate_provider_action": False,
                    "network_absence_claim": False,
                    "classification": "one source-bound process-local hostname metadata read during installed import; not a network operation and not evidence that no network activity occurred",
                },
                "local hostname metadata event record differs from the exact installed event_clock call",
            )
            ipv6_probe = crossrepo.get("localIPv6probe")
            require(isinstance(ipv6_probe, dict), "step 06 omitted the separate local IPv6 capability probe record")
            require(ipv6_probe.get("phase") == "installed_crossrepo_import", "IPv6 probe phase changed")
            require(ipv6_probe.get("caller_module") == "urllib3.util.connection", "IPv6 probe caller module changed")
            require(ipv6_probe.get("source_path") == str(URLLIB3_SOURCE.resolve()), "installed urllib3 source path changed")
            require(ipv6_probe.get("source_bytes") == URLLIB3_SOURCE_BYTES, "installed urllib3 source size changed")
            require(ipv6_probe.get("source_sha256") == URLLIB3_SOURCE_SHA256, "installed urllib3 source pin changed")
            require(ipv6_probe.get("wheel_name") == URLLIB3_WHEEL.name, "urllib3 wheel name changed")
            require(ipv6_probe.get("wheel_path") == str(URLLIB3_WHEEL.resolve()), "urllib3 wheel path changed")
            require(ipv6_probe.get("wheel_sha256") == URLLIB3_WHEEL_SHA256, "urllib3 wheel pin changed")
            require(ipv6_probe.get("wheel_member") == URLLIB3_WHEEL_MEMBER, "urllib3 wheel member changed")
            require(ipv6_probe.get("wheel_member_bytes") == URLLIB3_SOURCE_BYTES, "urllib3 wheel member size changed")
            require(ipv6_probe.get("wheel_member_sha256") == URLLIB3_SOURCE_SHA256, "urllib3 wheel member hash changed")
            require(ipv6_probe.get("socket_source_path") == str(PYTHON_SOCKET_SOURCE.resolve()), "stdlib socket.py path changed")
            require(ipv6_probe.get("socket_source_bytes") == PYTHON_SOCKET_SOURCE_BYTES, "stdlib socket.py size changed")
            require(ipv6_probe.get("socket_source_sha256") == PYTHON_SOCKET_SOURCE_SHA256, "stdlib socket.py pin changed")
            require(ipv6_probe.get("probe_host") == "::1", "IPv6 probe host changed")
            socket_has_ipv6 = ipv6_probe.get("socket_has_ipv6")
            require(type(socket_has_ipv6) is bool, "socket.has_ipv6 was not reported as its real boolean")
            first_hook = ipv6_probe.get("first_hook")
            second_hook = ipv6_probe.get("second_hook")
            require(isinstance(first_hook, dict) and isinstance(second_hook, dict), "IPv6 probe hook records are missing")
            require(set(first_hook) == {"socket_new_events", "socket_bind_events", "callchains"}, "first IPv6 hook record shape changed")
            require(set(second_hook) == {"socket_new_confirmations", "socket_bind_confirmations", "callchains"}, "second IPv6 hook record shape changed")
            first_new = first_hook.get("socket_new_events")
            first_bind = first_hook.get("socket_bind_events")
            second_new = second_hook.get("socket_new_confirmations")
            second_bind = second_hook.get("socket_bind_confirmations")
            require(type(first_new) is int and type(first_bind) is int, "first IPv6 audit hook counts are invalid")
            require(type(second_new) is int and type(second_bind) is int, "second IPv6 audit hook counts are invalid")
            first_chains = first_hook.get("callchains")
            second_chains = second_hook.get("callchains")
            require(isinstance(first_chains, dict) and first_chains == second_chains, "IPv6 audit hooks did not confirm the same source call chain")
            require(first_new == (1 if socket_has_ipv6 else 0) and second_new == first_new, "IPv6 constructor event counts differ from socket.has_ipv6 or between hooks")
            require(first_bind in (0, 1) and second_bind == first_bind and first_bind <= first_new, "IPv6 bind counts exceed the one real probe or differ between hooks")
            expected_chain_names = set()
            if first_new:
                expected_chain_names.add("socket.__new__")
                require(first_chains.get("socket.__new__") == "stdlib_socket_init_to_urllib3_has_ipv6", "IPv6 constructor source chain changed")
            if first_bind:
                expected_chain_names.add("socket.bind")
                require(first_chains.get("socket.bind") == "urllib3_has_ipv6_loopback_bind", "IPv6 bind source chain changed")
            require(set(first_chains) == expected_chain_names, "IPv6 event source-chain set differs from actual event counts")
            require(ipv6_probe.get("socket_object_created_event") is bool(first_new), "IPv6 object creation projection disagrees with hook counts")
            constructor_arguments = ipv6_probe.get("constructor_arguments")
            expected_constructor_arguments = (
                {"family": int(socket.AF_INET6), "type": int(socket.SOCK_STREAM), "protocol": 0}
                if first_new
                else None
            )
            require(constructor_arguments == expected_constructor_arguments, "IPv6 constructor parameters differ from AF_INET6/SOCK_STREAM/protocol 0")
            require(ipv6_probe.get("same_event_frame_and_thread_confirmed") is bool(first_new), "IPv6 hook frame/thread confirmation differs from observed constructor-event count")
            require(ipv6_probe.get("same_socket_object_for_bind") is (True if first_bind else None), "IPv6 bind did not use the exact audited socket object")
            require(ipv6_probe.get("bind_event_count") == first_bind, "IPv6 bind output count changed")
            require(ipv6_probe.get("bind_address") == (["::1", 0] if first_bind else None), "IPv6 bind address differs from the fixed loopback endpoint")
            fileno_after_probe = ipv6_probe.get("fileno_after_original_probe_return")
            require(fileno_after_probe == (-1 if first_new else None), "IPv6 probe socket was not closed or left no descriptor after return")
            require(ipv6_probe.get("no_open_socket_after_probe") is True, "IPv6 probe left an open socket")
            require(ipv6_probe.get("close_verified_after_bind_attempt") is bool(first_bind), "IPv6 original close verification differs from the bind path")
            ipv6_result = ipv6_probe.get("HAS_IPV6")
            require(type(ipv6_result) is bool and ipv6_probe.get("HAS_IPV6_type") == "bool", "urllib3 HAS_IPV6 real boolean result was changed or misreported")
            require(not ipv6_result or first_bind == 1, "urllib3 reported IPv6 support without a real bind attempt")
            require(socket_has_ipv6 or (first_new == 0 and first_bind == 0 and ipv6_result is False), "urllib3 probe diverged from the real no-IPv6 path")
            require(ipv6_probe.get("candidate_provider_action") is False, "local IPv6 probe was classified as provider activity")
            require(ipv6_probe.get("external_network_action") is False, "loopback capability probe was classified as external network activity")
            require(ipv6_probe.get("network_absence_claim") is False, "local probe was used to claim absence of network activity")
            require(
                ipv6_probe.get("classification")
                == "real installed urllib3 local IPv6 loopback capability probe; socket creation and optional ::1 port-zero bind are local OS activity, not provider traffic or proof of network absence",
                "local IPv6 probe classification changed",
            )
            require(any(URLLIB3_SOURCE.resolve().is_relative_to(root) for root in roots), "urllib3 probe source escaped the installed consumer venv")
            local_probes = crossrepo.get("harness_local_process_probes")
            require(isinstance(local_probes, list) and len(local_probes) == 1, "step 06 omitted its single harness-local NumPy probe record")
            numpy_probe = local_probes[0]
            require(numpy_probe.get("caller_module") == "numpy.testing._private.utils", "NumPy probe caller module changed")
            require(numpy_probe.get("caller_name") == "check_support_sve", "NumPy probe caller changed")
            require(numpy_probe.get("caller_first_line") == NUMPY_SVE_CALLER_FIRST_LINE, "NumPy probe caller line changed")
            require(numpy_probe.get("source_path") == str(NUMPY_UTILS_SOURCE.resolve()), "NumPy probe source path changed")
            require(numpy_probe.get("source_sha256") == NUMPY_UTILS_SOURCE_SHA256, "NumPy probe source pin changed")
            require(numpy_probe.get("command_argument") == "lscpu", "NumPy probe command changed")
            require(numpy_probe.get("shell") is False, "NumPy local probe used a shell")
            require(numpy_probe.get("timeout_seconds") == NUMPY_LOCAL_PROBE_TIMEOUT_SECONDS, "NumPy probe timeout changed")
            require(numpy_probe.get("run_calls") == 1 and numpy_probe.get("popen_audit_events") == 1, "NumPy probe did not have exactly one audited invocation")
            require(
                numpy_probe.get("popen_audit_event")
                == {"executable": None, "command_line": "lscpu", "cwd": None, "env_is_none": True},
                "NumPy probe Popen audit arguments differ from the pinned Windows string-argv semantics",
            )
            require(numpy_probe.get("candidate_provider_action") is False, "NumPy local probe was classified as a provider action")
            require(
                numpy_probe.get("classification")
                == "one harness-observed optional local CPU-feature probe during NumPy import; not a provider or account action",
                "NumPy local probe classification changed",
            )
            if numpy_probe.get("outcome") == "file_not_found":
                require(numpy_probe.get("exception_type") == "FileNotFoundError", "NumPy missing-command exception changed")
                require(numpy_probe.get("process_created") is False, "missing lscpu unexpectedly created a process")
                require(numpy_probe.get("return_code") is None, "missing lscpu unexpectedly returned a process code")
                require(numpy_probe.get("supports_sve") is False, "NumPy did not use its genuine OSError fallback")
            elif numpy_probe.get("outcome") == "completed":
                require(numpy_probe.get("exception_type") is None, "completed NumPy probe carried an exception")
                require(numpy_probe.get("process_created") is True, "completed local probe did not report its process")
                require(isinstance(numpy_probe.get("return_code"), int), "completed NumPy probe omitted its return code")
                require(isinstance(numpy_probe.get("stdout_character_count"), int), "completed NumPy probe omitted stdout count")
                require(isinstance(numpy_probe.get("stderr_character_count"), int), "completed NumPy probe omitted stderr count")
                require(isinstance(numpy_probe.get("stdout_sha256"), str) and len(numpy_probe["stdout_sha256"]) == 64, "completed NumPy probe stdout digest is missing")
                require(isinstance(numpy_probe.get("stderr_sha256"), str) and len(numpy_probe["stderr_sha256"]) == 64, "completed NumPy probe stderr digest is missing")
                require(isinstance(numpy_probe.get("stdout_contains_sve"), bool), "completed NumPy probe stdout projection is missing")
                require(numpy_probe.get("supports_sve") is numpy_probe.get("stdout_contains_sve"), "NumPy feature result differs from genuine stdout")
            else:
                require(False, "NumPy probe timed out or produced an unapproved exception")
            platform_prewarm = crossrepo.get("harness_platform_prewarm")
            require(isinstance(platform_prewarm, dict), "step 06 omitted harness platform prewarm evidence")
            require(platform_prewarm.get("machine") and isinstance(platform_prewarm.get("machine"), str), "platform machine value was not obtained")
            require(platform_prewarm.get("command") == binding["windows_cmd_path"], "platform ver command path changed")
            require(platform_prewarm.get("argv") == [binding["windows_cmd_path"], "/d", "/c", "ver"], "platform ver command arguments changed")
            expected_audit_command = subprocess.list2cmdline(
                [binding["windows_cmd_path"], "/d", "/c", "ver"]
            )
            require(
                platform_prewarm.get("audit_event")
                == {
                    "executable": None,
                    "command_line": expected_audit_command,
                    "cwd": None,
                    "env": None,
                },
                "platform ver Popen audit event differs from pinned Windows list2cmdline semantics",
            )
            require(platform_prewarm.get("shell") is False, "platform ver probe used a shell")
            require(platform_prewarm.get("timeout_seconds") == PLATFORM_VER_TIMEOUT_SECONDS, "platform ver timeout changed")
            require(platform_prewarm.get("check_output_calls") == 1 and platform_prewarm.get("popen_events") == 1, "platform prewarm did not use exactly one authorized cmd process")
            require(platform_prewarm.get("hostname_events") == 1, "platform prewarm did not use exactly one real hostname query")
            require(platform_prewarm.get("completion_observed") is True, "platform ver completion was not observed")
            require(
                platform_prewarm.get("completion_source")
                == "pinned subprocess.check_output returned normally with its default check=True behavior",
                "platform ver completion source changed",
            )
            require(platform_prewarm.get("return_code") == 0, "platform ver did not complete successfully")
            require(
                isinstance(platform_prewarm.get("stdout_character_count"), int)
                and platform_prewarm["stdout_character_count"] > 0
                and isinstance(platform_prewarm.get("stdout_utf8_sha256"), str)
                and len(platform_prewarm["stdout_utf8_sha256"]) == 64,
                "platform ver decoded stdout completion summary is incomplete",
            )
            require(
                platform_prewarm.get("platform_metadata_environment")
                == binding["platform_metadata_environment"],
                "platform metadata environment values differ from the exact parent values",
            )
            require(platform_prewarm.get("platform_source_sha256") == PYTHON_PLATFORM_SOURCE_SHA256, "platform.py runtime source changed")
            require(platform_prewarm.get("subprocess_source_sha256") == PYTHON_SUBPROCESS_SOURCE_SHA256, "subprocess.py runtime source changed")
            require(platform_prewarm.get("candidate_provider_action") is False, "harness platform probe was classified as a provider action")
            roots = [Path(value).resolve() for value in original["installed_certlib"]["site_roots"]]
            for name, value in crossrepo["origins"].items():
                require(any(Path(value).resolve().is_relative_to(root) for root in roots), f"module did not load from consumer venv: {name}")
            require(any(Path(crossrepo["provider_origin"]).resolve().is_relative_to(root) for root in roots), "provider module did not load from consumer venv")
            require(crossrepo["origins"]["certlib"] == original["installed_certlib"]["origins"]["certlib"], "cross-repo certlib origin differs from step 05")
            outcome["crossrepo"] = crossrepo
            outcome["status"] = "PASS_STEP06_INSTALLED_CROSSREPO_ORIGIN_WITH_RECORDED_LOCAL_PROBE"
        except Exception as exc:
            outcome["postcheck_error"] = type(exc).__name__ + ": " + str(exc)
            outcome["status"] = "HOLD_STEP06_POSTCHECK_FAILED"
    save_json(OUTPUT / "result.json", outcome)
    print(json.dumps({"result": str(OUTPUT / "result.json"), "status": outcome["status"], "step06_exit_code": exit_code, "timed_out": timed_out}, sort_keys=True))
    return 0 if outcome["status"] == "PASS_STEP06_INSTALLED_CROSSREPO_ORIGIN_WITH_RECORDED_LOCAL_PROBE" else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="run only original step 06 from the fixed, source-pinned child file")
    args = parser.parse_args()
    if not args.execute:
        print("inert; pass --execute only when Root schedules the supplement")
        return 0
    try:
        return execute()
    except Exception as exc:
        print(f"MIG04 step-06 supplement preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
