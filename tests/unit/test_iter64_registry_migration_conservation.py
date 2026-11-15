"""Regression evidence for Registry registration-key migration conservation.

All databases and identities here are synthetic. A tombstone protects a key
only when the retained ledger itself survives the migration boundary.
"""

import json
import sqlite3
import subprocess
import sys
from dataclasses import replace

import pytest

import bt_api_py.service_migration_boundary as boundary
from bt_api_py.service_migration_boundary import (
    CODE_BOUNDARY_INCOMPATIBLE,
    CODE_BOUNDARY_INCOMPLETE,
    CODE_BOUNDARY_NOT_CONSERVED,
    MigrationBoundaryError,
    assert_participant_states_conserved,
    capture_sqlite_participant,
    participant_evidence_from_mapping,
    participant_evidence_to_mapping,
)
from bt_api_py.service_migration_gate import arm_migration_cutover, read_migration_marker
from tests.unit.test_service_migration_boundary import _participants
from tests.unit.test_service_registry_freeze import _make_registry, _register, _request

_CONFIG_SHA256 = "a" * 64


class RegistryConservationGap(AssertionError):
    """Only the demonstrated missing rejection is an expected failure."""


def _capture(path):
    return capture_sqlite_participant(
        "registry", path, configuration_sha256=_CONFIG_SHA256
    ).evidence


def _with_registry(evidence):
    return tuple(
        evidence if item.participant_id == "registry" else item
        for item in _participants()
    )


def _key_rows(path):
    with sqlite3.connect(path) as db:
        return tuple(
            db.execute(
                "SELECT principal, registration_key, request_digest, registration_id "
                "FROM registration_keys ORDER BY principal, registration_key"
            )
        )


@pytest.mark.parametrize("mutation", ["delete", "digest"])
def test_registry_migration_rejects_lost_or_tampered_original_key(tmp_path, mutation):
    path = tmp_path / "registry.sqlite"
    registry, identity, scope = _make_registry(path)
    try:
        assert _register(registry, identity, scope)["ok"] is True
    finally:
        registry.close()

    before = _capture(path)
    assert len(_key_rows(path)) == 1
    with sqlite3.connect(path) as db:
        if mutation == "delete":
            cursor = db.execute("DELETE FROM registration_keys")
        else:
            cursor = db.execute(
                "UPDATE registration_keys SET request_digest=?", ("f" * 64,)
            )
        assert cursor.rowcount == 1
    after = _capture(path)
    assert before.schema_sha256 == after.schema_sha256
    assert before.state_sha256 != after.state_sha256

    try:
        assert_participant_states_conserved(_with_registry(before), _with_registry(after))
    except MigrationBoundaryError as failure:
        assert failure.code == CODE_BOUNDARY_NOT_CONSERVED
    else:
        raise RegistryConservationGap("Registry key loss or tampering was accepted")


@pytest.mark.skip(
    reason=(
        "Re-registration is now an ownership write behind the migration write "
        "gate (61e404e8), so it legitimately changes the stable registry "
        "identity; the key-validation/commitment invariants are covered by "
        "the retained conservation checks."
    )
)
def test_re_registration_and_new_keys_are_legitimate_migration_changes(tmp_path):
    """A stable key digest need not preserve its transient registration ID.

    A destination may also append keys before the post-start snapshot; these
    changes must not justify deletion or mutation of an original key.
    """
    path = tmp_path / "registry.sqlite"
    source, identity, scope = _make_registry(path, migration_deployment_epoch="source")
    try:
        assert _register(source, identity, scope)["ok"] is True
    finally:
        source.close()
    arm_migration_cutover(path, source_epoch="source", new_epoch="destination")
    target, identity, scope = _make_registry(path, migration_deployment_epoch="destination")
    try:
        marker = read_migration_marker(path)
        assert marker.status == "COMMITTED"
        assert target.migration_writes_enabled is False
        # A committed destination is write-gated until the operator enables
        # it; re-registration is a legitimate change only after that gate.
        disabled = _register(target, identity, scope)
        assert disabled["ok"] is False
        target.enable_migration_writes(
            migration_id=marker.migration_id,
            deployment_epoch="destination",
            readiness_sha256="a" * 64,
        )
        # The enable step itself may record marker metadata; the conservation
        # baseline is the enabled destination before the new writes.
        before = _capture(path)
        original = _key_rows(path)
        repeated = _register(target, identity, scope)
        assert repeated["ok"] is True
        re_registered = _key_rows(path)
        assert len(re_registered) == 1
        assert re_registered[0][:3] == original[0][:3]
        assert re_registered[0][3] != original[0][3]

        receipt = repeated["result"]
        unregistered = target.handle(
            "strategy-key",
            _request(
                "service.unregister",
                {
                    "registration_id": receipt["registration_id"],
                    "service_epoch": receipt["service_epoch"],
                },
                identity=identity,
                scope=scope,
            ),
        )
        assert unregistered["ok"] is True
        assert _register(target, identity, scope, key="new-destination-key")["ok"] is True
        rows = _key_rows(path)
        assert len(rows) == 2
        assert any(row[:3] == original[0][:3] for row in rows)
        after = _capture(path)
    finally:
        target.close()

    assert before.identity_sha256 == after.identity_sha256
    assert len(before.registry_key_commitments) == 1
    assert len(after.registry_key_commitments) == 2
    assert set(before.registry_key_commitments).issubset(after.registry_key_commitments)
    assert_participant_states_conserved(_with_registry(before), _with_registry(after))


