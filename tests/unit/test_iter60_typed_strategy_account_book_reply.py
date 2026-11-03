"""Iteration 60 (re-verification G5): typed ``strategy_account_book`` service replies.

The producer (``service_execution_account_books``) adds the strategy's book as
one strict Base ``strategy_account_book`` typed part (``bt_api.wire.v1.part``,
encoded once, no base64 layer since 60.08 minor 5) to a strategy
``OWNER_BOOK`` reply; ``ServiceStrategyAccountBookClient`` requires and decodes it. The real
service handler round trip is in
``test_service_execution_account_books_integration``.
"""

from __future__ import annotations

import copy

import pytest

pytest.importorskip("bt_api_execution")

from bt_api_base.account_book_protocol import (  # noqa: E402
    StrategyAccountBookSnapshot,
    parse_strategy_account_book_reply,
)

from bt_api_py.service_account_book_client import (  # noqa: E402
    ServiceStrategyAccountBookClient,
    ServiceStrategyAccountBookClientError,
)
from bt_api_py.service_execution_account_books import _typed_strategy_book  # noqa: E402

ACCOUNT = "account.synthetic"
ACCOUNT_KEY = "execution-account-a"
STRATEGY = "strategy.synthetic"


def _cut(generation="journal-r1", cursor=4):
    return {
        "account_key": ACCOUNT_KEY,
        "owner_directory_revision": "financial-owners-r1",
        "captured_at_ns": 400,
        "watermarks": [
            {
                "source_kind": "execution_outbox",
                "source_ref": "sqlite-execution-outbox",
                "generation": generation,
                "cursor": cursor,
                "as_of_ns": 300,
            },
            {
                "source_kind": "account_facts",
                "source_ref": "provider-facts",
                "generation": "facts-r1",
                "cursor": 4,
                "as_of_ns": 300,
            },
            {
                "source_kind": "account_activity",
                "source_ref": "provider-activity",
                "generation": "activity-r1",
                "cursor": 2,
                "as_of_ns": 300,
            },
        ],
    }


def _projected(*, complete=True, owner_ref=STRATEGY):
    cut = _cut()
    return {
        "view": "OWNER_BOOK",
        "owner_kind": "STRATEGY",
        "owner_ref": owner_ref,
        "coverage": {
            "account_key": ACCOUNT_KEY,
            "complete": complete,
            "scope_directory_revision": "owners-r1",
            "journal_generation": "journal-r1",
            "baseline_execution_cursor": 2,
            "current_execution_cursor": 4,
        },
        "books": [
            {
                "owner_kind": "STRATEGY",
                "owner_ref": owner_ref,
                "current_positions": [{"instrument": "BTC-USDT", "net_quantity": "1.25"}],
                "fees_since_baseline": [{"currency": "USDT", "amount": "0.04"}],
            }
        ],
        "financial": {
            "available": True,
            "source": "PERSISTED_EXECUTION_FINANCIAL_PROJECTION",
            "freshness": "PERSISTED_CUT_NOT_LIVE_SOURCE_VERIFIED",
            "cut_relationship": "INDEPENDENT_OF_LEGACY_ACCOUNT_BOOK_CUT",
            "complete": True,
            "ledger_cut": cut,
            "view": "OWNER_BOOK",
            "owner_kind": "STRATEGY",
            "owner_ref": owner_ref,
            "books": [
                {
                    "owner": {
                        "kind": "STRATEGY",
                        "owner_ref": owner_ref,
                        "execution_scope_key": "scope-strategy-a",
                    },
                    "ledger_cut": cut,
                    "cash": [{"currency": "USDT", "amount": "1000"}],
                    "positions": [
                        {
                            "instrument": "BTC-USDT",
                            "quantity": "1.25",
                            "cost_basis": "125000",
                            "cost_currency": "USDT",
                            "cost_method": "AVERAGE_COST",
                            "product_model": "SPOT",
                            "metadata_version": "synthetic-spot-v1",
                        }
                    ],
                    "cumulative_fees": [{"currency": "USDT", "amount": "0.25"}],
                    "cost_basis_complete": True,
                }
            ],
        },
    }


def _reply(projected=None, typed=True):
    projected = dict(_projected() if projected is None else projected)
    if typed:
        projected["strategy_account_book"] = _typed_strategy_book(
            projected, strategy_ref=STRATEGY, account_ref=ACCOUNT
        )
    return {"ok": True, "code": "OK", "result": projected}


def _client(strategy_ref=STRATEGY, account_ref=ACCOUNT):
    # Only the reply decoding is under test here; the runtime/route binding is
    # exercised through a real runtime in the integration test.
    client = object.__new__(ServiceStrategyAccountBookClient)
    object.__setattr__(client, "strategy_ref", strategy_ref)
    object.__setattr__(client, "account_ref", account_ref)
    return client


def _code(call):
    with pytest.raises(ServiceStrategyAccountBookClientError) as raised:
        call()
    return raised.value.code


