"""RED contract cases for the versioned public CTP recovery approval."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
from datetime import UTC, datetime, timedelta

import pytest

from bt_api_py import NormalizedApiError

from .test_ctp_execution_approval import (
    _context,
    _iso,
    _payload,
    _runtime_approval_fixture,
    _runtime_payload,
    _signed_artifact,
)
from .test_execution_recovery import (
    ACCOUNT,
    BUNDLE_INSTRUMENTS,
    CYCLE,
    STRATEGY_IDENTITY,
    TRADING_DAY,
    VENUE,
    barrier,
    bundle_position,
    bundle_proof,
    bundle_remote_trade,
    make_session,
    order_request,
    order_update,
    public_recovery_api,
    snapshot,
    write_bundle_crashed_journal,
)
from .test_execution_recovery import (
    context as recovery_context,
)

RECOVERY_SCHEMA = "ctp-execution-recovery-approval-v1"
RECOVERY_PURPOSE = "ctp_execution_recovery"


@pytest.fixture()
def signing_material():
    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes_raw()
    root = {
        "schema_version": "ctp-execution-trust-root-v1",
        "keys": {
            "operator-test-1": {
                "public_key": base64.urlsafe_b64encode(public_key)
                .decode("ascii")
                .rstrip("="),
                "role": "independent_operator",
                "purposes": ["ctp_execution_approval", RECOVERY_PURPOSE],
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


def _recovery_payload(**changes):
    from bt_api_py._ctp_execution_authorization import (
        recovery_action_digest,
    )

    now = datetime.now(UTC)
    actions = [
        {
            "action_id": "close:0",
            "action_kind": "close",
            "instrument_id": "m2701",
            "exchange_id": "DCE",
            "side": "sell",
            "position_side": "long",
            "offset": "close",
            "quantity": "1",
            "quantity_unit": "contracts",
            "account_fingerprint": "acct_synthetic_u1a",
            "trading_day": "20260911",
            "connection_generation": 7,
            "environment_profile": "synthetic_demo",
            "candidate_id": "candidate-u1a-synthetic",
            "execution_cycle_id": "cycle-u1a-1",
            "expires_at": _iso(now + timedelta(minutes=5)),
        },
        {
            "action_id": "close:1",
            "action_kind": "close",
            "instrument_id": "m2701-C-3400",
            "exchange_id": "DCE",
            "side": "sell",
            "position_side": "long",
            "offset": "close",
            "quantity": "1",
            "quantity_unit": "contracts",
            "account_fingerprint": "acct_synthetic_u1a",
            "trading_day": "20260911",
            "connection_generation": 7,
            "environment_profile": "synthetic_demo",
            "candidate_id": "candidate-u1a-synthetic",
            "execution_cycle_id": "cycle-u1a-1",
            "expires_at": _iso(now + timedelta(minutes=5)),
        },
    ]
    payload = _payload(now)
    payload.update(
        {
            "schema_version": RECOVERY_SCHEMA,
            "purpose": RECOVERY_PURPOSE,
            "recovery_scope_version": "ctp-execution-recovery-v1",
            "recovery_plan_sha256": "b" * 64,
            "recovery_token_sha256": "c" * 64,
            "receipt_sha256": "d" * 64,
            "source_hashes_sha256": "e" * 64,
            "ctp_package_sha256": "f" * 64,
            "recovery_actions": actions,
            "recovery_action_sha256": recovery_action_digest(actions),
        }
    )
    payload.update(changes)
    return payload


def _signed_recovery_artifact(payload, private_key):
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
            "schema_version": RECOVERY_SCHEMA,
            "algorithm": "Ed25519",
            "payload": payload,
            "signature": base64.urlsafe_b64encode(signature)
            .decode("ascii")
            .rstrip("="),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _public_bundle_recovery_fixture(monkeypatch, tmp_path, signing_material, name):
    """Build the bounded synthetic lower bridge used by CP02 regression cases."""

    private_key, root = signing_material
    from bt_api_py import _execution_session as session_module
    from bt_api_py._ctp_execution_authorization import (
        recovery_action_digest,
        recovery_plan_digest,
    )

    monkeypatch.setattr(
        session_module,
        "_ledger_registry_root",
        lambda: tmp_path / f"{name}-ledger-registry",
    )
    path = tmp_path / f"{name}.jsonl"
    write_bundle_crashed_journal(path, instruments=BUNDLE_INSTRUMENTS[:2])
    api, session, feed = public_recovery_api(path)
    current_proof = bundle_proof(4)
    session.prepare_recovery(
        current_proof,
        lambda: recovery_context(current_proof),
        venue=VENUE,
    )
    current = snapshot(
        positions=[bundle_position(item) for item in BUNDLE_INSTRUMENTS[:2]],
        trades=[
            bundle_remote_trade(item, trade_id=f"TRADE{index}")
            for index, item in enumerate(BUNDLE_INSTRUMENTS[:2], start=1)
        ],
    )
    plan = session.build_recovery_plan(current, barrier=barrier(session, current))
    now = datetime.now(UTC)
    payload = _payload(now)
    payload.update(
        {
            "schema_version": RECOVERY_SCHEMA,
            "purpose": RECOVERY_PURPOSE,
            "candidate_id": "candidate-u1b",
            "strategy_id": "iter22-midfreq",
            "strategy_identity_sha256": STRATEGY_IDENTITY,
            "execution_cycle_id": CYCLE,
            "authorized_instruments": [
                {"exchange_id": exchange, "instrument_id": instrument}
                for exchange, instrument in (
                    item.split(".", 1) for item in BUNDLE_INSTRUMENTS
                )
            ],
            "primary_instrument": {"exchange_id": "CZCE", "instrument_id": "SA701"},
            "account_fingerprint": ACCOUNT,
            "trading_day": TRADING_DAY,
            "connection_generation": 4,
            "environment_profile": "simnow_demo",
            "bt_api_ctp_sha256": "3" * 64,
            "native_sha256": "2" * 64,
            "backtrader_sha256": "9" * 64,
            "bt_api_py_sha256": "a" * 64,
            "bt_api_base_sha256": "b" * 64,
            "dependency_hashes_sha256": "5" * 64,
            "preflight_sha256": "6" * 64,
            "recovery_scope_version": "ctp-execution-recovery-v1",
            "recovery_plan_sha256": recovery_plan_digest(plan),
            "recovery_token_sha256": plan["recovery_token_sha256"],
            "receipt_sha256": "1" * 64,
            "source_hashes_sha256": "4" * 64,
            "ctp_package_sha256": "3" * 64,
        }
    )
    payload["recovery_actions"] = [
        {
            "action_id": f"close:{index}",
            "action_kind": "close",
            "instrument_id": allowed["symbol"],
            "exchange_id": allowed["exchange_id"],
            "side": allowed["side"],
            "position_side": allowed["position_side"],
            "offset": allowed["offset"],
            "quantity": allowed["quantity"],
            "quantity_unit": allowed["quantity_unit"],
            "account_fingerprint": ACCOUNT,
            "trading_day": TRADING_DAY,
            "connection_generation": 4,
            "environment_profile": "simnow_demo",
            "candidate_id": "candidate-u1b",
            "execution_cycle_id": CYCLE,
            "expires_at": payload["expires_at"],
        }
        for index, allowed in enumerate(plan["allowed_closes"])
    ]
    payload["recovery_action_sha256"] = recovery_action_digest(
        payload["recovery_actions"]
    )
    payload["approval_id"] = f"approval-u1b-{name}"
    payload["nonce"] = f"nonce-u1b-{name}"
    artifact = _signed_recovery_artifact(payload, private_key)
    approval_context = _context()
    approval_context.update(
        {
            field: payload[field]
            for field in approval_context
            if field != "source" and field in payload
        }
    )
    api._ctp_execution_runtime_python_identity = lambda: {
        "backtrader_sha256": payload["backtrader_sha256"],
        "bt_api_py_sha256": payload["bt_api_py_sha256"],
        "bt_api_base_sha256": payload["bt_api_base_sha256"],
        "dependency_hashes_sha256": payload["dependency_hashes_sha256"],
    }
    api._ctp_execution_arm_context = lambda _venue: recovery_context(
        bundle_proof(feed.state["connection_generation"])
    )
    capability = api.redeem_ctp_execution_approval(
        artifact,
        trust_root=root,
        context=approval_context,
    )
    return api, session, feed, path, plan, capability, session_module


def test_versioned_recovery_approval_verifies_and_keeps_full_action_binding(
    signing_material,
):
    private_key, root = signing_material
    root = {**root, "keys": {"operator-test-1": {**root["keys"]["operator-test-1"]}}}
    root["keys"]["operator-test-1"]["purposes"] = [
        "ctp_execution_approval",
        RECOVERY_PURPOSE,
    ]
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    approval = verify_ctp_execution_approval(
        _signed_recovery_artifact(_recovery_payload(), private_key),
        trust_root=root,
        context=_context(),
    )
    assert approval.purpose == RECOVERY_PURPOSE
    assert approval.recovery_plan_sha256 == "b" * 64
    assert len(approval.recovery_actions) == 2
    assert approval.recovery_actions[0]["instrument_id"] == "m2701"


@pytest.mark.parametrize(
    "change",
    [
        {"recovery_actions": []},
        {"recovery_actions": [{"action_id": "close:0"}]},
        {"recovery_actions": [{"action_id": "close:0", "offset": "open"}]},
        {"purpose": "ctp_execution_approval"},
    ],
)
def test_recovery_action_shape_cannot_be_reduced_to_a_symbol_or_open_action(
    signing_material, change
):
    private_key, root = signing_material
    root = {**root, "keys": {"operator-test-1": {**root["keys"]["operator-test-1"]}}}
    root["keys"]["operator-test-1"]["purposes"] = [RECOVERY_PURPOSE]
    from bt_api_py._ctp_execution_authorization import verify_ctp_execution_approval

    with pytest.raises(NormalizedApiError):
        verify_ctp_execution_approval(
            _signed_recovery_artifact(_recovery_payload(**change), private_key),
            trust_root=root,
            context=_context(),
        )


def test_recovery_action_digest_changes_when_side_or_offset_changes():
    from bt_api_py._ctp_execution_authorization import recovery_action_digest

    actions = _recovery_payload()["recovery_actions"]
    altered = [dict(actions[0]), dict(actions[1])]
    altered[1]["side"] = "buy"
    assert recovery_action_digest(actions) != recovery_action_digest(altered)


def test_public_recovery_mapping_remains_audit_only(tmp_path):
    from bt_api_py import BtApi

    api = BtApi(
        execution_config={
            "market_data_only": True,
            "order_journal": str(tmp_path / "recovery.jsonl"),
        }
    )
    with pytest.raises(NormalizedApiError) as raised:
        api.arm_execution_recovery(
            authorization={"purpose": RECOVERY_PURPOSE},
            recovery_token_sha256="c" * 64,
        )
    assert raised.value.code == "ctp_execution_authorization_required"
    api.close()


def test_recovery_capability_retains_one_shot_plan_fields_after_redemption(
    signing_material, tmp_path
):
    private_key, root = signing_material
    payload = _recovery_payload()
    artifact = _signed_recovery_artifact(payload, private_key)
    from bt_api_py import BtApi

    api = BtApi(
        execution_config={
            "market_data_only": True,
            "order_journal": str(tmp_path / "recovery-redemption.jsonl"),
        }
    )
    capability = api.redeem_ctp_execution_approval(
        artifact,
        trust_root=root,
        context=_context(),
    )
    assert capability.purpose == RECOVERY_PURPOSE
    assert capability.recovery_token_sha256 == payload["recovery_token_sha256"]
    assert capability.recovery_action_sha256 == payload["recovery_action_sha256"]
    api.close()


def test_public_recovery_arm_reaches_core_and_budget_stays_blocked(
    monkeypatch, signing_material, tmp_path
):
    """The public opaque route may arm the lower bridge while O2 still blocks writes."""

    private_key, root = signing_material
    from bt_api_py import _execution_session as session_module
    from bt_api_py._ctp_execution_authorization import (
        recovery_action_digest,
        recovery_plan_digest,
    )

    monkeypatch.setattr(
        session_module,
        "_ledger_registry_root",
        lambda: tmp_path / "ledger-registry",
    )
    path = tmp_path / "recovery-arm.jsonl"
    write_bundle_crashed_journal(path, instruments=BUNDLE_INSTRUMENTS[:2])
    api, session, feed = public_recovery_api(path)
    current_proof = bundle_proof(4)
    session.prepare_recovery(
        current_proof,
        lambda: recovery_context(current_proof),
        venue=VENUE,
    )
    current = snapshot(
        positions=[bundle_position(item) for item in BUNDLE_INSTRUMENTS[:2]],
        trades=[
            bundle_remote_trade(item, trade_id=f"TRADE{index}")
            for index, item in enumerate(BUNDLE_INSTRUMENTS[:2], start=1)
        ],
    )
    plan = session.build_recovery_plan(current, barrier=barrier(session, current))
    now = datetime.now(UTC)
    payload = _payload(now)
    payload.update(
        {
            "schema_version": RECOVERY_SCHEMA,
            "purpose": RECOVERY_PURPOSE,
            "candidate_id": "candidate-u1b",
            "strategy_id": "iter22-midfreq",
            "strategy_identity_sha256": STRATEGY_IDENTITY,
            "execution_cycle_id": CYCLE,
            "authorized_instruments": [
                {"exchange_id": exchange, "instrument_id": instrument}
                for exchange, instrument in (
                    item.split(".", 1) for item in BUNDLE_INSTRUMENTS
                )
            ],
            "primary_instrument": {"exchange_id": "CZCE", "instrument_id": "SA701"},
            "account_fingerprint": ACCOUNT,
            "trading_day": TRADING_DAY,
            "connection_generation": 4,
            "environment_profile": "simnow_demo",
            "bt_api_ctp_sha256": "3" * 64,
            "native_sha256": "2" * 64,
            "backtrader_sha256": "9" * 64,
            "bt_api_py_sha256": "a" * 64,
            "bt_api_base_sha256": "b" * 64,
            "dependency_hashes_sha256": "5" * 64,
            "preflight_sha256": "6" * 64,
            "recovery_scope_version": "ctp-execution-recovery-v1",
            "recovery_plan_sha256": recovery_plan_digest(plan),
            "recovery_token_sha256": plan["recovery_token_sha256"],
            "receipt_sha256": "1" * 64,
            "source_hashes_sha256": "4" * 64,
            "ctp_package_sha256": "3" * 64,
        }
    )
    payload["recovery_actions"] = [
        {
            "action_id": f"close:{index}",
            "action_kind": "close",
            "instrument_id": allowed["symbol"],
            "exchange_id": allowed["exchange_id"],
            "side": allowed["side"],
            "position_side": allowed["position_side"],
            "offset": allowed["offset"],
            "quantity": allowed["quantity"],
            "quantity_unit": allowed["quantity_unit"],
            "account_fingerprint": ACCOUNT,
            "trading_day": TRADING_DAY,
            "connection_generation": 4,
            "environment_profile": "simnow_demo",
            "candidate_id": "candidate-u1b",
            "execution_cycle_id": CYCLE,
            "expires_at": payload["expires_at"],
        }
        for index, allowed in enumerate(plan["allowed_closes"])
    ]
    payload["recovery_action_sha256"] = recovery_action_digest(
        payload["recovery_actions"]
    )
    payload["approval_id"] = "approval-u1b-arm"
    payload["nonce"] = "nonce-u1b-arm"
    artifact = _signed_recovery_artifact(payload, private_key)
    approval_context = _context()
    approval_context.update(
        {
            field: payload[field]
            for field in approval_context
            if field != "source" and field in payload
        }
    )
    api._ctp_execution_runtime_python_identity = lambda: {
        "backtrader_sha256": payload["backtrader_sha256"],
        "bt_api_py_sha256": payload["bt_api_py_sha256"],
        "bt_api_base_sha256": payload["bt_api_base_sha256"],
        "dependency_hashes_sha256": payload["dependency_hashes_sha256"],
    }
    api._ctp_execution_arm_context = lambda _venue: recovery_context(current_proof)
    try:
        capability = api.redeem_ctp_execution_approval(
            artifact,
            trust_root=root,
            context=approval_context,
        )
        result = api.arm_execution_recovery(
            authorization=capability,
            recovery_token_sha256=plan["recovery_token_sha256"],
        )
        assert result["recovery_only"] is True
        assert feed.get_execution_gate_state()["armed"] is True
        assert any(
            "ctp_execution_recovery_arm_consumed" in line
            for line in path.read_text().splitlines()
        )
        with pytest.raises(NormalizedApiError) as raised:
            session.require_write(
                "make_order",
                placement=True,
                venue=VENUE,
                recovery_action=True,
            )
        assert raised.value.code == "ctp_recovery_budget_capability_missing"
    finally:
        session.close()


def test_legacy_demo_category_accepts_a_verified_distinct_demo_profile(
    monkeypatch, tmp_path
):
    """A legacy journal's durable category must not be confused with profile."""

    from bt_api_py import _execution_session as session_module

    monkeypatch.setattr(
        session_module,
        "_ledger_registry_root",
        lambda: tmp_path / "legacy-ledger-registry",
    )
    journal = tmp_path / "legacy-category.jsonl"
    session = make_session(journal)
    # Model the category-only identity loaded from a pre-U1b journal.  The
    # next bind must upgrade it with the verified current profile.
    session._ctp_execution_identity = {
        "provider": "CTP",
        "environment": "demo",
        "account_id": ACCOUNT,
        "account_fingerprint": ACCOUNT,
    }
    session._arm_venue = VENUE
    try:
        session.bind_ctp_approval_identity(
            VENUE,
            account_fingerprint=ACCOUNT,
            environment_profile="astra_synthetic_demo",
        )
        assert session._ctp_execution_identity["environment"] == "demo"
        assert (
            session._ctp_execution_identity["environment_profile"]
            == "astra_synthetic_demo"
        )
    finally:
        session.close()


