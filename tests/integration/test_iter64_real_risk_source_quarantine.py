"""Real local Risk/Gateway processes with synthetic v1 source-quarantine facts.

The strategy client here addresses Risk directly.  It is deliberately not
claimed to be the path of a BT order: the current SDK v1 facade stops its own
new requests locally.  All keys, accounts, ports, databases and identities in
this test are created for this test.  This is not provider, account or source
authority evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import ExitStack, suppress
from decimal import Decimal
from pathlib import Path

import pytest
import zmq

from tests.integration._iter64_web_real_host_support import (
    DIRECT,
    DIRECT_OPS,
    _admin,
    _cleanup_owned_fixture,
    _direct_admin,
    _events,
    _fixture_env,
    _fixture_live,
    _fixture_root,
    _gateway_event_check,
    _private_write,
    _record_fixture_outcome,
    _SyntheticConfig,
    _SyntheticKeys,
    _wait_for,
)
from tests.integration.test_iter64_risk_service_process import (
    ACCOUNT,
    CONTROL,
    DOMAIN,
    GATEWAY,
    OPERATOR,
    RISK,
    RISK_HOST,
    RUN,
    SDK_ROOT,
    STRATEGY,
    _instrument,
    _request,
    _risk_client,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint

OLD_KEY = "synthetic-old-reserved"
NEW_KEY = "synthetic-new-key"
PERMIT = "synthetic-old-permit"
RESERVATION = "synthetic-old-reservation"

# References remain available while this Python process lives; process exit or
# external OS action can still independently release them.
_RETAINED_SEED_WRITERS = {}


def _fenced_seed_step(path: Path, scope, action):
    """Mark seed cleanup unknown on any failure; never retry its writer."""
    from bt_api_risk.pretrade.ledger import PretradeLedger

    from bt_api_py.risk_service_host import _open_fenced_journal

    stack = ExitStack()
    try:
        journal, fence = stack.enter_context(_open_fenced_journal(path, scope))
        ledger = PretradeLedger(journal, fence)
        action(ledger)
        stack.close()
    except BaseException:
        # close() may have partially released its callback; stack retention is
        # not proof that a fence remains held. Never retry or operate it again.
        _RETAINED_SEED_WRITERS[str(path)] = stack
        raise RuntimeError("SYNTHETIC_SEED_WRITER_UNCERTAIN") from None


def _seed_historical_permit(path: Path) -> tuple[str, str]:
    """Seed only this test's not-yet-running database under its writer fence."""
    from bt_api_base.risk_service_protocol import RiskOrderView
    from bt_api_risk.core.admission import AccountScope
    from bt_api_risk.pretrade import CallerIdentity, PolicyStore
    from bt_api_risk.pretrade.controls import derive_effect
    from bt_api_risk.pretrade.engine import PretradeEngine
    from bt_api_risk.pretrade.rules import RuleRegistry

    from bt_api_py.gateway_direct_client import canonical_gateway_payload
    from bt_api_py.risk_service_component import RiskOrderService

    scope = AccountScope(provider="ctp", account_id=ACCOUNT, environment="simnow")
    instrument = _instrument()
    caller = CallerIdentity(STRATEGY.principal_ref, STRATEGY.actor_ref, "strategy",
                            frozenset({STRATEGY.actor_ref}), RUN, frozenset({"risk.order.reserve"}))
    payload = canonical_gateway_payload(_request(OLD_KEY, "synthetic-client-old", "2").to_dict())
    mapper = RiskOrderService(
        None, None, account_scope=ACCOUNT, route="CTP___FUTURE", execution_domain_ref="paper:demo",
        principals={}, instrument_for=lambda venue, native: instrument
        if (venue, native) == (instrument.venue, instrument.native_symbol) else None,
        ready=lambda: True,
    )
    view, mapped_payload, refusal = mapper.build_view(caller, OLD_KEY, json.loads(payload), None, None)
    assert type(view) is RiskOrderView and refusal is None and mapped_payload == payload
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    effect, position_side, bucket = derive_effect(view.side, view.offset, instrument.venue)
    now = time.time_ns()
    values = {
        "permit_id": PERMIT,
        "strategy_ref": STRATEGY.actor_ref,
        "request_key": OLD_KEY,
        "run_id": RUN,
        "principal_ref": STRATEGY.principal_ref,
        "origin": "strategy",
        "view_json": json.dumps(view.to_mapping(), sort_keys=True),
        "view_digest": view.digest(),
        "payload_json": payload,
        "payload_digest": digest,
        "instrument_id": instrument.instrument_id,
        "instrument_json": json.dumps(instrument.to_dict(), sort_keys=True),
        "side": view.side,
        "order_offset": view.offset,
        "effect": effect,
        "position_side": position_side,
        "bucket": bucket,
        "quantity": str(view.quantity),
        "limit_price": str(view.limit_price),
        "trading_day": "20300101",
        "policy_version": 1,
        "state": "RESERVED",
        "reservation_id": RESERVATION,
        "gateway_state": "RESERVED",
        "gw_version": 1,
        "gw_digest": digest,
        "owner_generation": "synthetic-old-owner",
        "reservation_generation": "synthetic-old-owner",
        "created_ns": now,
        "expires_ns": now + 20_000_000_000,
        "updated_ns": now,
    }
    def insert_and_check(ledger):
        verifier = PretradeEngine(
            ledger, PolicyStore(ledger), RuleRegistry(), None, None, None,
            account_scope=ACCOUNT, route="CTP___FUTURE", now_ns=time.time_ns,
            reservation_ttl_seconds=20, actor_principal="synthetic-risk-actor",
            dispatch_recheck_seconds=5, dispatch_recheck_limit=8,
        )
        with ledger.transaction() as txn:
            txn.insert_permit(values)
            permit = txn.permit_by_key(STRATEGY.actor_ref, OLD_KEY)
            assert permit is not None and permit.state == "RESERVED"
            assert permit.quantity == permit.occupancy == Decimal("2")
            # Both checks prove local encoding consistency, not account provenance.
            assert verifier._permit_view_if_consistent(permit) == view
            stored_view = RiskOrderView.from_mapping(json.loads(permit.view_json))
            assert stored_view.to_mapping() == view.to_mapping()
            assert stored_view.digest() == permit.view_digest == view.digest()
            assert permit.payload_json == mapped_payload == payload
            assert permit.payload_digest == hashlib.sha256(payload.encode("utf-8")).hexdigest()

    _fenced_seed_step(path, scope, insert_and_check)
    # Reacquisition is explicit evidence that this test's seed writer exited.
    def verify_reacquisition(ledger):
        with ledger.transaction() as txn:
            assert txn.permit_by_key(STRATEGY.actor_ref, OLD_KEY).state == "RESERVED"

    _fenced_seed_step(path, scope, verify_reacquisition)
    return view.digest(), payload