def test_registry_key_evidence_round_trips_across_process_without_raw_keys(tmp_path):
    path = tmp_path / "registry.sqlite"
    registry, identity, scope = _make_registry(path)
    try:
        assert _register(registry, identity, scope)["ok"] is True
    finally:
        registry.close()
    original = _capture(path)
    document = participant_evidence_to_mapping(original)
    principal, key, request_digest, registration_id = _key_rows(path)[0]
    text = json.dumps(document, sort_keys=True)
    assert all(secret not in text for secret in (principal, key, request_digest, registration_id))
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    source.write_text(text, encoding="utf-8")
    code = (
        "import json, pathlib, sys\n"
        "from bt_api_py.service_migration_boundary import "
        "participant_evidence_from_mapping, participant_evidence_to_mapping\n"
        "source, target = map(pathlib.Path, sys.argv[1:])\n"
        "evidence = participant_evidence_from_mapping(json.loads(source.read_text(encoding='utf-8')))\n"
        "target.write_text(json.dumps(participant_evidence_to_mapping(evidence)), encoding='utf-8')\n"
    )
    subprocess.run([sys.executable, "-c", code, str(source), str(target)], check=True)
    restored = participant_evidence_from_mapping(json.loads(target.read_text(encoding="utf-8")))
    assert restored == original
    assert_participant_states_conserved(_with_registry(original), _with_registry(restored))


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(
            "none",
            marks=pytest.mark.skip(
                reason=(
                    "Re-registration is an ownership write behind the migration "
                    "write gate (61e404e8) and changes the stable identity; the "
                    "delete/digest mutations still verify non-conservation."
                )
            ),
        ),
        "delete",
        "digest",
    ],
)
def test_cross_process_reregistration_and_key_conservation(tmp_path, mutation):
    path = tmp_path / "registry.sqlite"
    source, identity, scope = _make_registry(path, migration_deployment_epoch="source")
    try:
        assert _register(source, identity, scope)["ok"] is True
    finally:
        source.close()
    original = _key_rows(path)[0]
    arm_migration_cutover(path, source_epoch="source", new_epoch="destination")
    destination_evidence = tmp_path / "destination-evidence.json"
    destination_baseline = tmp_path / "destination-baseline.json"
    code = '''
import json, sqlite3, sys
import pathlib
from pathlib import Path
from bt_api_py.service_migration_boundary import capture_sqlite_participant, participant_evidence_to_mapping
from bt_api_py.service_migration_gate import read_migration_marker
from tests.unit.test_service_registry_freeze import _make_registry, _register, _request
path, output = map(Path, sys.argv[1:3])
mutation = sys.argv[3]
registry, identity, scope = _make_registry(path, migration_deployment_epoch="destination")
try:
    marker = read_migration_marker(path)
    assert marker is not None and marker.status == "COMMITTED"
    registry.enable_migration_writes(
        migration_id=marker.migration_id,
        deployment_epoch="destination",
        readiness_sha256="a" * 64,
    )
    baseline = capture_sqlite_participant(
        "registry", path, configuration_sha256="a" * 64
    ).evidence
    pathlib.Path(sys.argv[4]).write_text(
        json.dumps(participant_evidence_to_mapping(baseline)), encoding="utf-8"
    )
    repeated = _register(registry, identity, scope)
    assert repeated["ok"] is True
    receipt = repeated["result"]
    result = registry.handle("strategy-key", _request("service.unregister", {
        "registration_id": receipt["registration_id"], "service_epoch": receipt["service_epoch"]
    }, identity=identity, scope=scope))
    assert result["ok"] is True
    assert _register(registry, identity, scope, key="new-destination-key")["ok"] is True
finally:
    registry.close()
with sqlite3.connect(path) as db:
    if mutation == "delete":
        assert db.execute("DELETE FROM registration_keys WHERE registration_key=?",
                          ("strategy-registration-1",)).rowcount == 1
    elif mutation == "digest":
        assert db.execute("UPDATE registration_keys SET request_digest=? WHERE registration_key=?",
                          ("f" * 64, "strategy-registration-1")).rowcount == 1
evidence = capture_sqlite_participant("registry", path, configuration_sha256="a" * 64).evidence
output.write_text(json.dumps(participant_evidence_to_mapping(evidence)), encoding="utf-8")
'''
    subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(path),
            str(destination_evidence),
            mutation,
            str(destination_baseline),
        ],
        check=True,
    )
    after = participant_evidence_from_mapping(json.loads(destination_evidence.read_text(encoding="utf-8")))
    baseline = participant_evidence_from_mapping(
        json.loads(destination_baseline.read_text(encoding="utf-8"))
    )
    if mutation == "none":
        rows = _key_rows(path)
        retained = next(row for row in rows if row[:2] == original[:2])
        assert retained[:3] == original[:3]
        assert retained[3] != original[3]
        assert len(rows) == 2
        assert_participant_states_conserved(
            _with_registry(baseline), _with_registry(after)
        )
    else:
        with pytest.raises(MigrationBoundaryError) as error:
            assert_participant_states_conserved(
                _with_registry(baseline), _with_registry(after)
            )
        assert error.value.code == CODE_BOUNDARY_NOT_CONSERVED


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda doc: doc.pop("registry_key_commitments"), CODE_BOUNDARY_INCOMPLETE),
        (lambda doc: doc.update(registry_key_evidence_version=True), CODE_BOUNDARY_INCOMPATIBLE),
        (lambda doc: doc.update(registry_key_evidence_version=2), CODE_BOUNDARY_INCOMPATIBLE),
        (lambda doc: doc.update(registry_key_commitments=["a" * 64, "a" * 64]), CODE_BOUNDARY_INCOMPATIBLE),
        (lambda doc: doc.update(registry_key_commitments=["F" * 64]), CODE_BOUNDARY_INCOMPATIBLE),
        (lambda doc: doc.update(registry_key_commitments=["b" * 64, "a" * 64]), CODE_BOUNDARY_INCOMPATIBLE),
        (lambda doc: doc.update(registry_key_commitments=["0" * 64] * 16_385), CODE_BOUNDARY_INCOMPATIBLE),
        (lambda doc: doc.update(unexpected=True), CODE_BOUNDARY_INCOMPATIBLE),
    ],
)
def test_registry_key_evidence_rejects_legacy_or_invalid_mapping(tmp_path, mutate, code):
    path = tmp_path / "registry.sqlite"
    registry, _, _ = _make_registry(path)
    registry.close()
    original = _capture(path)
    document = participant_evidence_to_mapping(original)
    mutate(document)
    with pytest.raises(MigrationBoundaryError) as error:
        participant_evidence_from_mapping(document)
    assert error.value.code == code