def test_redeemed_capability_keeps_the_sealed_material_collector(
    signing_material, monkeypatch, tmp_path
):
    """Recovery validation must retain the SDK-owned source references."""

    private_key, root = signing_material
    api, _feed, _strategy_file, context = _runtime_approval_fixture(
        tmp_path, monkeypatch, "sealed-recovery-material"
    )
    try:
        capability = api.redeem_ctp_execution_approval(
            _signed_artifact(_runtime_payload(context), private_key),
            trust_root=root,
            context=context,
        )
        assert capability._context is context
    finally:
        api.close()


def test_recovery_consumed_commit_rechecks_generation_before_return(
    monkeypatch, signing_material, tmp_path
):
    api, session, feed, journal, plan, capability, session_module = (
        _public_bundle_recovery_fixture(
            monkeypatch, tmp_path, signing_material, "consumed-generation"
        )
    )
    original_fsync = os.fsync
    observed = False

    def mutate_after_consumed_row(fd):
        nonlocal observed
        if not observed and journal.exists():
            rows = [
                json.loads(line) for line in journal.read_text().splitlines() if line
            ]
            if rows and rows[-1].get("event") == "ctp_execution_recovery_arm_consumed":
                observed = True
                feed.state["connection_generation"] = 5
        return original_fsync(fd)

    monkeypatch.setattr(session_module.os, "fsync", mutate_after_consumed_row)
    try:
        with pytest.raises(NormalizedApiError) as raised:
            api.arm_execution_recovery(
                authorization=capability,
                recovery_token_sha256=plan["recovery_token_sha256"],
            )
        assert raised.value.code == "ctp_execution_authorization_context_mismatch"
        assert observed is True
        assert feed.get_execution_gate_state()["armed"] is False
        assert plan["recovery_token_sha256"] in session._recovery_used_tokens
        feed.state["connection_generation"] = 4
        with pytest.raises(NormalizedApiError):
            api.arm_execution_recovery(
                authorization=capability,
                recovery_token_sha256=plan["recovery_token_sha256"],
            )
    finally:
        session.close()


