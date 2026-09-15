"""RED contract cases for the public normal-entry CTP approval arm."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal  # noqa: F401  (parity with sibling fixtures)

import pytest

from bt_api_py import NormalizedApiError

from .test_ctp_execution_approval import (
    _iso,
    _payload,
    _signed_artifact,
)
from .test_execution_arming import (
    ACCOUNT_FINGERPRINT,
    BUNDLE_INSTRUMENTS,
    BUNDLE_SCOPE_VERSION,
    PROFILE,
    STRATEGY_IDENTITY,
    TRADING_DAY,
    VENUE,
    _install_account_stream,
    _ManagedFeed,
    _ready_state,
    _session,
)
from .test_execution_recovery import (
    CYCLE,
)

ENTRY_SCHEMA = "ctp-execution-entry-approval-v1"
ENTRY_PURPOSE = "ctp_execution_approval"


@pytest.fixture()
def signing_material():
    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw()
    root = {
        "schema_version": "ctp-execution-trust-root-v1",
        "keys": {
            "operator-entry-1": {
                "public_key": base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("="),
                "role": "independent_operator",
                "purposes": [ENTRY_PURPOSE, "ctp_execution_recovery"],
                "not_before": _iso(datetime.now(UTC) - timedelta(minutes=1)),
                "expires_at": _iso(datetime.now(UTC) + timedelta(hours=1)),
            }
        },
        "revocation_snapshot": {
            "version": 1,
            "issued_at": _iso(datetime.now(UTC) - timedelta(minutes=1)),
            "expires_at": _iso(datetime.now(UTC) + timedelta(hours=1)),
            "revoked_approval_ids": [],
            "revoked_nonces": [],
        },
    }
    return private_key, root


def _entry_payload(now=None, **changes):
    payload = _payload(now)
    payload.update(
        {
            "schema_version": ENTRY_SCHEMA,
            "purpose": ENTRY_PURPOSE,
            "candidate_id": "candidate-iter23-25",
            "strategy_id": "iter22-midfreq",
            "strategy_identity_sha256": STRATEGY_IDENTITY,
            "execution_cycle_id": CYCLE,
            "authorized_instruments": [
                {"exchange_id": exchange, "instrument_id": instrument}
                for exchange, instrument in (item.split(".", 1) for item in BUNDLE_INSTRUMENTS)
            ],
            "primary_instrument": {"exchange_id": "CZCE", "instrument_id": "SA701"},
            "account_fingerprint": ACCOUNT_FINGERPRINT,
            "trading_day": TRADING_DAY,
            "connection_generation": 3,
            "environment_profile": PROFILE,
            "bt_api_ctp_sha256": "3" * 64,
            "native_sha256": "2" * 64,
            "backtrader_sha256": "9" * 64,
            "bt_api_py_sha256": "a" * 64,
            "bt_api_base_sha256": "b" * 64,
            "dependency_hashes_sha256": "5" * 64,
            "preflight_sha256": "6" * 64,
            "receipt_sha256": "1" * 64,
            "source_hashes_sha256": "4" * 64,
            "ctp_package_sha256": "3" * 64,
            "revocation_snapshot_version": 1,
            "issuer_key_id": "operator-entry-1",
        }
    )
    payload.update(changes)
    return payload


def _entry_proof(payload):
    authorized = [
        f"{item['exchange_id']}.{item['instrument_id']}"
        for item in payload["authorized_instruments"]
    ]
    primary = payload["primary_instrument"]
    return {
        "account_fingerprint": payload["account_fingerprint"],
        "trading_day": payload["trading_day"],
        "instrument": f"{primary['exchange_id']}.{primary['instrument_id']}",
        "connection_generation": payload["connection_generation"],
        "environment_profile": payload["environment_profile"],
        "receipt_sha256": payload["receipt_sha256"],
        "native_sha256": payload["native_sha256"],
        "ctp_package_sha256": payload["ctp_package_sha256"],
        "source_hashes_sha256": payload["source_hashes_sha256"],
        "dependency_hashes_sha256": payload["dependency_hashes_sha256"],
        "preflight_sha256": payload["preflight_sha256"],
        "scope_version": BUNDLE_SCOPE_VERSION,
        "authorized_instruments": authorized,
    }


def _signed_entry_artifact(payload, private_key) -> bytes:
    payload_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    signature = private_key.sign(payload_bytes)
    return json.dumps(
        {
            "schema_version": ENTRY_SCHEMA,
            "algorithm": "Ed25519",
            "payload": payload,
            "signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _entry_fixture(
    monkeypatch,
    tmp_path,
    signing_material,
    name,
    *,
    payload_changes=None,
):
    from bt_api_py import _execution_session as session_module
    from bt_api_py._contracts import TransportMode
    from bt_api_py.bt_api import BtApi

    private_key, root = signing_material
    monkeypatch.setattr(
        session_module,
        "_ledger_registry_root",
        lambda: tmp_path / f"{name}-ledger-registry",
    )
    session = _session(tmp_path)
    feed = _ManagedFeed(_ready_state())
    api = object.__new__(BtApi)
    api.transport_mode = TransportMode.DIRECT
    api.exchange_feeds = {VENUE: feed}
    api.exchange_kwargs = {VENUE: {"auto_settlement_confirm": False}}
    api.data_queues = {VENUE: __import__("queue").Queue()}
    api._subscription_streams = []
    api._subscription_flags = {}
    api._execution_session = session
    api._ctp_execution_capability = object()
    feed.configure_execution_gate(api._ctp_execution_capability)
    api._ctp_execution_runtime_identity = lambda: {
        "native_sha256": "2" * 64,
        "ctp_package_sha256": "3" * 64,
    }
    payload = _entry_payload(**(payload_changes or {}))
    api._ctp_execution_runtime_python_identity = lambda: {
        "backtrader_sha256": payload["backtrader_sha256"],
        "bt_api_py_sha256": payload["bt_api_py_sha256"],
        "bt_api_base_sha256": payload["bt_api_base_sha256"],
        "dependency_hashes_sha256": payload["dependency_hashes_sha256"],
    }
    artifact = _signed_entry_artifact(payload, private_key)
    from .test_ctp_execution_approval import _context as _approval_context_seed

    approval_context = _approval_context_seed()
    approval_context.update(
        {
            field: payload[field]
            for field in approval_context
            if field != "source" and field in payload
        }
    )
    capability = api.redeem_ctp_execution_approval(
        artifact,
        trust_root=root,
        context=approval_context,
    )
    return api, session, feed, capability, payload


def test_entry_approval_artifact_verifies_with_entry_schema(
    monkeypatch, tmp_path, signing_material
):
    api, _session, _feed, capability, payload = _entry_fixture(
        monkeypatch, tmp_path, signing_material, "verify-entry"
    )
    try:
        assert capability.purpose == ENTRY_PURPOSE
        assert capability.schema_version == ENTRY_SCHEMA
        bound_instruments = [dict(item) for item in capability.bindings["authorized_instruments"]]
        assert bound_instruments == payload["authorized_instruments"]
    finally:
        _session.close()


def test_arm_execution_from_approval_arms_v2_bundle(monkeypatch, tmp_path, signing_material):
    _install_account_stream(monkeypatch)
    api, session, feed, capability, payload = _entry_fixture(
        monkeypatch, tmp_path, signing_material, "arm-entry"
    )
    try:
        result = api.arm_execution_from_approval(capability)

        proof = _entry_proof(payload)
        expected_hash = hashlib.sha256(
            json.dumps(
                proof,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        assert result == {
            "armed": True,
            "market_data_only": False,
            "proof_sha256": expected_hash,
        }
        assert session.config["market_data_only"] is False
        gate = feed.get_execution_gate_state()
        assert gate["armed"] is True
        assert gate["scope_version"] == BUNDLE_SCOPE_VERSION
        assert gate["authorized_instruments"] == BUNDLE_INSTRUMENTS
        assert session._arm_proof["authorized_instruments"] == BUNDLE_INSTRUMENTS
    finally:
        session.close()


def test_arm_from_approval_is_one_shot(monkeypatch, tmp_path, signing_material):
    _install_account_stream(monkeypatch)
    api, session, _feed, capability, _payload = _entry_fixture(
        monkeypatch, tmp_path, signing_material, "arm-once"
    )
    try:
        api.arm_execution_from_approval(capability)
        with pytest.raises(NormalizedApiError) as reused:
            api.arm_execution_from_approval(capability)

        assert reused.value.code == "ctp_execution_authorization_required"
    finally:
        session.close()


def test_arm_from_approval_rejects_plain_mapping_and_foreign_objects(
    monkeypatch, tmp_path, signing_material
):
    api, session, _feed, _capability, payload = _entry_fixture(
        monkeypatch, tmp_path, signing_material, "arm-forged"
    )
    try:
        forged = _entry_proof(payload)
        with pytest.raises(NormalizedApiError) as raised:
            api.arm_execution_from_approval(forged)

        assert raised.value.code == "ctp_execution_authorization_required"
        assert session.config["market_data_only"] is True
    finally:
        session.close()


def test_arm_from_approval_rejects_runtime_material_drift(monkeypatch, tmp_path, signing_material):
    api, session, _feed, capability, _payload = _entry_fixture(
        monkeypatch,
        tmp_path,
        signing_material,
        "arm-material",
        payload_changes={"backtrader_sha256": "8" * 64},
    )
    api._ctp_execution_runtime_python_identity = lambda: {
        "backtrader_sha256": "9" * 64,
        "bt_api_py_sha256": "a" * 64,
        "bt_api_base_sha256": "b" * 64,
        "dependency_hashes_sha256": "5" * 64,
    }
    try:
        with pytest.raises(NormalizedApiError) as raised:
            api.arm_execution_from_approval(capability)

        assert raised.value.code == "ctp_execution_authorization_material_mismatch"
        assert session.config["market_data_only"] is True
    finally:
        session.close()


def test_arm_from_approval_rejects_context_mismatch(monkeypatch, tmp_path, signing_material):
    api, session, _feed, capability, _payload = _entry_fixture(
        monkeypatch,
        tmp_path,
        signing_material,
        "arm-context",
        payload_changes={"trading_day": "20260910"},
    )
    try:
        with pytest.raises(NormalizedApiError) as raised:
            api.arm_execution_from_approval(capability)

        assert raised.value.code == "ctp_execution_authorization_context_mismatch"
        assert session.config["market_data_only"] is True
    finally:
        session.close()


def test_arm_from_approval_rejects_base_schema_capability(monkeypatch, tmp_path, signing_material):
    """A base-schema ordinary approval stays audit-only and cannot arm."""

    private_key, root = signing_material
    from bt_api_py import _execution_session as session_module
    from bt_api_py._contracts import TransportMode
    from bt_api_py.bt_api import BtApi

    monkeypatch.setattr(
        session_module,
        "_ledger_registry_root",
        lambda: tmp_path / "arm-base-ledger-registry",
    )
    session = _session(tmp_path)
    feed = _ManagedFeed(_ready_state())
    api = object.__new__(BtApi)
    api.transport_mode = TransportMode.DIRECT
    api.exchange_feeds = {VENUE: feed}
    api.exchange_kwargs = {VENUE: {"auto_settlement_confirm": False}}
    api.data_queues = {VENUE: __import__("queue").Queue()}
    api._subscription_streams = []
    api._subscription_flags = {}
    api._execution_session = session
    api._ctp_execution_capability = object()
    feed.configure_execution_gate(api._ctp_execution_capability)
    api._ctp_execution_runtime_identity = lambda: {
        "native_sha256": "2" * 64,
        "ctp_package_sha256": "3" * 64,
    }
    payload = _entry_payload()
    payload.pop("receipt_sha256")
    payload.pop("source_hashes_sha256")
    payload.pop("ctp_package_sha256")
    payload["schema_version"] = "ctp-execution-approval-v1"
    payload["issuer_key_id"] = "operator-entry-1"
    payload["revocation_snapshot_version"] = 1
    api._ctp_execution_runtime_python_identity = lambda: {
        "backtrader_sha256": payload["backtrader_sha256"],
        "bt_api_py_sha256": payload["bt_api_py_sha256"],
        "bt_api_base_sha256": payload["bt_api_base_sha256"],
        "dependency_hashes_sha256": payload["dependency_hashes_sha256"],
    }
    artifact = _signed_artifact(payload, private_key)

    from .test_ctp_execution_approval import _context as _approval_context_seed

    approval_context = _approval_context_seed()
    approval_context.update(
        {
            field: payload[field]
            for field in approval_context
            if field != "source" and field in payload
        }
    )
    capability = api.redeem_ctp_execution_approval(
        artifact,
        trust_root=root,
        context=approval_context,
    )
    try:
        assert capability.schema_version == "ctp-execution-approval-v1"
        with pytest.raises(NormalizedApiError) as raised:
            api.arm_execution_from_approval(capability)

        assert raised.value.code == "ctp_execution_authorization_required"
        assert session.config["market_data_only"] is True
    finally:
        session.close()


def test_confirm_ctp_settlement_from_approval_confirms_once(
    monkeypatch, tmp_path, signing_material
):
    _install_account_stream(monkeypatch)
    api, session, feed, capability, _payload = _entry_fixture(
        monkeypatch, tmp_path, signing_material, "settlement-entry"
    )
    feed._session_state["settlement_state"] = "unconfirmed"
    feed._session_state["settlement_readback_verified"] = False
    try:
        result = api.confirm_ctp_settlement_from_approval(capability)

        assert result is True
        assert feed.settlement_calls == [5.0]
        assert capability._settlement_used is True
        with pytest.raises(NormalizedApiError) as reused:
            api.confirm_ctp_settlement_from_approval(capability)

        assert reused.value.code == "ctp_settlement_authorization_required"
    finally:
        session.close()


def test_confirm_ctp_settlement_from_approval_rejects_identity_mismatch(
    monkeypatch, tmp_path, signing_material
):
    _install_account_stream(monkeypatch)
    api, session, feed, capability, _payload = _entry_fixture(
        monkeypatch,
        tmp_path,
        signing_material,
        "settlement-mismatch",
        payload_changes={"trading_day": "20260910"},
    )
    feed._session_state["settlement_state"] = "unconfirmed"
    try:
        with pytest.raises(NormalizedApiError) as raised:
            api.confirm_ctp_settlement_from_approval(capability)

        assert raised.value.code == "ctp_settlement_authorization_context_mismatch"
        assert feed.settlement_calls == []
    finally:
        session.close()


def test_confirm_ctp_settlement_from_approval_rejects_plain_capability_missing(
    monkeypatch, tmp_path, signing_material
):
    _install_account_stream(monkeypatch)
    api, session, _feed, capability, _payload = _entry_fixture(
        monkeypatch, tmp_path, signing_material, "settlement-plain"
    )
    try:
        with pytest.raises(NormalizedApiError) as raised:
            api.confirm_ctp_settlement_from_approval(None)

        assert raised.value.code == "ctp_settlement_authorization_required"
    finally:
        session.close()


def test_arm_from_approval_rejects_expired_capability(monkeypatch, tmp_path, signing_material):
    api, session, _feed, capability, _payload = _entry_fixture(
        monkeypatch,
        tmp_path,
        signing_material,
        "arm-expired",
    )

    class _FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(minutes=10)

    monkeypatch.setattr("bt_api_py.bt_api.datetime", _FutureClock)
    try:
        with pytest.raises(NormalizedApiError) as raised:
            api.arm_execution_from_approval(capability)

        assert raised.value.code == "ctp_approval_expired"
        assert session.config["market_data_only"] is True
    finally:
        session.close()
