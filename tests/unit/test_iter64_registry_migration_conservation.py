"""Regression evidence for Registry registration-key migration conservation.

All databases and identities here are synthetic. A tombstone protects a key
only when the retained ledger itself survives the migration boundary.
"""

from dataclasses import replace
import sqlite3

import pytest

from bt_api_py.service_migration_boundary import (
    CODE_BOUNDARY_NOT_CONSERVED,
    MigrationBoundaryError,
    assert_participant_states_conserved,
    capture_sqlite_participant,
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


@pytest.mark.xfail(
    strict=True,
    raises=RegistryConservationGap,
    reason="GAP: Registry migration identity omits registration_keys",
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
    before = _capture(path)
    original = _key_rows(path)

    arm_migration_cutover(path, source_epoch="source", new_epoch="destination")
    target, identity, scope = _make_registry(path, migration_deployment_epoch="destination")
    try:
        assert read_migration_marker(path).status == "COMMITTED"
        assert target.migration_writes_enabled is False
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

    assert_participant_states_conserved(_with_registry(before), _with_registry(after))
