"""Contract tests for production-only, non-authorizing CTP write approval evidence."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bt_api_py._contracts.errors import NormalizedApiError

SCHEMA = "ctp-production-managed-write-approval-v1"
ROOT_SCHEMA = "ctp-production-write-trust-root-v1"
PURPOSE = "ctp_production_managed_write"
ISSUER_KEY_ID = "prod-issuer-1"
ISSUER_ROLE = "independent_production_approver"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _artifact(schema: str, payload: dict[str, Any], signer) -> bytes:
    payload_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return json.dumps(
        {
            "schema_version": schema,
            "algorithm": "Ed25519",
            "payload": payload,
            "signature": _b64(signer.sign(payload_bytes)),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@pytest.fixture()
def crypto():
    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    now = datetime.now(UTC)
    root_signer = ed25519.Ed25519PrivateKey.generate()
    issuer_signer = ed25519.Ed25519PrivateKey.generate()
    root_payload = {
        "schema_version": ROOT_SCHEMA,
        "root_id": "production-root-1",
        "issued_at": _iso(now - timedelta(minutes=1)),
        "expires_at": _iso(now + timedelta(days=30)),
        "keys": {
            ISSUER_KEY_ID: {
                "public_key": _b64(issuer_signer.public_key().public_bytes_raw()),
                "role": ISSUER_ROLE,
                "purposes": [PURPOSE],
                "not_before": _iso(now - timedelta(minutes=1)),
                "expires_at": _iso(now + timedelta(days=1)),
            }
        },
        "revocation_snapshot": {
            "version": 7,
            "issued_at": _iso(now - timedelta(minutes=1)),
            "expires_at": _iso(now + timedelta(hours=12)),
            "revoked_approval_ids": [],
            "revoked_nonces": [],
        },
    }
    root_artifact = _artifact(ROOT_SCHEMA, root_payload, root_signer)
    anchor = root_signer.public_key().public_bytes_raw()
    return now, issuer_signer, root_signer, root_payload, root_artifact, anchor


def _context_values() -> dict[str, Any]:
    return {
        "environment": "production",
        "broker_id": "9999",
        "account_fingerprint": "a" * 64,
        "md_front": "tcp://md.production.invalid:41213",
        "td_front": "tcp://td.production.invalid:41205",
        "trading_day": "20260924",
        "connection_generation": 41,
        "strategy_id": "strategy-prod-1",
        "runtime_id": "runtime-prod-1",
        "artifact_sha256": "b" * 64,
        "config_sha256": "c" * 64,
    }


def _payload(now: datetime, **changes) -> dict[str, Any]:
    values = _context_values()
    values.update(
        {
            "schema_version": SCHEMA,
            "approval_id": "approval-prod-1",
            "nonce": "nonce-prod-1",
            "issuer_key_id": ISSUER_KEY_ID,
            "issuer_role": ISSUER_ROLE,
            "purpose": PURPOSE,
            "issued_at": _iso(now - timedelta(seconds=1)),
            "not_before": _iso(now - timedelta(seconds=1)),
            "expires_at": _iso(now + timedelta(minutes=10)),
            "revocation_snapshot_version": 7,
            "orders": [
                {
                    "intent_id": "intent-entry-1",
                    "instrument_id": "rb2701",
                    "exchange_id": "SHFE",
                    "side": "buy",
                    "offset": "open",
                    "hedge_flag": "1",
                    "volume": 2,
                    "limit_price": "3250.5",
                }
            ],
            "cancellations": [
                {
                    "cancel_id": "cancel-entry-1",
                    "target_order_ref": "000041",
                    "instrument_id": "rb2701",
                    "exchange_id": "SHFE",
                }
            ],
        }
    )
    values.update(changes)
    return values


def _verify(crypto, payload: dict[str, Any], *, root=None, anchor=None, context_values=None):
    from bt_api_py._ctp_production_execution_approval import (
        _new_runtime_context,
        verify_ctp_production_managed_write_approval,
    )

    now, issuer, _root_signer, _root_payload, root_artifact, trust_anchor = crypto
    owner = object()
    context = _new_runtime_context(context_values or _context_values(), owner=owner)
    return verify_ctp_production_managed_write_approval(
        _artifact(SCHEMA, payload, issuer),
        trust_root_artifact=root if root is not None else root_artifact,
        trust_anchor_public_key=anchor if anchor is not None else trust_anchor,
        context=context,
        owner=owner,
        minimum_revocation_snapshot_version=7,
        _now=now,
    )


def test_production_evidence_verifies_exact_runtime_and_scope(crypto):
    from bt_api_py._ctp_production_execution_approval import (
        PRODUCTION_APPROVAL_SCHEMA_VERSION,
        CtpProductionWriteApprovalEvidence,
    )

    now = crypto[0]
    result = _verify(crypto, _payload(now))
    assert type(result) is CtpProductionWriteApprovalEvidence
    assert result.approval_id == "approval-prod-1"
    assert result.environment == "production"
    assert result.payload["schema_version"] == PRODUCTION_APPROVAL_SCHEMA_VERSION
    assert result.payload["orders"][0]["volume"] == 2
    assert result.payload["cancellations"][0]["target_order_ref"] == "000041"
    assert result.as_dict()["authorizes_write"] is False
    assert not hasattr(result, "arm")
    assert not hasattr(result, "submit_order")
    assert not hasattr(result, "cancel_order")
    with pytest.raises(TypeError):
        result.payload["orders"][0]["volume"] = 99


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("environment", "simulation", "ctp_production_approval_environment_mismatch"),
        ("md_front", "tcp://other.invalid:41213", "ctp_production_approval_context_mismatch"),
        ("td_front", "tcp://other.invalid:41205", "ctp_production_approval_context_mismatch"),
        ("broker_id", "9998", "ctp_production_approval_context_mismatch"),
        ("account_fingerprint", "d" * 64, "ctp_production_approval_context_mismatch"),
        ("trading_day", "20260925", "ctp_production_approval_context_mismatch"),
        ("connection_generation", 42, "ctp_production_approval_context_mismatch"),
        ("strategy_id", "strategy-prod-2", "ctp_production_approval_context_mismatch"),
        ("runtime_id", "runtime-prod-2", "ctp_production_approval_context_mismatch"),
        ("artifact_sha256", "e" * 64, "ctp_production_approval_context_mismatch"),
        ("config_sha256", "f" * 64, "ctp_production_approval_context_mismatch"),
    ],
)
def test_signed_context_mismatch_is_rejected(crypto, field, value, code):
    now = crypto[0]
    payload = _payload(now, **{field: value})
    with pytest.raises(NormalizedApiError) as raised:
        _verify(crypto, payload)
    assert raised.value.code == code


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("purpose", "ctp_execution_approval", "ctp_production_approval_purpose_mismatch"),
        (
            "schema_version",
            "ctp-execution-approval-v2-simnow-binding",
            "ctp_production_approval_unknown_schema",
        ),
    ],
)
def test_simnow_and_legacy_approval_contracts_are_not_accepted(crypto, field, value, code):
    now, issuer, _root_signer, _root_payload, root_artifact, anchor = crypto
    payload = _payload(now, **{field: value})
    from bt_api_py._ctp_production_execution_approval import (
        _new_runtime_context,
        verify_ctp_production_managed_write_approval,
    )

    owner = object()
    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_production_managed_write_approval(
            _artifact(SCHEMA, payload, issuer),
            trust_root_artifact=root_artifact,
            trust_anchor_public_key=anchor,
            context=_new_runtime_context(_context_values(), owner=owner),
            owner=owner,
            minimum_revocation_snapshot_version=7,
            _now=now,
        )
    assert raised.value.code == code


def test_unsealed_mapping_context_and_wrong_owner_are_rejected(crypto):
    now, issuer, _root_signer, _root_payload, root_artifact, anchor = crypto
    from bt_api_py._ctp_production_execution_approval import (
        _new_runtime_context,
        verify_ctp_production_managed_write_approval,
    )

    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_production_managed_write_approval(
            _artifact(SCHEMA, _payload(now), issuer),
            trust_root_artifact=root_artifact,
            trust_anchor_public_key=anchor,
            context=_context_values(),  # type: ignore[arg-type]
            owner=object(),
            minimum_revocation_snapshot_version=7,
            _now=now,
        )
    assert raised.value.code == "ctp_production_approval_context_untrusted"
    owner = object()
    context = _new_runtime_context(_context_values(), owner=owner)
    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_production_managed_write_approval(
            _artifact(SCHEMA, _payload(now), issuer),
            trust_root_artifact=root_artifact,
            trust_anchor_public_key=anchor,
            context=context,
            owner=object(),
            minimum_revocation_snapshot_version=7,
            _now=now,
        )
    assert raised.value.code == "ctp_production_approval_context_owner_mismatch"


def test_independent_trust_anchor_and_root_signature_are_required(crypto):
    import base64

    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    now, _issuer, _root_signer, _root_payload, root_artifact, anchor = crypto
    payload = _payload(now)
    wrong_anchor = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    with pytest.raises(NormalizedApiError) as raised:
        _verify(crypto, payload, anchor=wrong_anchor)
    assert raised.value.code == "ctp_production_approval_trust_root_signature_invalid"

    root_value = json.loads(root_artifact)
    root_value["signature"] = base64.urlsafe_b64encode(b"x" * 64).decode("ascii").rstrip("=")
    with pytest.raises(NormalizedApiError) as raised:
        _verify(crypto, payload, root=json.dumps(root_value).encode())
    assert raised.value.code == "ctp_production_approval_trust_root_signature_invalid"


def test_issuer_signing_key_must_be_separate_from_trust_anchor(crypto):
    now, _issuer, root_signer, root_payload, _root_artifact, anchor = crypto
    root_payload["keys"][ISSUER_KEY_ID]["public_key"] = _b64(anchor)
    reused_key_root = _artifact(ROOT_SCHEMA, root_payload, root_signer)
    with pytest.raises(NormalizedApiError) as raised:
        _verify(crypto, _payload(now), root=reused_key_root)
    assert raised.value.code == "ctp_production_approval_issuer_key_must_differ_from_trust_anchor"


def test_approval_signature_revocation_and_expiry_are_enforced(crypto):
    now, issuer, root_signer, root_payload, root_artifact, anchor = crypto
    from bt_api_py._ctp_production_execution_approval import (
        _new_runtime_context,
        verify_ctp_production_managed_write_approval,
    )

    payload = _payload(now)
    # A changed payload with the old signature fails even when its shape is valid.
    artifact_value = json.loads(_artifact(SCHEMA, payload, issuer))
    artifact_value["payload"]["orders"][0]["volume"] = 3
    artifact = json.dumps(artifact_value).encode()
    owner = object()
    context = _new_runtime_context(_context_values(), owner=owner)
    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_production_managed_write_approval(
            artifact,
            trust_root_artifact=root_artifact,
            trust_anchor_public_key=anchor,
            context=context,
            owner=owner,
            minimum_revocation_snapshot_version=7,
            _now=now,
        )
    assert raised.value.code in {
        "ctp_production_approval_invalid_order_scope",
        "ctp_production_approval_signature_invalid",
    }

    expired = _payload(
        now,
        issued_at=_iso(now - timedelta(seconds=3)),
        not_before=_iso(now - timedelta(seconds=2)),
        expires_at=_iso(now - timedelta(seconds=1)),
    )
    with pytest.raises(NormalizedApiError) as raised:
        _verify(crypto, expired)
    assert raised.value.code == "ctp_production_approval_expired"

    root_payload["revocation_snapshot"]["revoked_approval_ids"] = ["approval-prod-1"]
    revoked_root = _artifact(ROOT_SCHEMA, root_payload, root_signer)
    with pytest.raises(NormalizedApiError) as raised:
        _verify(crypto, _payload(now), root=revoked_root)
    assert raised.value.code == "ctp_production_approval_revoked"


def test_revocation_snapshot_rollback_floor_is_enforced(crypto):
    now, _issuer, root_signer, root_payload, _root_artifact, _anchor = crypto
    root_payload["revocation_snapshot"]["version"] = 6
    older_root = _artifact(ROOT_SCHEMA, root_payload, root_signer)
    with pytest.raises(NormalizedApiError) as raised:
        _verify(crypto, _payload(now), root=older_root)
    assert raised.value.code == "ctp_production_approval_revocation_version_rollback"


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"orders": []}, "ctp_production_approval_order_scope_out_of_bounds"),
        (
            {
                "orders": [
                    {
                        "intent_id": "too-large",
                        "instrument_id": "rb2701",
                        "exchange_id": "SHFE",
                        "side": "buy",
                        "offset": "open",
                        "hedge_flag": "1",
                        "volume": 1001,
                        "limit_price": "1",
                    }
                ]
            },
            "ctp_production_approval_order_volume_out_of_bounds",
        ),
        (
            {
                "orders": [
                    {
                        "intent_id": "bad-price",
                        "instrument_id": "rb2701",
                        "exchange_id": "SHFE",
                        "side": "buy",
                        "offset": "open",
                        "hedge_flag": "1",
                        "volume": 1,
                        "limit_price": "NaN",
                    }
                ]
            },
            "ctp_production_approval_invalid_order_scope",
        ),
        (
            {
                "cancellations": [
                    {
                        "cancel_id": "bad-cancel",
                        "target_order_ref": "x",
                        "instrument_id": "rb2701",
                        "exchange_id": "SHFE",
                        "extra": True,
                    }
                ]
            },
            "ctp_production_approval_invalid_cancel_scope",
        ),
    ],
)
def test_bounded_exact_order_and_cancel_schema_rejects_unsafe_scope(crypto, changes, expected):
    now = crypto[0]
    payload = _payload(now, **changes)
    with pytest.raises(NormalizedApiError) as raised:
        _verify(crypto, payload)
    assert raised.value.code == expected


def test_invalid_front_duplicate_json_and_direct_evidence_construction_fail(crypto):
    now = crypto[0]
    with pytest.raises(NormalizedApiError) as raised:
        _verify(crypto, _payload(now, td_front="tcp://user:pass@td.invalid:41205"))
    assert raised.value.code == "ctp_production_approval_invalid_front"

    from bt_api_py._ctp_production_execution_approval import (
        CtpProductionWriteApprovalEvidence,
        _new_runtime_context,
        verify_ctp_production_managed_write_approval,
    )

    owner = object()
    context = _new_runtime_context(_context_values(), owner=owner)
    duplicate = b'{"schema_version":"wrong","schema_version":"wrong"}'
    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_production_managed_write_approval(
            duplicate,
            trust_root_artifact=crypto[4],
            trust_anchor_public_key=crypto[5],
            context=context,
            owner=owner,
            minimum_revocation_snapshot_version=7,
            _now=now,
        )
    assert raised.value.code == "ctp_production_approval_duplicate_json_key"
    with pytest.raises(TypeError):
        CtpProductionWriteApprovalEvidence(
            payload={},
            payload_sha256="0" * 64,
            trust_root_sha256="0" * 64,
            trust_anchor_key_sha256="0" * 64,
            revocation_snapshot={},
            _seal=None,
        )
