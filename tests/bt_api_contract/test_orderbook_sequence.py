"""Orderbook sequence transparency through the normalized event contract.

Exchanges attach continuity identifiers to depth updates (Binance ``u`` /
``lastUpdateId``, OKX l2 ``checksum`` sequences). Dropping them made it
impossible for consumers to detect dropped or out-of-order books. The
normalized orderbook event must expose a ``sequence`` when the venue
container preserved one. Missing sequence evidence remains ``None`` and is
never presented as a continuous sequence zero.
"""

from __future__ import annotations

import json

from bt_api_binance.containers.orderbooks.binance_orderbook import (
    BinanceRequestOrderBookData,
    BinanceWssOrderBookData,
)

from bt_api_py._normalization import normalize_event


def test_orderbook_dict_event_exposes_binance_update_id_as_sequence():
    event = normalize_event(
        {"bids": [[100, 2]], "asks": [[101, 3]], "u": 12345},
        "BINANCE___SWAP",
        kind="orderbook",
        symbol="BTCUSDT",
    )

    assert event["sequence"] == 12345


def test_orderbook_dict_event_exposes_named_sequence_field():
    event = normalize_event(
        {"bids": [[100, 2]], "asks": [[101, 3]], "sequence": 42},
        "BINANCE___SWAP",
        kind="orderbook",
        symbol="BTCUSDT",
    )

    assert event["sequence"] == 42


def test_orderbook_event_without_sequence_is_explicitly_unknown():
    event = normalize_event(
        {"bids": [[100, 2]], "asks": [[101, 3]]},
        "OKX___SWAP",
        kind="orderbook",
        symbol="BTC-USDT-SWAP",
    )

    assert event["sequence"] is None
    assert event["continuity_status"] == "unverified"
    assert event["previous_sequence"] is None
    assert event["received_monotonic_ns"] > 0
    assert event["clock_domain_id"]
    assert event["event_id"]


def test_zero_previous_sequence_is_a_delta_without_claiming_continuity():
    event = normalize_event(
        {"bids": [[100, 2]], "asks": [[101, 3]], "u": 9, "pu": 0},
        "BINANCE___SWAP",
        kind="orderbook",
        symbol="BTCUSDT",
    )

    assert event["sequence"] == 9
    assert event["previous_sequence"] == 0
    assert event["snapshot_or_delta"] == "delta"
    assert event["continuity_status"] == "unverified"


def test_binance_wss_container_preserves_final_update_id():
    raw = json.dumps(
        {
            "s": "BTCUSDT",
            "E": 1700000000000,
            "u": 777,
            "b": [["100", "2"]],
            "a": [["101", "3"]],
        }
    )
    book = BinanceWssOrderBookData(raw, "BTCUSDT", "SWAP")

    assert book.init_data().get_all_data()["sequence_id"] == 777

    event = normalize_event(book, "BINANCE___SWAP", kind="orderbook", symbol="BTCUSDT")

    assert event["sequence"] == 777


def test_binance_rest_container_preserves_last_update_id():
    raw = json.dumps(
        {
            "lastUpdateId": 9001,
            "bids": [["100", "2"]],
            "asks": [["101", "3"]],
        }
    )
    book = BinanceRequestOrderBookData(raw, "BTCUSDT", "SWAP")

    assert book.init_data().get_all_data()["sequence_id"] == 9001

    event = normalize_event(book, "BINANCE___SWAP", kind="orderbook", symbol="BTCUSDT")

    assert event["sequence"] == 9001