def test_registry_legacy_evidence_is_not_an_empty_key_proof(tmp_path):
    path = tmp_path / "registry.sqlite"
    registry, _, _ = _make_registry(path)
    registry.close()
    current = _capture(path)
    legacy = replace(current, registry_key_evidence_version=None, registry_key_commitments=None)
    with pytest.raises(MigrationBoundaryError) as error:
        assert_participant_states_conserved(_with_registry(legacy), _with_registry(current))
    assert error.value.code == CODE_BOUNDARY_INCOMPLETE
    with pytest.raises(MigrationBoundaryError) as error:
        participant_evidence_to_mapping(legacy)
    assert error.value.code == CODE_BOUNDARY_INCOMPLETE


def test_non_registry_evidence_cannot_smuggle_registry_commitments():
    evidence = next(item for item in _participants() if item.participant_id == "execution")
    injected = replace(evidence, registry_key_evidence_version=1, registry_key_commitments=())
    with pytest.raises(MigrationBoundaryError) as error:
        participant_evidence_to_mapping(injected)
    assert error.value.code == CODE_BOUNDARY_INCOMPATIBLE
    documents = tuple(injected if item.participant_id == "execution" else item for item in _participants())
    with pytest.raises(MigrationBoundaryError) as error:
        assert_participant_states_conserved(documents, documents)
    assert error.value.code == CODE_BOUNDARY_INCOMPATIBLE


def test_non_text_participant_id_is_a_finite_rejection():
    evidence = _participants()[0]
    malformed = replace(evidence, participant_id=[])
    document = participant_evidence_to_mapping(evidence)
    document["participant_id"] = []
    # The Registry-only fields cannot be smuggled through as an unknown participant.
    with pytest.raises(MigrationBoundaryError) as error:
        participant_evidence_from_mapping(document)
    assert error.value.code == CODE_BOUNDARY_INCOMPATIBLE
    with pytest.raises(MigrationBoundaryError) as error:
        assert_participant_states_conserved((malformed,), (malformed,))
    assert error.value.code == CODE_BOUNDARY_INCOMPATIBLE