def _readonly_rows(path: Path, query: str, parameters=()):
    # Only paths created under this test's tmp_path are ever passed here.
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as connection:
        connection.execute("PRAGMA query_only=ON")
        return connection.execute(query, parameters).fetchall()


def _permit_rows(path: Path):
    return _readonly_rows(
        path,
        "SELECT * FROM risk_pretrade_permits WHERE strategy_ref=? ORDER BY request_key",
        (STRATEGY.actor_ref,),
    )


def _gateway_counts(path: Path):
    # Counts only show that no new rows were persisted; they do not observe RPC attempts.
    return tuple(
        _readonly_rows(path, query)[0][0]
        for query in ("SELECT count(*) FROM direct_journal_commands",
                      "SELECT count(*) FROM direct_journal_releases")
    )


def _write_risk_fixture(root: Path, keys, gateway_endpoint: str, risk_endpoint: str) -> Path:
    """Write only this fixture's synthetic keys/config through 0600 atomic files."""
    key_dir = root / "keys"
    key_dir.mkdir(mode=0o700)
    for name, (public, secret) in keys.items():
        _private_write(key_dir / (name + ".public"), public.decode("ascii") + "\n")
        _private_write(key_dir / (name + ".secret"), secret.decode("ascii") + "\n")
    _private_write(root / "risk_policy.yaml", (
        "schema: bt_api_risk.policy.v2\n"
        "policy_id: it-64-09\n"
        "account: {route: CTP___FUTURE, account_scope: " + ACCOUNT + "}\n"
        "per_order:\n  max_quantity: {strategy-a: {rb: \"2\"}}\n"
        "self_trade: {enabled: true, on_unverifiable: deny}\n"
    ))
    config = _SyntheticConfig({
        "schema": "bt_api_risk.service.v1",
        "service": {
            "service_id": RISK_HOST.actor_ref, "principal_ref": RISK_HOST.principal_ref,
            "instance_id": "risk-a", "scope_ref": "risk-scope", "rpc_bind": risk_endpoint,
            "public_key_file": "keys/risk_server.public", "secret_key_file": "keys/risk_server.secret",
        },
        "account": {
            "route": "CTP___FUTURE", "provider": "ctp", "environment": "simnow",
            "account_scope": ACCOUNT, "execution_domain_ref": DOMAIN,
            "instruments": {"rb": {"venue": "SHFE", "native_symbol": "rb", "product_type": "future",
                                   "multiplier": "10", "tick_size": "1"}},
        },
        "storage": {"directory": "state"},
        "policy": {"file": "risk_policy.yaml"},
        "principals": [
            {"principal_ref": STRATEGY.principal_ref, "role": "strategy",
             "actor_ref": STRATEGY.actor_ref, "strategy_refs": [STRATEGY.actor_ref],
             "operations": ["risk.order.reserve", "risk.order.dispatch", "risk.order.release",
                            "risk.order.status", "risk.status"], "public_key_file": "keys/strategy.public"},
            {"principal_ref": OPERATOR.principal_ref, "role": "operator", "actor_ref": OPERATOR.actor_ref,
             "strategy_refs": [], "operations": ["risk.admin.status", "risk.unknown.list", "risk.audit.read",
                                                   "risk.latch.list"],
             "public_key_file": "keys/operator.public"},
            {"principal_ref": DIRECT.principal_ref, "role": "operator", "actor_ref": DIRECT.actor_ref,
             "strategy_refs": [], "operations": sorted(DIRECT_OPS), "public_key_file": "keys/independent.public"},
        ],
        "gateway": {
            "service_id": GATEWAY.actor_ref, "principal_ref": GATEWAY.principal_ref,
            "instance_id": "gateway-a", "scope_ref": "scope-a", "rpc_endpoint": gateway_endpoint,
            "server_public_key_file": "keys/gateway_server.public",
            "delegate": {"principal_ref": RISK.principal_ref, "actor_ref": RISK.actor_ref,
                         "public_key_file": "keys/risk_delegate.public",
                         "secret_key_file": "keys/risk_delegate.secret",
                         "grant_revisions": {STRATEGY.actor_ref: "risk-v1"}},
            "control": {"principal_ref": CONTROL.principal_ref, "actor_ref": CONTROL.actor_ref,
                        "public_key_file": "keys/risk_control.public",
                        "secret_key_file": "keys/risk_control.secret"},
        },
        "deadlines": {"call_timeout_seconds": 5, "reservation_ttl_seconds": 20,
                      "dispatch_recheck_seconds": 5},
        "refresh": {"loop_timeout_ms": 20, "observation_poll_seconds": "0.1",
                    "maintain_seconds": 1, "dispatch_recheck_limit": 8},
    })
    path = root / "risk_service.json"
    _private_write(path, json.dumps(config))
    return path


