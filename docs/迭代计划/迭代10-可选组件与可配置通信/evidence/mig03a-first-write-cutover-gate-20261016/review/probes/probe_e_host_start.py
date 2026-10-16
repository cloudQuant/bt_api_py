"""Probe E: host-level gate via ServiceHost.start() with real SQLite-adjacent state.

Independent probe (own config builder, no reuse of the author's test helpers):
1. no marker + default kwarg      -> pre-existing UNSUPPORTED_PUBLIC_PORT, no marker created
2. sealed + old epoch kwarg       -> ServiceHostError code REVERSE_MIGRATION_REFUSED, no side effects
3. sealed + destination epoch     -> passes gate, hits pre-existing UNSUPPORTED_PUBLIC_PORT
4. sealed + accepted reverse      -> old epoch passes gate, hits pre-existing failure
5. corrupt marker                 -> CUTOVER_MARKER_UNREADABLE surfaced via ServiceHostError
Exit 0 = all expected; prints JSON summary.
"""

from __future__ import annotations

import json
import os
import socket
import sys

sys.path.insert(0, os.environ.get("REVIEW_REPO", "D:/bt_api_py"))

import zmq  # noqa: E402
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope  # noqa: E402

from bt_api_py.service_config import parse_service_config  # noqa: E402
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority  # noqa: E402
from bt_api_py.service_migration_gate import (  # noqa: E402
    accept_reverse_migration,
    arm_migration_cutover,
    record_migration_first_write,
)
from bt_api_py.service_registry import PrincipalGrant  # noqa: E402

BASE = sys.argv[1]
os.makedirs(BASE, exist_ok=True)
failures: list[str] = []
checks: list[str] = []

OLD = "0f8d3b6c9a1e4f2d8b7a6c5d4e3f2a10"
NEW = "1a2b3c4d5e6f708192a3b4c5d6e7f801"
SCOPE = WireScope("paper:demo", account_ref="account-a", strategy_ref="strategy-a")


def endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{listener.getsockname()[1]}"


def host_config():
    raw = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "monitor-service",
        "scope_ref": "root-scope-v1",
        "transport_ref": "listener-v1",
        "registration_policy_ref": "registration-policy-v1",
        "service_requirements_ref": "service-requirements-v1",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "monitor-ingress",
                    "bind_ports": ["service.telemetry_ingress"],
                    "options": {},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint()},
                    "security_profile_ref": "listener-security-v1",
                    "options": {"high_water_mark": 32, "max_message_bytes": 65_536},
                },
            },
        },
    }
    return parse_service_config(raw)


def authority_for(config):
    client_public, _ = zmq.curve_keypair()
    server_public, server_secret = zmq.curve_keypair()
    return TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, "monitor-service", config.service_id),
        scope=SCOPE,
        scope_ref=config.scope_ref,
        scope_version="scope-v1",
        security_ref="listener-security-v1",
        security_version="security-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: "curve-principal"},
        grants={"curve-principal": (PrincipalGrant(WireIdentity(WireRole.STRATEGY_CLIENT, "curve-principal", "strategy-client-a"), ()),)},
    )


def make_host(state: str, epoch):
    config = host_config()
    calls: list[str] = []
    kwargs = {} if epoch is _UNSET else {"migration_deployment_epoch": epoch}
    host = ServiceHost(config, authority_for(config), state, lambda component, *_a: calls.append(component), **kwargs)
    return host, calls


_UNSET = object()


def start_and_capture(host) -> tuple[str, str]:
    try:
        host.start()
        return ("STARTED", host.state)
    except ServiceHostError as exc:
        return (exc.code, host.state)


def fresh_state(label: str) -> str:
    d = os.path.join(BASE, label)
    os.makedirs(d, exist_ok=True)
    state = os.path.join(d, "registry.sqlite3")
    with open(state, "wb") as fh:
        fh.write(b"old-state-bytes")
    return state


