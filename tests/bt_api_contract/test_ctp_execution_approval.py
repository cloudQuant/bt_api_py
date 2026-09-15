"""Counterexamples for the U1a signed CTP approval contract.

These tests deliberately exercise the hostile inputs before the SDK has a
verifier implementation.  The signing helper is test-only and has no product
equivalent: production code must receive an independently signed artifact.
"""

from __future__ import annotations

import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from bt_api_py import BtApi, NormalizedApiError, TransportMode

SCHEMA = "ctp-execution-approval-v1"
ALGORITHM = "Ed25519"
KEY_ID = "operator-test-1"
FIXED_PUBLIC_KEY_B64 = "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"
FIXED_SIGNATURE_B64 = (
    "TwiANcyIexBY9hl7zaPjEXEvWy5jLUX048sPwV9kWL5kQpuSUrexNuWbljub_G0QeGYzdIkSDZFZbhRVAJiyCQ"
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _context() -> dict:
    return {
        "source": "synthetic_test",
        "context_source": "synthetic_test",
        "candidate_id": "candidate-u1a-synthetic",
        "strategy_id": "strategy-u1a",
        "strategy_identity_sha256": "1" * 64,
        "execution_cycle_id": "cycle-u1a-1",
        "configuration_sha256": "2" * 64,
        "backtrader_sha256": "3" * 64,
        "bt_api_py_sha256": "4" * 64,
        "bt_api_ctp_sha256": "5" * 64,
        "bt_api_base_sha256": "6" * 64,
        "native_sha256": "7" * 64,
        "dependency_hashes_sha256": "8" * 64,
        "authorized_instruments": [
            {"instrument_id": "m2701-C-3400", "exchange_id": "DCE"},
            {"instrument_id": "m2701-P-3200", "exchange_id": "DCE"},
            {"instrument_id": "m2701", "exchange_id": "DCE"},
        ],
        "primary_instrument": {"instrument_id": "m2701", "exchange_id": "DCE"},
        "account_fingerprint": "acct_synthetic_u1a",
        "trading_day": "20260911",
        "connection_generation": 7,
        "environment_profile": "synthetic_demo",
        "preflight_sha256": "9" * 64,
        "evidence_sha256": "a" * 64,
        "budget_policy_id": "future-o2-policy-u1a",
        "budget_limit": "100000",
        "future_reservation_id": "future-o2-reservation-u1a",
    }


def _payload(now: datetime | None = None, **changes) -> dict:
    now = now or datetime.now(UTC)
    context = _context()
    context.pop("source")
    payload = {
        **context,
        "schema_version": SCHEMA,
        "algorithm": ALGORITHM,
        "approval_id": "approval-u1a-1",
        "nonce": "nonce-u1a-1",
        "issuer_key_id": KEY_ID,
        "issuer_role": "independent_operator",
        "purpose": "ctp_execution_approval",
        "issued_at": _iso(now - timedelta(seconds=1)),
        "not_before": _iso(now - timedelta(seconds=1)),
        "expires_at": _iso(now + timedelta(minutes=5)),
        "revocation_snapshot_version": 3,
    }
    payload.update(changes)
    return payload


def _signed_artifact(payload, private_key) -> bytes:
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
            "schema_version": SCHEMA,
            "algorithm": ALGORITHM,
            "payload": payload,
            "signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@pytest.fixture()
def signing_material():
    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw()
    root = {
        "schema_version": "ctp-execution-trust-root-v1",
        "keys": {
            KEY_ID: {
                "public_key": base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("="),
                "role": "independent_operator",
                "purposes": ["ctp_execution_approval"],
                "not_before": _iso(datetime.now(UTC) - timedelta(minutes=1)),
                "expires_at": _iso(datetime.now(UTC) + timedelta(hours=1)),
            }
        },
        "revocation_snapshot": {
            "version": 3,
            "issued_at": _iso(datetime.now(UTC) - timedelta(minutes=1)),
            "expires_at": _iso(datetime.now(UTC) + timedelta(hours=1)),
            "revoked_approval_ids": [],
            "revoked_nonces": [],
        },
    }
    return private_key, root


def test_positive_verification_binds_complete_context(signing_material):
    private_key, root = signing_material
    from bt_api_py._ctp_execution_authorization import (
        verify_ctp_execution_approval,
    )

    result = verify_ctp_execution_approval(
        _signed_artifact(_payload(), private_key),
        trust_root=root,
        context=_context(),
    )
    assert result.approval_id == "approval-u1a-1"
    assert result.payload["connection_generation"] == 7
    assert [dict(item) for item in result.payload["authorized_instruments"]] == _context()[
        "authorized_instruments"
    ]


def test_non_synthetic_context_cannot_self_assert_runtime_identity(signing_material):
    private_key, root = signing_material
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    context = _context()
    context["source"] = "sdk_runtime"
    context["context_source"] = "sdk_runtime"
    payload = _payload(context_source="sdk_runtime")
    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_execution_approval(
            _signed_artifact(payload, private_key),
            trust_root=root,
            context=context,
        )
    assert raised.value.code == "ctp_approval_context_untrusted"


def test_runtime_context_builder_seals_and_recomputes_identity(monkeypatch, signing_material):
    from bt_api_py import BtApi
    from bt_api_py._ctp_execution_authorization import CtpExecutionApprovalContext

    api = object.__new__(BtApi)
    api.transport_mode = TransportMode.DIRECT
    api.exchange_feeds = {
        "CTP___FUTURE": SimpleNamespace(
            get_environment_info=lambda: {
                "verified": True,
                "environment": "demo",
                "profile": "simnow_demo",
            }
        )
    }
    monkeypatch.setattr(
        api,
        "_ctp_execution_runtime_identity",
        lambda: {
            "native_sha256": "a" * 64,
            "ctp_package_sha256": "b" * 64,
        },
    )
    monkeypatch.setattr(
        api,
        "_ctp_execution_runtime_python_identity",
        lambda: {
            "backtrader_sha256": "c" * 64,
            "bt_api_py_sha256": "d" * 64,
            "bt_api_base_sha256": "e" * 64,
            "dependency_hashes_sha256": "f" * 64,
        },
    )
    monkeypatch.setattr(
        api,
        "get_ctp_session_state",
        lambda _exchange: {
            "account_fingerprint": "acct_1234567890abcdef",
            "trading_day": "20260911",
            "connection_generation": 9,
            "environment_profile": "simnow_demo",
        },
    )
    monkeypatch.setattr(
        api,
        "get_environment_info",
        lambda _exchange: {"verified": True, "environment": "demo"},
    )
    context = api.build_ctp_execution_approval_context(
        _context(),
        exchange_name="CTP___FUTURE",
        configuration={"market_data_only": False},
        strategy_source=b"strategy-source",
        preflight={"complete": True},
        evidence={"complete": True},
    )
    assert type(context) is CtpExecutionApprovalContext
    values = context.as_dict()
    assert values["source"] == "sdk_runtime"
    assert values["context_source"] == "sdk_runtime"
    assert values["native_sha256"] == "a" * 64
    assert values["bt_api_py_sha256"] == "d" * 64
    assert values["connection_generation"] == 9
    assert values["account_fingerprint"] == "acct_1234567890abcdef"
    private_key, root = signing_material
    payload = _payload(**{field: value for field, value in values.items() if field != "source"})
    verified = api.verify_ctp_execution_approval(
        _signed_artifact(payload, private_key), trust_root=root, context=context
    )
    assert verified.bindings["connection_generation"] == 9


def test_runtime_context_builder_rejects_unmatched_deployment_manifest(monkeypatch):
    from bt_api_py import BtApi

    api = object.__new__(BtApi)
    api.transport_mode = TransportMode.DIRECT
    api.exchange_feeds = {
        "CTP___FUTURE": SimpleNamespace(
            get_environment_info=lambda: {
                "verified": True,
                "environment": "demo",
                "profile": "simnow_demo",
            }
        )
    }
    monkeypatch.setattr(
        api,
        "_ctp_execution_runtime_identity",
        lambda: {"native_sha256": "a" * 64, "ctp_package_sha256": "b" * 64},
    )
    monkeypatch.setattr(
        api,
        "_ctp_execution_runtime_python_identity",
        lambda: {
            "backtrader_sha256": "c" * 64,
            "bt_api_py_sha256": "d" * 64,
            "bt_api_base_sha256": "e" * 64,
            "dependency_hashes_sha256": "f" * 64,
        },
    )
    monkeypatch.setattr(
        api,
        "get_ctp_session_state",
        lambda _exchange: {
            "account_fingerprint": "acct_1234567890abcdef",
            "trading_day": "20260911",
            "connection_generation": 9,
            "environment_profile": "simnow_demo",
        },
    )
    monkeypatch.setattr(
        api,
        "get_environment_info",
        lambda _exchange: {"verified": True, "environment": "demo"},
    )
    with pytest.raises(NormalizedApiError) as raised:
        api.build_ctp_execution_approval_context(
            _context(),
            exchange_name="CTP___FUTURE",
            configuration={"market_data_only": False},
            strategy_source=b"strategy-source",
            preflight={"complete": True},
            evidence={"complete": True},
            deployment_manifest={"runtime_hashes": {"native_sha256": "0" * 64}},
        )
    assert raised.value.code == "ctp_approval_runtime_manifest_mismatch"


def test_fixed_signed_approval_vector_is_verified_without_a_product_signer():
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    fixed_now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)
    payload = _payload(
        fixed_now,
        approval_id="approval-fixed-rfc8032",
        nonce="nonce-fixed-rfc8032",
    )
    root = {
        "schema_version": "ctp-execution-trust-root-v1",
        "keys": {
            KEY_ID: {
                "public_key": FIXED_PUBLIC_KEY_B64,
                "role": "independent_operator",
                "purposes": ["ctp_execution_approval"],
                "not_before": _iso(fixed_now - timedelta(hours=1)),
                "expires_at": _iso(fixed_now + timedelta(hours=1)),
            }
        },
        "revocation_snapshot": {
            "version": 3,
            "issued_at": _iso(fixed_now - timedelta(hours=1)),
            "expires_at": _iso(fixed_now + timedelta(hours=1)),
            "revoked_approval_ids": [],
            "revoked_nonces": [],
        },
    }
    artifact = json.dumps(
        {
            "schema_version": SCHEMA,
            "algorithm": ALGORITHM,
            "payload": payload,
            "signature": FIXED_SIGNATURE_B64,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    result = verify_ctp_execution_approval(
        artifact,
        trust_root=root,
        context=_context(),
        _now=fixed_now,
    )
    assert result.approval_id == "approval-fixed-rfc8032"


@pytest.mark.parametrize(
    "change",
    [
        {"candidate_id": "attacker"},
        {"connection_generation": 8},
        {"bt_api_py_sha256": "f" * 64},
        {"authorized_instruments": _context()["authorized_instruments"][:2]},
        {"purpose": "ctp_execution_recovery"},
    ],
)
def test_tampered_or_wrong_purpose_artifact_is_rejected(signing_material, change):
    private_key, root = signing_material
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    artifact = _signed_artifact(_payload(**change), private_key)
    with pytest.raises(NormalizedApiError):
        verify_ctp_execution_approval(artifact, trust_root=root, context=_context())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strategy_identity_sha256", "b" * 64),
        ("execution_cycle_id", "cycle-u1a-2"),
        ("configuration_sha256", "b" * 64),
        ("backtrader_sha256", "b" * 64),
        ("bt_api_ctp_sha256", "b" * 64),
        ("bt_api_base_sha256", "b" * 64),
        ("native_sha256", "b" * 64),
        ("dependency_hashes_sha256", "b" * 64),
        ("primary_instrument", _context()["authorized_instruments"][0]),
        ("account_fingerprint", "acct_other_u1a"),
        ("trading_day", "20260912"),
        ("environment_profile", "production"),
        ("preflight_sha256", "b" * 64),
        ("evidence_sha256", "b" * 64),
        ("budget_policy_id", "other-policy"),
        ("budget_limit", "200000"),
        ("future_reservation_id", "other-reservation"),
    ],
)
def test_every_context_binding_is_compared_to_the_signed_payload(signing_material, field, value):
    private_key, root = signing_material
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    artifact = _signed_artifact(_payload(**{field: value}), private_key)
    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_execution_approval(artifact, trust_root=root, context=_context())
    assert raised.value.code == "ctp_approval_context_mismatch"


