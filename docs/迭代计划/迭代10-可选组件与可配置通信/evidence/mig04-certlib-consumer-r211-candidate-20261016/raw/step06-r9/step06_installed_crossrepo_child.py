
import importlib, importlib.metadata as md, json, hashlib, os, platform, socket, subprocess, sys, threading
from pathlib import Path
_harness_blocked = []
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

_socket_module_path = Path(socket.__file__).resolve()
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
        if _observe_local_ipv6_probe("first", event, args, sys._getframe(1)):
            return
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
    blocked.append(name)
    raise RuntimeError("blocked external operation: " + name)
def audit(event, args):
    if event.startswith("socket."):
        if _observe_local_ipv6_probe("second", event, args, sys._getframe(1)):
            return
        if event == "socket.gethostname" and args == ():
            _confirm_local_hostname(sys._getframe(1))
            return
        deny(event)
socket.create_connection = lambda *a, **k: deny("socket.create_connection")
socket.getaddrinfo = lambda *a, **k: deny("socket.getaddrinfo")
sys.addaudithook(audit)
assert "urllib3.util.connection" not in sys.modules
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
roots = [Path(value).resolve() for value in __import__("site").getsitepackages()]
modules = {"app": app, "backtrader": backtrader,
    "bt_api_base": bt_api_base,
    "bt_api_base.event_clock": _bt_api_base_event_clock,
    "bt_api_binance": bt_api_binance, "certlib": certlib}
origins = {name: str(Path(module.__file__).resolve()) for name, module in modules.items()}
for name, value in origins.items():
    assert any(Path(value).is_relative_to(root) for root in roots), (name, value)
entry_points = md.entry_points(group="bt_api.plugins")
matches = [item for item in entry_points if item.name == "binance"]
assert len(matches) == 1
provider = matches[0].load()
provider_module = importlib.import_module(provider.__module__)
provider_path = Path(provider_module.__file__).resolve()
assert any(provider_path.is_relative_to(root) for root in roots)
_urllib3_connection_module = sys.modules.get("urllib3.util.connection")
assert _urllib3_connection_module is not None
_harness_local_ipv6_probe = _finish_local_ipv6_probe(_urllib3_connection_module)
assert not blocked, blocked
versions = {name: md.version(name) for name in (
    "ai-for-investor-backend", "backtrader", "bt_api_base",
    "bt_api_binance", "bt_api_py", "bt-api-binance-certlib")}
print(json.dumps({"origins": origins, "provider_origin": str(provider_path),
    "provider_entry_point": matches[0].value,
    "versions": versions, "blocked_network_or_process_attempts": blocked, "harness_platform_prewarm": _harness_platform_prewarm, "harness_local_process_probes": _harness_local_process_probes, "harness_local_socket_metadata": _harness_local_socket_metadata, "localIPv6probe": _harness_local_ipv6_probe}, sort_keys=True))