def test_sealed_recovery_material_change_after_redeem_is_rejected(
    signing_material, monkeypatch, tmp_path
):
    private_key, root = signing_material
    api, _feed, strategy_file, context = _runtime_approval_fixture(
        tmp_path, monkeypatch, "sealed-material-change"
    )
    try:
        capability = api.redeem_ctp_execution_approval(
            _signed_artifact(_runtime_payload(context), private_key),
            trust_root=root,
            context=context,
        )
        strategy_file.write_text("synthetic strategy changed after redeem\n")
        with pytest.raises(NormalizedApiError) as raised:
            api._validate_ctp_recovery_material(
                capability,
                operation="arm_execution_recovery",
            )
        assert raised.value.code == "ctp_execution_authorization_material_mismatch"
    finally:
        api.close()


def test_dispatch_guard_runs_after_durable_intent_and_blocks_lower_transport(
    tmp_path,
):
    """A final write gate must run after the intent fsync and before transport."""

    journal = tmp_path / "dispatch-guard.jsonl"
    session = make_session(journal)
    session.config["market_data_only"] = False
    session._acquire_lock()
    request = order_request(client_order_id="000000000077")
    lower_calls = []
    guard_observations = []

    def final_guard(context):
        rows = [json.loads(line) for line in journal.read_text().splitlines() if line]
        guard_observations.append((context["operation"], rows[-1]["event"]))
        raise NormalizedApiError(
            context["operation"], "ctp_approval_expired", definite_reject=True
        )

    try:
        result = session.invoke(
            "make_order",
            VENUE,
            request,
            lambda: lower_calls.append(request.client_order_id),
            pre_dispatch=final_guard,
        )
        assert result["status"] == "rejected"
        assert lower_calls == []
        assert guard_observations == [("make_order", "intent")]
        assert any(
            json.loads(line)["event"] == "intent"
            for line in journal.read_text().splitlines()
            if line
        )
    finally:
        session.close()