def test_producer_sends_the_validated_book_as_one_typed_part():
    from bt_api_base.wire_codec_v1 import SUPPORTED_MINOR, TYPED_PART_SCHEMA, schema_digest

    reply = _reply()
    typed = reply["result"]["strategy_account_book"]
    assert set(typed) == {
        "schema_id", "schema_version", "major", "minor", "registry_digest", "kind", "data"
    }
    assert typed["schema_id"] == TYPED_PART_SCHEMA
    assert typed["kind"] == "strategy_account_book"
    assert typed["minor"] == SUPPORTED_MINOR and typed["registry_digest"] == schema_digest()
    book = _client()._typed_snapshot(reply)
    assert type(book) is StrategyAccountBookSnapshot
    assert book == parse_strategy_account_book_reply(
        reply, strategy_ref=STRATEGY, account_ref=ACCOUNT
    )


def test_producer_sends_a_finite_code_for_a_book_it_cannot_build():
    reply = _reply(_projected(complete=False))
    typed = reply["result"]["strategy_account_book"]
    assert set(typed) == {"schema_id", "schema_version", "error_code"}
    assert typed["error_code"] == "STRATEGY_BOOK_COVERAGE_INCOMPLETE"
    assert _code(lambda: _client()._typed_snapshot(reply)) == "STRATEGY_BOOK_COVERAGE_INCOMPLETE"


def test_client_refuses_the_untyped_shape():
    assert _code(lambda: _client()._typed_snapshot(_reply(typed=False))) == (
        "ACCOUNT_BOOK_TYPED_REPLY_REQUIRED"
    )


def test_client_keeps_remote_rejection_codes():
    from bt_api_base.account_book_protocol import AccountBookProjectionError

    with pytest.raises(AccountBookProjectionError) as raised:
        _client()._typed_snapshot({"ok": False, "code": "SCOPE_DENIED", "result": {}})
    assert raised.value.code == "SCOPE_DENIED"


@pytest.mark.parametrize(
    "field, value",
    [
        ("registry_digest", "0" * 64),
        ("minor", 4),
        ("schema_id", "other.schema"),
    ],
)
def test_client_refuses_another_contract(field, value):
    reply = _reply()
    reply["result"]["strategy_account_book"] = {
        **reply["result"]["strategy_account_book"],
        field: value,
    }
    assert _code(lambda: _client()._typed_snapshot(reply)) == "ACCOUNT_BOOK_TYPED_CONTRACT_MISMATCH"


def _with_part(reply, mutate):
    typed = copy.deepcopy(reply["result"]["strategy_account_book"])
    mutate(typed)
    reply["result"]["strategy_account_book"] = typed
    return reply


def test_client_refuses_other_minors_kinds_and_tampered_parts():
    newer = _with_part(_reply(), lambda part: part.update(minor=part["minor"] + 1))
    assert _code(lambda: _client()._typed_snapshot(newer)) == "ACCOUNT_BOOK_TYPED_CONTRACT_MISMATCH"
    other_kind = _with_part(_reply(), lambda part: part.update(kind="account"))
    assert _code(lambda: _client()._typed_snapshot(other_kind)) == "WIRE_KIND_INVALID"
    extra = _with_part(_reply(), lambda part: part["data"].update(extra=True))
    assert _code(lambda: _client()._typed_snapshot(extra)) == "WIRE_FIELD_SET_INVALID"
    floats = _with_part(_reply(), lambda part: part["data"].update(current_execution_cursor=1.0))
    assert _code(lambda: _client()._typed_snapshot(floats)) == "WIRE_VALUE_INVALID"
    malformed = _reply()
    malformed["result"]["strategy_account_book"] = {
        "schema_id": "bt_api.service.strategy_account_book.reply",
        "schema_version": 2,
        "message": "a v1 base64 message is no longer accepted",
    }
    assert _code(lambda: _client()._typed_snapshot(malformed)) == (
        "ACCOUNT_BOOK_TYPED_CONTRACT_MISMATCH"
    )


def test_client_keeps_its_own_owner_account_and_cut_checks():
    assert _code(lambda: _client(strategy_ref="other")._typed_snapshot(_reply())) == (
        "OWNER_BINDING_MISMATCH"
    )
    assert _code(lambda: _client(account_ref="other-account")._typed_snapshot(_reply())) == (
        "OWNER_BINDING_MISMATCH"
    )
    # The typed book must be at the same cut as the reply's scoped coverage.
    moved = _reply()
    moved["result"]["coverage"] = {**moved["result"]["coverage"], "current_execution_cursor": 5}
    assert _code(lambda: _client()._typed_snapshot(moved)) == "ACCOUNT_BOOK_TYPED_CUT_MISMATCH"
    incomplete = _reply()
    incomplete["result"]["coverage"] = {**incomplete["result"]["coverage"], "complete": False}
    assert _code(lambda: _client()._typed_snapshot(incomplete)) == (
        "STRATEGY_BOOK_COVERAGE_INCOMPLETE"
    )


def test_client_refuses_a_part_whose_book_disagrees_with_the_reply_cut():
    def move(part):
        part["data"]["current_execution_cursor"] += 1

    assert _code(lambda: _client()._typed_snapshot(_with_part(_reply(), move))) == (
        "ACCOUNT_BOOK_TYPED_CUT_MISMATCH"
    )