def test_payload_public_key_and_unknown_key_do_not_establish_trust(signing_material):
    private_key, root = signing_material
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    payload = _payload(public_key="self-supplied")
    artifact = _signed_artifact(payload, private_key)
    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_execution_approval(artifact, trust_root=root, context=_context())
    assert raised.value.code in {
        "ctp_approval_unknown_payload_key",
        "ctp_approval_invalid_artifact",
    }


def test_missing_trust_root_is_stable_fail_closed(signing_material):
    private_key, _root = signing_material
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_execution_approval(
            _signed_artifact(_payload(), private_key),
            trust_root=None,
            context=_context(),
        )
    assert raised.value.code == "BLOCKED_OPERATOR_TRUST_ROOT"


def test_duplicate_json_keys_and_nan_are_rejected(signing_material):
    _private_key, root = signing_material
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    duplicate = b'{"algorithm":"Ed25519","algorithm":"Ed25519","payload":{},"schema_version":"ctp-execution-approval-v1","signature":"AA"}'
    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_execution_approval(duplicate, trust_root=root, context=_context())
    assert raised.value.code == "ctp_approval_duplicate_json_key"
    with pytest.raises(NormalizedApiError):
        verify_ctp_execution_approval(
            b'{"algorithm":"Ed25519","payload":{"budget_limit":NaN},"schema_version":"ctp-execution-approval-v1","signature":"AA"}',
            trust_root=root,
            context=_context(),
        )


