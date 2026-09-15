"""Offline regressions for the public CTP execution-arming boundary."""

from __future__ import annotations

import hashlib
import json
import queue
import sys
import threading
import time
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from bt_api_py import (
    BtApi,
    CancelOrderRequest,
    NormalizedApiError,
    OrderRequest,
    TransportMode,
)
from bt_api_py import bt_api as bt_api_module
from bt_api_py._contracts import CapabilityNotSupportedError
from bt_api_py._contracts.models import OrderType, Side
from bt_api_py._execution_session import _ExecutionSession

VENUE = "CTP___FUTURE"
INSTRUMENT = "CZCE.SA609"
ACCOUNT_DIGEST = "0123456789abcdef"
ACCOUNT_FINGERPRINT = f"acct_{ACCOUNT_DIGEST}"
TRADING_DAY = "20260909"
PROFILE = "simnow_demo"
STRATEGY_IDENTITY = "7" * 64
BUNDLE_SCOPE_VERSION = "ctp-contract-bundle-v1"
BUNDLE_INSTRUMENTS = ["CZCE.SA701", "CZCE.SA701C1080", "CZCE.SA701P1080"]


def _proof(**changes):
    result = {
        "account_fingerprint": ACCOUNT_FINGERPRINT,
        "trading_day": TRADING_DAY,
        "instrument": INSTRUMENT,
        "connection_generation": 3,
        "environment_profile": PROFILE,
        "receipt_sha256": "1" * 64,
        "native_sha256": "2" * 64,
        "ctp_package_sha256": "3" * 64,
        "source_hashes_sha256": "4" * 64,
        "dependency_hashes_sha256": "5" * 64,
        "preflight_sha256": "6" * 64,
    }
    result.update(changes)
    return result


def _bundle_proof(**changes):
    result = _proof(
        instrument=BUNDLE_INSTRUMENTS[0],
        scope_version=BUNDLE_SCOPE_VERSION,
        authorized_instruments=list(BUNDLE_INSTRUMENTS),
    )
    result.update(changes)
    return result


def _context(proof=None, **changes):
    proof = proof or _proof()
    result = {
        "account_fingerprint": proof["account_fingerprint"],
        "trading_day": proof["trading_day"],
        "connection_generation": proof["connection_generation"],
        "environment_profile": proof["environment_profile"],
        "native_sha256": proof["native_sha256"],
        "ctp_package_sha256": proof["ctp_package_sha256"],
        "account_stream_ready": True,
    }
    result.update(changes)
    return result


def _session(tmp_path, *, risk=False, journal=True, require_journal=True):
    return _ExecutionSession(
        {
            "market_data_only": True,
            "require_order_journal": require_journal,
            "order_journal": (
                str(tmp_path / f"orders-{time.time_ns()}.jsonl") if journal else None
            ),
            "account_ids": {},
            "required_environments": {VENUE: "demo"},
            "strategy_id": "iter22-midfreq",
            "strategy_identity_sha256": STRATEGY_IDENTITY,
            "account_maximum_loss_bps": "100" if risk else None,
            "account_risk_max_age_seconds": "2",
        },
        exchange_names=(VENUE,),
    )


def _budget_evidence(session, proof_value):
    """Build a minimal write-eligible CTP path-budget evidence package.

    The O2 enforcement requires an armed CTP ``make_order`` to carry an
    ``sdk_runtime`` sourced reservation.  The evidence below mirrors what the
    SDK runtime collector produces for the managed SimNow chain; the session
    still re-validates it against its own arm proof, so nothing is bypassed.
    """
    raw_scope = list(proof_value.get("authorized_instruments") or [])
    if not raw_scope:
        raw_scope = [proof_value["instrument"]]
    if len(raw_scope) < 2:
        # Budget evidence mandates a 2-3 leg authorized scope; for single
        # instrument (V1) arming derive a deterministic companion option leg.
        exchange, symbol = raw_scope[0].split(".", 1)
        raw_scope.append(f"{exchange}.{symbol}C1080")
    instruments = [
        dict(zip(("exchange_id", "instrument_id"), item.split(".", 1), strict=True))
        for item in raw_scope
    ]
    budget_context = {
        "account_fingerprint": proof_value["account_fingerprint"],
        "trading_day": proof_value["trading_day"],
        "connection_generation": proof_value["connection_generation"],
        "environment_profile": proof_value["environment_profile"],
        "candidate_id": "iter22-budget-test",
        "strategy_id": session.config.get("strategy_id") or "iter22-midfreq",
        "strategy_identity_sha256": session.config.get("strategy_identity_sha256")
        or STRATEGY_IDENTITY,
        "execution_cycle_id": "budget-cycle",
        "scope_version": "test-budget-scope-v1",
        "authorized_instruments": [dict(item) for item in instruments],
        "primary_instrument": dict(instruments[0]),
    }
    costs = {
        "future_gross_margin": "1",
        "seller_option_gross_margin": "1",
        "paid_long_premium": "1",
        "fees_financing": "1",
        "stress_cash_loss": "1",
        "unresolved_reserve": "1",
    }
    states = [
        {
            "state_id": kind,
            "state_kind": kind,
            "context": dict(budget_context),
            "costs": dict(costs),
        }
        for kind in ("prefix", "partial", "unknown", "cancel", "late_fill")
    ]
    states.append(
        {
            "state_id": "recovery",
            "state_kind": "recovery",
            "non_overlapping": True,
            "recovery_increment_cny": "2",
            "context": dict(budget_context),
            "costs": dict(costs),
        }
    )
    return {
        "schema_version": "ctp-execution-budget-v1",
        "source": "sdk_runtime",
        "source_version": "test-collector-v1",
        "money_unit": "CNY",
        **budget_context,
        "reachable_states": states,
        "fresh_available_cny": "100000",
        "account_available_authoritative": True,
        "seller_margin_source_verified": True,
        "absorption_source_verified": True,
    }


def _reserve_budget(session, proof_value, *, mode="ordinary"):
    return session.reserve_ctp_execution_budget(_budget_evidence(session, proof_value), mode=mode)


