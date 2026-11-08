"""Prepared insert failures through the SDK and guarded synthetic ledger."""

import json

import pytest
from bt_api_base.direct_place_protocol import PreparedPlaceRequest
from bt_api_base.direct_write_protocol import NativeOrderIdentity
from bt_api_ctp.gateway_cancel_source import source_tags_digest
from bt_api_ctp.gateway_place_source import place_attempt_key
from bt_api_ctp.gateway_return_buffer import CtpGatewayReturnBuffer

from bt_api_py.ctp_gateway_return_ingestion import CtpGatewayReturnIngestionError
from bt_api_py.direct_write import project_direct_strong_identity_keys
from tests.integration.test_iter63_ctp_gateway_return_ingestion import (
    DAY,
    INSTRUMENT,
    _fact_count,
    _order,
    _trade,
)
from tests.integration.test_iter63_ctp_gateway_return_ingestion import (
    owner_guard as owner_guard,
)
from tests.integration.test_iter63_ctp_gateway_return_ingestion import (
    system as system,
)
from tests.unit.test_iter63_gateway_cancel_return_ingestion import SOURCE, TAGS

SOURCE_BINDING = {
    "schema": "ctp-place-source-v1",
    "source_ref": SOURCE,
    "source_tags_digest": source_tags_digest(TAGS),
    "login_request_id": 1,
}


def prepared_command(journal, key="place-a", number=11):
    ref = f"{number:012d}"
    identity = NativeOrderIdentity(
        "CTP___FUTURE",
        ref,
        {
            "trading_day": DAY,
            "front_id": 7,
            "session_id": 19,
            "order_ref": ref,
            "exchange_id": "SHFE",
        },
        1,
    )
    fields = {
        "BrokerID": "9999",
        "InvestorID": "account",
        "UserID": "account",
        "InstrumentID": "rb2701",
        "ExchangeID": "SHFE",
        "Direction": "0",
        "CombOffsetFlag": "0",
        "CombHedgeFlag": "1",
        "VolumeTotalOriginal": 2,
        "MinVolume": 1,
        "ForceCloseReason": "0",
        "IsAutoSuspend": 0,
        "UserForceClose": 0,
        "ContingentCondition": "1",
        "OrderPriceType": "2",
        "TimeCondition": "3",
        "VolumeCondition": "1",
        "LimitPrice": float(100).hex(),
        "OrderRef": ref,
        "RequestID": number,
    }
    native_key = place_attempt_key(
        broker="9999",
        investor="account",
        day=DAY,
        front=7,
        session=19,
        request_id=number,
        order_ref=ref,
        source_binding=SOURCE_BINDING,
    )
    prepared = PreparedPlaceRequest(
        "prepared-" + key, identity, "rb2701", DAY, 1, fields, (native_key,), SOURCE_BINDING
    )
    command = journal.reserve(
        "strategy-a",
        key,
        "principal-a",
        {"symbol": "rb2701", "side": "buy", "offset": "open", "quantity": "2"},
    )
    wire = {
        "exchange_name": identity.exchange_name,
        "client_order_id": ref,
        "session_generation": 1,
        "fields_json": json.dumps(dict(identity.fields), sort_keys=True, separators=(",", ":")),
    }
    journal.begin_dispatch(
        "strategy-a",
        key,
        command.reservation_id,
        command.digest,
        "principal-a",
        command.version,
        wire,
        project_direct_strong_identity_keys(identity, day_scoped_ids=True),
        prepared_place=prepared,
    )
    return command, prepared


def error_buffer():
    return CtpGatewayReturnBuffer(broker_id="9999", investor_id="account")


def append_place_error(buffer, prepared, *, kind="insert_response", code=9, **changes):
    row = dict(prepared.request_fields)
    row["LimitPrice"] = float.fromhex(row["LimitPrice"])
    row.update(changes)
    assert buffer._append(
        kind=kind,
        row=row,
        source_ref=SOURCE,
        source_tags=TAGS,
        connection_generation=1,
        reception_trading_day=DAY,
        front_id=7,
        session_id=19,
        request_id=row["RequestID"] if kind == "insert_response" else None,
        is_last=True if kind == "insert_response" else None,
        error_code=code,
        login_request_id=1,
    )


def assert_current(buffer, record):
    if not buffer._contains_record(record):
        raise RuntimeError("stale ticket")


def test_distinct_places_same_error_deduplicate_replay_and_keep_original(system):
    ingestor, _, _, _, journal, ledger = system
    one, prepared_one = prepared_command(journal, "place-a", 11)
    two, prepared_two = prepared_command(journal, "place-b", 12)
    buffer = error_buffer()
    append_place_error(buffer, prepared_one)
    append_place_error(buffer, prepared_one, kind="insert_error")
    append_place_error(buffer, prepared_two)
    batch = buffer.offer()

    def check(record):
        assert_current(buffer, record)

    assert ingestor.ingest_callbacks(batch, assert_current=check) == 3
    assert ingestor.ingest_callbacks(batch, assert_current=check) == 3
    events = ledger.read_events("strategy-a", after_sequence=0, limit=10)
    assert len(events) == 2 and events[0].fact_digest != events[1].fact_digest
    assert [event.place_attribution.prepared_digest for event in events] == [
        prepared_one.digest(),
        prepared_two.digest(),
    ]
    assert all(event.fact.status is None for event in events)
    assert ledger.position("strategy-a", INSTRUMENT) is None
    assert journal.get("strategy-a", "place-a", "principal-a").status == "DISPATCHING"
    assert journal.get("strategy-a", "place-b", "principal-a").status == "DISPATCHING"
    buffer.ack(batch)