@pytest.mark.parametrize(
    ("artifact", "expected"),
    [
        (123, "ctp_approval_artifact_encoding_required"),
        (b"\xef\xbb\xbf{}", "ctp_approval_ambiguous_encoding"),
        (b"[]", "ctp_approval_invalid_artifact"),
        ("\ud800", "ctp_approval_ambiguous_encoding"),
    ],
)
def test_artifact_encoding_is_strict(signing_material, artifact, expected):
    _private_key, root = signing_material
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_execution_approval(artifact, trust_root=root, context=_context())
    assert raised.value.code == expected


@pytest.mark.parametrize(
    "change",
    [
        {"budget_limit": "1e3"},
        {"budget_limit": "0"},
        {"authorized_instruments": _context()["authorized_instruments"][:1]},
        {
            "authorized_instruments": _context()["authorized_instruments"]
            + [_context()["authorized_instruments"][0]]
        },
        {"issued_at": "2026-09-11T12:00:00Z"},
    ],
)
def test_payload_numeric_scope_and_time_types_are_strict(signing_material, change):
    private_key, root = signing_material
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    with pytest.raises(NormalizedApiError):
        verify_ctp_execution_approval(
            _signed_artifact(_payload(**change), private_key),
            trust_root=root,
            context=_context(),
        )


def test_untrusted_clock_and_invalid_signature_are_rejected(signing_material):
    private_key, root = signing_material
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    artifact = _signed_artifact(_payload(), private_key)
    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_execution_approval(
            artifact, trust_root=root, context=_context(), _now=datetime.now()
        )
    assert raised.value.code == "ctp_approval_clock_untrusted"
    tampered = json.loads(artifact)
    tampered["signature"] = "A"
    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_execution_approval(
            json.dumps(tampered, separators=(",", ":")),
            trust_root=root,
            context=_context(),
        )
    assert raised.value.code == "ctp_approval_invalid_signature_encoding"


