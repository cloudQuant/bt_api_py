"""Focused tests for the MIG03 increment-A migration cutover gate (11.3 step 6).

Covers the four required proofs:

* without a marker nothing changes (benign no-op passthrough);
* before the first new business write the controlled rollback path (old
  deployment/epoch start plus marker removal) is still allowed and untouched;
* after the first new business write the old deployment/epoch start is refused
  with the specific machine-checkable code ``REVERSE_MIGRATION_REFUSED``;
* the refusal is durable across a process restart (fresh interpreter) and the
  accepted cutover epoch still passes.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_migration_gate import (
    BUSINESS_WRITE_FACT_KINDS,
    REFUSAL_REVERSE_MIGRATION_REFUSED,
    MigrationGateError,
    accept_reverse_migration,
    arm_migration_cutover,
    enforce_migration_start,
    marker_path,
    read_migration_marker,
    record_migration_first_write,
    rollback_migration_cutover,
)
from bt_api_py.service_registry import PrincipalGrant

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Synthetic registry service_epoch facts: the old deployment epoch (host A)
# and the cutover/new owner epoch (host B, section 11.3 step 5).
_OLD_EPOCH = "0f8d3b6c9a1e4f2d8b7a6c5d4e3f2a10"
_NEW_EPOCH = "1a2b3c4d5e6f708192a3b4c5d6e7f801"

_SCOPE = WireScope("paper:demo", account_ref="account-a", strategy_ref="strategy-a")
_CLIENT_IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, "curve-principal", "strategy-client-a")


def _state(tmp_path: Path) -> Path:
    return tmp_path / "registry.sqlite3"


# ---------------------------------------------------------------------------
# Marker lifecycle (module-level gate)
# ---------------------------------------------------------------------------


def test_no_marker_is_a_benign_noop(tmp_path) -> None:
    state = _state(tmp_path)
    state.write_bytes(b"state-bytes")

    assert read_migration_marker(state) is None
    assert enforce_migration_start(state, deployment_epoch=_OLD_EPOCH) is None
    assert enforce_migration_start(state, deployment_epoch=None) is None
    assert record_migration_first_write(state, fact_kind="dispatch") is False
    assert rollback_migration_cutover(state, deployment_epoch=_OLD_EPOCH) is False

    assert not marker_path(state).exists()
    assert state.read_bytes() == b"state-bytes"
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["registry.sqlite3"]


def test_arm_is_durable_idempotent_and_conflicting_arm_refuses(tmp_path) -> None:
    state = _state(tmp_path)
    armed = arm_migration_cutover(state, source_epoch=_OLD_EPOCH, new_epoch=_NEW_EPOCH)

    assert armed.status == "COMMITTED"
    assert len(armed.migration_id) == 32
    assert marker_path(state).exists()
    assert read_migration_marker(state) == armed

    repeated = arm_migration_cutover(state, source_epoch=_OLD_EPOCH, new_epoch=_NEW_EPOCH)
    assert repeated == armed

    with pytest.raises(MigrationGateError) as conflict:
        arm_migration_cutover(state, source_epoch="f" * 32, new_epoch=_NEW_EPOCH)
    assert conflict.value.code == "CUTOVER_MARKER_CONFLICT"

    with pytest.raises(MigrationGateError) as same_epoch:
        arm_migration_cutover(state, source_epoch=_OLD_EPOCH, new_epoch=_OLD_EPOCH)
    assert same_epoch.value.code == "INVALID_MIGRATION_GATE_ARGUMENT"


def test_pre_first_write_old_epoch_start_allowed_and_rollback_restores_unchanged(
    tmp_path,
) -> None:
    state = _state(tmp_path)
    state.write_bytes(b"old-state")
    armed = arm_migration_cutover(state, source_epoch=_OLD_EPOCH, new_epoch=_NEW_EPOCH)

    # Rollback window: the old deployment/epoch start is permitted, unchanged,
    # and the start check itself is read-only.
    assert enforce_migration_start(state, deployment_epoch=_OLD_EPOCH) is None
    assert enforce_migration_start(state, deployment_epoch=None) is None
    assert read_migration_marker(state) == armed

    # Complete restore by the old complete deployment removes the marker.
    assert rollback_migration_cutover(state, deployment_epoch=_OLD_EPOCH) is True
    assert read_migration_marker(state) is None
    assert not marker_path(state).exists()
    assert state.read_bytes() == b"old-state"

    # Afterwards behavior is fully unchanged and the state can be re-armed.
    assert enforce_migration_start(state, deployment_epoch=_OLD_EPOCH) is None
    assert rollback_migration_cutover(state) is False
    rearmed = arm_migration_cutover(state, source_epoch=_OLD_EPOCH, new_epoch=_NEW_EPOCH)
    assert rearmed.status == "COMMITTED"


def test_first_business_write_seals_and_old_epoch_start_is_refused(tmp_path) -> None:
    state = _state(tmp_path)
    armed = arm_migration_cutover(state, source_epoch=_OLD_EPOCH, new_epoch=_NEW_EPOCH)

    assert {
        "dispatch",
        "order",
        "ownership",
        "reservation",
        "idempotency",
        "cursor",
    } == BUSINESS_WRITE_FACT_KINDS
    assert (
        record_migration_first_write(
            state, fact_kind="dispatch", recorded_at="2026-10-16T00:00:00Z"
        )
        is True
    )
    # Later writes are idempotent and keep the FIRST recorded fact.
    assert record_migration_first_write(state, fact_kind="reservation") is False
    marker = read_migration_marker(state)
    assert marker.sealed
    assert marker.first_write_fact_kind == "dispatch"
    assert marker.first_write_recorded_at == "2026-10-16T00:00:00Z"
    assert marker.migration_id == armed.migration_id
    assert marker.source_epoch == _OLD_EPOCH and marker.destination_epoch == _NEW_EPOCH

    # Re-arming the same cutover never unseals the marker.
    rearmed = arm_migration_cutover(state, source_epoch=_OLD_EPOCH, new_epoch=_NEW_EPOCH)
    assert rearmed == marker and rearmed.sealed

    with pytest.raises(MigrationGateError) as bad_kind:
        record_migration_first_write(state, fact_kind="telemetry")
    assert bad_kind.value.code == "INVALID_MIGRATION_GATE_ARGUMENT"

    # The declared cutover epoch may continue.
    assert enforce_migration_start(state, deployment_epoch=_NEW_EPOCH) is None

    # The old deployment/epoch (and any undeclared/unknown start) is refused
    # with the specific machine-checkable code; no accepted reverse migration
    # exists by default.
    for deployment_epoch in (_OLD_EPOCH, None, "unknown-epoch"):
        with pytest.raises(MigrationGateError) as refused:
            enforce_migration_start(state, deployment_epoch=deployment_epoch)
        assert refused.value.code == REFUSAL_REVERSE_MIGRATION_REFUSED

    # Rollback after the first write is refused by default as well.
    with pytest.raises(MigrationGateError) as rollback_refused:
        rollback_migration_cutover(state, deployment_epoch=_OLD_EPOCH)
    assert rollback_refused.value.code == REFUSAL_REVERSE_MIGRATION_REFUSED

    # Refusal attempts did not mutate the sealed state.
    assert read_migration_marker(state) == marker


def test_accepted_reverse_migration_is_required_to_restore_old_epoch(tmp_path) -> None:
    state = _state(tmp_path)
    arm_migration_cutover(state, source_epoch=_OLD_EPOCH, new_epoch=_NEW_EPOCH)
    record_migration_first_write(
        state, fact_kind="ownership", recorded_at="2026-10-16T00:00:01Z"
    )

    accepted = accept_reverse_migration(
        state, accepted_epoch=_OLD_EPOCH, reference="reverse-plan-2026-10-16"
    )
    assert accepted.accepted_reverse_epoch == _OLD_EPOCH
    assert accepted.accepted_reverse_reference == "reverse-plan-2026-10-16"
    assert accepted.sealed

    # The accepted reverse epoch may start again; unknown epochs stay refused.
    assert enforce_migration_start(state, deployment_epoch=_OLD_EPOCH) is None
    with pytest.raises(MigrationGateError) as still_refused:
        enforce_migration_start(state, deployment_epoch="unknown-epoch")
    assert still_refused.value.code == REFUSAL_REVERSE_MIGRATION_REFUSED

    # A wrong epoch cannot roll back even with an accepted reverse epoch.
    with pytest.raises(MigrationGateError):
        rollback_migration_cutover(state, deployment_epoch="unknown-epoch")

    # With the accepted reverse conversion, the complete rollback proceeds.
    assert rollback_migration_cutover(state, deployment_epoch=_OLD_EPOCH) is True
    assert read_migration_marker(state) is None

    with pytest.raises(MigrationGateError) as no_active:
        accept_reverse_migration(state, accepted_epoch=_OLD_EPOCH, reference="reverse-plan")
    assert no_active.value.code == "NO_ACTIVE_CUTOVER"


def test_corrupt_or_foreign_marker_fails_closed(tmp_path) -> None:
    state = _state(tmp_path)
    path = marker_path(state)
    path.write_text("{not json", encoding="utf-8")

    calls = (
        lambda: read_migration_marker(state),
        lambda: enforce_migration_start(state, deployment_epoch=_OLD_EPOCH),
        lambda: arm_migration_cutover(state, source_epoch=_OLD_EPOCH, new_epoch=_NEW_EPOCH),
        lambda: rollback_migration_cutover(state),
    )
    for call in calls:
        with pytest.raises(MigrationGateError) as unreadable:
            call()
        assert unreadable.value.code == "CUTOVER_MARKER_UNREADABLE"

    path.write_text(
        json.dumps({"schema_version": 99, "status": "COMMITTED"}), encoding="utf-8"
    )
    with pytest.raises(MigrationGateError) as foreign:
        enforce_migration_start(state, deployment_epoch=None)
    assert foreign.value.code == "CUTOVER_MARKER_UNREADABLE"


def _gate_probe(state: Path, deployment_epoch: str | None) -> dict[str, Any]:
    """Run the start gate and a rollback attempt in a fresh interpreter."""

    script = "\n".join(
        [
            "import json",
            "from bt_api_py.service_migration_gate import (",
            "    MigrationGateError,",
            "    enforce_migration_start,",
            "    rollback_migration_cutover,",
            ")",
            f"state = {str(state)!r}",
            f"epoch = {deployment_epoch!r}",
            "result = {}",
            "try:",
            "    enforce_migration_start(state, deployment_epoch=epoch)",
            "    result['start'] = None",
            "except MigrationGateError as exc:",
            "    result['start'] = exc.code",
            "try:",
            "    result['rollback'] = rollback_migration_cutover(state, deployment_epoch=epoch)",
            "except MigrationGateError as exc:",
            "    result['rollback'] = exc.code",
            "print(json.dumps(result))",
        ]
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(entry for entry in sys.path if entry)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(_REPO_ROOT),
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_refusal_and_rollback_refusal_are_durable_across_process_restart(tmp_path) -> None:
    state = _state(tmp_path)
    arm_migration_cutover(state, source_epoch=_OLD_EPOCH, new_epoch=_NEW_EPOCH)
    record_migration_first_write(
        state, fact_kind="cursor", recorded_at="2026-10-16T00:00:02Z"
    )
    sealed = read_migration_marker(state)

    # Fresh interpreter, same durable state: old epoch start AND rollback are
    # both refused with the specific code, and the state is untouched.
    assert _gate_probe(state, _OLD_EPOCH) == {
        "start": REFUSAL_REVERSE_MIGRATION_REFUSED,
        "rollback": REFUSAL_REVERSE_MIGRATION_REFUSED,
    }
    assert read_migration_marker(state) == sealed

    # The accepted cutover epoch still passes in a fresh interpreter; after a
    # seal even it may not silently roll the cutover back.
    assert _gate_probe(state, _NEW_EPOCH) == {
        "start": None,
        "rollback": REFUSAL_REVERSE_MIGRATION_REFUSED,
    }
    assert read_migration_marker(state) == sealed


# ---------------------------------------------------------------------------
# ServiceHost start wiring (the minimal production hook)
# ---------------------------------------------------------------------------


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    return f"tcp://127.0.0.1:{port}"


def _host_config() -> Any:
    raw: dict[str, Any] = {
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
                    "endpoints": {"rpc_bind": _endpoint()},
                    "security_profile_ref": "listener-security-v1",
                    "options": {"high_water_mark": 32, "max_message_bytes": 65_536},
                },
            },
        },
    }
    return parse_service_config(raw)


def _host_authority(config: Any) -> TrustedServiceAuthority:
    client_public, _client_secret = zmq.curve_keypair()
    server_public, server_secret = zmq.curve_keypair()
    return TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, "monitor-service", config.service_id),
        scope=_SCOPE,
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
        grants={"curve-principal": (PrincipalGrant(_CLIENT_IDENTITY, ()),)},
    )


def _start_host(state: Path, *, deployment_epoch: str | None):
    config = _host_config()
    authority = _host_authority(config)
    calls: list[str] = []
    host = ServiceHost(
        config,
        authority,
        state,
        lambda component, *_args: calls.append(component),
        migration_deployment_epoch=deployment_epoch,
    )
    return host, calls


def test_service_host_start_refuses_sealed_old_epoch_with_specific_code(tmp_path) -> None:
    state = _state(tmp_path)
    # The same state file the old deployment would open.
    state.write_bytes(b"old-state-bytes")
    arm_migration_cutover(state, source_epoch=_OLD_EPOCH, new_epoch=_NEW_EPOCH)
    record_migration_first_write(
        state, fact_kind="dispatch", recorded_at="2026-10-16T00:00:03Z"
    )

    host, calls = _start_host(state, deployment_epoch=_OLD_EPOCH)
    with pytest.raises(ServiceHostError) as refused:
        host.start()

    assert refused.value.code == REFUSAL_REVERSE_MIGRATION_REFUSED
    assert host.state == "failed"
    # The refusal precedes every side effect: no resources were resolved, the
    # protected state was not modified by the refused old deployment, and the
    # sealed marker is intact.
    assert calls == []
    assert state.read_bytes() == b"old-state-bytes"
    assert read_migration_marker(state).sealed


@pytest.mark.parametrize(
    ("marker", "deployment_epoch"),
    [
        (None, None),
        ("committed", _OLD_EPOCH),
        ("committed", None),
        ("sealed", _NEW_EPOCH),
    ],
)
def test_service_host_start_unchanged_without_marker_or_before_first_write(
    tmp_path, marker, deployment_epoch
) -> None:
    state = _state(tmp_path)
    if marker is not None:
        arm_migration_cutover(state, source_epoch=_OLD_EPOCH, new_epoch=_NEW_EPOCH)
        if marker == "sealed":
            record_migration_first_write(
                state, fact_kind="dispatch", recorded_at="2026-10-16T00:00:04Z"
            )

    host, calls = _start_host(state, deployment_epoch=deployment_epoch)
    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    # The gate did not interfere: the pre-existing early validation failure is
    # observed exactly as before the wiring (no marker => no behavior change;
    # pre-first-write rollback window and the declared cutover epoch pass too).
    assert exc_info.value.code == "UNSUPPORTED_PUBLIC_PORT"
    assert calls == []
    if marker is None:
        assert not marker_path(state).exists()