def _strategy_snapshot_certain(snapshot, *, closed: bool) -> bool:
    try:
        from bt_api_py.service_runtime_thread import RuntimeSnapshot

        if type(snapshot) is not RuntimeSnapshot:
            return False
        fields = vars(snapshot)
        required = ("state", "thread_alive", "healthy", "error_code", "cleanup_failures", "unregister_uncertainties",
                    "owner_cleanup_uncertainties", "unknown_registration_count", "strict_unregister_ack")
        if any(name not in fields for name in required):
            return False
        counts = (fields["cleanup_failures"], fields["unregister_uncertainties"],
                  fields["owner_cleanup_uncertainties"], fields["unknown_registration_count"])
        expected_state = "closed" if closed else "running"
        return (type(fields["state"]) is str and fields["state"] == expected_state
                and fields["thread_alive"] is (not closed) and fields["healthy"] is (not closed)
                and fields["error_code"] is None
                and fields["strict_unregister_ack"] is True
                and all(type(value) is int and value == 0 for value in counts))
    except BaseException:
        return False


def _clients_certain(admin, strategy) -> bool:
    """Check local client evidence without initiating unregister or network I/O."""
    try:
        if admin is not None:
            state = admin.state
            counts = (admin.cleanup_failures, admin.unregister_uncertainties, admin.unknown_registration_count)
            if (type(state) is not str or state not in ("registered", "closed")
                    or admin._require_unregister_ack is not True
                    or not all(type(value) is int and value == 0 for value in counts)
                    or (state == "registered" and admin.healthy is not True)):
                return False
        if strategy is not None:
            if strategy._pending_candidate_unknown is not False or strategy._pending_runtime is not None:
                return False
            if strategy._runtime.strict_cleanup_required is not True:
                return False
            snapshot = strategy.runtime_status()
            if not (_strategy_snapshot_certain(snapshot, closed=False)
                    or _strategy_snapshot_certain(snapshot, closed=True)):
                return False
        return True
    except BaseException:
        return False


