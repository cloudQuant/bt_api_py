"""CFG06b phase-1: risk ``group_policy`` store/service control plane in isolation.

Unit-level coverage for the internal-only group admission gate that is later
registered as the risk ``group_policy`` builtin and consumed in-process by the
group coordinator. These tests never start a ServiceHost and never expose
``service.group_admission`` anywhere: they pin the exact sqlite schema,
self-certifying immutable version semantics (idempotent re-pin, conflict
refusal), reference/version/document refusals, version-drift rejection before
scope decisions, fail-closed behavior on missing refs, closed stores, closed
services, tampered rows, and durability across store reopen (recovery).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType

import pytest

from bt_api_risk.service_component import (
    GROUP_POLICY_OPERATIONS,
    GroupAdmissionDecision,
    GroupPolicyError,
    GroupPolicyService,
    GroupPolicyStore,
)

_POLICY_REF = "cfg06b-group-policy-v3"
_DOCUMENT = {
    "admitted_portfolios": ["portfolio-a", "portfolio-b"],
    "denied_group_ids": ["group-blocked"],
}


def _canonical_json(document: object) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _version(document: object) -> str:
    return hashlib.sha256(_canonical_json(document).encode("utf-8")).hexdigest()


def _store(tmp_path: Path) -> GroupPolicyStore:
    return GroupPolicyStore(str(tmp_path / "group-policy.sqlite"))


def _pinned_service(tmp_path: Path) -> tuple[GroupPolicyStore, GroupPolicyService, str]:
    store = _store(tmp_path)
    version = _version(_DOCUMENT)
    store.pin(_POLICY_REF, version, _DOCUMENT)
    return store, GroupPolicyService(store, _POLICY_REF, version), version


def test_store_pins_the_exact_schema_and_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "group-policy.sqlite"
    store = GroupPolicyStore(str(path))
    version = _version(_DOCUMENT)
    pinned = store.pin(_POLICY_REF, version, _DOCUMENT)
    store.close()

    with sqlite3.connect(str(path)) as probe:
        columns = {
            row[1]: (row[2], row[3], row[5])
            for row in probe.execute("PRAGMA table_info(group_policy_versions)")
        }
        rows = probe.execute(
            "SELECT group_policy_ref, policy_version, document_json "
            "FROM group_policy_versions"
        ).fetchall()

    assert list(columns) == [
        "group_policy_ref",
        "policy_version",
        "document_json",
        "pinned_utc",
    ]
    assert columns["group_policy_ref"] == ("TEXT", 0, 1)  # primary key
    assert columns["policy_version"] == ("TEXT", 1, 0)
    assert columns["document_json"] == ("TEXT", 1, 0)
    assert columns["pinned_utc"] == ("TEXT", 1, 0)
    assert rows == [(_POLICY_REF, version, _canonical_json(_DOCUMENT))]

    reopened = GroupPolicyStore(str(path))
    resolved = reopened.resolve(_POLICY_REF)
    reopened.close()

    assert dict(resolved) == dict(pinned)
    assert resolved["document"] == _DOCUMENT
    assert isinstance(resolved, MappingProxyType)
    with pytest.raises(TypeError):
        resolved["policy_version"] = "0" * 64  # type: ignore[index]


def test_pin_is_idempotent_and_conflicting_versions_are_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    version = _version(_DOCUMENT)
    first = store.pin(_POLICY_REF, version, _DOCUMENT)
    second = store.pin(_POLICY_REF, version, dict(_DOCUMENT))
    assert dict(second) == dict(first)

    # Wire-frozen tuples normalize to the same canonical document and digest.
    frozen = {
        "admitted_portfolios": ("portfolio-a", "portfolio-b"),
        "denied_group_ids": ("group-blocked",),
    }
    assert dict(store.pin(_POLICY_REF, version, frozen)) == dict(first)

    other_document = {"admitted_portfolios": ["portfolio-a"], "denied_group_ids": []}
    with pytest.raises(GroupPolicyError) as conflict:
        store.pin(_POLICY_REF, _version(other_document), other_document)
    assert conflict.value.code == "GROUP_POLICY_VERSION_CONFLICT"
    assert dict(store.resolve(_POLICY_REF)) == dict(first)

    with sqlite3.connect(str(tmp_path / "group-policy.sqlite")) as probe:
        assert probe.execute("SELECT COUNT(*) FROM group_policy_versions").fetchone() == (1,)
    store.close()


def test_store_requires_a_real_path(tmp_path: Path) -> None:
    for bad_path in ("", None, 7):
        with pytest.raises(GroupPolicyError) as exc_info:
            GroupPolicyStore(bad_path)  # type: ignore[arg-type]
        assert exc_info.value.code == "INVALID_STORE_PATH"


@pytest.mark.parametrize(
    ("bad_ref", "code"),
    [
        (None, "INVALID_REFERENCE"),
        ("", "INVALID_REFERENCE"),
        ("group policy v1", "INVALID_REFERENCE"),
        ("latest", "VERSIONED_REFERENCE_REQUIRED"),
        ("group-policy", "VERSIONED_REFERENCE_REQUIRED"),
        ("group-policy-v0", "VERSIONED_REFERENCE_REQUIRED"),
    ],
)
def test_pin_refuses_mutable_or_malformed_references(
    tmp_path: Path, bad_ref: object, code: str
) -> None:
    store = _store(tmp_path)
    with pytest.raises(GroupPolicyError) as exc_info:
        store.pin(bad_ref, _version(_DOCUMENT), _DOCUMENT)  # type: ignore[arg-type]
    assert exc_info.value.code == code
    store.close()


def test_pin_refuses_bad_versions_and_documents(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for bad_version in ("", "v3", "z" * 64, "A" * 64, 7, None):
        with pytest.raises(GroupPolicyError) as version_exc:
            store.pin(_POLICY_REF, bad_version, _DOCUMENT)  # type: ignore[arg-type]
        assert version_exc.value.code == "INVALID_POLICY_VERSION"

    # A pattern-valid digest that is not the digest of this document is a
    # different version altogether: self-certifying versions only.
    with pytest.raises(GroupPolicyError) as digest_exc:
        store.pin(_POLICY_REF, "a" * 64, _DOCUMENT)
    assert digest_exc.value.code == "INVALID_POLICY_VERSION"

    bad_documents = [
        {"admitted_portfolios": ["portfolio-a"]},
        {"admitted_portfolios": ["portfolio-a"], "denied_group_ids": [], "extra": []},
        {"admitted_portfolios": "portfolio-a", "denied_group_ids": []},
        {"admitted_portfolios": [1], "denied_group_ids": []},
        {"admitted_portfolios": ["portfolio-a", "portfolio-a"], "denied_group_ids": []},
        {"admitted_portfolios": [""], "denied_group_ids": []},
        {"admitted_portfolios": ["a" * 257], "denied_group_ids": []},
        ["not", "a", "mapping"],
    ]
    for document in bad_documents:
        with pytest.raises(GroupPolicyError) as document_exc:
            store.pin(_POLICY_REF, _version(document), document)
        assert document_exc.value.code == "INVALID_DOCUMENT"
    store.close()


def test_resolve_refuses_unknown_and_mutable_references(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(GroupPolicyError) as not_found:
        store.resolve("cfg06b-unknown-v1")
    assert not_found.value.code == "GROUP_POLICY_NOT_FOUND"

    with pytest.raises(GroupPolicyError) as mutable:
        store.resolve("cfg06b-latest")
    assert mutable.value.code == "VERSIONED_REFERENCE_REQUIRED"
    store.close()


def test_service_requires_type_checked_store_and_versioned_activation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    version = _version(_DOCUMENT)

    with pytest.raises(GroupPolicyError) as bad_store:
        GroupPolicyService(object(), _POLICY_REF, version)  # type: ignore[arg-type]
    assert bad_store.value.code == "INVALID_GROUP_POLICY_STORE"

    with pytest.raises(GroupPolicyError) as mutable_ref:
        GroupPolicyService(store, "group-policy", version)
    assert mutable_ref.value.code == "VERSIONED_REFERENCE_REQUIRED"

    with pytest.raises(GroupPolicyError) as bad_version:
        GroupPolicyService(store, _POLICY_REF, "not-a-digest")
    assert bad_version.value.code == "INVALID_POLICY_VERSION"

    service = GroupPolicyService(store, _POLICY_REF, version)
    assert service.group_policy_ref == _POLICY_REF
    assert service.policy_version == version
    assert GROUP_POLICY_OPERATIONS == frozenset({"group.admission.evaluate"})
    assert GroupPolicyService.SUPPORTED_OPERATIONS is GROUP_POLICY_OPERATIONS
    store.close()


def test_service_ready_tracks_the_exact_activated_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    version = _version(_DOCUMENT)
    service = GroupPolicyService(store, _POLICY_REF, version)
    assert service.ready is False  # nothing pinned yet

    store.pin(_POLICY_REF, version, _DOCUMENT)
    assert service.ready is True

    # A gate activated for a different (shape-valid) digest is not ready.
    other = GroupPolicyService(store, _POLICY_REF, "b" * 64)
    assert other.ready is False
    # A gate pointed at an unpinned reference is not ready either.
    assert GroupPolicyService(store, "cfg06b-other-v1", version).ready is False

    service.close()
    assert service.ready is False

    fresh = GroupPolicyService(store, _POLICY_REF, version)
    assert fresh.ready is True
    store.close()
    assert fresh.ready is False  # a closed store fails closed


def test_evaluate_returns_immutable_decisions_with_constant_codes(tmp_path: Path) -> None:
    store, service, version = _pinned_service(tmp_path)

    admitted = service.evaluate(portfolio_ref="portfolio-a", group_id="group-1")
    assert type(admitted) is GroupAdmissionDecision
    assert admitted.admitted is True
    assert admitted.code == "OK"
    assert admitted.group_policy_ref == _POLICY_REF
    assert admitted.policy_version == version
    with pytest.raises(FrozenInstanceError):
        admitted.admitted = False  # type: ignore[misc]

    scope_denied = service.evaluate(portfolio_ref="portfolio-c", group_id="group-1")
    assert scope_denied.admitted is False
    assert scope_denied.code == "GROUP_POLICY_SCOPE_DENIED"

    policy_denied = service.evaluate(portfolio_ref="portfolio-a", group_id="group-blocked")
    assert policy_denied.admitted is False
    assert policy_denied.code == "GROUP_POLICY_DENIED"

    # Scope is decided before the group-denial list inside an admitted scope.
    precedence = service.evaluate(portfolio_ref="portfolio-c", group_id="group-blocked")
    assert precedence.code == "GROUP_POLICY_SCOPE_DENIED"
    store.close()


def test_evaluate_rejects_version_drift_before_any_scope_decision(tmp_path: Path) -> None:
    store, _, version = _pinned_service(tmp_path)
    stale = GroupPolicyService(store, _POLICY_REF, "c" * 64)
    assert stale.ready is False

    decision = stale.evaluate(portfolio_ref="portfolio-c", group_id="group-blocked")
    assert decision.admitted is False
    assert decision.code == "GROUP_POLICY_VERSION_REJECTED"
    assert decision.group_policy_ref == _POLICY_REF
    assert decision.policy_version == "c" * 64
    store.close()


def test_evaluate_fails_closed_on_unpinned_ref_invalid_context_and_close(tmp_path: Path) -> None:
    store = _store(tmp_path)
    version = _version(_DOCUMENT)
    unpinned = GroupPolicyService(store, "cfg06b-unpinned-v1", version)
    with pytest.raises(GroupPolicyError) as not_found:
        unpinned.evaluate(portfolio_ref="portfolio-a", group_id="group-1")
    assert not_found.value.code == "GROUP_POLICY_NOT_FOUND"

    store.pin(_POLICY_REF, version, _DOCUMENT)
    service = GroupPolicyService(store, _POLICY_REF, version)
    for portfolio_ref, group_id in (
        ("", "group-1"),
        ("portfolio-a", ""),
        (None, "group-1"),
        ("portfolio-a", 7),
    ):
        with pytest.raises(GroupPolicyError) as context_exc:
            service.evaluate(portfolio_ref=portfolio_ref, group_id=group_id)  # type: ignore[arg-type]
        assert context_exc.value.code == "INVALID_ADMISSION_CONTEXT"

    service.close()
    with pytest.raises(GroupPolicyError) as closed:
        service.evaluate(portfolio_ref="portfolio-a", group_id="group-1")
    assert closed.value.code == "CLOSED"
    store.close()


def test_evaluate_refuses_a_tampered_stored_document(tmp_path: Path) -> None:
    path = tmp_path / "group-policy.sqlite"
    store = GroupPolicyStore(str(path))
    version = _version(_DOCUMENT)
    store.pin(_POLICY_REF, version, _DOCUMENT)
    service = GroupPolicyService(store, _POLICY_REF, version)

    with sqlite3.connect(str(path)) as probe:
        probe.execute(
            "UPDATE group_policy_versions SET document_json=? WHERE group_policy_ref=?",
            ('{"admitted_portfolios":"portfolio-a","denied_group_ids":[]}', _POLICY_REF),
        )

    with pytest.raises(GroupPolicyError) as exc_info:
        service.evaluate(portfolio_ref="portfolio-a", group_id="group-1")
    assert exc_info.value.code == "INVALID_DOCUMENT"
    store.close()


def test_evaluate_recovery_after_store_reopen(tmp_path: Path) -> None:
    path = tmp_path / "group-policy.sqlite"
    store = GroupPolicyStore(str(path))
    version = _version(_DOCUMENT)
    store.pin(_POLICY_REF, version, _DOCUMENT)
    service = GroupPolicyService(store, _POLICY_REF, version)
    assert service.evaluate(portfolio_ref="portfolio-a", group_id="group-1").admitted is True

    store.close()
    with pytest.raises(GroupPolicyError) as unavailable:
        service.evaluate(portfolio_ref="portfolio-a", group_id="group-1")
    assert unavailable.value.code == "GROUP_POLICY_STORAGE_UNAVAILABLE"
    assert service.ready is False

    recovered_store = GroupPolicyStore(str(path))
    recovered = GroupPolicyService(recovered_store, _POLICY_REF, version)
    assert recovered.ready is True
    decision = recovered.evaluate(portfolio_ref="portfolio-a", group_id="group-1")
    assert decision.admitted is True
    assert decision.code == "OK"
    assert decision.policy_version == version
    recovered_store.close()
