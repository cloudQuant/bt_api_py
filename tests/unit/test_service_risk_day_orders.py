"""Normal DAY requests through real Risk service and durable recovery; synthetic provider."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import json
import time

import pytest
from tests.unit import test_service_risk_order_exposure_integration as fixtures
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_py.bt_api import BtApi
from bt_api_py._venue_mappers import MAPPERS
from bt_api_py._venue_mappers.ctp import map_order_request as map_ctp_order
from bt_api_py.service_risk_order_entry import (
    RiskOrderEntryService, RiskOrderTarget, RiskOrderEntryError,
    _validate_recovery_observation_source,
)
from bt_api_py.service_risk_order_journal import RiskOrderJournal, canonical_json, sha256_text
from bt_api_risk import (
    DurableRiskGate, DurableRiskAdmissionAdapter, InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata, InstrumentRiskRegistry, RiskPolicy,
)


class RecordingFeed(fixtures._V2SyntheticBtApiFeed):
    def __init__(self, path):
        super().__init__(str(path), ("open",))
        self.sent = []

    def make_order(self, *args, **kwargs):
        self.sent.append(dict(kwargs))
        return super().make_order(*args, **kwargs)


class ProcessLoss(BaseException):
    """Synthetic interruption after the real durable PROOF_READY write."""


@contextmanager
def stack(path, monkeypatch, profile=("DAY",)):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    # Use the real CTP mapping path with a synthetic response feed and fixture identity.
    monkeypatch.setitem(MAPPERS, fixtures._EXCHANGE, map_ctp_order)
    scope = fixtures._ACCOUNT_SCOPE
    now = time.time_ns()
    metadata = InstrumentRiskMetadata(
        instrument=fixtures._V2_INSTRUMENT, metadata_version=fixtures._V2_METADATA_VERSION,
        as_of_ns=now - 1_000_000_000, expires_at_ns=now + 3_600_000_000_000,
        tick_size=Decimal("0.01"), quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"), max_gross_notional=Decimal("1000"),
        taker_fee_bps=Decimal("0"), fixed_fee=Decimal("0"),
        max_slippage_bps=Decimal("0"), quantity_unit="BTC", valuation_unit="USD",
    )
    # Persisted commands bind the metadata digest, so a restart reuses identical facts.
    metadata_path = path / "metadata-times.txt"
    if metadata_path.exists():
        start, end = map(int, metadata_path.read_text().split())
        metadata = replace(metadata, as_of_ns=start, expires_at_ns=end)
    else:
        metadata_path.write_text(f"{metadata.as_of_ns} {metadata.expires_at_ns}")
    registry = InstrumentRiskRegistry((metadata,))
    journal = RiskOrderJournal(path / "journal.sqlite3", writer_id="day-order-owner")
    gate = DurableRiskGate(path / "risk.sqlite3", RiskPolicy(
        "day-order-policy-v1", Decimal("1000"), max_increase_count=5,
        permit_ttl_seconds=60, notional_unit="USD",
    ), execution_journal_authority=journal)
    api = BtApi(debug=False)
    try:
        feed = RecordingFeed(path / "provider.sqlite3")
        api.exchange_feeds[fixtures._EXCHANGE] = feed
        target = RiskOrderTarget(scope, fixtures._SCOPE.strategy_ref,
                                 fixtures._EXCHANGE, scope.account_id, registry)
        contract = fixtures._contract(profile)
        service = RiskOrderEntryService(
            api, {fixtures._SCOPE: target},
            {fixtures._SCOPE: DurableRiskAdmissionAdapter(
                gate, InstrumentRiskAdmissionMapper(scope, registry))},
            journal, gate, {fixtures._SCOPE: contract},
        )
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, fixtures.base._PRINCIPAL,
                                fixtures._SCOPE.strategy_ref)
        def call(operation, arguments):
            return service.handle(operation, principal_ref=identity.principal_ref,
                                  identity=identity, scope=fixtures._SCOPE, arguments=arguments)
        yield SimpleNamespace(service=service, journal=journal, gate=gate, feed=feed,
                              target=target, contract=contract, scope=scope, call=call,
                              principal=identity.principal_ref)
    finally:
        try:
            api.close()
        finally:
            gate.close()


def arguments(tif="DAY", intent="day-order"):
    value = fixtures.base._order_arguments(intent)
    value.update(instrument=fixtures._V2_INSTRUMENT,
                 metadata_version=fixtures._V2_METADATA_VERSION, time_in_force=tif)
    return value


@pytest.mark.parametrize("profile,tif", [(("DAY",), "DAY"), (("GTC", "DAY"), "DAY"), (("GTC",), "GTC")])
def test_supported_normal_tif_is_preserved_through_dispatch(tmp_path, monkeypatch, profile, tif):
    with stack(tmp_path, monkeypatch, profile) as env:
        assert env.service.capability_state(fixtures._SCOPE)["orders.submit"] is True
        reply = env.call("orders.submit", arguments(tif))
        assert reply["ok"] and reply["result"]["record"]["state"] == "ACKED_TRACKED", reply
        command = env.journal.get_command(env.scope, env.principal, "day-order")
        assert command.canonical_payload["time_in_force"] == tif
        assert command.order_request["time_in_force"] == tif
        assert command.payload_sha256 == sha256_text(canonical_json(dict(command.canonical_payload)))
        assert command.order_request_sha256 == sha256_text(canonical_json({
            "exchange_name": fixtures._EXCHANGE, "operation": "BtApi.make_order(normalized=True)",
            "request": dict(command.order_request)}))
        assert len(env.feed.sent) == 1 and env.feed.sent[0]["time_in_force"] == tif
        before = env.feed.snapshot()
        assert env.call("orders.submit", arguments(tif))["result"]["record"]["state"] == "ACKED_TRACKED"
        assert env.feed.snapshot() == before and len(env.feed.sent) == 1


@pytest.mark.parametrize("profile,tif", [(("DAY",), "GTC"), (("GTC",), "DAY"), (("GTC", "DAY"), "IOC"), (("DAY",), "FOK")])
def test_unadvertised_or_unsupported_normal_tif_never_dispatches(tmp_path, monkeypatch, profile, tif):
    with stack(tmp_path, monkeypatch, profile) as env:
        reply = env.call("orders.submit", arguments(tif))
        assert reply["ok"] is False and reply["code"] == "LIMIT_GTC_ONLY", reply
        assert env.journal.get_command(env.scope, env.principal, "day-order") is None
        assert env.feed.sent == [] and env.feed.snapshot()[0] == ()


def test_day_proof_ready_reopens_and_resolves_without_resending(tmp_path, monkeypatch):
    with stack(tmp_path, monkeypatch) as env:
        def lose_process(_proof):
            raise ProcessLoss()
        monkeypatch.setattr(env.gate, "resolve_dispatch_freeze", lose_process)
        with pytest.raises(ProcessLoss):
            env.call("orders.submit", arguments())
        command = env.journal.get_command(env.scope, env.principal, "day-order")
        assert command.state == "PROOF_READY"
        receipt = env.gate.dispatch_resolution_receipt(command.permit_id)
        assert receipt.dispatch_latch_active is True
        original_writes = env.feed.snapshot()[0]
        assert len(original_writes) == 1
        with env.journal.writer_fence(env.scope) as fence:
            source = env.journal.load_dispatch_proof_for_recovery(
                fence, principal_ref=env.principal, intent_id=command.intent_id, claim=receipt.claim)
            assert source is not None
            _validate_recovery_observation_source(source, target=env.target, contract=env.contract)
            # Exercise semantic recovery checks even when a modified public
            # projection has a self-consistent observation digest. These copied
            # source objects never modify the persisted journal or Risk receipt.
            for event_name, field, value in (
                ("order_observation_event", "order_revision", 2),
                ("order_observation_event", "source_alias_map_sha256", "f" * 64),
                ("order_observation_event", "claim_digest", "f" * 64),
                ("order_observation_event", "risk_intent_id", "another-intent"),
                ("order_observation_event", "metadata_facts", {}),
                ("order_observation_event", "position_effect", "CLOSE"),
                ("order_observation_event", "unexpected_field", "untrusted"),
                ("order_observation_event", "updated_at_ns", True),
                ("fill_observation_event", "fill_watermark_revision", 2),
                ("fill_observation_event", "fill_row_provenance", [{}]),
                ("fill_observation_event", "filled_quantity", 0),
            ):
                event = getattr(source, event_name)
                payload = json.loads(canonical_json(dict(event.payload)))
                payload[field] = value
                digest_payload = dict(payload)
                digest_payload.pop("provider_observation_sha256")
                digest_payload.pop("updated_at_ns")
                payload["provider_observation_sha256"] = sha256_text(canonical_json(digest_payload))
                changed_source = replace(source, **{event_name: replace(event, payload=payload)})
                with pytest.raises(RiskOrderEntryError):
                    _validate_recovery_observation_source(
                        changed_source, target=env.target, contract=env.contract)
            assert env.gate.dispatch_resolution_receipt(command.permit_id).dispatch_latch_active is True
            assert env.feed.snapshot()[0] == original_writes
            with pytest.raises(RiskOrderEntryError, match="RECOVERY_REQUEST_INVALID"):
                _validate_recovery_observation_source(
                    source, target=env.target, contract=fixtures._contract(("GTC",)))
            changed_request = dict(source.command.order_request, time_in_force="GTC")
            changed = replace(source, command=replace(source.command, order_request=changed_request))
            with pytest.raises(RiskOrderEntryError, match="RECOVERY_REQUEST_INVALID"):
                _validate_recovery_observation_source(changed, target=env.target, contract=env.contract)
    with stack(tmp_path, monkeypatch) as reopened:
        recovered = reopened.service.recover_interrupted_dispatches()
        assert len(recovered) == 1 and recovered[0].state == "ACKED_TRACKED"
        command = recovered[0]
        assert command.canonical_payload["time_in_force"] == "DAY"
        assert command.order_request["time_in_force"] == "DAY"
        assert reopened.gate.dispatch_resolution_receipt(command.permit_id).dispatch_latch_active is False
        assert reopened.feed.sent == [] and reopened.feed.snapshot()[0] == original_writes
        reply = reopened.call("orders.submit", arguments())
        assert reply["result"]["record"]["state"] == "ACKED_TRACKED", reply
        assert reopened.feed.sent == [] and reopened.feed.snapshot()[0] == original_writes
