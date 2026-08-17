import pytest

from bt_api_py.forwarding import (
    MAX_MESSAGE_BYTES as FORWARDING_MAX_MESSAGE_BYTES,
)
from bt_api_py.forwarding import (
    ForwardingError,
    MarketEvent,
    OrderCommand,
    PrivateEvent,
)
from bt_api_py.forwarding import (
    normalize_market_symbol as forwarding_normalize_market_symbol,
)
from bt_api_py.forwarding.schema import (
    MAX_MESSAGE_BYTES,
    deserialize_message,
    normalize_market_symbol,
    serialize_message,
)


def test_market_event_round_trips_with_topic() -> None:
    event = MarketEvent(
        event_type="tick",
        exchange="binance",
        market_type="swap",
        symbol="BTC-USDT",
        payload={"price": 65000.0},
    )

    decoded = deserialize_message(serialize_message(event))

    assert decoded == event
    assert decoded.topic == "md.BINANCE.SWAP.BTC-USDT.tick"


def test_order_command_defaults_to_idempotent_client_id() -> None:
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        idempotency_key="idem-1",
    )

    decoded = deserialize_message(serialize_message(command))

    assert decoded.idempotency_key == "idem-1"
    assert decoded.client_order_id == "idem-1"
    assert decoded.command_type == "place_order"


def test_private_event_round_trips_strategy_topic() -> None:
    event = PrivateEvent(
        event_type="orders",
        account_id="paper",
        strategy_id="s1",
        payload={"kind": "order", "status": "filled"},
    )

    decoded = deserialize_message(serialize_message(event))

    assert decoded.topic == "strategy.s1.orders"
    assert decoded.payload["status"] == "filled"
    assert decoded.payload["account_id"] == "paper"
    assert decoded.payload["strategy_id"] == "s1"


def test_private_event_promotes_real_trade_identifiers_into_payload() -> None:
    event = PrivateEvent(
        event_type="trades",
        account_id="paper",
        strategy_id="s1",
        payload={"kind": "trade"},
        client_order_id="bt-1",
        order_ref="000000001",
        external_order_id="SYS001",
        order_sys_id="SYS001",
        trade_id="TRD001",
        id_source="exchange",
        raw_fields={"OrderSysID": "SYS001", "TradeID": "TRD001"},
    )

    decoded = deserialize_message(serialize_message(event))

    assert decoded.payload["client_order_id"] == "bt-1"
    assert decoded.payload["order_ref"] == "000000001"
    assert decoded.payload["external_order_id"] == "SYS001"
    assert decoded.payload["order_sys_id"] == "SYS001"
    assert decoded.payload["trade_id"] == "TRD001"
    assert decoded.payload["id_source"] == "exchange"
    assert decoded.payload["raw_fields"] == {"OrderSysID": "SYS001", "TradeID": "TRD001"}


@pytest.mark.parametrize("payload", ["{", b"\xff"])
def test_deserialize_message_wraps_malformed_payload_errors(payload) -> None:
    with pytest.raises(ForwardingError, match="Invalid forwarding message payload") as exc_info:
        deserialize_message(payload)

    assert exc_info.value.__cause__ is not None


def test_deserialize_message_rejects_non_object_json_payload() -> None:
    with pytest.raises(ForwardingError, match="Forwarding message payload must be an object"):
        deserialize_message("[]")


def test_serialize_message_rejects_unsupported_message_type() -> None:
    with pytest.raises(ForwardingError, match="Unsupported forwarding message type"):
        serialize_message(object())


def test_serialize_message_wraps_unserializable_payload_errors() -> None:
    with pytest.raises(
        ForwardingError,
        match="Forwarding message payload is not JSON serializable",
    ) as exc_info:
        serialize_message({"bad": object()})

    assert exc_info.value.__cause__ is not None


def test_serialize_message_rejects_oversized_payload() -> None:
    with pytest.raises(ForwardingError, match="Forwarding message exceeds maximum size"):
        serialize_message({"payload": "x" * MAX_MESSAGE_BYTES})


def test_deserialize_message_rejects_oversized_bytes_before_parsing() -> None:
    with pytest.raises(ForwardingError, match="Forwarding message exceeds maximum size"):
        deserialize_message(b"x" * (MAX_MESSAGE_BYTES + 1))


def test_forwarding_package_exports_message_size_limit() -> None:
    assert FORWARDING_MAX_MESSAGE_BYTES == MAX_MESSAGE_BYTES


def test_market_symbol_normalization_is_shared_by_topic_helpers() -> None:
    assert normalize_market_symbol("BTC/USDT") == "BTC-USDT"
    assert forwarding_normalize_market_symbol("BTC/USDT") == "BTC-USDT"