# 1. default kwarg, no marker
state = fresh_state("no-marker")
host, calls = make_host(state, _UNSET)
code, hstate = start_and_capture(host)
if code != "UNSUPPORTED_PUBLIC_PORT" or hstate != "failed" or calls:
    failures.append(f"1 no-marker default: code={code} state={hstate} calls={calls}")
elif os.path.exists(state + ".migration-cutover.json"):
    failures.append("1 no-marker default: marker unexpectedly created")
else:
    checks.append("1 no marker + default kwarg: pre-existing failure, no marker created")

# 2. sealed + old epoch
state = fresh_state("sealed-old")
arm_migration_cutover(state, source_epoch=OLD, new_epoch=NEW)
record_migration_first_write(state, fact_kind="dispatch", recorded_at="2026-10-16T00:00:00Z")
host, calls = make_host(state, OLD)
code, hstate = start_and_capture(host)
if code != "REVERSE_MIGRATION_REFUSED" or hstate != "failed" or calls:
    failures.append(f"2 sealed-old: code={code} state={hstate} calls={calls}")
elif open(state, "rb").read() != b"old-state-bytes":
    failures.append("2 sealed-old: state file mutated")
else:
    checks.append("2 sealed + old epoch: REVERSE_MIGRATION_REFUSED, no side effects, state intact")

# 3. sealed + destination epoch passes the gate (proceeds to pre-existing failure)
state = fresh_state("sealed-dest")
arm_migration_cutover(state, source_epoch=OLD, new_epoch=NEW)
record_migration_first_write(state, fact_kind="cursor", recorded_at="2026-10-16T00:00:00Z")
host, calls = make_host(state, NEW)
code, hstate = start_and_capture(host)
if code != "UNSUPPORTED_PUBLIC_PORT":
    failures.append(f"3 sealed-dest: gate did not pass through, code={code}")
else:
    checks.append("3 sealed + destination epoch: gate passthrough to pre-existing failure")

# 4. sealed + accepted reverse: old epoch passes gate
state = fresh_state("sealed-accepted")
arm_migration_cutover(state, source_epoch=OLD, new_epoch=NEW)
record_migration_first_write(state, fact_kind="order", recorded_at="2026-10-16T00:00:00Z")
accept_reverse_migration(state, accepted_epoch=OLD, reference="receipt-42")
host, calls = make_host(state, OLD)
code, hstate = start_and_capture(host)
if code != "UNSUPPORTED_PUBLIC_PORT":
    failures.append(f"4 sealed-accepted: gate did not pass after acceptance, code={code}")
else:
    checks.append("4 sealed + accepted reverse + old epoch: gate passthrough allowed")

# 5. corrupt marker fails closed at host level
state = fresh_state("corrupt")
with open(state + ".migration-cutover.json", "w", encoding="utf-8") as fh:
    fh.write("{broken")
host, calls = make_host(state, OLD)
code, hstate = start_and_capture(host)
if code != "CUTOVER_MARKER_UNREADABLE" or calls:
    failures.append(f"5 corrupt: code={code} calls={calls}")
else:
    checks.append("5 corrupt marker: CUTOVER_MARKER_UNREADABLE surfaced via ServiceHostError, no side effects")

# 6. invalid kwarg type validation (empty string / non-str)
try:
    config = host_config()
    ServiceHost(
        config,
        authority_for(config),
        os.path.join(BASE, "no-marker", "registry.sqlite3"),
        lambda *_a: None,
        migration_deployment_epoch="",
    )
    failures.append("6 empty epoch accepted")
except ServiceHostError as exc:
    if exc.code != "INVALID_DEPLOYMENT_EPOCH":
        failures.append(f"6 wrong code {exc.code}")
    else:
        checks.append("6 empty-string epoch rejected INVALID_DEPLOYMENT_EPOCH")

print(json.dumps({"checks": checks, "failures": failures}, indent=1))
sys.exit(1 if failures else 0)