def test_expired_and_not_before_approval_are_rejected(signing_material):
    private_key, root = signing_material
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    now = datetime.now(UTC)
    for changes, code in (
        (
            {
                "issued_at": _iso(now - timedelta(hours=2)),
                "not_before": _iso(now - timedelta(hours=2)),
                "expires_at": _iso(now - timedelta(hours=1)),
            },
            "ctp_approval_expired",
        ),
        (
            {"not_before": _iso(now + timedelta(minutes=1))},
            "ctp_approval_not_yet_valid",
        ),
    ):
        with pytest.raises(NormalizedApiError) as raised:
            verify_ctp_execution_approval(
                _signed_artifact(_payload(now, **changes), private_key),
                trust_root=root,
                context=_context(),
            )
        assert raised.value.code == code


def test_same_journal_allows_one_durable_redemption_then_rejects_restart_replay(
    signing_material, tmp_path: Path
):
    private_key, root = signing_material
    api = BtApi(
        execution_config={
            "market_data_only": True,
            "order_journal": str(tmp_path / "a.jsonl"),
        }
    )
    verified = api.verify_ctp_execution_approval(
        _signed_artifact(_payload(), private_key), trust_root=root, context=_context()
    )
    capability = api.redeem_ctp_execution_approval(verified, trust_root=root, context=_context())
    assert capability.approval_id == verified.approval_id
    assert api.get_execution_summary()["market_data_only"] is True
    api.close()

    recovered = BtApi(
        execution_config={
            "market_data_only": True,
            "order_journal": str(tmp_path / "a.jsonl"),
        }
    )
    with pytest.raises(NormalizedApiError) as raised:
        recovered.redeem_ctp_execution_approval(verified, trust_root=root, context=_context())
    assert raised.value.code == "ctp_approval_already_consumed"
    recovered.close()


def test_public_redeem_can_verify_a_signed_artifact_in_one_explicit_step(
    signing_material, tmp_path: Path
):
    private_key, root = signing_material
    api = BtApi(
        execution_config={
            "market_data_only": True,
            "order_journal": str(tmp_path / "direct.jsonl"),
        }
    )
    capability = api.redeem_ctp_execution_approval(
        _signed_artifact(_payload(), private_key),
        trust_root=root,
        context=_context(),
    )
    assert capability.purpose == "ctp_execution_approval"
    api.close()


def test_two_threads_cannot_double_spend_same_approval(signing_material, tmp_path: Path):
    private_key, root = signing_material
    api = BtApi(
        execution_config={
            "market_data_only": True,
            "order_journal": str(tmp_path / "b.jsonl"),
        }
    )
    verified = api.verify_ctp_execution_approval(
        _signed_artifact(_payload(), private_key), trust_root=root, context=_context()
    )

    def redeem():
        try:
            api.redeem_ctp_execution_approval(verified, trust_root=root, context=_context())
            return "ok"
        except NormalizedApiError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _item: redeem(), range(2)))
    assert outcomes.count("ok") == 1
    assert outcomes.count("ctp_approval_already_consumed") == 1
    api.close()


def test_two_api_instances_cannot_double_spend_same_journal_nonce(signing_material, tmp_path: Path):
    private_key, root = signing_material
    journal = tmp_path / "two-api.jsonl"
    first = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    second = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    artifact = _signed_artifact(_payload(), private_key)
    verified = [
        api.verify_ctp_execution_approval(artifact, trust_root=root, context=_context())
        for api in (first, second)
    ]

    def redeem(api, approval):
        try:
            return api.redeem_ctp_execution_approval(approval, trust_root=root, context=_context())
        except NormalizedApiError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                redeem,
                (first, second),
                verified,
            )
        )
    assert sum(type(result).__name__ == "CtpExecutionApprovalCapability" for result in results) == 1
    failures = [result for result in results if isinstance(result, NormalizedApiError)]
    assert len(failures) == 1
    assert failures[0].code in {
        "ctp_approval_already_consumed",
        "execution_session_locked_or_unavailable",
    }
    first.close()
    second.close()


def test_read_only_preauthorization_is_really_persisted_and_generation_is_not_overwritten(
    signing_material, tmp_path: Path
):
    private_key, root = signing_material
    journal = tmp_path / "readonly.jsonl"
    api = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    verified = api.verify_ctp_execution_approval(
        _signed_artifact(_payload(), private_key), trust_root=root, context=_context()
    )
    api.preauthorize_ctp_execution_approval(verified, trust_root=root, context=_context())
    row = json.loads(journal.read_text().splitlines()[-1])
    assert row["event"] == "ctp_execution_approval_pre_authorized"
    assert row["connection_generation"] == 7
    assert row["approval_id"] == verified.approval_id
    assert api.get_execution_summary()["market_data_only"] is True
    api.close()