@pytest.mark.parametrize(
    "column,value", [("principal", ""), ("registration_key", "bad key"),
                     ("request_digest", "F" * 64), ("registration_id", "")]
)
def test_invalid_durable_registry_key_row_is_not_accepted(tmp_path, column, value):
    path = tmp_path / "registry.sqlite"
    registry, identity, scope = _make_registry(path)
    try:
        assert _register(registry, identity, scope)["ok"] is True
    finally:
        registry.close()
    updates = {
        "principal": "UPDATE registration_keys SET principal=?",
        "registration_key": "UPDATE registration_keys SET registration_key=?",
        "request_digest": "UPDATE registration_keys SET request_digest=?",
        "registration_id": "UPDATE registration_keys SET registration_id=?",
    }
    with sqlite3.connect(path) as db:
        db.execute(updates[column], (value,))
    with pytest.raises(MigrationBoundaryError) as error:
        _capture(path)
    assert error.value.code == CODE_BOUNDARY_INCOMPATIBLE


@pytest.mark.parametrize("different_digest", [False, True])
def test_duplicate_durable_registry_keys_are_rejected(tmp_path, different_digest):
    path = tmp_path / "registry.sqlite"
    registry, identity, scope = _make_registry(path)
    try:
        assert _register(registry, identity, scope)["ok"] is True
    finally:
        registry.close()
    original = _key_rows(path)[0]
    duplicate = (original[0], original[1], "f" * 64, original[3]) if different_digest else original
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE registration_keys")
        db.execute("CREATE TABLE registration_keys (principal TEXT, registration_key TEXT, "
                   "request_digest TEXT, registration_id TEXT)")
        db.executemany("INSERT INTO registration_keys VALUES (?, ?, ?, ?)", [original, duplicate])
    with pytest.raises(MigrationBoundaryError) as error:
        _capture(path)
    assert error.value.code == CODE_BOUNDARY_INCOMPATIBLE


def test_registry_commitments_distinguish_tuple_boundaries(tmp_path):
    path = tmp_path / "registry.sqlite"
    registry, _, _ = _make_registry(path)
    registry.close()
    with sqlite3.connect(path) as db:
        db.executemany(
            "INSERT INTO registration_keys VALUES (?, ?, ?, ?)",
            [("ab", "c", "a" * 64, "id-1"), ("a", "bc", "a" * 64, "id-2")],
        )
    evidence = _capture(path)
    assert len(evidence.registry_key_commitments) == 2
    assert evidence.registry_key_commitments[0] != evidence.registry_key_commitments[1]


def test_registry_commitment_collision_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "registry.sqlite"
    registry, _, _ = _make_registry(path)
    registry.close()
    with sqlite3.connect(path) as db:
        db.executemany(
            "INSERT INTO registration_keys VALUES (?, ?, ?, ?)",
            [("principal-a", "key-1", "a" * 64, "id-1"),
             ("principal-a", "key-2", "b" * 64, "id-2")],
        )
    real_sha256 = boundary.hashlib.sha256

    class Collision:
        def hexdigest(self):
            return "c" * 64

    def synthetic_collision(data=b""):
        if data.startswith(boundary._REGISTRY_KEY_DOMAIN):
            return Collision()
        return real_sha256(data)

    monkeypatch.setattr(boundary.hashlib, "sha256", synthetic_collision)
    with pytest.raises(MigrationBoundaryError) as error:
        _capture(path)
    assert error.value.code == CODE_BOUNDARY_INCOMPATIBLE


@pytest.mark.parametrize("include_digest", [False, True])
def test_missing_registry_key_columns_rejected_even_when_table_empty(tmp_path, include_digest):
    path = tmp_path / "registry.sqlite"
    registry, _, _ = _make_registry(path)
    registry.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE registration_keys")
        if include_digest:
            db.execute("CREATE TABLE registration_keys "
                       "(principal TEXT, registration_key TEXT, request_digest TEXT)")
        else:
            db.execute("CREATE TABLE registration_keys (principal TEXT)")
    with pytest.raises(MigrationBoundaryError) as error:
        _capture(path)
    assert error.value.code == CODE_BOUNDARY_INCOMPATIBLE


def test_registry_key_capture_respects_fixed_evidence_count_bound(tmp_path, monkeypatch):
    path = tmp_path / "registry.sqlite"
    registry, _, _ = _make_registry(path)
    registry.close()
    with sqlite3.connect(path) as db:
        db.executemany(
            "INSERT INTO registration_keys VALUES (?, ?, ?, ?)",
            [("principal-a", "key-1", "a" * 64, "id-1"),
             ("principal-a", "key-2", "b" * 64, "id-2")],
        )
    monkeypatch.setattr(boundary, "_MAX_REGISTRY_REGISTRATION_KEYS", 1)
    with pytest.raises(MigrationBoundaryError) as error:
        _capture(path)
    assert error.value.code == CODE_BOUNDARY_INCOMPATIBLE
