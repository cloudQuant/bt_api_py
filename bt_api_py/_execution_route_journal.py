"""Transactional, non-authoritative snapshots of candidate Execution routes.

This table does not prove a Gateway intent was never sent.  The caller must
own the same SQLite transaction as its trusted intent/new-request fence before
using this data for routing; absent such a fence even a stored NO_MATCH is not
permission to send. No facade, provider, or service is opened by this module.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py._execution_route_match import _OrderFacts, _Outcome, _RuleSet
from bt_api_py.gateway_direct_client import GatewayOrderIntent, gateway_intent_to_mapping
from bt_api_py.gateway_order_contract import decode_gateway_order_request

_TABLE = 'execution_route_candidates_v1'
_SCHEMA = '''CREATE TABLE execution_route_candidates_v1 (
    account TEXT NOT NULL,
    strategy TEXT NOT NULL,
    request_key TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY (account, strategy, request_key)
) WITHOUT ROWID'''
_LIMIT = 131072


class RouteJournalError(RuntimeError):
    """The candidate is absent, conflicting, malformed or cannot be persisted."""


@dataclass(frozen=True)
class RouteCandidate:
    """A local calculation only; it is not an admission or a send permit."""
    outcome: _Outcome
    rule_index: int | None
    snapshot: str


def _canonical(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                         allow_nan=False)
    if len(encoded) > _LIMIT:
        raise RouteJournalError('SNAPSHOT_TOO_LARGE')
    return encoded


def _require_transaction(db: sqlite3.Connection) -> None:
    if type(db) is not sqlite3.Connection or not db.in_transaction:
        raise RouteJournalError('TRANSACTION_REQUIRED')


def _row(value: object) -> tuple | None:
    # sqlite3.Row keeps its own comparison semantics; normalize the supported
    # host row formats without treating unknown custom objects as evidence.
    if value is None:
        return None
    if type(value) is tuple or type(value) is sqlite3.Row:
        return tuple(value)
    raise RouteJournalError('UNSUPPORTED_ROW_FACTORY')


def install_route_table(db: sqlite3.Connection) -> None:
    """Install once in an explicit host-owned transaction; never replace a table."""
    _require_transaction(db)
    existing = _row(db.execute("SELECT sql FROM main.sqlite_master WHERE name = ?", (_TABLE,)).fetchone())
    if existing is None:
        db.execute(_SCHEMA.replace('CREATE TABLE ', 'CREATE TABLE main.', 1))
    elif existing != (_SCHEMA,):
        raise RouteJournalError('SCHEMA_MISMATCH')


def _ensure_table(db: sqlite3.Connection) -> None:
    _require_transaction(db)
    row = _row(db.execute("SELECT sql FROM main.sqlite_master WHERE name = ?", (_TABLE,)).fetchone())
    if row != (_SCHEMA,):
        raise RouteJournalError('SCHEMA_MISMATCH')


def _rules_snapshot(rules: _RuleSet) -> list[dict[str, object]]:
    if type(rules) is not _RuleSet or len(rules.rules) > 1024:
        raise RouteJournalError('INVALID_RULES')
    return [dict(instrument=None if r.instrument is None else r.instrument.to_dict(),
                 side=r.side, offset=r.offset, order_type=r.order_type,
                 minimum_quantity=(None if r.minimum_quantity is None
                                   else str(r.minimum_quantity)),
                 quantity_unit=r.quantity_unit) for r in rules.rules]


def _snapshot(intent: GatewayOrderIntent, configuration_id: str,
              instruments: tuple[MarketInstrumentKey, ...], rules: _RuleSet) -> RouteCandidate:
    try:
        mapping = gateway_intent_to_mapping(intent)
        if (type(configuration_id) is not str or not configuration_id
                or configuration_id != configuration_id.strip()
                or len(configuration_id.encode('utf-8')) > 256):
            raise ValueError('configuration id')
        if (type(instruments) is not tuple or len(instruments) > 4096
                or any(type(k) is not MarketInstrumentKey for k in instruments)):
            raise ValueError('instruments')
        request = decode_gateway_order_request(json.loads(intent.canonical_payload),
                                               intent.binding.account_scope)
        # No symbol-only identity guessing; the configured complete identity must
        # be unique for the venue/symbol represented by this Gateway request.
        choices = [k for k in instruments if k.native_symbol == request.symbol
                   and k.venue == request.exchange_id]
        if len(choices) != 1:
            raise ValueError('instrument ambiguous or missing')
        key = choices[0]
        rule_rows = _rules_snapshot(rules)
        decision = rules.match(_OrderFacts(key, request.side.value, request.offset,
                                          request.order_type, request.quantity,
                                          request.quantity_unit))
        value = dict(schema=1, intent=mapping, configuration_id=configuration_id,
                     instruments=[k.to_dict() for k in instruments], rules=rule_rows,
                     instrument=key.to_dict(), outcome=decision.outcome.value,
                     rule_index=decision.rule_index)
        return RouteCandidate(decision.outcome, decision.rule_index, _canonical(value))
    except RouteJournalError:
        raise
    except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
        raise RouteJournalError('INVALID_CANDIDATE') from exc


def record_route_candidate(db: sqlite3.Connection, intent: GatewayOrderIntent, *,
                           configuration_id: str, instruments: tuple[MarketInstrumentKey, ...],
                           rules: _RuleSet) -> RouteCandidate:
    """Remember a calculation under the host transaction; do not commit or send.

    The first snapshot wins; differing intent, run, policy, instrument or result
    is a conflict. The caller must resolve missing/legacy send provenance.
    """
    _ensure_table(db)
    candidate = _snapshot(intent, configuration_id, instruments, rules)
    identity = (intent.binding.account_scope, intent.binding.strategy_scope, intent.request_key)
    digest = hashlib.sha256(candidate.snapshot.encode('ascii')).hexdigest()
    db.execute(f'INSERT OR IGNORE INTO main.{_TABLE} VALUES (?, ?, ?, ?, ?)',
               (*identity, candidate.snapshot, digest))
    row = _row(db.execute(f'SELECT snapshot, digest FROM main.{_TABLE} WHERE account=? AND strategy=? '
                          'AND request_key=?', identity).fetchone())
    if row != (candidate.snapshot, digest):
        raise RouteJournalError('CANDIDATE_CONFLICT')
    return candidate


def load_route_candidate(db: sqlite3.Connection, intent: GatewayOrderIntent) -> RouteCandidate | None:
    """Return a validated local candidate, or None (unknown provenance).

    A successful read is not evidence of a committed transaction, a new request,
    old-writer fencing, authority, or that no previous order was sent.
    """
    _ensure_table(db)
    try:
        mapping = gateway_intent_to_mapping(intent)
        identity = (intent.binding.account_scope, intent.binding.strategy_scope, intent.request_key)
        row = _row(db.execute(f'SELECT snapshot, digest FROM main.{_TABLE} WHERE account=? AND strategy=? '
                              'AND request_key=?', identity).fetchone())
        if row is None:
            return None
        raw, digest = row
        if (type(raw) is not str or len(raw) > _LIMIT or type(digest) is not str
                or hashlib.sha256(raw.encode('ascii')).hexdigest() != digest):
            raise ValueError('corrupt')
        data = json.loads(raw)
        if (_canonical(data) != raw or type(data) is not dict
                or set(data) != {'schema', 'intent', 'configuration_id', 'instruments', 'rules',
                                 'instrument', 'outcome', 'rule_index'}
                or type(data['schema']) is not int or data['schema'] != 1
                or data['intent'] != mapping):
            raise ValueError('identity')
        # Reconstruct and recompute rather than accepting a stored claimed result.
        from bt_api_py._execution_route_match import _Rule
        rule_rows = data['rules']
        if type(rule_rows) is not list or len(rule_rows) > 1024:
            raise ValueError('rules')
        rules = []
        for r in rule_rows:
            if type(r) is not dict or set(r) != {'instrument', 'side', 'offset', 'order_type',
                                                   'minimum_quantity', 'quantity_unit'}:
                raise ValueError('rule')
            instrument = None if r['instrument'] is None else MarketInstrumentKey.from_dict(r['instrument'])
            minimum = None if r['minimum_quantity'] is None else Decimal(r['minimum_quantity'])
            rules.append(_Rule(instrument, r['side'], r['offset'], r['order_type'], minimum,
                               r['quantity_unit']))
        if type(data['instruments']) is not list or len(data['instruments']) > 4096:
            raise ValueError('instruments')
        candidate = _snapshot(intent, data['configuration_id'],
                              tuple(MarketInstrumentKey.from_dict(x) for x in data['instruments']),
                              _RuleSet(rules))
        if candidate.snapshot != raw:
            raise ValueError('mismatch')
        return candidate
    except RouteJournalError:
        raise
    except Exception as exc:
        raise RouteJournalError('CANDIDATE_UNAVAILABLE') from exc