def test_old_revocation_snapshot_and_revoked_approval_are_rejected(signing_material):
    private_key, root = signing_material
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    revoked_root = json.loads(json.dumps(root))
    revoked_root["revocation_snapshot"]["revoked_approval_ids"] = ["approval-u1a-1"]
    with pytest.raises(NormalizedApiError) as raised:
        verify_ctp_execution_approval(
            _signed_artifact(_payload(), private_key),
            trust_root=revoked_root,
            context=_context(),
        )
    assert raised.value.code == "ctp_approval_revoked"


def test_revocation_snapshot_version_is_fenced_by_the_same_journal(
    signing_material, tmp_path: Path
):
    private_key, root = signing_material
    journal = tmp_path / "revocations.jsonl"
    api = BtApi(execution_config={"market_data_only": False, "order_journal": str(journal)})
    first = api.verify_ctp_execution_approval(
        _signed_artifact(_payload(), private_key), trust_root=root, context=_context()
    )
    api.redeem_ctp_execution_approval(first, trust_root=root, context=_context())
    api.close()

    newer_root = json.loads(json.dumps(root))
    newer_root["revocation_snapshot"]["version"] = 4
    newer_payload = _payload(
        approval_id="approval-u1a-2", nonce="nonce-u1a-2", revocation_snapshot_version=4
    )
    newer = BtApi(execution_config={"market_data_only": False, "order_journal": str(journal)})
    verified_newer = newer.verify_ctp_execution_approval(
        _signed_artifact(newer_payload, private_key),
        trust_root=newer_root,
        context=_context(),
    )
    newer.redeem_ctp_execution_approval(verified_newer, trust_root=newer_root, context=_context())
    newer.close()

    # A restart must retain the higher durable snapshot version.  Replaying
    # an otherwise valid artifact against an older operator snapshot is a
    # rollback, even though its Ed25519 signature still verifies.
    old = BtApi(execution_config={"market_data_only": False, "order_journal": str(journal)})
    with pytest.raises(NormalizedApiError) as raised:
        old.redeem_ctp_execution_approval(first, trust_root=root, context=_context())
    assert raised.value.code in {
        "ctp_approval_revocation_version_stale",
        "ctp_approval_already_consumed",
        "ctp_approval_revocation_version_rollback",
    }
    old.close()


def test_fsync_failure_never_returns_a_consumable_capability(
    signing_material, tmp_path: Path, monkeypatch
):
    private_key, root = signing_material
    from bt_api_py import _execution_session as session_module

    journal = tmp_path / "fsync.jsonl"
    api = BtApi(execution_config={"market_data_only": False, "order_journal": str(journal)})
    verified = api.verify_ctp_execution_approval(
        _signed_artifact(_payload(), private_key), trust_root=root, context=_context()
    )

    def fail_fsync(_fd):
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(session_module.os, "fsync", fail_fsync)
    with pytest.raises(NormalizedApiError) as raised:
        api.redeem_ctp_execution_approval(verified, trust_root=root, context=_context())
    assert raised.value.code in {
        "persistence_failed",
        "execution_session_locked_or_unavailable",
    }
    api.close()


def test_consumption_started_fences_restart_after_completion_failure(
    signing_material, tmp_path: Path, monkeypatch
):
    private_key, root = signing_material
    journal = tmp_path / "pending.jsonl"
    api = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    verified = api.verify_ctp_execution_approval(
        _signed_artifact(_payload(), private_key), trust_root=root, context=_context()
    )
    session = api._execution_session
    original_journal = session._journal

    def fail_completion(event, row, **kwargs):
        if event == "ctp_execution_approval_consumed":
            raise NormalizedApiError("journal", "persistence_failed", definite_reject=True)
        return original_journal(event, row, **kwargs)

    monkeypatch.setattr(session, "_journal", fail_completion)
    with pytest.raises(NormalizedApiError):
        api.redeem_ctp_execution_approval(verified, trust_root=root, context=_context())
    with pytest.raises(NormalizedApiError) as raised:
        api.redeem_ctp_execution_approval(verified, trust_root=root, context=_context())
    assert raised.value.code == "ctp_approval_consumption_uncertain"
    api.close()

    recovered = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    with pytest.raises(NormalizedApiError) as raised:
        recovered.redeem_ctp_execution_approval(verified, trust_root=root, context=_context())
    assert raised.value.code == "ctp_approval_consumption_uncertain"
    recovered.close()


def test_journal_approval_payload_tampering_is_rejected_after_restart(
    signing_material, tmp_path: Path
):
    private_key, root = signing_material
    journal = tmp_path / "tampered.jsonl"
    api = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    verified = api.verify_ctp_execution_approval(
        _signed_artifact(_payload(), private_key), trust_root=root, context=_context()
    )
    api.redeem_ctp_execution_approval(verified, trust_root=root, context=_context())
    api.close()
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    for row in rows:
        if row["event"] == "ctp_execution_approval_consumed":
            row["connection_generation"] = 999
    journal.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows))
    recovered = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    with pytest.raises(NormalizedApiError) as raised:
        recovered.redeem_ctp_execution_approval(verified, trust_root=root, context=_context())
    assert raised.value.code == "unreadable_journal"
    recovered.close()


