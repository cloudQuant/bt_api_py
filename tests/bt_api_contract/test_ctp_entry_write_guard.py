"""Per-write CTP entry-approval revalidation at the session dispatch fence."""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bt_api_py import NormalizedApiError

from .test_ctp_entry_approval_arm import (
    ENTRY_PURPOSE,
    ENTRY_SCHEMA,
    _entry_payload,
    _iso,
    _signed_entry_artifact,
)
from .test_ctp_execution_approval import _context as _approval_context_seed
from .test_execution_arming import (
    ACCOUNT_FINGERPRINT,
    BUNDLE_INSTRUMENTS,
    PROFILE,
    STRATEGY_IDENTITY,
    TRADING_DAY,
    VENUE,
    _api_for_arm,
    _arm,
    _bound_order,
    _install_account_stream,
    _ManagedFeed,
    _ready_state,
)
from .test_execution_arming import (
    _proof as _legacy_proof,
)
from .test_execution_recovery import CYCLE


@pytest.fixture()
def entry_signing_material():
    from datetime import UTC, datetime, timedelta

    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw()
    root = {
        "schema_version": "ctp-execution-trust-root-v1",
        "keys": {
            "operator-entry-1": {
                "public_key": __import__("base64")
                .urlsafe_b64encode(public_key)
                .decode("ascii")
                .rstrip("="),
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


def _runtime_entry_payload(context_values):
    from bt_api_py._ctp_execution_authorization import SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION

    payload = _entry_payload()
    payload.update({key: value for key, value in context_values.items() if key != "source"})
    payload["schema_version"] = (
        SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION
        if "credential_binding_key_id" in context_values
        else ENTRY_SCHEMA
    )
    payload["purpose"] = ENTRY_PURPOSE
    payload["candidate_id"] = "candidate-iter23-25"
    payload["strategy_id"] = "iter22-midfreq"
    payload["execution_cycle_id"] = CYCLE
    payload["authorized_instruments"] = [
        {"exchange_id": "CZCE", "instrument_id": "SA701"},
        {"exchange_id": "CZCE", "instrument_id": "SA701C1080"},
        {"exchange_id": "CZCE", "instrument_id": "SA701P1080"},
    ]
    payload["primary_instrument"] = {"exchange_id": "CZCE", "instrument_id": "SA701"}
    # The proof extension is signed independently from the context collector.
    payload.setdefault("receipt_sha256", "1" * 64)
    payload.setdefault("source_hashes_sha256", "4" * 64)
    payload.setdefault("ctp_package_sha256", "3" * 64)
    payload["bt_api_ctp_sha256"] = payload["ctp_package_sha256"]
    return payload


def _runtime_entry_fixture(
    monkeypatch,
    tmp_path,
    entry_signing_material,
    name,
    *,
    profile=PROFILE,
    credential_binding_provider=None,
    arm=True,
):
    from bt_api_py import _execution_session as session_module
    from bt_api_py._contracts import TransportMode
    from bt_api_py.bt_api import BtApi

    private_key, trust_root = entry_signing_material
    monkeypatch.setattr(
        session_module,
        "_ledger_registry_root",
        lambda: tmp_path / f"{name}-ledger-registry",
    )
    strategy_path = tmp_path / f"{name}-strategy.py"
    strategy_path.write_text("def strategy(): return 'approved-v1'\n", encoding="utf-8")
    strategy_identity = hashlib.sha256(strategy_path.read_bytes()).hexdigest()
    journal_path = tmp_path / f"{name}.jsonl"
    provisioned = os.name == "nt"
    if provisioned:
        journal_path.touch()
    session = session_module._ExecutionSession(
        {
            "market_data_only": True,
            "require_order_journal": True,
            "order_journal": str(journal_path),
            "windows_ctp_journal_preprovisioned": provisioned,
            "account_ids": {},
            "required_environments": {VENUE: "demo"},
            "strategy_id": "iter22-midfreq",
            "strategy_identity_sha256": strategy_identity,
            "account_maximum_loss_bps": None,
            "account_risk_max_age_seconds": "2",
        },
        exchange_names=(VENUE,),
    )
    state = _ready_state(environment_profile=profile)
    feed = _ManagedFeed(state)
    feed.get_environment_info = lambda: {
        "verified": True,
        "environment": "demo",
        "profile": state["environment_profile"],
    }
    td_front = "tcp://synthetic-td"
    md_front = "tcp://synthetic-md"
    feed._execution_bound_td_front = td_front
    feed._execution_bound_md_front = md_front
    feed._trader = SimpleNamespace(
        front=td_front,
        _bound_front=td_front,
        _session_native_front=td_front,
        _connection_generation=state["connection_generation"],
    )
    feed._md_client = SimpleNamespace(front=md_front, connection_generation=11)
    feed._md_stream_generation = 4
    api = object.__new__(BtApi)
    api.transport_mode = TransportMode.DIRECT
    api.exchange_feeds = {VENUE: feed}
    api.exchange_kwargs = {VENUE: {"auto_settlement_confirm": False}}
    api.data_queues = {VENUE: __import__("queue").Queue()}
    api._subscription_streams = []
    api._subscription_flags = {}
    api._execution_session = session
    api._ctp_execution_capability = object()
    api._ctp_private_ingress_fences = {}
    api._ctp_private_ingress_queues = {}
    feed.configure_execution_gate(api._ctp_execution_capability)
    api.list_exchanges = lambda: [VENUE]
    api.get_ctp_session_state = lambda _exchange: feed.get_session_state()
    api.get_environment_info = lambda _exchange: feed.get_environment_info()
    api._ctp_execution_runtime_identity = lambda: {
        "native_sha256": "2" * 64,
        "ctp_package_sha256": "3" * 64,
    }
    api._ctp_execution_runtime_python_identity = lambda: {
        "backtrader_sha256": "9" * 64,
        "bt_api_py_sha256": "a" * 64,
        "bt_api_base_sha256": "b" * 64,
        "dependency_hashes_sha256": "5" * 64,
    }

    seed = _approval_context_seed()
    seed.update(
        {
            "candidate_id": "candidate-iter23-25",
            "strategy_id": "iter22-midfreq",
            "strategy_identity_sha256": STRATEGY_IDENTITY,
            "execution_cycle_id": CYCLE,
            "authorized_instruments": [
                {"exchange_id": "CZCE", "instrument_id": item.split(".", 1)[1]}
                for item in BUNDLE_INSTRUMENTS
            ],
            "primary_instrument": {"exchange_id": "CZCE", "instrument_id": "SA701"},
            "account_fingerprint": ACCOUNT_FINGERPRINT,
            "trading_day": TRADING_DAY,
            "connection_generation": 3,
            "environment_profile": profile,
            "native_sha256": "2" * 64,
            "bt_api_ctp_sha256": "3" * 64,
            "backtrader_sha256": "9" * 64,
            "bt_api_py_sha256": "a" * 64,
            "bt_api_base_sha256": "b" * 64,
            "dependency_hashes_sha256": "5" * 64,
        }
    )
    credential_binding_verifier = None
    if credential_binding_provider is not None:
        from bt_api_py.bt_api import _issue_ctp_controlled_test_authority_for_core

        credential_binding_verifier = api._create_ctp_credential_binding_verifier_for_test(
            credential_binding_provider,
            authority=_issue_ctp_controlled_test_authority_for_core(),
        )
    try:
        context = api.build_ctp_execution_approval_context(
            seed,
            exchange_name=VENUE,
            configuration={"mode": "synthetic-read-only"},
            strategy_source=strategy_path,
            preflight={"complete": True},
            evidence={"complete": True},
            credential_binding_verifier=credential_binding_verifier,
        )
    except Exception:
        session.close()
        raise
    payload = _runtime_entry_payload(context.as_dict())
    artifact = _signed_entry_artifact(payload, private_key)
    if payload["schema_version"] != ENTRY_SCHEMA:
        artifact_value = __import__("json").loads(artifact)
        artifact_value["schema_version"] = payload["schema_version"]
        artifact = (
            __import__("json")
            .dumps(
                artifact_value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            .encode("utf-8")
        )
    capability = api.redeem_ctp_execution_approval(
        artifact,
        trust_root=trust_root,
        context=context,
    )
    if arm:
        _install_account_stream(monkeypatch)
        try:
            api.arm_execution_from_approval(capability)
        except Exception:
            session.close()
            raise
    return api, session, feed, capability, payload, strategy_path


def _bound_entry_order(session):
    request, budget, binding = _bound_order(
        session,
        session._arm_proof,
        "SA701",
        order_ref_number=101,
        exchange_id="CZCE",
        cycle=CYCLE,
    )
    request = replace(
        request,
        strategy_identity_sha256=session.config["strategy_identity_sha256"],
    )
    return request, budget, binding


@pytest.mark.parametrize(
    "mutation", ["account", "trading_day", "generation", "artifact", "approval"]
)
def test_entry_submit_rejects_candidate_binding_before_intent_or_native_write(
    monkeypatch, tmp_path, entry_signing_material, mutation
):
    api, session, feed, capability, _payload, strategy_path = _runtime_entry_fixture(
        monkeypatch, tmp_path, entry_signing_material, f"submit-{mutation}"
    )
    request, budget, _binding = _bound_entry_order(session)
    native_calls = []
    preauthorize_calls = []
    journal_before = session.path.read_bytes()

    def drift_after_first_gate():
        preauthorize_calls.append(True)
        if mutation == "account":
            feed._session_state["account_fingerprint"] = "f" * 16
        elif mutation == "trading_day":
            feed._session_state["trading_day"] = "20260910"
        elif mutation == "generation":
            feed._session_state["connection_generation"] = 4
        elif mutation == "artifact":
            strategy_path.write_text("def strategy(): return 'changed-v2'\n", encoding="utf-8")
        else:
            approval = capability._approval
            object.__setattr__(approval, "signature", b"changed-approval-signature")

    def ReqOrderInsert():
        native_calls.append("ReqOrderInsert")
        return {"order_id": "must-not-be-created"}

    try:
        with pytest.raises(NormalizedApiError) as raised:
            session.invoke(
                "make_order",
                VENUE,
                request,
                ReqOrderInsert,
                preauthorize=drift_after_first_gate,
                pre_dispatch=session.finalize_dispatch,
                budget_capability=budget,
            )
        assert raised.value.code == "ctp_order_identity_binding_missing_or_mismatch"
        assert preauthorize_calls == []
        assert native_calls == []
        assert session.path.read_bytes() == journal_before
    finally:
        session.close()


def test_entry_cancel_rejects_candidate_action_before_intent_or_native_write(
    monkeypatch, tmp_path, entry_signing_material
):
    _api, session, feed, _capability, _payload, _strategy_path = _runtime_entry_fixture(
        monkeypatch, tmp_path, entry_signing_material, "cancel-generation"
    )
    _order_request, budget, binding = _bound_entry_order(session)
    client_order_id = binding["client_order_id"]
    runtime_order_id = binding["runtime_order_id"]
    session.orders[(VENUE, client_order_id)] = {
        "symbol": "SA701",
        "exchange_name": VENUE,
        "exchange_id": "CZCE",
        "account_id": ACCOUNT_FINGERPRINT,
        "client_order_id": client_order_id,
        "runtime_order_id": runtime_order_id,
        "execution_cycle_id": CYCLE,
        "connection_generation": session._arm_proof["connection_generation"],
        "terminal": False,
    }
    action_id = session.next_runtime_action_id(
        VENUE,
        account_id=ACCOUNT_FINGERPRINT,
        runtime_order_id=runtime_order_id,
    )
    from bt_api_py import CancelOrderRequest

    request = CancelOrderRequest(
        symbol="SA701",
        account_id=ACCOUNT_FINGERPRINT,
        client_order_id=client_order_id,
        exchange_id="CZCE",
        runtime_order_id=runtime_order_id,
        runtime_action_id=action_id,
    )
    native_calls = []
    preauthorize_calls = []
    journal_before = session.path.read_bytes()

    def drift_after_first_gate():
        preauthorize_calls.append(True)
        feed._session_state["connection_generation"] = 4

    def ReqOrderAction():
        native_calls.append("ReqOrderAction")
        return {"order_id": "must-not-be-cancelled"}

    try:
        with pytest.raises(NormalizedApiError) as raised:
            session.invoke(
                "cancel_order",
                VENUE,
                request,
                ReqOrderAction,
                preauthorize=drift_after_first_gate,
                pre_dispatch=session.finalize_dispatch,
                budget_capability=budget,
            )
        assert raised.value.code == "ctp_cancel_identity_binding_missing_or_mismatch"
        assert preauthorize_calls == []
        assert native_calls == []
        assert session.path.read_bytes() == journal_before
    finally:
        session.close()


def test_official_simnow_fake_credential_tag_cannot_supply_active_front_binding(
    monkeypatch, tmp_path, entry_signing_material
):
    """A changing credential tag cannot turn the intentionally closed SimNow arm on."""
    binding = {
        "credential_binding_key_id": "runtime-binding-key-1",
        "credential_binding_hmac_sha256": "a" * 64,
    }
    with pytest.raises(NormalizedApiError) as raised:
        _runtime_entry_fixture(
            monkeypatch,
            tmp_path,
            entry_signing_material,
            "entry-set1-tag-drift",
            profile="set1_group1",
            credential_binding_provider=lambda: dict(binding),
            arm=False,
        )
    assert raised.value.code == "ctp_credential_binding_active_front_unavailable"


def test_managed_ctp_without_sealed_entry_guard_and_caller_lambda_stay_closed(
    monkeypatch, tmp_path
):
    from bt_api_py import _execution_session as session_module

    monkeypatch.setattr(
        session_module,
        "_ledger_registry_root",
        lambda: tmp_path / "legacy-entry-ledger-registry",
    )
    _install_account_stream(monkeypatch)
    api, session, _state = _api_for_arm(tmp_path)
    try:
        _arm(api, _legacy_proof())
        native_calls = []
        with pytest.raises(NormalizedApiError) as missing:
            api._finalize_ctp_execution_dispatch(
                session,
                VENUE,
                {"operation": "make_order"},
            )
        assert missing.value.code == "ctp_entry_authorization_guard_invalid"
        assert native_calls == []

    finally:
        session.close()

    forged_api, forged_session, _state = _api_for_arm(tmp_path)
    try:
        _arm(forged_api, _legacy_proof())
        forged_session._entry_write_guard = lambda *_args, **_kwargs: None
        native_calls = []
        with pytest.raises(NormalizedApiError) as forged:
            forged_api._finalize_ctp_execution_dispatch(
                forged_session,
                VENUE,
                {"operation": "cancel_order"},
            )
        assert forged.value.code == "ctp_entry_authorization_guard_invalid"
        assert native_calls == []
    finally:
        forged_session.close()


@pytest.mark.asyncio
async def test_async_worker_cannot_start_from_candidate_binding_without_handoff(
    monkeypatch, tmp_path, entry_signing_material
):
    _api, session, feed, _capability, _payload, _strategy_path = _runtime_entry_fixture(
        monkeypatch, tmp_path, entry_signing_material, "async-handoff"
    )
    request, budget, _binding = _bound_entry_order(session)
    native_calls = []
    held = {}
    journal_before = session.path.read_bytes()

    def bind_context(context):
        held["context"] = context

    async def queued_worker():
        feed._session_state["connection_generation"] = 4
        held["context"]["_async_handoff"] = True
        session.finalize_dispatch(held["context"])
        native_calls.append("ReqOrderInsert")
        return {"order_id": "must-not-be-created"}

    try:
        with pytest.raises(NormalizedApiError) as raised:
            await session.async_invoke(
                "make_order",
                VENUE,
                request,
                queued_worker,
                pre_dispatch=session.finalize_dispatch,
                on_context=bind_context,
                budget_capability=budget,
            )
        assert raised.value.code == "ctp_order_identity_binding_missing_or_mismatch"
        assert held == {}
        assert native_calls == []
        assert session.path.read_bytes() == journal_before
    finally:
        session.close()