def test_async_dispatch_guard_runs_before_lower_transport(tmp_path):
    """The async managed path shares the same post-WAL final gate."""

    journal = tmp_path / "async-dispatch-guard.jsonl"
    session = make_session(journal)
    session.config["market_data_only"] = False
    session._acquire_lock()
    request = order_request(client_order_id="000000000078")
    lower_calls = []

    def final_guard(context):
        rows = [json.loads(line) for line in journal.read_text().splitlines() if line]
        assert context["operation"] == "make_order"
        assert rows[-1]["event"] == "intent"
        raise NormalizedApiError(
            context["operation"], "ctp_approval_expired", definite_reject=True
        )

    async def lower():
        lower_calls.append(request.client_order_id)

    async def run():
        return await session.async_invoke(
            "make_order",
            VENUE,
            request,
            lower,
            pre_dispatch=final_guard,
        )

    try:
        result = asyncio.run(run())
        assert result["status"] == "rejected"
        assert lower_calls == []
    finally:
        session.close()


def test_async_direct_worker_handoff_runs_inside_worker_before_lower_call():
    """DirectBackend sync fallback gates the worker immediately before transport."""

    from bt_api_py import BtApi
    from bt_api_py._direct_backend import DirectBackend

    events = []
    feed = object()
    backend = DirectBackend(lambda _venue: feed, {VENUE: feed})
    backend.make_order = lambda *_args, **_kwargs: events.append(
        ("lower", threading.get_ident())
    )
    api = object.__new__(BtApi)
    api._backend = backend
    loop_thread = threading.get_ident()

    async def run():
        await api._async_backend_call(
            "make_order",
            VENUE,
            object(),
            pre_dispatch=lambda: events.append(("handoff", threading.get_ident())),
        )

    asyncio.run(run())
    assert [kind for kind, _thread_id in events] == ["handoff", "lower"]
    assert events[0][1] == events[1][1] != loop_thread