def test_torn_approval_tail_is_rejected_after_restart(signing_material, tmp_path: Path):
    private_key, root = signing_material
    journal = tmp_path / "torn.jsonl"
    api = BtApi(execution_config={"market_data_only": False, "order_journal": str(journal)})
    verified = api.verify_ctp_execution_approval(
        _signed_artifact(_payload(), private_key), trust_root=root, context=_context()
    )
    api.redeem_ctp_execution_approval(verified, trust_root=root, context=_context())
    api.close()
    with journal.open("ab") as stream:
        stream.write(b'{"event":"ctp_execution_approval_consumed"')
    with pytest.raises(NormalizedApiError) as raised:
        BtApi(execution_config={"market_data_only": False, "order_journal": str(journal)})
    assert raised.value.code == "unreadable_journal"


def test_public_revocation_snapshot_uses_the_same_journal(signing_material, tmp_path: Path):
    _private_key, root = signing_material
    journal = tmp_path / "revocation-only.jsonl"
    api = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    result = api.record_ctp_execution_approval_revocation_snapshot(trust_root=root)
    assert result["revocation_snapshot_version"] == 3
    assert result["updated"] is True
    assert json.loads(journal.read_text().splitlines()[-1])["event"] == (
        "ctp_execution_approval_revocation_snapshot"
    )
    api.close()

    recovered = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    same = recovered.record_ctp_execution_approval_revocation_snapshot(trust_root=root)
    assert same["updated"] is False
    recovered.close()


def test_public_api_does_not_accept_mapping_as_opaque_redeem(tmp_path: Path):
    api = BtApi(
        execution_config={
            "market_data_only": True,
            "order_journal": str(tmp_path / "c.jsonl"),
        }
    )
    with pytest.raises(NormalizedApiError) as raised:
        api.redeem_ctp_execution_approval(
            cast("Any", {"approval_id": "approval-u1a-1"}), trust_root={}, context={}
        )
    assert raised.value.code in {
        "BLOCKED_OPERATOR_TRUST_ROOT",
        "ctp_approval_opaque_required",
    }
    api.close()


def test_verified_result_constructor_is_not_a_public_approval_issuer():
    from bt_api_py import CtpExecutionApproval

    with pytest.raises(TypeError):
        cast("Any", CtpExecutionApproval)(
            payload={},
            payload_sha256="0" * 64,
            trust_root_sha256="0" * 64,
            signature=b"",
            revocation_snapshot={},
        )


def test_session_rejects_unwritable_journal_before_returning_capability(
    signing_material, tmp_path: Path
):
    private_key, root = signing_material
    journal = tmp_path / "directory"
    journal.mkdir()
    api = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    verified = api.verify_ctp_execution_approval(
        _signed_artifact(_payload(), private_key), trust_root=root, context=_context()
    )
    with pytest.raises(NormalizedApiError):
        api.redeem_ctp_execution_approval(verified, trust_root=root, context=_context())
    api.close()


def test_old_mapping_recovery_entry_stays_rejected(tmp_path: Path):
    api = BtApi(
        execution_config={
            "market_data_only": True,
            "order_journal": str(tmp_path / "d.jsonl"),
        }
    )
    with pytest.raises(NormalizedApiError) as raised:
        api.arm_execution_recovery(proof={}, recovery_token_sha256="x")
    assert raised.value.code == "ctp_execution_authorization_required"
    api.close()


def _runtime_approval_fixture(tmp_path, monkeypatch, name):
    """Build the same sealed runtime fixture used by the independent probes."""
    from bt_api_py import _execution_session as session_module

    monkeypatch.setattr(
        session_module,
        "_ledger_registry_root",
        lambda: tmp_path / "approval-ledger-registry",
    )
    api = BtApi(
        execution_config={
            "market_data_only": True,
            "order_journal": str(tmp_path / f"{name}.jsonl"),
        }
    )
    feed = SimpleNamespace(
        state={
            "account_fingerprint": "acct_1234567890abcdef",
            "trading_day": "20260911",
            "connection_generation": 7,
            "environment_profile": "simnow_demo",
        }
    )
    feed.get_session_state = lambda: dict(feed.state)
    feed.get_environment_info = lambda: {
        "verified": True,
        "environment": "demo",
        "simulated": True,
        "profile": feed.state["environment_profile"],
    }
    feed.get_execution_gate_state = lambda: {"managed": True, "armed": False}
    feed.disarm_execution_gate = lambda *_args: None
    feed.disconnect = lambda: None
    api.exchange_feeds["CTP___FUTURE"] = feed
    monkeypatch.setattr(
        api,
        "_ctp_execution_runtime_identity",
        lambda: {"native_sha256": "a" * 64, "ctp_package_sha256": "b" * 64},
    )
    monkeypatch.setattr(
        api,
        "_ctp_execution_runtime_python_identity",
        lambda: {
            "backtrader_sha256": "c" * 64,
            "bt_api_py_sha256": "d" * 64,
            "bt_api_base_sha256": "e" * 64,
            "dependency_hashes_sha256": "f" * 64,
        },
    )
    monkeypatch.setattr(
        api,
        "get_environment_info",
        lambda _exchange: {
            "verified": True,
            "environment": "demo",
            "profile": feed.state["environment_profile"],
        },
    )
    strategy_file = tmp_path / f"{name}-strategy.txt"
    strategy_file.write_text("synthetic strategy version 1\n")
    context = api.build_ctp_execution_approval_context(
        _context(),
        exchange_name="CTP___FUTURE",
        configuration={"mode": "synthetic-read-only"},
        strategy_source=strategy_file,
        preflight={"complete": True},
        evidence={"complete": True},
    )
    return api, feed, strategy_file, context