@pytest.mark.parametrize("change", ["unknown_key", "bad_price"])
def test_malformed_second_record_preserves_whole_batch(system, change):
    ingestor, _, _, _, journal, _ = system
    _, first = prepared_command(journal, "place-a", 11)
    _, second = prepared_command(journal, "place-b", 12)
    buffer = error_buffer()
    append_place_error(buffer, first)
    append_place_error(
        buffer, second, **({"RequestID": 99} if change == "unknown_key" else {"LimitPrice": 101.0})
    )
    batch = buffer.offer()
    with pytest.raises(CtpGatewayReturnIngestionError, match="PROJECTION_FAILED"):
        ingestor.ingest_callbacks(
            batch, assert_current=lambda record: assert_current(buffer, record)
        )
    assert _fact_count(journal) == 0 and buffer.offer() is batch


def test_zero_response_not_acked_and_currentness_rechecked_in_transaction(system):
    ingestor, _, _, _, journal, _ = system
    _, prepared = prepared_command(journal)
    buffer = error_buffer()
    append_place_error(buffer, prepared, code=0)
    batch = buffer.offer()
    with pytest.raises(CtpGatewayReturnIngestionError, match="UNSUPPORTED_RETURN"):
        ingestor.ingest_callbacks(
            batch, assert_current=lambda record: assert_current(buffer, record)
        )
    assert _fact_count(journal) == 0 and buffer.offer() is batch

    other = error_buffer()
    append_place_error(other, prepared)
    offered = other.offer()
    calls = []

    def stale(record):
        assert_current(other, record)
        calls.append(1)
        if len(calls) == 3:
            raise ValueError("synthetic stale source")

    with pytest.raises(CtpGatewayReturnIngestionError, match="SOURCE_STALE") as exc:
        ingestor.ingest_callbacks(offered, assert_current=stale)
    assert exc.value.completed_count == 0 and not exc.value.in_flight_may_have_committed
    assert _fact_count(journal) == 0 and other.offer() is offered


def test_late_order_and_trade_still_apply_after_error(system):
    ingestor, _, _, native, journal, ledger = system
    _, prepared = prepared_command(journal)
    buffer = error_buffer()
    append_place_error(buffer, prepared)
    batch = buffer.offer()
    assert (
        ingestor.ingest_callbacks(
            batch, assert_current=lambda record: assert_current(buffer, record)
        )
        == 1
    )
    native.rows["orders"] = [_order(OrderRef=prepared.identity.client_order_id)]
    native.rows["trades"] = [_trade(OrderRef=prepared.identity.client_order_id)]
    ingestor.read_and_ingest("orders", timeout=1)
    ingestor.read_and_ingest("trades", timeout=1)
    assert ledger.position("strategy-a", INSTRUMENT) == "1"
    assert journal.get("strategy-a", "place-a", "principal-a").status == "DISPATCHING"


def test_lost_place_commit_keeps_prefix_uncertain_and_replay_idempotent(system, monkeypatch):
    from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger

    ingestor, _, _, _, journal, ledger = system
    _, first = prepared_command(journal, "place-a", 11)
    _, second = prepared_command(journal, "place-b", 12)
    buffer = error_buffer()
    append_place_error(buffer, first)
    append_place_error(buffer, second)
    batch = buffer.offer()
    ingest = GatewayDirectExecutionLedger.ingest_place_error
    calls = []

    def lose_second_reply(self, *args, **kwargs):
        result = ingest(self, *args, **kwargs)
        calls.append(1)
        if len(calls) == 2:
            raise OSError("synthetic post-commit failure")
        return result

    monkeypatch.setattr(GatewayDirectExecutionLedger, "ingest_place_error", lose_second_reply)
    with pytest.raises(CtpGatewayReturnIngestionError, match="INGEST_FAILED") as exc:
        ingestor.ingest_callbacks(
            batch, assert_current=lambda record: assert_current(buffer, record)
        )
    assert exc.value.completed_count == 1 and exc.value.in_flight_may_have_committed
    assert _fact_count(journal) == 2 and buffer.offer() is batch
    monkeypatch.setattr(GatewayDirectExecutionLedger, "ingest_place_error", ingest)
    assert (
        ingestor.ingest_callbacks(
            batch, assert_current=lambda record: assert_current(buffer, record)
        )
        == 2
    )
    assert _fact_count(journal) == 2
    assert len(ledger.read_events("strategy-a", after_sequence=0, limit=10)) == 2