def test_cancel_dispatch_guard_runs_after_cancel_intent(tmp_path):
    """Cancels also retain their durable intent when the final gate rejects."""

    from bt_api_py import CancelOrderRequest

    journal = tmp_path / "cancel-dispatch-guard.jsonl"
    session = make_session(journal)
    session.config["market_data_only"] = False
    session._acquire_lock()
    order = order_request(client_order_id="000000000079")
    session.invoke(
        "make_order",
        VENUE,
        order,
        lambda: order_update(client_order_id=order.client_order_id, order_id="SYS79"),
    )
    cancel = CancelOrderRequest(
        symbol=order.symbol,
        account_id=order.account_id,
        client_order_id=order.client_order_id,
        order_id="SYS79",
        exchange_id=order.exchange_id,
    )
    lower_calls = []

    def final_guard(context):
        rows = [json.loads(line) for line in journal.read_text().splitlines() if line]
        assert context["operation"] == "cancel_order"
        assert rows[-1]["event"] == "cancel_intent"
        raise NormalizedApiError(
            context["operation"], "ctp_approval_expired", definite_reject=True
        )

    try:
        result = session.invoke(
            "cancel_order",
            VENUE,
            cancel,
            lambda: lower_calls.append(cancel.order_id),
            pre_dispatch=final_guard,
        )
        assert result["execution_unknown"] is True
        assert lower_calls == []
    finally:
        session.close()


