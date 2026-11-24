"""Reusable Windows harness for starting the five 007 services together.

Consolidates the synthetic deployment helpers proven during the 2026-11-18/19
Windows parity work.  Test/acceptance harness only: every key/config is
synthetic, every protected path gets an owner-only DACL (the Windows parity of
POSIX 0600), and no real .env / credential / account / venue is read or
contacted.

Services (all synthetic):
  * Monitor   - monitor_service_host CLI -> MONITOR_LISTENERS_READY
  * Runtime   - runtime_operator_cli CLI -> runtime_control_listening
  * Gateway   - real Gateway components (RiskScenarioEnv) or operator --dry-run
  * Risk      - risk_service_host CLI -> ready.json {"state":"serving"}
  * Execution - execution_parent_service_host CLI -> listener (UNREADY until a
                client registers, by design)

Usage from a script with a ``__main__`` guard (RiskScenarioEnv uses spawn)::

    from tests.integration._win11_service_harness import FiveServiceHarness
    with FiveServiceHarness() as h:
        h.start_core()          # Gateway + Risk (real process chain)
        h.start_monitor()
        h.start_runtime()
        h.start_execution()
        print(h.liveness())     # all five booleans

Off Windows this module is inert: helpers raise RuntimeError and the POSIX
fixtures remain the source of truth.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WINDOWS = os.name == "nt"

SDK_ROOT = Path(__file__).resolve().parents[2]
PYPATH_FILE = Path("D:/_iter63_test_stub/pypath.txt")
EXTRA_SRC = [
    str(SDK_ROOT / "bt_api/bt_api_runtime/runtime/src"),
    str(SDK_ROOT / "bt_api/bt_api_runtime/contracts/src"),
]
EP_STUB = Path("D:/temp/ep_stub")
GATEWAY_PROFILE = "ctp-local-readonly"


def _require_windows() -> None:
    if not WINDOWS:
        raise RuntimeError("Win11 service harness is Windows-only")


# ---------------------------------------------------------------------------
# Common utilities
# ---------------------------------------------------------------------------

def base_pythonpath() -> str:
    """Source-tree PYTHONPATH stub (semicolon-separated on Windows)."""
    if PYPATH_FILE.exists():
        return PYPATH_FILE.read_text().strip()
    return os.environ.get("PYTHONPATH", "")


def bootstrap_sys_path() -> None:
    """Make source packages win over stale site-packages installs.

    The stub is one semicolon-separated string; it must be split on os.pathsep
    before insertion, otherwise the stale installed bt_api_ctp (missing
    gateway_instrument_metadata) shadows the source tree.
    """
    _require_windows()
    parts = [p for p in base_pythonpath().split(os.pathsep) if p]
    ordered = parts + [str(SDK_ROOT), str(SDK_ROOT / "bt_api_py"), *EXTRA_SRC]
    for p in reversed(ordered):
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)


def subprocess_env(*, with_ep_stub: bool = False) -> dict:
    """Environment for child service processes (source tree first)."""
    _require_windows()
    parts = [base_pythonpath(), str(SDK_ROOT), str(SDK_ROOT / "bt_api_py"), *EXTRA_SRC]
    if with_ep_stub:
        parts.insert(0, str(EP_STUB))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def owner_only(path: Path) -> None:
    """Narrow a file/dir DACL to the owner SID (Windows parity of chmod 0600).

    Uses the proven _win32_security helper so the harness and the product's
    protected-read checks agree on what "private" means.
    """
    _require_windows()
    from bt_api_py._win32_security import _set_owner_only_acl
    _set_owner_only_acl(Path(path))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def register_gateway_profile_stub() -> Path:
    """Create a temp dist-info that registers the real source factory.

    Fixes the install-shape blocker (PROFILE_NOT_REGISTERED) without modifying
    the repo or requiring pip install -e.  Put the returned dir first on
    PYTHONPATH via ``subprocess_env(with_ep_stub=True)``.
    """
    _require_windows()
    d = EP_STUB / "bt_api_py_synthetic-0.0.dist-info"
    d.mkdir(parents=True, exist_ok=True)
    (d / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: bt-api-py-synthetic\nVersion: 0.0\n",
        encoding="utf-8")
    (d / "entry_points.txt").write_text(
        f"[bt_api_py.gateway_operator_profile_factories]\n"
        f"{GATEWAY_PROFILE} = bt_api_py.ctp_gateway_local_profile:profile_factory\n",
        encoding="utf-8")
    return EP_STUB


# ---------------------------------------------------------------------------
# Synthetic config builders (each returns (cmd, cwd, config_path))
# ---------------------------------------------------------------------------

def build_monitor(root: Path) -> tuple[list[str], Path]:
    """Synthetic Monitor config: CURVE keys with owner-only DACLs."""
    _require_windows()
    import zmq
    import yaml

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name in ("events", "heartbeat", "producer", "gateway"):
        public, secret = zmq.curve_keypair()
        for suffix, value in (("public", public), ("secret", secret)):
            p = root / f"{name}.{suffix}"
            p.write_bytes(value)
            owner_only(p)

    def listener(name, principal):
        return {
            "service_id": f"{name}-service", "principal_ref": f"{name}-principal",
            "instance_id": f"{name}-instance", "scope_ref": f"{name}-scope",
            "rpc_bind": f"tcp://127.0.0.1:{free_port()}",
            "public_key_file": f"{name}.public", "secret_key_file": f"{name}.secret",
            "principals": [{"principal_ref": principal, "actor_ref": f"{principal}-service",
                            "public_key_file": f"{principal}.public"}],
        }

    doc = {
        "schema": "bt_api_monitor.service.v1",
        "scope": {"execution_domain_ref": "synthetic-domain", "account_ref": "synthetic-account"},
        "storage": {"directory": "data", "outbox_scope": "events",
                    "event_types": ["synthetic.observation"]},
        "ingress": listener("events", "producer"),
        "heartbeat": listener("heartbeat", "gateway"),
    }
    cfg = root / "monitor.yaml"
    cfg.write_text(yaml.safe_dump(doc), encoding="utf-8")
    owner_only(cfg)
    cmd = [sys.executable, "-u", "-m", "bt_api_py.monitor_service_host", "run", "--config", str(cfg)]
    return cmd, cfg


def build_runtime(root: Path) -> tuple[list[str], Path]:
    """Synthetic Runtime config: direct unit + owner-only subject-token DACL."""
    _require_windows()
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    port = free_port()
    strat = root / "strategy_alpha"
    strat.mkdir(exist_ok=True)
    (strat / "run.py").write_text("# synthetic strategy\n", encoding="utf-8")
    runtime_cfg = {
        "schema": "bt_api_runtime/v1", "home": str(root / "runtime_home"),
        "api": {"transport": "http_json", "bind": "127.0.0.1", "port": port,
                "identity_mechanism": "subject_token"},
        "operators": [{"name": "alice", "role": "operator", "units": ["alpha"]}],
        "monitor_events": {"enabled": False},
        "units": [{"name": "alpha", "kind": "strategy", "dir": str(strat), "entry": "run.py",
                   "python": sys.executable, "access": "direct", "heartbeat_interval_s": 1,
                   "start_timeout_s": 10, "stop": {"graceful_timeout_s": 2, "kill_timeout_s": 1}}],
    }
    rp = root / "runtime.json"
    rp.write_text(json.dumps(runtime_cfg), encoding="utf-8")
    token = "synthetic-token-" + "a" * 32
    tokens = root / "tokens.json"
    tokens.write_text(json.dumps({
        "schema": "bt_api_py.runtime_subject_tokens.v1",
        "tokens": [{"token": token, "operator": "alice"}]}), encoding="utf-8")
    owner_only(tokens)
    cfg = root / "operator.json"
    cfg.write_text(json.dumps({
        "schema": "bt_api_py.runtime_operator.v1", "runtime_config_file": str(rp),
        "subject_token_file": str(tokens), "gateway": None, "monitor": None}), encoding="utf-8")
    cmd = [sys.executable, "-u", "-m", "bt_api_py.runtime_operator_cli", "run", "--config", str(cfg)]
    return cmd, cfg


def build_execution(root: Path) -> tuple[list[str], Path]:
    """Synthetic Execution parent config via the repo's proven fixture builder."""
    _require_windows()
    from tests.unit.test_iter65_execution_parent_service_host import setup_config

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    raw, endpoint, keys = setup_config(root)
    for p in (root / "journal", root / "registry"):
        if p.exists():
            owner_only(p)
    for name in ("server.pub", "server.key", "client.pub"):
        p = root / name
        if p.exists():
            owner_only(p)
    for grant in raw.get("strategy_grants", []):
        kp = grant.get("public_key_file")
        if kp and Path(kp).exists():
            owner_only(Path(kp))
    cfg = root / "config.json"
    cfg.write_text(json.dumps(raw), encoding="utf-8")
    owner_only(cfg)
    cmd = [sys.executable, "-u", "-m", "bt_api_py.execution_parent_service_host",
           "--config", str(cfg)]
    return cmd, cfg


