"""Focused checks for the fixed MIG03 inventory preflight and conservation contract."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from bt_api_py.service_migration_boundary import (
    CODE_BOUNDARY_INCOMPATIBLE,
    CODE_BOUNDARY_INCOMPLETE,
    CODE_BOUNDARY_NOT_CONSERVED,
    CODE_BOUNDARY_NOT_QUIESCENT,
    CODE_BOUNDARY_OLD_EPOCH_ACCEPTED,
    CODE_BOUNDARY_UNAVAILABLE,
    CODE_BOUNDARY_UNKNOWN_PARTICIPANT,
    CODE_BOUNDARY_INVALID,
    DISPOSITION_IN_PLACE_RETAINED,
    MigrationBoundaryError,
    ParticipantEvidence,
    ConsumerStopEvidence,
    StateConservation,
    arm_migration_boundary,
    assert_participant_states_conserved,
    assert_state_conserved,
    capture_sqlite_participant,
    consumer_inventory_sha256,
    not_applicable_participant,
    prepare_migration_boundary,
    require_old_epoch_refused_after_new_write,
)
from bt_api_py.service_migration_gate import marker_path

_CONFIG_SHA256 = "a" * 64
_STATE_SHA256 = "b" * 64
_IDENTITY_SHA256 = "c" * 64
_SCHEMA_SHA256 = "d" * 64


def _consumers() -> tuple[ConsumerStopEvidence, ...]:
    return (
        ConsumerStopEvidence(
            "old-strategy-client",
            201,
            close_acknowledged=True,
            exit_code=0,
            checkpoint_sha256=_IDENTITY_SHA256,
        ),
    )


def _participants() -> tuple[ParticipantEvidence, ...]:
    retained = tuple(
        ParticipantEvidence(
            participant_id=participant_id,
            disposition=DISPOSITION_IN_PLACE_RETAINED,
            source_ref=f"synthetic://{participant_id}",
            configuration_sha256=_CONFIG_SHA256,
            state_sha256=_STATE_SHA256,
            identity_sha256=(
                consumer_inventory_sha256(_consumers())
                if participant_id == "consumers"
                else _IDENTITY_SHA256
            ),
            schema_sha256=_SCHEMA_SHA256,
            row_count=1,
        )
        for participant_id in (
            "registry",
            "execution",
            "risk",
            "gateway",
        "consumers",
            "afi_operations",
            "afi_paper",
        )
    )
    return retained + (
        not_applicable_participant(
            "groups",
            evidence_ref="service-config://synthetic/no-group-component",
            configuration_sha256=_CONFIG_SHA256,
        ),
    )


def _plan(**changes):
    arguments = {
        "migration_id": "mig03-boundary-unit-r1",
        "source_epoch": "stable-old-deployment-r1",
        "destination_epoch": "stable-new-deployment-r1",
        "old_writer_pid": 201,
        "old_writer_exit_code": 0,
        "consumers": _consumers(),
        "participants": _participants(),
        "fence_reference": "synthetic-process-and-service-epoch-observation",
    }
    arguments.update(changes)
    return prepare_migration_boundary(**arguments)


def test_preflight_requires_every_fixed_boundary_category_before_arm(tmp_path: Path) -> None:
    state_path = tmp_path / "registry.sqlite3"
    state_path.write_bytes(b"synthetic state")
    marker = marker_path(state_path)
    participants = list(_participants())
    participants.pop()
    with pytest.raises(MigrationBoundaryError) as incomplete:
        _plan(participants=participants)
    assert incomplete.value.code == CODE_BOUNDARY_INCOMPLETE
    assert not marker.exists()

    with pytest.raises(MigrationBoundaryError) as unknown:
        _plan(
            participants=(*_participants()[:-1], replace(_participants()[0], participant_id="other"))
        )
    assert unknown.value.code == CODE_BOUNDARY_UNKNOWN_PARTICIPANT
    assert not marker.exists()

    unavailable = list(_participants())
    unavailable[1] = replace(unavailable[1], disposition="UNAVAILABLE")
    with pytest.raises(MigrationBoundaryError) as unavailable_error:
        _plan(participants=unavailable)
    assert unavailable_error.value.code == CODE_BOUNDARY_UNAVAILABLE
    assert not marker.exists()

    incompatible = list(_participants())
    incompatible[1] = replace(incompatible[1], disposition="INCOMPATIBLE")
    with pytest.raises(MigrationBoundaryError) as incompatible_error:
        _plan(participants=incompatible)
    assert incompatible_error.value.code == CODE_BOUNDARY_INCOMPATIBLE
    assert not marker.exists()

    valid = _plan()
    marker_record = arm_migration_boundary(state_path, valid)
    assert marker_record.source_epoch == "stable-old-deployment-r1"
    assert marker_record.destination_epoch == "stable-new-deployment-r1"
    assert marker.exists()


def test_preflight_requires_real_normal_exit_and_consumer_close_facts() -> None:
    with pytest.raises(MigrationBoundaryError) as old_writer_alive:
        _plan(old_writer_exit_code=3)
    assert old_writer_alive.value.code == CODE_BOUNDARY_NOT_QUIESCENT

    bad_consumer = (
        ConsumerStopEvidence("open-client", 202, close_acknowledged=False),
    )
    with pytest.raises(MigrationBoundaryError) as consumer_open:
        _plan(consumers=bad_consumer)
    assert consumer_open.value.code == CODE_BOUNDARY_NOT_QUIESCENT

    changed_consumer_set = (
        ConsumerStopEvidence(
            "different-client",
            202,
            close_acknowledged=True,
            exit_code=0,
            checkpoint_sha256=_IDENTITY_SHA256,
        ),
    )
    with pytest.raises(MigrationBoundaryError) as unbound_consumer:
        _plan(consumers=changed_consumer_set)
    assert unbound_consumer.value.code == CODE_BOUNDARY_NOT_QUIESCENT

    missing_receipt = ()
    with pytest.raises(MigrationBoundaryError) as no_consumer:
        _plan(consumers=missing_receipt)
    assert no_consumer.value.code == CODE_BOUNDARY_INCOMPLETE


def test_post_write_check_rejects_old_epoch_acceptance_and_missing_write_kind() -> None:
    plan = _plan()
    require_old_epoch_refused_after_new_write(
        plan,
        serving_epoch=plan.destination_epoch,
        first_write_fact_kind="dispatch",
        refusal_code="REVERSE_MIGRATION_REFUSED",
        resource_provider_calls=0,
    )
    with pytest.raises(MigrationBoundaryError) as old_accepted:
        require_old_epoch_refused_after_new_write(
            plan,
            serving_epoch=plan.destination_epoch,
            first_write_fact_kind="dispatch",
            refusal_code="STARTED",
            resource_provider_calls=0,
        )
    assert old_accepted.value.code == CODE_BOUNDARY_OLD_EPOCH_ACCEPTED
    with pytest.raises(MigrationBoundaryError) as invalid_kind:
        require_old_epoch_refused_after_new_write(
            plan,
            serving_epoch=plan.destination_epoch,
            first_write_fact_kind="query",
            refusal_code="REVERSE_MIGRATION_REFUSED",
            resource_provider_calls=0,
        )
    assert invalid_kind.value.code == CODE_BOUNDARY_OLD_EPOCH_ACCEPTED


def test_conservation_compares_ids_amounts_positions_unknown_and_stream_epoch() -> None:
    baseline = StateConservation(
        identity_links=(
            ("legacy_order", "old-order-1", "provider_order", "native-order-9"),
        ),
        amounts=(("filled_quantity", "2.000"), ("commission_usd", "0.03")),
        positions=(("account|SYNTH-USD", "2"),),
        unresolved_ids=("intent-unknown-1",),
        reservation_ids=("permit-1",),
        stream_cursors=(("consumer-1", "gateway-epoch-1", 3, 2),),
    )
    equivalent = StateConservation(
        identity_links=baseline.identity_links,
        amounts=(("filled_quantity", "2"), ("commission_usd", "0.0300")),
        positions=baseline.positions,
        unresolved_ids=baseline.unresolved_ids,
        reservation_ids=baseline.reservation_ids,
        stream_cursors=baseline.stream_cursors,
    )
    assert_state_conserved(baseline, equivalent)

    changed_fill = replace(equivalent, amounts=(("filled_quantity", "1"),))
    with pytest.raises(MigrationBoundaryError) as amount_mismatch:
        assert_state_conserved(baseline, changed_fill)
    assert amount_mismatch.value.code == CODE_BOUNDARY_NOT_CONSERVED

    changed_cursor_epoch = replace(
        equivalent,
        stream_cursors=(("consumer-1", "new-stream-epoch", 3, 2),),
    )
    with pytest.raises(MigrationBoundaryError) as cursor_mismatch:
        assert_state_conserved(baseline, changed_cursor_epoch)
    assert cursor_mismatch.value.code == CODE_BOUNDARY_NOT_CONSERVED


def test_control_store_changes_keep_registry_identities_and_consumer_snapshot() -> None:
    before = _participants()
    after = tuple(
        replace(
            item,
            state_sha256="e" * 64,
        )
        if item.participant_id == "registry"
        else item
        for item in before
    )
    assert_participant_states_conserved(before, after)

    changed_registry_identity = tuple(
        replace(item, identity_sha256="f" * 64)
        if item.participant_id == "registry"
        else item
        for item in after
    )
    with pytest.raises(MigrationBoundaryError) as registry_drift:
        assert_participant_states_conserved(before, changed_registry_identity)
    assert registry_drift.value.code == CODE_BOUNDARY_NOT_CONSERVED

    changed_consumer_snapshot = tuple(
        replace(item, state_sha256="0" * 64)
        if item.participant_id == "consumers"
        else item
        for item in before
    )
    with pytest.raises(MigrationBoundaryError) as consumer_drift:
        assert_participant_states_conserved(before, changed_consumer_snapshot)
    assert consumer_drift.value.code == CODE_BOUNDARY_NOT_CONSERVED

    duplicate_participant = (*before, before[0])
    with pytest.raises(MigrationBoundaryError) as duplicate:
        assert_participant_states_conserved(before, duplicate_participant)
    assert duplicate.value.code == CODE_BOUNDARY_INCOMPATIBLE


def test_amount_conservation_keeps_precision_beyond_decimal_context() -> None:
    amount = "123456789012345678901234567890.123456789012345678901"
    equivalent = amount + "000"
    baseline = StateConservation((), (("fill", amount),), (), (), (), ())
    assert_state_conserved(
        baseline,
        StateConservation((), (("fill", equivalent),), (), (), (), ()),
    )
    with pytest.raises(MigrationBoundaryError) as precision_change:
        assert_state_conserved(
            baseline,
            StateConservation(
                (),
                (("fill", "123456789012345678901234567890.123456789012345678902"),),
                (),
                (),
                (),
                (),
            ),
        )
    assert precision_change.value.code == CODE_BOUNDARY_NOT_CONSERVED


def test_sqlite_snapshot_is_read_only_bounded_and_schema_pinned(tmp_path: Path) -> None:
    path = tmp_path / "execution.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE execution_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE execution_records (
            intent_id TEXT PRIMARY KEY, state TEXT NOT NULL, provider_order_id TEXT,
            payload BLOB NOT NULL
        );
        CREATE TABLE execution_outbox (
            sequence INTEGER PRIMARY KEY, intent_id TEXT NOT NULL, event_type TEXT NOT NULL
        );
        INSERT INTO execution_meta VALUES ('schema_version', '23');
        INSERT INTO execution_records VALUES ('intent-1', 'UNKNOWN', NULL, x'0001ff');
        INSERT INTO execution_outbox VALUES (1, 'intent-1', 'accepted');
        """
    )
    connection.commit()
    connection.close()
    before_bytes = hashlib.sha256(path.read_bytes()).hexdigest()
    first = capture_sqlite_participant(
        "execution",
        path,
        configuration_sha256=_CONFIG_SHA256,
    )
    second = capture_sqlite_participant(
        "execution",
        path,
        configuration_sha256=_CONFIG_SHA256,
        expected_schema_sha256=first.schema_sha256,
    )
    after_bytes = hashlib.sha256(path.read_bytes()).hexdigest()
    assert first.evidence.state_sha256 == second.evidence.state_sha256
    assert first.evidence.row_count == 3
    assert before_bytes == after_bytes

    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE execution_records SET payload = x'0002ff' "
            "WHERE intent_id = 'intent-1'"
        )
        connection.commit()
    finally:
        connection.close()
    changed = capture_sqlite_participant(
        "execution",
        path,
        configuration_sha256=_CONFIG_SHA256,
        expected_schema_sha256=first.schema_sha256,
    )
    assert changed.evidence.state_sha256 != first.evidence.state_sha256

    with pytest.raises(MigrationBoundaryError) as schema_drift:
        capture_sqlite_participant(
            "execution",
            path,
            configuration_sha256=_CONFIG_SHA256,
            expected_schema_sha256="0" * 64,
        )
    assert schema_drift.value.code == CODE_BOUNDARY_INCOMPATIBLE
    with pytest.raises(MigrationBoundaryError) as unknown_store:
        capture_sqlite_participant(
            "unknown",
            path,
            configuration_sha256=_CONFIG_SHA256,
        )
    assert unknown_store.value.code == CODE_BOUNDARY_UNKNOWN_PARTICIPANT
    with pytest.raises(MigrationBoundaryError) as missing_store:
        capture_sqlite_participant(
            "risk",
            tmp_path / "missing.sqlite3",
            configuration_sha256=_CONFIG_SHA256,
        )
    assert missing_store.value.code == CODE_BOUNDARY_UNAVAILABLE

    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE INDEX execution_records_by_state ON execution_records(state)"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(MigrationBoundaryError) as auxiliary_schema_drift:
        capture_sqlite_participant(
            "execution",
            path,
            configuration_sha256=_CONFIG_SHA256,
            expected_schema_sha256=first.schema_sha256,
        )
    assert auxiliary_schema_drift.value.code == CODE_BOUNDARY_INCOMPATIBLE


def test_external_participant_requires_schema_and_identity_pins() -> None:
    with pytest.raises(MigrationBoundaryError) as malformed:
        _plan(
            participants=(
                replace(_participants()[0], schema_sha256=None),
                *_participants()[1:],
            )
        )
    assert malformed.value.code in {CODE_BOUNDARY_INVALID, CODE_BOUNDARY_INCOMPATIBLE}