def _runtime_payload(context, now=None, **changes):
    values = context.as_dict()
    values.pop("source")
    values.update(changes)
    return _payload(now, **values)


@pytest.mark.parametrize(
    "drift",
    [
        ("connection_generation", 8),
        ("account_fingerprint", "acct_1234567890abcdee"),
        ("trading_day", "20260912"),
        ("strategy_source", "synthetic strategy version 2\n"),
    ],
)
def test_redeem_recollects_current_runtime_scope_and_material_before_consuming(
    signing_material, tmp_path: Path, monkeypatch, drift
):
    private_key, root = signing_material
    api, feed, strategy_file, context = _runtime_approval_fixture(
        tmp_path, monkeypatch, "recollect"
    )
    proof = _signed_artifact(_runtime_payload(context), private_key)
    field, value = drift
    if field == "strategy_source":
        strategy_file.write_text(value)
    else:
        feed.state[field] = value
    with pytest.raises(NormalizedApiError) as raised:
        api.redeem_ctp_execution_approval(
            proof,
            trust_root=root,
            context=context,
        )
    assert raised.value.code == "ctp_approval_context_mismatch"
    api.close()


def test_same_ctp_account_cannot_consume_through_two_journals(
    signing_material, tmp_path: Path, monkeypatch
):
    private_key, root = signing_material
    first, _feed1, _path1, context1 = _runtime_approval_fixture(
        tmp_path, monkeypatch, "journal-one"
    )
    second, _feed2, _path2, context2 = _runtime_approval_fixture(
        tmp_path, monkeypatch, "journal-two"
    )
    proof = _signed_artifact(_runtime_payload(context1), private_key)

    def redeem(api, context):
        try:
            api.redeem_ctp_execution_approval(
                proof,
                trust_root=root,
                context=context,
            )
            return "ok"
        except NormalizedApiError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(redeem, (first, second), (context1, context2)))
    assert outcomes.count("ok") == 1
    assert sum(value != "ok" for value in outcomes) == 1
    first.close()
    second.close()


def test_same_ctp_account_cannot_replay_after_writer_handoff(
    signing_material, tmp_path: Path, monkeypatch
):
    """Closing a writer must not make the account nonce reusable elsewhere."""
    private_key, root = signing_material
    first, _feed1, _path1, context1 = _runtime_approval_fixture(
        tmp_path, monkeypatch, "handoff-one"
    )
    proof = _signed_artifact(_runtime_payload(context1), private_key)

    first.redeem_ctp_execution_approval(
        proof,
        trust_root=root,
        context=context1,
    )
    first.close()
    second, _feed2, _path2, context2 = _runtime_approval_fixture(
        tmp_path, monkeypatch, "handoff-two"
    )

    with pytest.raises(NormalizedApiError) as raised:
        second.redeem_ctp_execution_approval(
            proof,
            trust_root=root,
            context=context2,
        )
    assert raised.value.code == "authenticated_account_journal_conflict"
    second.close()


@pytest.mark.parametrize(
    "operation, mutation",
    [("redeem", "generation"), ("preauthorize", "generation"), ("redeem", "strategy")],
)
def test_runtime_change_during_lease_commit_is_rejected_before_approval_event(
    signing_material, tmp_path: Path, monkeypatch, operation, mutation
):
    private_key, root = signing_material
    api, feed, strategy_file, context = _runtime_approval_fixture(
        tmp_path, monkeypatch, f"lease-race-{operation}-{mutation}"
    )
    proof = _signed_artifact(_runtime_payload(context), private_key)
    journal = Path(api._execution_session.path)
    lock_path = Path(str(journal) + ".lock")
    from bt_api_py import _execution_session as session_module

    original_fsync = os.fsync
    mutated = False

    def change_at_lease_fsync(fd):
        nonlocal mutated
        if not mutated and lock_path.exists() and os.fstat(fd).st_ino == lock_path.stat().st_ino:
            mutated = True
            if mutation == "generation":
                feed.state["connection_generation"] = 8
            else:
                strategy_file.write_text("synthetic strategy changed during lease\n")
        return original_fsync(fd)

    monkeypatch.setattr(session_module.os, "fsync", change_at_lease_fsync)
    method = getattr(api, f"{operation}_ctp_execution_approval")
    with pytest.raises(NormalizedApiError) as raised:
        method(proof, trust_root=root, context=context)
    assert raised.value.code == "ctp_approval_context_mismatch"
    assert mutated is True
    if journal.exists():
        events = [json.loads(line)["event"] for line in journal.read_text().splitlines() if line]
        assert not {
            "ctp_execution_approval_pre_authorized",
            "ctp_execution_approval_consumption_started",
            "ctp_execution_approval_consumed",
        }.intersection(events)
    api.close()