def _close_strategy_client(strategy) -> bool:
    """Close only after caller verifies reporter, hosts and all clients."""
    try:
        return _strategy_snapshot_certain(strategy.close(timeout_seconds=15), closed=True)
    except BaseException:
        return False


def _cleanup_quarantine_fixture(gateway, risk, admin, strategy, events: Path, *,
                                gateway_ready: bool, risk_ready: bool, client_uncertain: bool) -> bool:
    """Use reporter challenges before each close/stop; any uncertainty stops cleanup."""
    def live():
        return _fixture_live(gateway, risk, events, gateway_ready=gateway_ready, risk_ready=risk_ready)

    def all_clients():
        return _clients_certain(admin, strategy)

    try:
        if client_uncertain is not False or not live() or not all_clients():
            return False
        if strategy is not None and (not live() or not all_clients() or not _close_strategy_client(strategy)):
            return False
        if not live() or not all_clients():
            return False
        return _cleanup_owned_fixture(gateway, risk, admin, None, events, gateway_ready=gateway_ready,
                                      risk_ready=risk_ready, client_uncertain=False,
                                      web_absent_proven=True, pre_action=all_clients)
    except BaseException:
        return False


def _assert_answer(result, outcome):
    assert result.outcome == "ANSWERED"
    assert result.answer is not None and result.answer.outcome == outcome


_RETAINED_PRESTART = {}


def _emit_cleanup_identity(root, gateway, risk):
    """Report only this fixture's validated Popen PIDs, without exception text."""
    def safe_pid(process):
        try:
            pid = process.pid
            return str(pid) if type(pid) is int and pid > 0 else "unknown"
        except BaseException:
            return "unknown"

    print("SYNTHETIC_CLEANUP_IDENTITY root=" + json.dumps(str(root))
          + " gateway_pid=" + safe_pid(gateway) + " risk_pid=" + safe_pid(risk)
          + " pid_may_be_reused=true stop_authorized=false", flush=True)