def build_gateway_dryrun(root: Path) -> tuple[list[str], Path]:
    """Gateway operator --dry-run (offline config validation, no CTP contact)."""
    _require_windows()
    import yaml

    bootstrap_sys_path()
    from tests.integration.test_iter63_gateway_direct_service import _service_config

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    register_gateway_profile_stub()
    cfg = root / "launch.yaml"
    cfg.write_text(yaml.safe_dump({
        "gateway_operator_config_schema_version": 2,
        "profile_ref": GATEWAY_PROFILE,
        "service_config": _service_config(f"tcp://127.0.0.1:{free_port()}").to_public_dict(),
    }), encoding="utf-8")
    owner_only(cfg)
    cmd = [sys.executable, "-u", "-m", "bt_api_py.gateway_operator_cli",
           "--config", str(cfg), "--dry-run"]
    return cmd, cfg


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class _Child:
    def __init__(self, name: str, cmd: list[str], cwd: Path, env: dict,
                 ready_token: str | None = None):
        self.name, self.cmd, self.cwd, self.env = name, cmd, cwd, env
        self.ready_token = ready_token
        self.proc: subprocess.Popen | None = None
        self.stdout = self.stderr = ""

    def start(self) -> None:
        self.proc = subprocess.Popen(
            self.cmd, cwd=self.cwd, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace")

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.stdout, self.stderr = self.proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.stdout, self.stderr = self.proc.communicate()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def report(self) -> dict:
        return {
            "alive": self.alive,
            "ready_seen": bool(self.ready_token and self.ready_token in self.stdout),
            "stdout_tail": self.stdout[-300:],
            "stderr_tail": self.stderr[-200:],
        }


class FiveServiceHarness:
    """Start and observe the five 007 services with synthetic config."""

    def __init__(self, root: Path | None = None):
        _require_windows()
        self.root = Path(root or tempfile.mkdtemp(prefix="five_svc_win_"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.children: dict[str, _Child] = {}
        self.risk_env = None
        self._closed = False

    # -- self-contained CLI services ----------------------------------------
    def start_monitor(self, *, observe_s: float = 8) -> dict:
        cmd, _ = build_monitor(self.root / "monitor")
        child = _Child("monitor", cmd, self.root / "monitor", subprocess_env(),
                       ready_token="MONITOR_LISTENERS_READY")
        self.children[child.name] = child
        child.start()
        time.sleep(observe_s)
        return child.report()

    def start_runtime(self, *, observe_s: float = 12) -> dict:
        cmd, _ = build_runtime(self.root / "runtime")
        child = _Child("runtime", cmd, self.root / "runtime", subprocess_env(),
                       ready_token="runtime_control_listening")
        self.children[child.name] = child
        child.start()
        time.sleep(observe_s)
        return child.report()

    def start_execution(self, *, observe_s: float = 10) -> dict:
        cmd, _ = build_execution(self.root / "execution")
        child = _Child("execution", cmd, self.root / "execution", subprocess_env())
        self.children[child.name] = child
        child.start()
        time.sleep(observe_s)
        return child.report()

    def start_gateway_dryrun(self, *, observe_s: float = 8) -> dict:
        cmd, _ = build_gateway_dryrun(self.root / "gateway")
        child = _Child("gateway-dryrun", cmd, self.root / "gateway",
                       subprocess_env(with_ep_stub=True), ready_token='"status": "PASS"')
        self.children[child.name] = child
        child.start()
        time.sleep(observe_s)
        return child.report()

    # -- real Gateway + Risk chain ------------------------------------------
    def start_core(self, *, strategies=()):
        """Start the real Gateway components + Risk CLI via RiskScenarioEnv."""
        _require_windows()
        bootstrap_sys_path()
        from tests.integration._iter64_risk_fixture import RiskScenarioEnv

        risk_root = self.root / "risk"
        risk_root.mkdir(parents=True, exist_ok=True)
        self.risk_env = RiskScenarioEnv(risk_root, bootstrap=False)
        self.risk_env.start(strategies=strategies)
        return self.risk_env

    def liveness(self) -> dict:
        """Snapshot of all five services' process liveness."""
        out = {}
        if self.risk_env is not None:
            out["gateway"] = bool(self.risk_env.gateway and self.risk_env.gateway.is_alive())
            out["risk"] = bool(self.risk_env.risk and self.risk_env.risk.poll() is None)
        for name in ("monitor", "runtime", "execution"):
            if name in self.children:
                out[name] = self.children[name].alive
        return out

    def report(self) -> dict:
        """Full report including readiness tokens and output tails."""
        result = {name: child.report() for name, child in self.children.items()}
        if self.risk_env is not None:
            ready_path = self.root / "risk" / "risk" / "ready.json"
            result["risk_chain"] = {
                "gateway_alive": bool(self.risk_env.gateway and self.risk_env.gateway.is_alive()),
                "risk_alive": bool(self.risk_env.risk and self.risk_env.risk.poll() is None),
                "ready": json.loads(ready_path.read_text(encoding="utf-8")) if ready_path.exists() else None,
            }
        result["simultaneous"] = self.liveness()
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for child in self.children.values():
            child.stop()
        if self.risk_env is not None:
            self.risk_env.close()

    def __enter__(self) -> "FiveServiceHarness":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


__all__ = [
    "WINDOWS",
    "FiveServiceHarness",
    "base_pythonpath",
    "bootstrap_sys_path",
    "subprocess_env",
    "owner_only",
    "free_port",
    "register_gateway_profile_stub",
    "build_monitor",
    "build_runtime",
    "build_execution",
    "build_gateway_dryrun",
]