def test_runtime_change_during_consumed_commit_is_rejected_after_durable_fence(
    signing_material, tmp_path: Path, monkeypatch
):
    private_key, root = signing_material
    api, feed, _strategy_file, context = _runtime_approval_fixture(
        tmp_path, monkeypatch, "consumed-commit-race"
    )
    proof = _signed_artifact(_runtime_payload(context), private_key)
    journal = Path(api._execution_session.path)
    from bt_api_py import _execution_session as session_module

    original_fsync = os.fsync
    observed = False

    def change_at_consumed_fsync(fd):
        nonlocal observed
        if not observed and journal.exists() and os.fstat(fd).st_ino == journal.stat().st_ino:
            rows = [json.loads(line) for line in journal.read_text().splitlines() if line]
            if rows and rows[-1]["event"] == "ctp_execution_approval_consumed":
                observed = True
                feed.state["connection_generation"] = 8
        return original_fsync(fd)

    monkeypatch.setattr(session_module.os, "fsync", change_at_consumed_fsync)
    with pytest.raises(NormalizedApiError) as raised:
        api.redeem_ctp_execution_approval(proof, trust_root=root, context=context)
    assert raised.value.code == "ctp_approval_context_mismatch"
    assert observed is True

    feed.state["connection_generation"] = 7
    with pytest.raises(NormalizedApiError) as raised:
        api.redeem_ctp_execution_approval(proof, trust_root=root, context=context)
    assert raised.value.code == "ctp_approval_already_consumed"
    api.close()


def test_consumed_fsync_failure_stays_permanently_uncertain_in_process(
    signing_material, tmp_path: Path, monkeypatch
):
    private_key, root = signing_material
    api, _feed, _strategy_file, context = _runtime_approval_fixture(
        tmp_path, monkeypatch, "fsync-after-consumed"
    )
    journal = Path(api._execution_session.path)
    proof = _signed_artifact(_runtime_payload(context), private_key)
    from bt_api_py import _execution_session as session_module

    original_fsync = os.fsync
    injected = False

    def fail_after_consumed_flush(fd):
        nonlocal injected
        if not injected and journal.exists() and os.fstat(fd).st_ino == journal.stat().st_ino:
            rows = [json.loads(line) for line in journal.read_text().splitlines() if line]
            if rows and rows[-1].get("event") == "ctp_execution_approval_consumed":
                injected = True
                raise OSError("synthetic fsync failure after consumed row flush")
        return original_fsync(fd)

    monkeypatch.setattr(session_module.os, "fsync", fail_after_consumed_flush)
    with pytest.raises(NormalizedApiError) as raised:
        api.redeem_ctp_execution_approval(
            proof,
            trust_root=root,
            context=context,
        )
    assert raised.value.code == "persistence_failed"
    monkeypatch.setattr(session_module.os, "fsync", original_fsync)
    with pytest.raises(NormalizedApiError) as raised:
        api.redeem_ctp_execution_approval(
            proof,
            trust_root=root,
            context=context,
        )
    assert raised.value.code == "ctp_approval_consumption_uncertain"
    assert injected is True
    assert api._execution_session.persistence_failed is True
    api.close()


def _root_with_revocations(root, version, *, approval_ids=(), nonces=()):
    result = json.loads(json.dumps(root))
    result["revocation_snapshot"].update(
        version=version,
        revoked_approval_ids=sorted(approval_ids),
        revoked_nonces=sorted(nonces),
    )
    return result


def test_newer_snapshot_cannot_forget_prior_revocation_before_preauthorization(
    signing_material, tmp_path: Path
):
    private_key, root = signing_material
    journal = tmp_path / "revocation-monotonic.jsonl"
    api = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    api.record_ctp_execution_approval_revocation_snapshot(
        trust_root=_root_with_revocations(root, 4, approval_ids=("revoked-a",))
    )
    proof = _signed_artifact(
        _payload(
            approval_id="revoked-a",
            nonce="nonce-revoked-a",
            revocation_snapshot_version=5,
        ),
        private_key,
    )
    with pytest.raises(NormalizedApiError) as raised:
        api.preauthorize_ctp_execution_approval(
            proof,
            trust_root=_root_with_revocations(root, 5),
            context=_context(),
        )
    assert raised.value.code == "ctp_approval_revoked"
    api.close()


def test_nested_revocation_in_preauthorization_survives_restart(signing_material, tmp_path: Path):
    private_key, root = signing_material
    journal = tmp_path / "revocation-nested.jsonl"
    first = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    first.preauthorize_ctp_execution_approval(
        _signed_artifact(
            _payload(
                approval_id="innocent-preauth",
                nonce="nonce-innocent-preauth",
            ),
            private_key,
        ),
        trust_root=_root_with_revocations(root, 3, approval_ids=("revoked-b",)),
        context=_context(),
    )
    first.close()

    recovered = BtApi(execution_config={"market_data_only": True, "order_journal": str(journal)})
    proof = _signed_artifact(
        _payload(
            approval_id="revoked-b",
            nonce="nonce-revoked-b",
            revocation_snapshot_version=4,
        ),
        private_key,
    )
    with pytest.raises(NormalizedApiError) as raised:
        recovered.redeem_ctp_execution_approval(
            proof,
            trust_root=_root_with_revocations(root, 4),
            context=_context(),
        )
    assert raised.value.code == "ctp_approval_revoked"
    recovered.close()