def _ready_state(**changes):
    state = {
        "connected": True,
        "read_only_ready": True,
        "trading_ready": True,
        "auth_state": "authenticated",
        "login_state": "logged_in",
        "settlement_state": "confirmed",
        "settlement_readback_verified": True,
        "auto_settlement_confirm": False,
        "connection_generation": 3,
        # Native CTP exposes the raw 16-character digest. The proof is acct_-prefixed.
        "account_fingerprint": ACCOUNT_DIGEST,
        "trading_day": TRADING_DAY,
        "environment_profile": PROFILE,
    }
    state.update(changes)
    return state


class _ManagedFeed:
    def __init__(self, session_state):
        self._session_state = session_state
        self.trader_client = SimpleNamespace(get_session_state=lambda: dict(self._session_state))
        self._capability = None
        self._settlement_authorizations = set()
        self.settlement_calls = []
        self._gate_state = {
            "managed": True,
            "armed": False,
            "connection_generation": None,
            "trading_day": None,
            "instrument": None,
            "scope_version": None,
            "authorized_instruments": None,
            "environment_profile": None,
            "proof_sha256": None,
            "revocation_reason": None,
        }

    def get_session_state(self):
        return dict(self._session_state)

    def get_environment_info(self):
        return {
            "environment": "demo",
            "verified": True,
            "profile": PROFILE,
        }

    def configure_execution_gate(self, capability):
        if self._capability not in (None, capability):
            raise RuntimeError("different capability")
        self._capability = capability
        return self.get_execution_gate_state()

    def _issue_execution_authorization_for_core(
        self,
        capability,
        proof,
        **_kwargs,
    ):
        if capability is not self._capability:
            raise RuntimeError("wrong capability")
        return {"proof": dict(proof)}

    def arm_execution_gate(self, capability, authorization):
        if capability is not self._capability:
            raise RuntimeError("wrong capability")
        proof = authorization["proof"]
        proof_sha256 = hashlib.sha256(
            json.dumps(
                proof,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        self._gate_state = {
            "managed": True,
            "armed": True,
            "connection_generation": proof["connection_generation"],
            "trading_day": proof["trading_day"],
            "instrument": proof["instrument"],
            "scope_version": proof.get("scope_version"),
            "authorized_instruments": (
                list(proof["authorized_instruments"]) if "authorized_instruments" in proof else None
            ),
            "environment_profile": proof["environment_profile"],
            "proof_sha256": proof_sha256,
            "revocation_reason": None,
        }
        return self.get_execution_gate_state()

    def _issue_settlement_authorization_for_core(self, capability):
        if capability is not self._capability:
            raise RuntimeError("wrong capability")
        authorization = object()
        self._settlement_authorizations.add(authorization)
        return authorization

    def confirm_settlement(
        self,
        *,
        timeout,
        _execution_capability,
        _settlement_authorization,
    ):
        if _execution_capability is not self._capability:
            raise RuntimeError("wrong capability")
        if _settlement_authorization not in self._settlement_authorizations:
            raise RuntimeError("wrong settlement authorization")
        self._settlement_authorizations.remove(_settlement_authorization)
        self.settlement_calls.append(timeout)
        return True

    def disarm_execution_gate(self, capability, reason="execution_arm_revoked"):
        if capability is not self._capability:
            raise RuntimeError("wrong capability")
        self._gate_state.update(
            armed=False,
            connection_generation=None,
            trading_day=None,
            instrument=None,
            scope_version=None,
            authorized_instruments=None,
            environment_profile=None,
            proof_sha256=None,
            revocation_reason=reason,
        )
        return self.get_execution_gate_state()

    def get_execution_gate_state(self):
        return dict(self._gate_state)


def _api_for_arm(tmp_path, *, state=None, risk=False):
    session = _session(tmp_path, risk=risk)
    session_state = state if state is not None else _ready_state()
    feed = _ManagedFeed(session_state)
    api = object.__new__(BtApi)
    api.transport_mode = TransportMode.DIRECT
    api.exchange_feeds = {VENUE: feed}
    api.exchange_kwargs = {VENUE: {"auto_settlement_confirm": False}}
    api.data_queues = {VENUE: queue.Queue()}
    api._subscription_streams = []
    api._subscription_flags = {}
    api._execution_session = session
    api._ctp_execution_capability = object()
    feed.configure_execution_gate(api._ctp_execution_capability)
    api._ctp_execution_runtime_identity = lambda: {
        "native_sha256": "2" * 64,
        "ctp_package_sha256": "3" * 64,
    }
    return api, session, session_state


def _authorization(api, proof=None, *, cycle="controlled-test-cycle"):
    return api._issue_ctp_execution_authorization_for_test(
        proof or _proof(),
        _test_authority=bt_api_module._issue_ctp_controlled_test_authority_for_core(),
        execution_cycle_id=cycle,
    )


def _arm(api, proof=None, *, cycle="controlled-test-cycle"):
    return api.arm_execution_from_preflight(_authorization(api, proof, cycle=cycle))


class _AccountStream:
    instances: list[Any] = []
    connected = True

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self.kwargs = kwargs
        self.stream_name = kwargs["stream_name"]
        self.trader_client = kwargs["request_feed"].trader_client
        self.started = False
        self.stopped = False
        self._running = False
        self.state = SimpleNamespace(value="disconnected")
        self.wait_timeouts = []
        type(self).instances.append(self)

    def start(self):
        self.started = True
        self._running = True
        self.state = SimpleNamespace(value="authenticated")

    def wait_connected(self, *, timeout):
        self.wait_timeouts.append(timeout)
        return type(self).connected

    def stop(self):
        self.stopped = True
        self._running = False
        self.state = SimpleNamespace(value="disconnected")


class _AccountStreamWithoutLifecycleProof:
    instances: list[Any] = []

    def __init__(self, _data_queue, **kwargs):
        self.stream_name = kwargs["stream_name"]
        self.started = False
        self.stopped = False
        type(self).instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def _reset_stream_fixture(monkeypatch, tmp_path):
    _AccountStream.instances = []
    _AccountStream.connected = True
    _AccountStreamWithoutLifecycleProof.instances = []
    monkeypatch.setattr(
        "bt_api_py._execution_session._ledger_registry_root",
        lambda: tmp_path / "execution-ledgers",
    )


def _install_account_stream(monkeypatch):
    monkeypatch.setattr(
        "bt_api_py.bt_api.ExchangeRegistry.get_stream_class",
        lambda exchange_name, stream_type: (
            _AccountStream if (exchange_name, stream_type) == (VENUE, "account") else None
        ),
    )


def test_managed_exchange_installs_private_producer_proxy_before_feed_caches_queue(
    monkeypatch, tmp_path
):
    session = _session(tmp_path)
    captured = {}
    feed = SimpleNamespace(disconnect=Mock())
    api = object.__new__(BtApi)
    api.transport_mode = TransportMode.DIRECT
    api.exchange_kwargs = {}
    api.exchange_feeds = {}
    api.data_queues = {}
    api._execution_session = session
    api._ctp_execution_capability = object()
    api._ctp_private_ingress_fences = {}
    api._ctp_private_ingress_queues = {}
    api.log = Mock()
    api._configure_ctp_execution_gate = Mock()
    api._validate_required_environment = Mock()

    def create_feed(exchange_name, data_queue, **_kwargs):
        captured["exchange_name"] = exchange_name
        captured["data_queue"] = data_queue
        return feed

    monkeypatch.setattr(
        "bt_api_py.bt_api._execution_credential_fingerprints",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "bt_api_py.bt_api.ExchangeRegistry.create_feed",
        create_feed,
    )
    try:
        api.add_exchange(VENUE, {})
        raw_queue = api.data_queues[VENUE]
        producer_queue = captured["data_queue"]
        assert captured["exchange_name"] == VENUE
        assert producer_queue is not raw_queue
        assert producer_queue.target is raw_queue

        ingress_revision = session.recovery_private_ingress_revision()
        producer_queue.put({"kind": "order", "status": "accepted"})
        assert raw_queue.qsize() == 1
        assert session.recovery_private_ingress_revision() == ingress_revision + 1
    finally:
        session.close()


def _arm_direct(session, proof=None, context=None):
    proof = proof or _proof()
    context = context or _context(proof)
    return session.arm_from_preflight(proof, lambda: dict(context))


def test_public_arm_uses_raw_native_account_identity_and_is_idempotent(monkeypatch, tmp_path):
    _install_account_stream(monkeypatch)
    api, session, _state = _api_for_arm(tmp_path)
    proof = _proof()
    expected_hash = hashlib.sha256(
        json.dumps(
            proof,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    try:
        first_authorization = _authorization(api, proof)
        first = api.arm_execution_from_preflight(first_authorization)
        with pytest.raises(NormalizedApiError) as reused:
            api.arm_execution_from_preflight(first_authorization)

        assert first == {
            "armed": True,
            "market_data_only": False,
            "proof_sha256": expected_hash,
        }
        assert reused.value.code == "ctp_execution_authorization_required"
        assert session.config["market_data_only"] is False
        assert session.lock_file is not None
        assert len(_AccountStream.instances) == 1
        stream = _AccountStream.instances[0]
        assert stream.data_queue is not api.data_queues[VENUE]
        assert stream.data_queue.target is api.data_queues[VENUE]
        assert stream.started is True
        assert stream.wait_timeouts == [5.0]
        assert stream.kwargs["request_feed"] is api.exchange_feeds[VENUE]
        assert api._subscription_flags[f"{VENUE}_account"] is True
    finally:
        session.close()


def test_public_state_hash_mapping_cannot_self_authorize_ctp_arm(tmp_path):
    """State returned by public CTP reads is evidence, never arm authority."""

    api, session, _state = _api_for_arm(tmp_path)
    try:
        public_state = api.get_ctp_session_state(VENUE)
        forged = _proof(
            account_fingerprint=f"acct_{public_state['account_fingerprint']}",
            trading_day=public_state["trading_day"],
            connection_generation=public_state["connection_generation"],
            environment_profile=public_state["environment_profile"],
        )

        with pytest.raises(NormalizedApiError) as raised:
            api.arm_execution_from_preflight(forged)

        assert raised.value.code == "ctp_execution_authorization_required"
        assert api.exchange_feeds[VENUE].get_execution_gate_state()["armed"] is False
        assert session.config["market_data_only"] is True
    finally:
        session.close()


def test_private_controlled_issuer_rejects_forged_test_authority(tmp_path):
    api, session, _state = _api_for_arm(tmp_path)
    try:
        with pytest.raises(NormalizedApiError) as raised:
            api._issue_ctp_execution_authorization_for_test(
                _proof(),
                _test_authority=object(),
            )

        assert raised.value.code == "ctp_execution_authorization_required"
        assert api.exchange_feeds[VENUE].get_execution_gate_state()["armed"] is False
    finally:
        session.close()


def test_prepare_execution_authorization_invalidates_unconsumed_arm_grant(tmp_path):
    api, session, _state = _api_for_arm(tmp_path)
    try:
        authorization = _authorization(api)
        api.prepare_execution_authorization()

        with pytest.raises(NormalizedApiError) as raised:
            api.arm_execution_from_preflight(authorization)

        assert raised.value.code == "ctp_execution_authorization_preflight_invalidated"
        assert api.exchange_feeds[VENUE].get_execution_gate_state()["armed"] is False
        assert session.config["market_data_only"] is True
    finally:
        session.close()


def test_generation_change_consumes_and_rejects_pending_arm_grant(tmp_path):
    api, session, state = _api_for_arm(tmp_path)
    try:
        authorization = _authorization(api)
        state["connection_generation"] = 4

        with pytest.raises(NormalizedApiError) as changed:
            api.arm_execution_from_preflight(authorization)
        with pytest.raises(NormalizedApiError) as reused:
            api.arm_execution_from_preflight(authorization)

        assert changed.value.code == "ctp_execution_authorization_context_mismatch"
        assert reused.value.code == "ctp_execution_authorization_required"
        assert api.exchange_feeds[VENUE].get_execution_gate_state()["armed"] is False
    finally:
        session.close()


def test_controlled_settlement_token_invalidates_unconsumed_arm_grant(tmp_path):
    api, session, _state = _api_for_arm(tmp_path)
    try:
        pending_arm = _authorization(api)
        session.config["market_data_only"] = False
        with pytest.raises(NormalizedApiError) as forged:
            api._issue_ctp_settlement_authorization_for_test(_test_authority=object())
        assert forged.value.code == "ctp_settlement_authorization_required"

        settlement = api._issue_ctp_settlement_authorization_for_test(
            _test_authority=bt_api_module._issue_ctp_controlled_test_authority_for_core()
        )
        assert api._confirm_ctp_settlement_for_core(settlement, timeout=0.25) is True
        assert api.exchange_feeds[VENUE].settlement_calls == [0.25]

        with pytest.raises(NormalizedApiError) as raised:
            api.arm_execution_from_preflight(pending_arm)

        assert raised.value.code == "ctp_execution_authorization_preflight_invalidated"
        assert session.config["market_data_only"] is True
    finally:
        session.close()


def test_private_ingress_revocation_invalidates_sibling_arm_grant(monkeypatch, tmp_path):
    _install_account_stream(monkeypatch)
    api, session, _state = _api_for_arm(tmp_path)
    try:
        first = _authorization(api)
        sibling = _authorization(api)
        api.arm_execution_from_preflight(first)

        api._revoke_ctp_arm_for_private_ingress(VENUE)
        with pytest.raises(NormalizedApiError) as raised:
            api.arm_execution_from_preflight(sibling)

        assert raised.value.code == "ctp_execution_authorization_preflight_invalidated"
        assert api.exchange_feeds[VENUE].get_execution_gate_state()["armed"] is False
    finally:
        session.close()


def test_public_arm_verifies_exact_v2_bundle_scope(monkeypatch, tmp_path):
    _install_account_stream(monkeypatch)
    api, session, _state = _api_for_arm(tmp_path)
    proof = _bundle_proof()
    try:
        result = _arm(api, proof)

        assert result["armed"] is True
        gate = api.exchange_feeds[VENUE].get_execution_gate_state()
        assert gate["scope_version"] == BUNDLE_SCOPE_VERSION
        assert gate["authorized_instruments"] == BUNDLE_INSTRUMENTS
    finally:
        session.close()


def test_v2_bundle_allows_only_exact_czce_contract_legs_before_journal(tmp_path):
    session = _session(tmp_path)
    transport = Mock(return_value={"status": "accepted", "order_id": "SYS1"})
    try:
        bundle = _bundle_proof()
        _arm_direct(session, proof=bundle, context=_context(bundle))

        for index, symbol in enumerate(BUNDLE_INSTRUMENTS, start=1):
            session.invoke(
                "make_order",
                VENUE,
                _order(
                    symbol.split(".", 1)[1],
                    client_order_id=f"00000000000{index}",
                ),
                transport,
                budget_capability=_reserve_budget(session, bundle),
            )
        submit_calls = session.submit_calls
        with pytest.raises(NormalizedApiError) as raised:
            session.invoke(
                "make_order",
                VENUE,
                _order("SA701C1100", client_order_id="000000000099"),
                transport,
            )

        assert raised.value.code == "execution_arm_instrument_mismatch"
        assert session.submit_calls == submit_calls == 3
        assert transport.call_count == 3
    finally:
        session.close()


def test_v2_bundle_accepts_native_dce_option_spelling_and_rejects_case_changes(
    tmp_path,
):
    instruments = ["DCE.m2701", "DCE.m2701-C-3400"]
    proof = _proof(
        instrument=instruments[0],
        scope_version=BUNDLE_SCOPE_VERSION,
        authorized_instruments=instruments,
    )
    session = _session(tmp_path)
    transport = Mock(return_value={"status": "accepted", "order_id": "SYS1"})
    try:
        _arm_direct(session, proof=proof, context=_context(proof))
        session.invoke(
            "make_order",
            VENUE,
            _order("m2701-C-3400", exchange_id="DCE"),
            transport,
            budget_capability=_reserve_budget(session, proof),
        )
        with pytest.raises(NormalizedApiError) as raised:
            session.invoke(
                "make_order",
                VENUE,
                _order(
                    "M2701-C-3400",
                    exchange_id="DCE",
                    client_order_id="000000000002",
                ),
                transport,
            )
        assert raised.value.code == "execution_arm_instrument_mismatch"
        assert transport.call_count == 1
    finally:
        session.close()


@pytest.mark.parametrize("operation", ("make_order", "cancel_order"))
@pytest.mark.parametrize(
    ("symbol", "exchange_id"),
    (
        (" m2701-C-3400", "DCE"),
        ("m2701-C-3400 ", "DCE"),
        ("DCE.m2701-C-3400", "DCE"),
        ("m2701-C-3400.DCE", "DCE"),
        ("m2701-C-3400", " dce"),
        ("m2701-C-3400", "dce"),
        ("m2701-C-3400", "DCE "),
        ("m2701-C-3400", None),
    ),
)
def test_v2_bundle_rejects_rewritten_native_wire_fields_before_journal_or_transport(
    tmp_path, operation, symbol, exchange_id
):
    """A V2 proof must bind the exact fields sent to the CTP native API."""
    instruments = ["DCE.m2701", "DCE.m2701-C-3400"]
    proof = _proof(
        instrument=instruments[0],
        scope_version=BUNDLE_SCOPE_VERSION,
        authorized_instruments=instruments,
    )
    session = _session(tmp_path)
    transport = Mock(return_value={"status": "accepted", "order_id": "SYS1"})
    try:
        _arm_direct(session, proof=proof, context=_context(proof))
        if operation == "make_order":
            request = _order(symbol, exchange_id=exchange_id)
        else:
            request = CancelOrderRequest(
                symbol=symbol,
                account_id=ACCOUNT_FINGERPRINT,
                client_order_id="000000000001",
                order_id="SYS1",
                exchange_id=exchange_id,
            )

        with pytest.raises(NormalizedApiError) as raised:
            session.invoke(operation, VENUE, request, transport)

        assert raised.value.code == "execution_arm_instrument_mismatch"
        assert session.submit_calls == 0
        assert session.cancel_calls == 0
        assert session.orders == {}
        assert not session.path.exists()
        transport.assert_not_called()
    finally:
        session.close()


def test_concurrent_disarm_cannot_be_undone_after_session_arm(monkeypatch, tmp_path):
    _install_account_stream(monkeypatch)
    api, session, _state = _api_for_arm(tmp_path)
    original_arm = session.arm_from_preflight
    session_armed = threading.Event()
    release_arm_return = threading.Event()
    disarm_started = threading.Event()
    arm_result = []
    arm_errors = []
    disarm_result = []
    disarm_errors = []

    def blocked_arm(*args, **kwargs):
        result = original_arm(*args, **kwargs)
        session_armed.set()
        if not release_arm_return.wait(timeout=5):
            raise RuntimeError("timed out waiting for concurrent disarm")
        return result

    session.arm_from_preflight = blocked_arm

    def run_arm():
        try:
            arm_result.append(_arm(api))
        except Exception as exc:  # pragma: no cover - asserted below
            arm_errors.append(exc)

    def run_disarm():
        disarm_started.set()
        try:
            disarm_result.append(api.disarm_execution("concurrent_disarm"))
        except Exception as exc:  # pragma: no cover - assertion reports it
            disarm_errors.append(exc)

    arm_thread = threading.Thread(target=run_arm)
    disarm_thread = threading.Thread(target=run_disarm)
    try:
        arm_thread.start()
        assert session_armed.wait(timeout=5)

        disarm_thread.start()
        assert disarm_started.wait(timeout=5)
        release_arm_return.set()
        arm_thread.join(timeout=5)
        disarm_thread.join(timeout=5)

        assert not arm_thread.is_alive()
        assert not disarm_thread.is_alive()
        assert arm_errors == []
        assert disarm_errors == []
        assert arm_result == [
            {
                "armed": True,
                "market_data_only": False,
                "proof_sha256": hashlib.sha256(
                    json.dumps(
                        _proof(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest(),
            }
        ]
        assert disarm_result[0]["market_data_only"] is True
        assert session.config["market_data_only"] is True
        assert api.exchange_feeds[VENUE].get_execution_gate_state()["armed"] is False
        stream = _AccountStream.instances[0]
        assert stream in api._subscription_streams
        assert stream._running is True
        assert stream.stopped is False
        assert api._subscription_flags[f"{VENUE}_account"] is True
        stream.data_queue.put({"kind": "order", **_order_update()})
        assert api.poll_event(VENUE)["kind"] == "order"
    finally:
        release_arm_return.set()
        arm_thread.join(timeout=5)
        disarm_thread.join(timeout=5)
        session.close()


@pytest.mark.parametrize(
    "proof",
    [
        {**_proof(), "extra": "forbidden"},
        {key: value for key, value in _proof().items() if key != "receipt_sha256"},
        _proof(account_fingerprint=ACCOUNT_DIGEST),
        _proof(account_fingerprint="acct_0123456789abcdeg"),
        _proof(native_sha256="A" * 64),
        _proof(connection_generation=True),
        _proof(trading_day="20260230"),
        _proof(instrument="SA609"),
        _proof(instrument="SA609.CZCE"),
        _proof(instrument="ZCE.SA609"),
        _proof(instrument="CZCE.SA2609"),
        _proof(instrument="DCE.SA-609"),
        _bundle_proof(authorized_instruments=BUNDLE_INSTRUMENTS[:1]),
        _bundle_proof(authorized_instruments=BUNDLE_INSTRUMENTS + ["CZCE.SA701P1100"]),
        _bundle_proof(authorized_instruments=list(reversed(BUNDLE_INSTRUMENTS))),
        _bundle_proof(
            authorized_instruments=["CZCE.SA701", "DCE.m2701-C-3400"],
        ),
        _bundle_proof(
            instrument="CZCE.SA701C1100",
            authorized_instruments=BUNDLE_INSTRUMENTS,
        ),
    ],
)
def test_malformed_proof_fails_without_leaving_read_only(tmp_path, proof):
    session = _session(tmp_path)
    try:
        with pytest.raises(NormalizedApiError) as raised:
            _arm_direct(session, proof=proof, context=_context())
        assert raised.value.code == "invalid_execution_arm_proof"
        assert session.config["market_data_only"] is True
        assert session.lock_file is None
    finally:
        session.close()


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "account_fingerprint",
            "acct_fedcba9876543210",
            "execution_arm_account_fingerprint_mismatch",
        ),
        ("trading_day", "20260910", "execution_arm_trading_day_mismatch"),
        ("connection_generation", 4, "execution_arm_connection_generation_mismatch"),
        (
            "environment_profile",
            "other_demo",
            "execution_arm_environment_profile_mismatch",
        ),
    ],
)
def test_arm_rejects_current_session_mismatch(tmp_path, field, value, code):
    session = _session(tmp_path)
    try:
        with pytest.raises(NormalizedApiError) as raised:
            _arm_direct(session, context=_context(**{field: value}))
        assert raised.value.code == code
        assert session.config["market_data_only"] is True
        assert session.lock_file is None
    finally:
        session.close()


@pytest.mark.parametrize("field", ["native_sha256", "ctp_package_sha256"])
def test_public_arm_rejects_proof_that_mismatches_loaded_runtime_identity(tmp_path, field):
    api, session, _state = _api_for_arm(tmp_path)
    try:
        with pytest.raises(NormalizedApiError) as raised:
            _arm(api, _proof(**{field: "9" * 64}))
        assert raised.value.code == "ctp_execution_authorization_context_mismatch"
        assert session.config["market_data_only"] is True
        assert session.lock_file is None
        assert api._subscription_streams == []
    finally:
        session.close()


@pytest.mark.parametrize("verified", [False, None])
def test_public_arm_requires_current_settlement_query_readback(tmp_path, verified):
    state = _ready_state()
    if verified is None:
        state.pop("settlement_readback_verified")
    else:
        state["settlement_readback_verified"] = verified
    api, session, _state = _api_for_arm(tmp_path, state=state)
    try:
        with pytest.raises(NormalizedApiError) as raised:
            _arm(api)
        assert raised.value.code == "ctp_session_not_trading_ready"
        assert session.config["market_data_only"] is True
        assert session.lock_file is None
        assert api.exchange_feeds[VENUE].get_execution_gate_state()["armed"] is False
        assert api._subscription_streams == []
    finally:
        session.close()


def test_runtime_identity_hashes_the_actual_loaded_native_and_package_files(monkeypatch, tmp_path):
    native_path = tmp_path / "_ctp_native.so"
    package_root = tmp_path / "bt_api_ctp"
    package_path = package_root / "__init__.py"
    native_path.write_bytes(b"loaded-native-binary")
    package_sources = {
        "__init__.py": b"loaded-package-module",
        "ctp/client.py": b"client",
        "feeds/live_ctp_feed.py": b"feed",
        "gateway/adapter.py": b"adapter",
        "query.py": b"query",
    }
    for relative_path, content in package_sources.items():
        path = package_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    native_sha256 = hashlib.sha256(native_path.read_bytes()).hexdigest()
    manifest = [
        {"path": relative_path, "sha256": hashlib.sha256(content).hexdigest()}
        for relative_path, content in sorted(package_sources.items())
    ]
    package_sha256 = hashlib.sha256(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    package = SimpleNamespace(
        __file__=str(package_path),
        get_ctp_native_diagnostics=lambda: {
            "native_loaded": True,
            "loaded_module_path": str(native_path),
            "loaded_module_sha256": native_sha256,
            "ctp_package_manifest": manifest,
            "ctp_package_sha256": package_sha256,
        },
    )
    monkeypatch.setitem(sys.modules, "bt_api_ctp", package)
    api = object.__new__(BtApi)

    assert api._ctp_execution_runtime_identity() == {
        "native_sha256": native_sha256,
        "ctp_package_sha256": package_sha256,
    }


def test_runtime_identity_rejects_diagnostics_not_matching_loaded_file(monkeypatch, tmp_path):
    native_path = tmp_path / "_ctp_native.so"
    package_path = tmp_path / "__init__.py"
    native_path.write_bytes(b"loaded-native-binary")
    package_path.write_bytes(b"loaded-package-module")
    package = SimpleNamespace(
        __file__=str(package_path),
        get_ctp_native_diagnostics=lambda: {
            "native_loaded": True,
            "loaded_module_path": str(native_path),
            "loaded_module_sha256": "0" * 64,
        },
    )
    monkeypatch.setitem(sys.modules, "bt_api_ctp", package)
    api = object.__new__(BtApi)

    with pytest.raises(NormalizedApiError) as raised:
        api._ctp_execution_runtime_identity()
    assert raised.value.code == "ctp_runtime_identity_unavailable"


def test_dynamic_session_fence_revokes_an_existing_arm(tmp_path):
    session = _session(tmp_path)
    state = _context()
    try:
        session.arm_from_preflight(_proof(), lambda: dict(state))
        state["connection_generation"] = 4

        with pytest.raises(NormalizedApiError) as raised:
            session.require_write("make_order", placement=True)
        assert raised.value.code == "execution_arm_connection_generation_mismatch"
        assert session.config["market_data_only"] is True
    finally:
        session.close()


@pytest.mark.parametrize("field", ["native_sha256", "ctp_package_sha256"])
def test_dynamic_runtime_identity_fence_revokes_an_existing_arm(tmp_path, field):
    session = _session(tmp_path)
    state = _context()
    try:
        session.arm_from_preflight(_proof(), lambda: dict(state))
        state[field] = "9" * 64

        with pytest.raises(NormalizedApiError) as raised:
            session.require_write("make_order", placement=True)
        assert raised.value.code == f"execution_arm_{field}_mismatch"
        assert session.config["market_data_only"] is True
    finally:
        session.close()


def test_different_proof_fails_closed_after_arm(tmp_path):
    session = _session(tmp_path)
    try:
        _arm_direct(session)
        with pytest.raises(NormalizedApiError) as raised:
            _arm_direct(session, proof=_proof(receipt_sha256="6" * 64))
        assert raised.value.code == "execution_already_armed"
        assert session.config["market_data_only"] is True
    finally:
        session.close()


def test_arm_requires_a_durable_order_journal_even_if_config_disables_it(tmp_path):
    session = _session(tmp_path, journal=False, require_journal=False)
    try:
        with pytest.raises(NormalizedApiError) as raised:
            _arm_direct(session)
        assert raised.value.code == "order_journal_required"
        assert session.config["market_data_only"] is True
        assert session.lock_file is None
    finally:
        session.close()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("persistence_failed", "unresolved_or_undurable_journal"),
        ("unknown_ids", "unresolved_or_undurable_journal"),
        ("missing_journal", "order_journal_required"),
    ],
)
def test_armed_placement_still_runs_existing_journal_guards(tmp_path, mutation, expected_code):
    session = _session(tmp_path)
    try:
        _arm_direct(session)
        if mutation == "persistence_failed":
            session.persistence_failed = True
        elif mutation == "unknown_ids":
            session.historical_unknown.add("unresolved")
        else:
            session.path = None

        with pytest.raises(NormalizedApiError) as raised:
            session.require_write("make_order", placement=True)
        assert raised.value.code == expected_code
    finally:
        session.close()


@pytest.mark.parametrize(
    ("risk_record", "verified_at", "expected_code"),
    [
        (None, 0, "account_risk_baseline_required"),
        (
            {"loss_limit_breached": True},
            time.monotonic_ns(),
            "account_maximum_loss_breached",
        ),
        (
            {"loss_limit_breached": False},
            time.monotonic_ns() - 3_000_000_000,
            "account_risk_snapshot_stale",
        ),
    ],
)
def test_armed_placement_still_runs_account_risk_guards(
    tmp_path, risk_record, verified_at, expected_code
):
    session = _session(tmp_path, risk=True)
    try:
        _arm_direct(session)
        session.risk_record = risk_record
        session.risk_last_verified_monotonic_ns = verified_at

        with pytest.raises(NormalizedApiError) as raised:
            session.require_write("make_order", placement=True)
        assert raised.value.code == expected_code
    finally:
        session.close()


def _order(
    symbol,
    client_order_id="000000000001",
    exchange_id="CZCE",
    cycle="cycle-1",
):
    return OrderRequest(
        symbol=symbol,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        account_id=ACCOUNT_FINGERPRINT,
        client_order_id=client_order_id,
        price=Decimal("1500"),
        time_in_force="GFD",
        quantity_unit="lots",
        offset="open",
        exchange_id=exchange_id,
        execution_cycle_id=cycle,
        execution_role="entry",
        strategy_identity_sha256=STRATEGY_IDENTITY,
    )


def test_cross_instrument_order_rejects_before_journal_or_transport(tmp_path):
    session = _session(tmp_path)
    transport = Mock(return_value={"status": "accepted", "order_id": "SYS1"})
    try:
        _arm_direct(session)
        assert not session.path.exists()

        with pytest.raises(NormalizedApiError) as raised:
            session.invoke(
                "make_order",
                VENUE,
                _order("CZCE.SR609"),
                transport,
            )
        assert raised.value.code == "execution_arm_instrument_mismatch"
        assert session.submit_calls == 0
        assert session.orders == {}
        assert not session.path.exists()
        transport.assert_not_called()
    finally:
        session.close()


def test_opaque_arm_grant_binds_the_authorized_strategy_cycle(monkeypatch, tmp_path):
    _install_account_stream(monkeypatch)
    api, session, _state = _api_for_arm(tmp_path)
    transport = Mock(return_value=_order_update())
    try:
        _arm(api, cycle="authorized-cycle")

        with pytest.raises(NormalizedApiError) as raised:
            session.invoke(
                "make_order",
                VENUE,
                _order("SA609.CZCE", cycle="different-cycle"),
                transport,
            )

        assert raised.value.code == "ctp_execution_authorization_identity_mismatch"
        transport.assert_not_called()
    finally:
        session.close()


def _order_update(*, status="accepted", terminal=False):
    return {
        "symbol": "SA609.CZCE",
        "account_id": ACCOUNT_FINGERPRINT,
        "client_order_id": "000000000001",
        "order_id": "SYS1",
        "exchange_id": "CZCE",
        "side": "buy",
        "status": status,
        "filled": 0,
        "avg_price": None,
        "terminal_confirmed": terminal,
    }


def test_account_stream_start_and_stop_join_private_producer_outside_session_lock(
    monkeypatch, tmp_path
):
    class JoiningProducerStream(_AccountStream):
        instances = []
        connected = True

        def start(self):
            self.started = True
            self._running = True
            self.state = SimpleNamespace(value="authenticated")
            event = {"kind": "order", **_order_update()}
            self.producer = threading.Thread(target=self.data_queue.put, args=(event,))
            self.producer.start()
            self.producer.join(timeout=1)
            self.producer_blocked = self.producer.is_alive()
            if self.producer_blocked:
                raise RuntimeError("private producer blocked by session mutex")

        def stop(self):
            self.producer.join(timeout=1)
            self.stop_joined = not self.producer.is_alive()
            super().stop()

    monkeypatch.setattr(
        "bt_api_py.bt_api.ExchangeRegistry.get_stream_class",
        lambda exchange_name, stream_type: (
            JoiningProducerStream if (exchange_name, stream_type) == (VENUE, "account") else None
        ),
    )
    api, session, _state = _api_for_arm(tmp_path)
    api.exchange_feeds[VENUE].arm_execution_gate = Mock(
        side_effect=RuntimeError("native arm failed")
    )
    try:
        with pytest.raises(NormalizedApiError) as raised:
            _arm(api)
        assert raised.value.code == "ctp_execution_gate_arm_failed"
        stream = JoiningProducerStream.instances[0]
        assert stream.producer_blocked is False
        assert stream.stopped is True
        assert stream.stop_joined is True
        assert not stream.producer.is_alive()
    finally:
        session.close()


def test_armed_same_instrument_order_uses_normal_journal_and_transport_path(tmp_path):
    session = _session(tmp_path)
    transport = Mock(return_value=_order_update())
    try:
        proof = _proof()
        _arm_direct(session, proof=proof)

        result = session.invoke(
            "make_order",
            VENUE,
            _order("SA609.CZCE"),
            transport,
            budget_capability=_reserve_budget(session, proof),
        )

        assert result["status"] == "accepted"
        assert result["execution_unknown"] is False
        assert session.submit_calls == 1
        transport.assert_called_once_with()
        rows = [json.loads(line) for line in session.path.read_text().splitlines()]
        assert [row["event"] for row in rows] == [
            "ctp_budget_reservation_started",
            "ctp_budget_reservation_committed",
            "intent",
            "ctp_budget_action_started",
            "order_update",
        ]
    finally:
        session.close()


def test_armed_tracked_same_instrument_cancel_remains_available(tmp_path):
    session = _session(tmp_path)
    place = Mock(return_value=_order_update())
    cancel = Mock(return_value=_order_update(status="canceled", terminal=True))
    try:
        proof = _proof()
        _arm_direct(session, proof=proof)
        session.invoke(
            "make_order",
            VENUE,
            _order("SA609.CZCE"),
            place,
            budget_capability=_reserve_budget(session, proof),
        )
        request = CancelOrderRequest(
            symbol="SA609.CZCE",
            account_id=ACCOUNT_FINGERPRINT,
            client_order_id="000000000001",
            order_id="SYS1",
            exchange_id="CZCE",
        )

        result = session.invoke(
            "cancel_order",
            VENUE,
            request,
            cancel,
            budget_capability=_reserve_budget(session, proof),
        )

        assert result["status"] == "canceled"
        assert result["terminal_confirmed"] is True
        assert session.cancel_calls == 1
        cancel.assert_called_once_with()
        rows = [json.loads(line) for line in session.path.read_text().splitlines()]
        assert [row["event"] for row in rows] == [
            "ctp_budget_reservation_started",
            "ctp_budget_reservation_committed",
            "intent",
            "ctp_budget_action_started",
            "order_update",
            "ctp_budget_reservation_started",
            "ctp_budget_reservation_committed",
            "cancel_intent",
            "ctp_budget_action_started",
            "order_update",
        ]
    finally:
        session.close()


def test_armed_cancel_rejects_untracked_and_cross_instrument_orders(tmp_path):
    session = _session(tmp_path)
    transport = Mock(return_value={"status": "canceled", "order_id": "SYS1"})
    try:
        _arm_direct(session)
        untracked = CancelOrderRequest(
            symbol="SA609.CZCE",
            account_id=ACCOUNT_FINGERPRINT,
            client_order_id="000000000001",
            exchange_id="CZCE",
        )
        with pytest.raises(NormalizedApiError) as raised:
            session.invoke("cancel_order", VENUE, untracked, transport)
        assert raised.value.code == "execution_arm_untracked_cancel"

        other = CancelOrderRequest(
            symbol="SR609.CZCE",
            account_id=ACCOUNT_FINGERPRINT,
            client_order_id="000000000001",
            exchange_id="CZCE",
        )
        with pytest.raises(NormalizedApiError) as raised:
            session.invoke("cancel_order", VENUE, other, transport)
        assert raised.value.code == "execution_arm_instrument_mismatch"
        assert session.cancel_calls == 0
        assert not session.path.exists()
        transport.assert_not_called()
    finally:
        session.close()


def test_empty_subscription_list_creates_and_waits_for_account_stream(monkeypatch, tmp_path):
    _install_account_stream(monkeypatch)
    api, session, _state = _api_for_arm(tmp_path)
    try:
        result = _arm(api)
        assert result["armed"] is True
        assert len(api._subscription_streams) == 1
        assert _AccountStream.instances[0].wait_timeouts == [5.0]
    finally:
        session.close()


def test_armed_ctp_does_not_expand_account_level_or_bulk_write_capabilities(monkeypatch, tmp_path):
    _install_account_stream(monkeypatch)
    api, session, _state = _api_for_arm(tmp_path)
    api._backend = Mock()
    try:
        _arm(api)

        with pytest.raises(CapabilityNotSupportedError):
            api.set_position_mode(VENUE, "net", normalized=True)
        with pytest.raises(CapabilityNotSupportedError):
            api.cancel_all(VENUE)

        api._backend.set_position_mode.assert_not_called()
        api._backend.cancel_all.assert_not_called()
        assert session.submit_calls == 0
        assert session.cancel_calls == 0
        assert not session.path.exists()
    finally:
        session.close()


@pytest.mark.parametrize("missing", ["queue", "stream_class"])
def test_missing_account_stream_dependency_rolls_back_and_stays_read_only(
    monkeypatch, tmp_path, missing
):
    _install_account_stream(monkeypatch)
    api, session, _state = _api_for_arm(tmp_path)
    if missing == "queue":
        api.data_queues.clear()
    else:
        monkeypatch.setattr(
            "bt_api_py.bt_api.ExchangeRegistry.get_stream_class",
            lambda *_args: None,
        )
    try:
        with pytest.raises(NormalizedApiError) as raised:
            _arm(api)
        assert raised.value.code == "ctp_account_stream_unavailable"
        assert session.config["market_data_only"] is True
        assert session.lock_file is None
        assert api._subscription_streams == []
        assert api._subscription_flags == {}
        assert api.exchange_kwargs[VENUE]["subscribe_account"] is False
    finally:
        session.close()


def test_missing_account_stream_feed_rolls_back_without_changing_read_only_mode(
    monkeypatch, tmp_path
):
    _install_account_stream(monkeypatch)
    api, session, _state = _api_for_arm(tmp_path)
    api.exchange_feeds.clear()
    try:
        with pytest.raises(NormalizedApiError) as raised:
            api._prepare_ctp_execution_stream(VENUE)
        assert raised.value.code == "ctp_account_stream_unavailable"
        assert session.config["market_data_only"] is True
        assert api._subscription_streams == []
        assert api._subscription_flags == {}
        assert "subscribe_account" not in api.exchange_kwargs[VENUE]
    finally:
        session.close()


def test_account_stream_wait_failure_rolls_back_and_stays_read_only(monkeypatch, tmp_path):
    _install_account_stream(monkeypatch)
    _AccountStream.connected = False
    api, session, _state = _api_for_arm(tmp_path)
    try:
        with pytest.raises(NormalizedApiError) as raised:
            _arm(api)
        assert raised.value.code == "ctp_account_stream_unavailable"
        assert session.config["market_data_only"] is True
        assert session.lock_file is None
        assert api._subscription_streams == []
        assert api._subscription_flags == {}
        assert api.exchange_kwargs[VENUE]["subscribe_account"] is False
        assert _AccountStream.instances[0].stopped is True
    finally:
        session.close()


def test_account_stream_without_lifecycle_proof_rolls_back_and_stays_read_only(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "bt_api_py.bt_api.ExchangeRegistry.get_stream_class",
        lambda *_args: _AccountStreamWithoutLifecycleProof,
    )
    api, session, _state = _api_for_arm(tmp_path)
    try:
        with pytest.raises(NormalizedApiError) as raised:
            _arm(api)
        assert raised.value.code == "ctp_account_stream_unavailable"
        assert session.config["market_data_only"] is True
        assert session.lock_file is None
        assert api._subscription_streams == []
        assert api._subscription_flags == {}
        assert api.exchange_kwargs[VENUE]["subscribe_account"] is False
        stream = _AccountStreamWithoutLifecycleProof.instances[0]
        assert stream.started is True
        assert stream.stopped is True
    finally:
        session.close()


def test_malformed_journal_load_restores_all_session_state_and_stays_read_only(
    tmp_path,
):
    session = _session(tmp_path)
    original_epoch = session.fencing_epoch
    session.path.write_text(
        json.dumps(
            {
                "event": "client_id_reservation",
                "exchange_name": VENUE,
                "account_id": ACCOUNT_FINGERPRINT,
                "client_order_id": "000000000001",
            }
        )
        + "\n{invalid-json\n",
        encoding="utf-8",
    )
    try:
        with pytest.raises(NormalizedApiError) as raised:
            _arm_direct(session)
        assert raised.value.code == "unreadable_journal"
        assert session.config["market_data_only"] is True
        assert session.lock_file is None
        assert session.fencing_epoch == original_epoch
        assert session.orders == {}
        assert session.used_ids == set()
        assert session.reserved_ids == set()
        assert session.historical_unknown == set()
        assert session.persistence_failed is False
        assert session.risk_error is None
    finally:
        session.close()


def test_malformed_risk_state_load_restores_failure_flags_and_stays_read_only(tmp_path):
    session = _session(tmp_path)
    session.risk_path.write_text("{invalid-json", encoding="utf-8")
    try:
        with pytest.raises(NormalizedApiError) as raised:
            _arm_direct(session)
        assert raised.value.code == "unreadable_or_mismatched_account_risk_state"
        assert session.config["market_data_only"] is True
        assert session.lock_file is None
        assert session.fencing_epoch == 0
        assert session.persistence_failed is False
        assert session.risk_error is None
        assert session.risk_record is None
        assert session.risk_transition_error is None
    finally:
        session.close()