def _record_cleanup(root, complete, gateway, risk, admin, strategy, keys):
    from tests.integration._iter64_web_real_host_support import _RetainedFixture

    try:
        complete = _record_fixture_outcome(root, complete, gateway, risk, (admin, strategy), None, None, keys)
    except BaseException:
        complete = False
        _RETAINED_PRESTART[str(root)] = _RetainedFixture(gateway, risk, admin, strategy, keys)
    if not complete:
        _emit_cleanup_identity(root, gateway, risk)
    return complete


def _exercise_source_quarantine(root: Path):
    from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
    from bt_api_risk.pretrade.engine import _v1_source_quarantined

    from tests.integration._iter64_web_real_host_support import _RetainedFixture

    gateway = risk = admin = strategy = keys = None
    gateway_ready = risk_confirmed = False
    client_uncertain = True
    events = root / "gateway-events.jsonl"
    stage = "SETUP"
    try:
        assert _v1_source_quarantined() is True
        keys = _SyntheticKeys({name: zmq.curve_keypair() for name in (
            "gateway_server", "strategy", "risk_delegate", "risk_control", "risk_server",
            "operator", "independent")})
        gateway_root, risk_root = root / "gateway", root / "risk"
        gateway_root.mkdir(mode=0o700)
        risk_root.mkdir(mode=0o700)
        gateway_endpoint, risk_endpoint = _free_endpoint(), _free_endpoint()
        config_path = _write_risk_fixture(risk_root, keys, gateway_endpoint, risk_endpoint)
        (risk_root / "state").mkdir(mode=0o700)
        risk_db = risk_root / "state" / "risk.sqlite"
        view_digest, seed_payload = _seed_historical_permit(risk_db)
        before_permits = _permit_rows(risk_db)
        assert len(before_permits) == 1
        gateway_config = root / "gateway-test.json"
        _private_write(gateway_config, json.dumps(_SyntheticConfig({
            "root": str(gateway_root), "endpoint": gateway_endpoint, "events": str(events),
            "keys": {name: [item.decode("ascii") for item in pair] for name, pair in keys.items()},
        })))
        stage = "GATEWAY_START"
        helper = SDK_ROOT / "tests/integration/_iter64_web_real_host_support.py"
        gateway = subprocess.Popen(
            [sys.executable, str(helper), "--synthetic-gateway", str(gateway_config)],
            cwd=str(SDK_ROOT), env=_fixture_env(root), stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _private_write(root / "fixture-identity.json", json.dumps({
            "gateway_pid": gateway.pid, "risk_pid": None, "state": "initializing",
        }))
        stage = "GATEWAY_READY"
        ready = _wait_for(lambda: next((event for event in _events(events)
                                        if event["event"] in {"ready", "error", "cleanup_uncertain"}), None))
        assert ready == {"event": "ready", "pid": gateway.pid}
        gateway_ready = _gateway_event_check(events, gateway)
        assert gateway_ready
        gateway_db = gateway_root / "journal.sqlite"
        before_gateway = _gateway_counts(gateway_db)
        ready_file = risk_root / "ready.json"
        stage = "RISK_START"
        risk = subprocess.Popen(
            [sys.executable, "-m", "bt_api_py.risk_service_host", "run", "--config", str(config_path),
             "--ready-file", str(ready_file)], cwd=str(SDK_ROOT), env=_fixture_env(root),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _private_write(root / "fixture-identity.json", json.dumps({
            "gateway_pid": gateway.pid, "risk_pid": risk.pid, "state": "initializing",
        }))

        def ready_check():
            if risk.poll() is not None or not _gateway_event_check(events, gateway):
                raise RuntimeError("SYNTHETIC_HOST_UNCERTAIN")
            return ready_file.exists()

        stage = "RISK_READY"
        _wait_for(ready_check, seconds=60)
        assert json.loads(ready_file.read_text(encoding="utf-8"))["pid"] == risk.pid
        risk_confirmed = True

        def require_live():
            if not _fixture_live(gateway, risk, events, gateway_ready=gateway_ready, risk_ready=risk_confirmed):
                raise RuntimeError("SYNTHETIC_HOST_UNCERTAIN")

        stage = "CLIENT_START"
        require_live()
        admin = _direct_admin(risk_endpoint, keys)
        admin.start()
        assert admin.healthy
        require_live()
        assert _admin(admin, "risk.policy.read")["document"]["self_trade"]["enabled"] is True
        require_live()
        assert _admin(admin, "risk.admin.status")["summary"]["available"] is False
        require_live()
        strategy = _risk_client(risk_endpoint, keys)
        started = strategy.start(timeout_seconds=15)
        assert started.healthy and started.strict_unregister_ack is True
        stage = "SOURCE_QUARANTINE"
        requests = (
            (OLD_KEY, _request(OLD_KEY, "synthetic-client-old", "2")),
            (NEW_KEY, _request(NEW_KEY, "synthetic-client-new")),
        )
        assert canonical_gateway_payload(requests[0][1].to_dict()) == seed_payload
        for key, request in requests:
            require_live()
            _assert_answer(strategy.reserve(request, request_key=key), "UNKNOWN")
            require_live()
            _assert_answer(strategy.dispatch(key, permit_id=PERMIT, reservation_id=RESERVATION,
                                             view_digest=view_digest), "UNKNOWN")
            require_live()
            _assert_answer(strategy.release(key, permit_id=PERMIT), "UNKNOWN")
        conflicting = _request(OLD_KEY, "synthetic-client-old", "1")
        assert canonical_gateway_payload(conflicting.to_dict()) != seed_payload
        require_live()
        _assert_answer(strategy.reserve(conflicting, request_key=OLD_KEY), "UNKNOWN")
        require_live()
        _assert_answer(strategy.status(OLD_KEY), "UNKNOWN")
        # A local NOT_FOUND is not evidence of no send by an older generation.
        require_live()
        _assert_answer(strategy.status(NEW_KEY), "NOT_FOUND")
        require_live()
        assert _permit_rows(risk_db) == before_permits
        # No added persisted rows does not prove that no network attempt occurred.
        assert _gateway_counts(gateway_db) == before_gateway
        client_uncertain = False
    except BaseException:
        print("SYNTHETIC_RISK_STAGE " + stage, flush=True)
        raise
    finally:
        if gateway is None:
            _RETAINED_PRESTART[str(root)] = _RetainedFixture(risk, admin, strategy, keys)
            with suppress(BaseException):
                _private_write(root / "fixture-identity.json", json.dumps({
                    "gateway_pid": None, "risk_pid": None, "state": "cleanup_unknown",
                }))
            _emit_cleanup_identity(root, None, risk)
            print("SYNTHETIC_CLEANUP cleanup_unknown", flush=True)
        else:
            complete = _cleanup_quarantine_fixture(
                gateway, risk, admin, strategy, events, gateway_ready=gateway_ready,
                risk_ready=risk_confirmed, client_uncertain=client_uncertain,
            )
            complete = _record_cleanup(root, complete, gateway, risk, admin, strategy, keys)
            print("SYNTHETIC_CLEANUP " + ("closed" if complete else "cleanup_unknown"), flush=True)
            if not complete:
                raise RuntimeError("SYNTHETIC_CLEANUP_UNKNOWN") from None


def test_real_risk_process_keeps_source_quarantined_permit():
    # The private POSIX root (getuid + 0700 owner checks) has no Windows
    # equivalent here; skip cleanly instead of reporting a false product
    # failure.  The same scenario is covered on POSIX hosts.
    if os.name != "posix" or not hasattr(os, "getuid"):
        pytest.skip("private POSIX fixture root unavailable on this platform")
    try:
        root = _fixture_root()
        print("SYNTHETIC_FIXTURE_ROOT " + str(root), flush=True)
        _exercise_source_quarantine(root)
    except KeyboardInterrupt:
        raise KeyboardInterrupt() from None
    except SystemExit:
        raise SystemExit(1) from None
    except BaseException:
        raise pytest.fail.Exception("SYNTHETIC_RISK_SCENARIO_FAILED", pytrace=False) from None