def test_final_recovery_gate_accepts_precollected_context_without_late_io(
    monkeypatch, signing_material, tmp_path
):
    """The bounded handoff gate must not recollect native material after its inputs are ready."""

    api, session, feed, _path, plan, capability, _session_module = (
        _public_bundle_recovery_fixture(
            monkeypatch, tmp_path, signing_material, "precollected-final-gate"
        )
    )
    try:
        api.arm_execution_recovery(
            authorization=capability,
            recovery_token_sha256=plan["recovery_token_sha256"],
        )
        proof = api._ctp_recovery_approval_proof(
            capability._approval,
            exchange_name=VENUE,
            session=session,
        )
        current_context = api._ctp_execution_arm_context(VENUE)
        runtime_python = api._ctp_execution_runtime_python_identity()
        authorized_plan, remaining_plan = session._recovery_authorized_plan_state()

        def late_io(*_args, **_kwargs):
            raise AssertionError("final handoff gate performed late material I/O")

        monkeypatch.setattr(api, "_ctp_recovery_approval_proof", late_io)
        monkeypatch.setattr(api, "_ctp_execution_arm_context", late_io)
        api._validate_ctp_recovery_dispatch_freshness(
            capability,
            plan["recovery_token_sha256"],
            session=session,
            exchange_name=VENUE,
            operation="make_order",
            runtime_python=runtime_python,
            current_proof=proof,
            current_context=current_context,
            recovery_plan=session._recovery_plan,
            authorized_plan=authorized_plan,
            remaining_plan=remaining_plan,
        )
    finally:
        session.close()
