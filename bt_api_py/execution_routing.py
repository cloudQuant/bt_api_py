"""SDK-side selection and routing of limit parent orders into Execution.

This module is the strategy process's local routing layer. It parses the
explicit ``execution_routing`` selection (account/strategy/domain + component
and an ordered rule list), decides whether one Gateway order intent is routed,
and, for a matched request, durably records a stable request identity and
content digest *before* calling exactly one injected Execution ``parent.submit``
port. It never sends to a provider, never calls a Gateway direct command, and
never retries an ambiguous submit under a new name.

An unmatched or unselected request is reported as ``DIRECT_PATH`` so the
existing SDK path continues unchanged. A matched request whose Execution port
is unavailable is a finite ``EXECUTION_UNAVAILABLE``; it is never silently sent
down the old path. A crash or lost reply leaves the original durable identity
and an ``EXECUTION_UNKNOWN`` outcome; replay returns that outcome and does not
resend.

The module imports neither ``bt_api_execution`` nor a provider package. The
port is supplied by trusted composition and may be a fake. Nothing here proves
Gateway acceptance, Risk approval, venue state, or account authorization: a
local PREPARED row is a local fact only.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py._contracts.models import OrderType
from bt_api_py._execution_route_journal import (
    RouteCandidate,
    RouteJournalError,
    install_route_table,
    load_route_candidate,
    record_route_candidate,
)
from bt_api_py._execution_route_match import _OrderFacts, _Outcome, _Rule, _RuleSet
from bt_api_py.gateway_direct_client import GatewayOrderIntent, _check_intent
from bt_api_py.gateway_order_contract import decode_gateway_order_request

_SIDES = frozenset({"buy", "sell"})
_OFFSETS = frozenset({"open", "close", "close_today", "close_yesterday"})
_CLOSING_OFFSETS = frozenset({"close", "close_today", "close_yesterday"})
_UNITS = frozenset({"base", "contracts", "lots", "native"})
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_VERSIONED_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*-v[1-9][0-9]*$")
_DECIMAL_RE = re.compile(r"^[0-9]{1,42}(?:\.[0-9]{1,42})?$")
_DAY_RE = re.compile(r"^[0-9]{8}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

_MAX_CONFIG_BYTES = 1_048_576
_MAX_PARAMS_BYTES = 65_536
_MAX_PAYLOAD_BYTES = 1_048_576
_MAX_RULES = 1024
_MAX_INSTRUMENTS = 4096
_MAX_CHILDREN = 4096
_MAX_NS = 1 << 63
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

_TABLE = "execution_parent_requests_v1"
_SCHEMA = """CREATE TABLE execution_parent_requests_v1 (
    account TEXT NOT NULL,
    strategy TEXT NOT NULL,
    request_key TEXT NOT NULL,
    intent_digest TEXT NOT NULL,
    request_id TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    configuration_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    result TEXT,
    cancel_operation_id TEXT,
    cancel_result TEXT,
    created_at_ns INTEGER NOT NULL,
    PRIMARY KEY (account, strategy, request_key)
) WITHOUT ROWID"""
_RESULT_FIELDS = frozenset({"status", "code", "parent_ref", "execution_state"})
_PAYLOAD_FIELDS = frozenset({"request_id", "order_type", "spec", "strategy", "delegation"})
_TOP_FIELDS = frozenset(
    {
        "schema_version",
        "component",
        "account_ref",
        "strategy_ref",
        "execution_domain_ref",
        "grant_revision",
        "delegation",
        "instruments",
        "rules",
    }
)
_RULE_FIELDS = frozenset(
    {
        "id",
        "match",
        "scheme",
        "algorithm",
        "deadline",
        "time_in_force",
        "trading_day",
        "allowed_trading_days",
        "target_position_ref",
    }
)
_MATCH_FIELDS = frozenset(
    {"instrument", "side", "offset", "order_types", "minimum_quantity", "quantity_unit"}
)


class ExecutionRoutingError(ValueError):
    """A finite routing/config error that never includes input values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ExecutionRoutingError(code)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _plain(value: object) -> object:
    """Return a detached JSON tree or fail with a finite code."""
    try:
        encoded = _canonical(value)
        if len(encoded.encode("utf-8")) > _MAX_CONFIG_BYTES:
            _fail("INVALID_EXECUTION_CONFIG")
        return json.loads(encoded)
    except ExecutionRoutingError:
        raise
    except Exception:
        _fail("INVALID_EXECUTION_CONFIG")


def _text(value: object, *, maximum: int = 256) -> str | None:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        return None
    try:
        if len(value.encode("utf-8", "strict")) > maximum:
            return None
    except UnicodeError:
        return None
    return value


def _ref(value: object) -> str | None:
    text = _text(value, maximum=128)
    if text is None or _REF_RE.fullmatch(text) is None:
        return None
    return text


def _versioned_ref(value: object) -> str | None:
    text = _text(value, maximum=128)
    if text is None or _VERSIONED_REF_RE.fullmatch(text) is None:
        return None
    return text


def _version(value: object) -> str | None:
    if type(value) is int and not isinstance(value, bool) and value > 0:
        return str(value)
    return _ref(value)


def _decimal_text(value: object) -> str | None:
    if (type(value) is str and _DECIMAL_RE.fullmatch(value) is not None) or (
        type(value) is int and not isinstance(value, bool) and 0 < value < 10**42
    ):
        number = Decimal(value)
    else:
        return None
    if not number.is_finite() or number <= 0:
        return None
    return format(number, "f")


def _date_text(value: object) -> str | None:
    if type(value) is not str or _DAY_RE.fullmatch(value) is None:
        return None
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return None
    return value


def _deadline(value: object) -> tuple[str, int] | None:
    if type(value) is not dict or set(value) != {"kind", "at"}:
        return None
    if value["kind"] != "absolute" or type(value["at"]) is not str or not value["at"]:
        return None
    try:
        parsed = datetime.fromisoformat(value["at"].replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    utc = parsed.astimezone(UTC)
    delta = utc - _EPOCH
    expires_at_ns = (
        delta.days * 86_400 + delta.seconds
    ) * 1_000_000_000 + delta.microseconds * 1_000
    if not 0 < expires_at_ns < _MAX_NS:
        return None
    normalized = utc.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return normalized, expires_at_ns


@dataclass(frozen=True)
class RulePolicy:
    """One parsed rule: its exact match conjunction plus frozen policy facts."""

    rule_id: str
    rule: _Rule
    scheme_name: str
    scheme_version: str
    algorithm_class: str
    algorithm_version: str
    algorithm_params: dict
    deadline_at: str
    deadline_ns: int
    time_in_force: str
    trading_day: str
    allowed_trading_days: tuple[str, ...]
    target_position_ref: str | None

    def to_plain(self) -> dict:
        match: dict = {}
        if self.rule.instrument is not None:
            match["instrument"] = self.rule.instrument.to_dict()
        if self.rule.side is not None:
            match["side"] = self.rule.side
        if self.rule.offset is not None:
            match["offset"] = self.rule.offset
        if self.rule.order_type is not None:
            match["order_types"] = [self.rule.order_type]
        if self.rule.minimum_quantity is not None:
            match["minimum_quantity"] = format(self.rule.minimum_quantity, "f")
            match["quantity_unit"] = self.rule.quantity_unit
        value = {
            "id": self.rule_id,
            "match": match,
            "scheme": {"name": self.scheme_name, "version": self.scheme_version},
            "algorithm": {
                "class": self.algorithm_class,
                "version": self.algorithm_version,
                "params": self.algorithm_params,
            },
            "deadline": {"kind": "absolute", "at": self.deadline_at},
            "time_in_force": self.time_in_force,
            "trading_day": self.trading_day,
            "allowed_trading_days": list(self.allowed_trading_days),
        }
        if self.target_position_ref is not None:
            value["target_position_ref"] = self.target_position_ref
        return value


@dataclass(frozen=True)
class ExecutionRoutingConfig:
    """One validated, immutable Execution selection snapshot."""

    account_ref: str
    strategy_ref: str
    execution_domain_ref: str
    component: str
    grant_revision: str
    delegation_id: str
    delegation_principal_ref: str
    instruments: tuple[MarketInstrumentKey, ...]
    policies: tuple[RulePolicy, ...]
    configuration_id: str

    @property
    def rules(self) -> tuple[_Rule, ...]:
        return tuple(policy.rule for policy in self.policies)

    @property
    def rule_set(self) -> _RuleSet:
        return _RuleSet(list(self.rules))

    def to_plain(self) -> dict:
        return {
            "schema_version": 1,
            "component": self.component,
            "account_ref": self.account_ref,
            "strategy_ref": self.strategy_ref,
            "execution_domain_ref": self.execution_domain_ref,
            "grant_revision": self.grant_revision,
            "delegation": {
                "delegation_id": self.delegation_id,
                "principal_ref": self.delegation_principal_ref,
            },
            "instruments": [instrument.to_dict() for instrument in self.instruments],
            "rules": [policy.to_plain() for policy in self.policies],
        }


def _instrument(value: object) -> MarketInstrumentKey | None:
    if type(value) is not dict:
        return None
    try:
        key = MarketInstrumentKey.from_dict(value)
        if key.to_dict() != value:
            return None
    except Exception:
        return None
    return key


def _params(value: object) -> dict | None:
    if type(value) is not dict:
        return None
    try:
        encoded = _canonical(value)
        if len(encoded.encode("utf-8")) > _MAX_PARAMS_BYTES:
            return None
        return json.loads(encoded)
    except Exception:
        return None


def _policy(value: object, instruments: tuple[MarketInstrumentKey, ...]) -> RulePolicy | None:
    if type(value) is not dict or not set(value) <= _RULE_FIELDS:
        return None
    if "id" not in value or "match" not in value or "scheme" not in value:
        return None
    if "algorithm" not in value or "deadline" not in value:
        return None
    if "time_in_force" not in value or "trading_day" not in value:
        return None
    match = value["match"]
    if type(match) is not dict or set(match) - _MATCH_FIELDS:
        return None
    rule_id = _ref(value["id"])
    if rule_id is None:
        return None
    instrument = None
    if "instrument" in match:
        instrument = _instrument(match["instrument"])
        if instrument is None:
            return None
        if instrument not in instruments:
            _fail("EXECUTION_ROUTING_INSTRUMENT_UNKNOWN")
    side = match.get("side")
    if "side" in match and (type(side) is not str or side not in _SIDES):
        return None
    offset = match.get("offset")
    if "offset" in match and (type(offset) is not str or offset not in _OFFSETS):
        return None
    order_type = None
    if "order_types" in match:
        order_types = match["order_types"]
        if type(order_types) is not list or not order_types:
            return None
        if any(type(item) is not str or item != "limit" for item in order_types):
            _fail("EXECUTION_ORDER_TYPE_UNSUPPORTED")
        if len(set(order_types)) != len(order_types):
            return None
        order_type = "limit"
    minimum = None
    quantity_unit = None
    if "minimum_quantity" in match:
        if "quantity_unit" not in match:
            return None
        minimum_text = _decimal_text(match["minimum_quantity"])
        quantity_unit = match["quantity_unit"]
        if minimum_text is None or type(quantity_unit) is not str or quantity_unit not in _UNITS:
            return None
        minimum = Decimal(minimum_text)
    elif "quantity_unit" in match:
        return None
    scheme = value["scheme"]
    if type(scheme) is not dict or set(scheme) != {"name", "version"}:
        return None
    scheme_name = _ref(scheme["name"])
    scheme_version = _version(scheme["version"])
    algorithm = value["algorithm"]
    if type(algorithm) is not dict or set(algorithm) != {"class", "version", "params"}:
        return None
    algorithm_class = _ref(algorithm["class"])
    algorithm_version = _version(algorithm["version"])
    algorithm_params = _params(algorithm["params"])
    deadline = _deadline(value["deadline"])
    time_in_force = value["time_in_force"]
    if type(time_in_force) is not str or time_in_force not in {"DAY", "GTD"}:
        return None
    trading_day = _date_text(value["trading_day"])
    raw_days = value.get("allowed_trading_days")
    if raw_days is None:
        if time_in_force != "DAY":
            return None
        allowed_days = (trading_day,)
    else:
        if (
            type(raw_days) is not list
            or not raw_days
            or len(raw_days) > 366
            or any(_date_text(day) is None for day in raw_days)
            or raw_days != sorted(set(raw_days))
            or raw_days[0] != trading_day
        ):
            return None
        if time_in_force == "DAY" and raw_days != [trading_day]:
            return None
        allowed_days = tuple(raw_days)
    target_position_ref = value.get("target_position_ref")
    if offset in _CLOSING_OFFSETS:
        target_position_ref = _ref(target_position_ref)
        if target_position_ref is None:
            return None
    elif target_position_ref is not None:
        return None
    if (
        rule_id is None
        or scheme_name is None
        or scheme_version is None
        or algorithm_class is None
        or algorithm_version is None
        or algorithm_params is None
        or deadline is None
        or trading_day is None
    ):
        return None
    try:
        rule = _Rule(
            instrument=instrument,
            side=side,
            offset=offset,
            order_type=order_type,
            minimum_quantity=minimum,
            quantity_unit=quantity_unit,
        )
    except ValueError:
        return None
    return RulePolicy(
        rule_id=rule_id,
        rule=rule,
        scheme_name=scheme_name,
        scheme_version=scheme_version,
        algorithm_class=algorithm_class,
        algorithm_version=algorithm_version,
        algorithm_params=algorithm_params,
        deadline_at=deadline[0],
        deadline_ns=deadline[1],
        time_in_force=time_in_force,
        trading_day=trading_day,
        allowed_trading_days=allowed_days,
        target_position_ref=target_position_ref,
    )


def parse_execution_routing(raw: object) -> ExecutionRoutingConfig:
    """Validate one ``execution_routing`` option without I/O.

    Unknown keys, duplicate rule IDs, unsupported static order types, naive or
    relative deadlines, non-decimal quantities and contradictory close/open
    targets are refused here, before any order can reach a service.
    """
    try:
        value = _plain(raw)
        if type(value) is not dict or set(value) != _TOP_FIELDS:
            _fail("INVALID_EXECUTION_CONFIG")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            _fail("INVALID_EXECUTION_CONFIG")
        component = _ref(value["component"])
        account_ref = _ref(value["account_ref"])
        strategy_ref = _ref(value["strategy_ref"])
        execution_domain_ref = _ref(value["execution_domain_ref"])
        grant_revision = _versioned_ref(value["grant_revision"])
        delegation = value["delegation"]
        if type(delegation) is not dict or set(delegation) != {"delegation_id", "principal_ref"}:
            _fail("INVALID_EXECUTION_CONFIG")
        delegation_id = _ref(delegation["delegation_id"])
        delegation_principal_ref = _ref(delegation["principal_ref"])
        if (
            component is None
            or account_ref is None
            or strategy_ref is None
            or execution_domain_ref is None
            or grant_revision is None
            or delegation_id is None
            or delegation_principal_ref is None
        ):
            _fail("INVALID_EXECUTION_CONFIG")
        raw_instruments = value["instruments"]
        if (
            type(raw_instruments) is not list
            or not raw_instruments
            or len(raw_instruments) > _MAX_INSTRUMENTS
        ):
            _fail("INVALID_EXECUTION_CONFIG")
        instruments = []
        for item in raw_instruments:
            key = _instrument(item)
            if key is None or key in instruments:
                _fail("INVALID_EXECUTION_CONFIG")
            instruments.append(key)
        instruments = tuple(instruments)
        raw_rules = value["rules"]
        if type(raw_rules) is not list or not raw_rules or len(raw_rules) > _MAX_RULES:
            _fail("INVALID_EXECUTION_CONFIG")
        policies = []
        seen_ids = set()
        for item in raw_rules:
            policy = _policy(item, instruments)
            if policy is None:
                _fail("INVALID_EXECUTION_CONFIG")
            if policy.rule_id in seen_ids:
                _fail("EXECUTION_ROUTING_RULE_DUPLICATE")
            seen_ids.add(policy.rule_id)
            policies.append(policy)
        plain = {
            "schema_version": 1,
            "component": component,
            "account_ref": account_ref,
            "strategy_ref": strategy_ref,
            "execution_domain_ref": execution_domain_ref,
            "grant_revision": grant_revision,
            "delegation": {
                "delegation_id": delegation_id,
                "principal_ref": delegation_principal_ref,
            },
            "instruments": [key.to_dict() for key in instruments],
            "rules": [policy.to_plain() for policy in policies],
        }
        return ExecutionRoutingConfig(
            account_ref=account_ref,
            strategy_ref=strategy_ref,
            execution_domain_ref=execution_domain_ref,
            component=component,
            grant_revision=grant_revision,
            delegation_id=delegation_id,
            delegation_principal_ref=delegation_principal_ref,
            instruments=instruments,
            policies=tuple(policies),
            configuration_id=_digest(plain),
        )
    except ExecutionRoutingError:
        raise
    except Exception:
        _fail("INVALID_EXECUTION_CONFIG")


class ExecutionRoutingOutcome(StrEnum):
    """Finite SDK routing outcomes; DIRECT_PATH keeps the existing path."""

    DIRECT_PATH = "DIRECT_PATH"
    EXECUTION_ACCEPTED = "EXECUTION_ACCEPTED"
    EXECUTION_LOCAL_RECEIPT = "EXECUTION_LOCAL_RECEIPT"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"
    EXECUTION_CANCEL_ACCEPTED = "EXECUTION_CANCEL_ACCEPTED"
    EXECUTION_UNAVAILABLE = "EXECUTION_UNAVAILABLE"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"


@dataclass(frozen=True)
class ExecutionRoutingResult:
    outcome: ExecutionRoutingOutcome
    source: str = "SDK_LOCAL"
    request_id: str | None = None
    request_sha256: str | None = None
    parent_ref: str | None = None
    code: str | None = None
    execution_state: str | None = None


@dataclass(frozen=True)
class ExecutionRoutingStatus:
    routed: bool
    outcome: ExecutionRoutingOutcome
    source: str = "SDK_LOCAL"
    request_id: str | None = None
    request_sha256: str | None = None
    parent_ref: str | None = None
    configuration_id: str | None = None
    execution_state: str | None = None
    children: tuple[dict, ...] = ()
    unknown_children: tuple[str, ...] = ()
    code: str | None = None
    cancel_outcome: str | None = None
    cancel_code: str | None = None


@dataclass(frozen=True)
class _ParentRecord:
    account: str
    strategy: str
    request_key: str
    intent_digest: str
    request_id: str
    request_sha256: str
    configuration_id: str
    payload: str
    result: str | None
    cancel_operation_id: str | None
    cancel_result: str | None


def _require_connection(db: object) -> sqlite3.Connection:
    if type(db) is not sqlite3.Connection:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")
    return db


def _require_transaction(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")


def _row(value: object) -> tuple | None:
    if value is None:
        return None
    if type(value) is tuple or type(value) is sqlite3.Row:
        return tuple(value)
    _fail("EXECUTION_LOCAL_RECORD_FAILED")


def _install_parent_table(db: sqlite3.Connection) -> None:
    _require_transaction(db)
    existing = _row(
        db.execute("SELECT sql FROM main.sqlite_master WHERE name = ?", (_TABLE,)).fetchone()
    )
    if existing is None:
        db.execute(_SCHEMA.replace("CREATE TABLE ", "CREATE TABLE main.", 1))
    elif existing != (_SCHEMA,):
        _fail("EXECUTION_LOCAL_RECORD_FAILED")


def install_execution_routing_tables(db: object) -> None:
    """Install both durable tables in one explicit host-owned transaction."""
    connection = _require_connection(db)
    _require_transaction(connection)
    install_route_table(connection)
    _install_parent_table(connection)


def _ensure_parent_table(db: sqlite3.Connection) -> None:
    _require_transaction(db)
    row = _row(
        db.execute("SELECT sql FROM main.sqlite_master WHERE name = ?", (_TABLE,)).fetchone()
    )
    if row != (_SCHEMA,):
        _fail("EXECUTION_LOCAL_RECORD_FAILED")


def _begin(db: sqlite3.Connection) -> None:
    try:
        db.execute("BEGIN IMMEDIATE")
    except sqlite3.Error:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")


def _begin_read(db: sqlite3.Connection) -> None:
    try:
        db.execute("BEGIN")
    except sqlite3.Error:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")


def _commit(db: sqlite3.Connection) -> None:
    try:
        db.commit()
    except sqlite3.Error:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")


def _rollback(db: sqlite3.Connection) -> None:
    try:
        if db.in_transaction:
            db.rollback()
    except sqlite3.Error:
        pass


def _validate_payload(payload: object, request_id: str, digest: str) -> None:
    if type(payload) is not str or len(payload.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")
    try:
        data = json.loads(payload)
    except Exception:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")
    if (
        type(data) is not dict
        or set(data) != _PAYLOAD_FIELDS
        or _canonical(data) != payload
        or data["request_id"] != request_id
        or data["order_type"] != "limit"
        or type(data["spec"]) is not dict
        or type(data["strategy"]) is not dict
        or type(data["delegation"]) is not dict
    ):
        _fail("EXECUTION_LOCAL_RECORD_FAILED")
    content = {name: data[name] for name in _PAYLOAD_FIELDS if name != "request_id"}
    if _digest(content) != digest:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")


def _validate_result(raw: object) -> dict:
    if type(raw) is not str or len(raw.encode("utf-8")) > 65_536:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")
    try:
        data = json.loads(raw)
    except Exception:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")
    if (
        type(data) is not dict
        or set(data) != _RESULT_FIELDS
        or _canonical(data) != raw
        or type(data["status"]) is not str
        or data["status"] not in {item.value for item in ExecutionRoutingOutcome}
        or (data["code"] is not None and type(data["code"]) is not str)
        or (data["parent_ref"] is not None and type(data["parent_ref"]) is not str)
        or (data["execution_state"] is not None and type(data["execution_state"]) is not str)
    ):
        _fail("EXECUTION_LOCAL_RECORD_FAILED")
    return data


def _record(value: tuple | None) -> _ParentRecord | None:
    if value is None:
        return None
    if len(value) != 11:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")
    (
        account,
        strategy,
        request_key,
        intent_digest,
        request_id,
        request_sha256,
        configuration_id,
        payload,
        result,
        cancel_operation_id,
        cancel_result,
    ) = value
    if (
        any(_text(item) is None for item in (account, strategy, request_key))
        or type(intent_digest) is not str
        or _HEX_RE.fullmatch(intent_digest) is None
        or any(_text(item) is None for item in (request_id, request_sha256, configuration_id))
        or _HEX_RE.fullmatch(request_sha256) is None
        or (result is not None and type(result) is not str)
        or (cancel_operation_id is not None and _text(cancel_operation_id) is None)
        or (cancel_result is not None and type(cancel_result) is not str)
    ):
        _fail("EXECUTION_LOCAL_RECORD_FAILED")
    _validate_payload(payload, request_id, request_sha256)
    if result is not None:
        _validate_result(result)
    if cancel_result is not None:
        _validate_result(cancel_result)
    return _ParentRecord(
        account,
        strategy,
        request_key,
        intent_digest,
        request_id,
        request_sha256,
        configuration_id,
        payload,
        result,
        cancel_operation_id,
        cancel_result,
    )


def _load_record(db: sqlite3.Connection, identity: tuple[str, str, str]) -> _ParentRecord | None:
    _require_transaction(db)
    _ensure_parent_table(db)
    return _record(
        _row(
            db.execute(
                "SELECT account, strategy, request_key, intent_digest, request_id, "
                "request_sha256, configuration_id, payload, result, cancel_operation_id, "
                "cancel_result FROM main.execution_parent_requests_v1 "
                "WHERE account=? AND strategy=? AND request_key=?",
                identity,
            ).fetchone()
        )
    )


def _insert_prepared(
    db: sqlite3.Connection,
    identity: tuple[str, str, str],
    intent_digest: str,
    request_id: str,
    request_sha256: str,
    configuration_id: str,
    payload: str,
    now_ns: int,
) -> tuple[_ParentRecord, bool]:
    _require_transaction(db)
    _ensure_parent_table(db)
    cursor = db.execute(
        "INSERT OR IGNORE INTO main.execution_parent_requests_v1 "
        "(account, strategy, request_key, intent_digest, request_id, request_sha256, "
        "configuration_id, payload, created_at_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*identity, intent_digest, request_id, request_sha256, configuration_id, payload, now_ns),
    )
    created = cursor.rowcount == 1
    stored = _load_record(db, identity)
    if stored is None:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")
    if (
        stored.intent_digest != intent_digest
        or stored.request_id != request_id
        or stored.request_sha256 != request_sha256
        or stored.payload != payload
    ):
        _fail("EXECUTION_REQUEST_CONFLICT")
    return stored, created


def _store_result(db: sqlite3.Connection, identity: tuple[str, str, str], value: str) -> str:
    _require_transaction(db)
    _ensure_parent_table(db)
    db.execute(
        "UPDATE main.execution_parent_requests_v1 SET result=? "
        "WHERE account=? AND strategy=? AND request_key=? AND result IS NULL",
        (value, *identity),
    )
    stored = _load_record(db, identity)
    if stored is None or stored.result is None:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")
    return stored.result


def _set_cancel_operation(
    db: sqlite3.Connection, identity: tuple[str, str, str], operation_id: str
) -> None:
    _require_transaction(db)
    _ensure_parent_table(db)
    db.execute(
        "UPDATE main.execution_parent_requests_v1 SET cancel_operation_id=? "
        "WHERE account=? AND strategy=? AND request_key=? AND cancel_operation_id IS NULL",
        (operation_id, *identity),
    )
    stored = _load_record(db, identity)
    if stored is None or stored.cancel_operation_id != operation_id:
        _fail("EXECUTION_REQUEST_CONFLICT")


def _store_cancel_result(db: sqlite3.Connection, identity: tuple[str, str, str], value: str) -> str:
    _require_transaction(db)
    _ensure_parent_table(db)
    db.execute(
        "UPDATE main.execution_parent_requests_v1 SET cancel_result=? "
        "WHERE account=? AND strategy=? AND request_key=? AND cancel_result IS NULL",
        (value, *identity),
    )
    stored = _load_record(db, identity)
    if stored is None or stored.cancel_result is None:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")
    return stored.cancel_result


def _positive_decimal(value: object) -> bool:
    return type(value) is Decimal and value.is_finite() and value > 0


def _order_type_value(value: object) -> str | None:
    if type(value) is OrderType:
        return value.value
    if type(value) is str and value in {"limit", "market"}:
        return value
    return None


def _count(value: object) -> int | None:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        return None
    return value


def _candidate_configuration(candidate: RouteCandidate) -> str:
    try:
        data = json.loads(candidate.snapshot)
        value = data["configuration_id"]
        if _text(value) is None:
            raise ValueError
        return value
    except Exception:
        _fail("EXECUTION_LOCAL_RECORD_FAILED")


def _reply_fields(reply: object) -> tuple[str | None, str | None, str | None, str | None]:
    try:
        if isinstance(reply, Mapping):
            status = reply.get("status")
            outcome = reply.get("outcome")
            if status is not None and outcome is not None and status != outcome:
                return None, None, None, None
            status = status if status is not None else outcome
            code = reply.get("code")
            parent_ref = reply.get("parent_ref")
            state = reply.get("state")
        else:
            status = getattr(reply, "outcome", None)
            if status is None:
                status = getattr(reply, "status", None)
            code = getattr(reply, "code", None)
            parent_ref = getattr(reply, "parent_ref", None)
            state = getattr(reply, "state", None)
    except Exception:
        return None, None, None, None
    if status is not None and type(status) is not str:
        status = None
    if code is not None and type(code) is not str:
        code = None
    if parent_ref is not None and type(parent_ref) is not str:
        parent_ref = None
    if state is not None and type(state) is not str:
        state = None
    return status, code, parent_ref, state


class ExecutionOrderRouter:
    """Durable SDK routing for one strategy's Gateway order intents.

    The connection must be an autocommit ``sqlite3.Connection`` with both
    tables installed; this object owns each ``BEGIN``/``COMMIT``. The caller
    constructs it only when Execution is explicitly selected.
    """

    def __init__(
        self,
        *,
        db: object,
        config: object,
        execution_port: object,
        risk_selected: object,
        clock=time.time_ns,
    ) -> None:
        connection = _require_connection(db)
        if connection.isolation_level is not None:
            _fail("EXECUTION_LOCAL_RECORD_FAILED")
        if type(config) is not ExecutionRoutingConfig:
            _fail("INVALID_EXECUTION_CONFIG")
        if type(risk_selected) is not bool:
            _fail("INVALID_EXECUTION_CONFIG")
        if not callable(clock):
            _fail("INVALID_EXECUTION_CONFIG")
        self._db = connection
        self._config = config
        self._port = execution_port
        self._risk_selected = risk_selected
        self._clock = clock

    # -- identity and matching ------------------------------------------------

    @staticmethod
    def _validate_intent(intent: object) -> GatewayOrderIntent:
        try:
            return _check_intent(intent)
        except Exception:
            _fail("INVALID_ORDER_INTENT")

    @staticmethod
    def _decode_request(intent: GatewayOrderIntent):
        try:
            payload = json.loads(intent.canonical_payload)
            return decode_gateway_order_request(payload, intent.binding.account_scope)
        except Exception:
            _fail("INVALID_ORDER_INTENT")

    @staticmethod
    def _identity(intent: GatewayOrderIntent) -> tuple[str, str, str]:
        return (
            intent.binding.account_scope,
            intent.binding.strategy_scope,
            intent.request_key,
        )

    @staticmethod
    def _seed(intent: GatewayOrderIntent) -> str:
        return _digest(
            {
                "account": intent.binding.account_scope,
                "strategy": intent.binding.strategy_scope,
                "run": intent.route.scope.run_id,
                "request_key": intent.request_key,
            }
        )

    def _scope_matches(self, intent: GatewayOrderIntent) -> bool:
        scope = intent.route.scope
        return (
            intent.binding.account_scope == self._config.account_ref
            and intent.binding.strategy_scope == self._config.strategy_ref
            and scope.account_ref == self._config.account_ref
            and scope.strategy_ref == self._config.strategy_ref
            and scope.execution_domain_ref == self._config.execution_domain_ref
            and _text(scope.run_id) is not None
            and _text(intent.binding.principal) is not None
            and _text(intent.binding.strategy_scope) is not None
        )

    def _resolve_instrument(self, request) -> MarketInstrumentKey | None:
        choices = [
            key
            for key in self._config.instruments
            if key.native_symbol == request.symbol and key.venue == request.exchange_id
        ]
        return choices[0] if len(choices) == 1 else None

    def _policy_rejection(self, request, policy: RulePolicy) -> str | None:
        if _order_type_value(request.order_type) != "limit":
            return "EXECUTION_ORDER_TYPE_UNSUPPORTED"
        if not _positive_decimal(request.price):
            return "EXECUTION_LIMIT_PRICE_REQUIRED"
        if not _positive_decimal(request.quantity):
            return "EXECUTION_QUANTITY_INVALID"
        if request.quantity_unit not in _UNITS:
            return "EXECUTION_QUANTITY_UNIT_UNKNOWN"
        side = getattr(request.side, "value", None)
        if side not in _SIDES:
            return "EXECUTION_ORDER_INVALID"
        if request.offset not in _OFFSETS:
            return "EXECUTION_ORDER_INVALID"
        if request.offset == "open":
            if policy.target_position_ref is not None:
                return "EXECUTION_SCOPE_DENIED"
        elif policy.target_position_ref is None:
            return "EXECUTION_SCOPE_DENIED"
        return None

    def _build(
        self,
        intent: GatewayOrderIntent,
        request,
        instrument: MarketInstrumentKey,
        policy: RulePolicy,
    ) -> tuple[str, str, str]:
        seed = self._seed(intent)
        request_id = "sdk-request-v1:" + seed[:40]
        parent_ref = "sdk-parent-v1:" + seed[:40]
        registration_operation_id = "sdk-registration-v1:" + seed[:40]
        risk_operation_id = "sdk-risk-v1:" + seed[:40]
        quantity = format(request.quantity, "f")
        limit_price = format(request.price, "f")
        plan_digest = _digest(
            {
                "request_id": request_id,
                "instrument": instrument.to_dict(),
                "quantity": quantity,
                "quantity_unit": request.quantity_unit,
                "side": request.side.value,
                "offset": request.offset,
                "limit_price": limit_price,
                "scheme": {"name": policy.scheme_name, "version": policy.scheme_version},
                "algorithm": {
                    "class": policy.algorithm_class,
                    "version": policy.algorithm_version,
                    "params": policy.algorithm_params,
                },
                "deadline": policy.deadline_at,
                "trading_day": policy.trading_day,
                "allowed_trading_days": list(policy.allowed_trading_days),
                "target_position_ref": policy.target_position_ref,
                "grant_revision": self._config.grant_revision,
                "risk_selected": self._risk_selected,
            }
        )
        spec = {
            "parent_ref": parent_ref,
            "registration_operation_id": registration_operation_id,
            "risk_operation_id": risk_operation_id,
            "account_scope": self._config.account_ref,
            "strategy_scope": self._config.strategy_ref,
            "execution_domain_ref": self._config.execution_domain_ref,
            "instrument": instrument.to_dict(),
            "quantity": quantity,
            "quantity_unit": request.quantity_unit,
            "side": request.side.value,
            "offset": request.offset,
            "limit_price": limit_price,
            "creation_trading_day": policy.trading_day,
            "allowed_trading_days": list(policy.allowed_trading_days),
            "expires_at_ns": policy.deadline_ns,
            "grant_revision": self._config.grant_revision,
            "plan_digest": plan_digest,
            "plan_version": policy.algorithm_version,
            "target_position_ref": policy.target_position_ref,
            "risk_selected": self._risk_selected,
            "time_in_force": policy.time_in_force,
        }
        strategy = {
            "account_ref": self._config.account_ref,
            "strategy_ref": self._config.strategy_ref,
            "execution_domain_ref": self._config.execution_domain_ref,
            "run_id": intent.route.scope.run_id,
            "principal_ref": intent.binding.principal,
            "actor_ref": intent.binding.strategy_scope,
        }
        delegation = {
            "delegation_id": self._config.delegation_id,
            "principal_ref": self._config.delegation_principal_ref,
        }
        content = {
            "order_type": "limit",
            "spec": spec,
            "strategy": strategy,
            "delegation": delegation,
        }
        digest = _digest(content)
        payload = dict(content)
        payload["request_id"] = request_id
        return request_id, digest, _canonical(payload)

    # -- result projection ----------------------------------------------------

    def _result(
        self,
        outcome: ExecutionRoutingOutcome,
        *,
        code: str | None = None,
        request_id: str | None = None,
        request_sha256: str | None = None,
        parent_ref: str | None = None,
        execution_state: str | None = None,
    ) -> ExecutionRoutingResult:
        return ExecutionRoutingResult(
            outcome=outcome,
            request_id=request_id,
            request_sha256=request_sha256,
            parent_ref=parent_ref,
            code=code,
            execution_state=execution_state,
        )

    def _submit_result(
        self, reply: object, *, request_id: str, request_sha256: str
    ) -> ExecutionRoutingResult:
        status, code, parent_ref, state = _reply_fields(reply)
        if status == "ACCEPTED":
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_ACCEPTED,
                request_id=request_id,
                request_sha256=request_sha256,
                parent_ref=parent_ref,
            )
        if status == "LOCAL_RECEIVED":
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_LOCAL_RECEIPT,
                request_id=request_id,
                request_sha256=request_sha256,
                parent_ref=parent_ref,
            )
        if status in {"REJECTED", "LOCAL_REJECTED"}:
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_REJECTED,
                code=code or "EXECUTION_REJECTED",
                request_id=request_id,
                request_sha256=request_sha256,
                parent_ref=parent_ref,
            )
        if status == "NOT_SENT":
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_UNAVAILABLE,
                code=code or "EXECUTION_UNAVAILABLE",
                request_id=request_id,
                request_sha256=request_sha256,
            )
        if status == "UNKNOWN":
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_UNKNOWN,
                code=code or "EXECUTION_UNKNOWN",
                request_id=request_id,
                request_sha256=request_sha256,
                execution_state=state or status,
            )
        return self._result(
            ExecutionRoutingOutcome.EXECUTION_UNKNOWN,
            code="EXECUTION_INVALID_REPLY",
            request_id=request_id,
            request_sha256=request_sha256,
            execution_state=state or status,
        )

    def _cancel_result(
        self, reply: object, *, request_id: str, request_sha256: str
    ) -> ExecutionRoutingResult:
        status, code, parent_ref, state = _reply_fields(reply)
        if status in {"ACCEPTED", "CANCEL_REQUESTED", "CANCEL_ACCEPTED"}:
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_CANCEL_ACCEPTED,
                request_id=request_id,
                request_sha256=request_sha256,
                parent_ref=parent_ref,
                execution_state=state,
            )
        if status in {"REJECTED", "LOCAL_REJECTED"}:
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_REJECTED,
                code=code or "EXECUTION_REJECTED",
                request_id=request_id,
                request_sha256=request_sha256,
            )
        if status == "NOT_SENT":
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_UNAVAILABLE,
                code=code or "EXECUTION_CANCEL_UNAVAILABLE",
                request_id=request_id,
                request_sha256=request_sha256,
            )
        return self._result(
            ExecutionRoutingOutcome.EXECUTION_UNKNOWN,
            code=code or "EXECUTION_UNKNOWN",
            request_id=request_id,
            request_sha256=request_sha256,
            execution_state=state,
        )

    def _stored_result(
        self, raw: str, *, request_id: str, request_sha256: str
    ) -> ExecutionRoutingResult:
        data = _validate_result(raw)
        return self._result(
            ExecutionRoutingOutcome(data["status"]),
            code=data["code"],
            request_id=request_id,
            request_sha256=request_sha256,
            parent_ref=data["parent_ref"],
            execution_state=data["execution_state"],
        )

    def _check_replay(self, intent: GatewayOrderIntent, record: _ParentRecord) -> None:
        if record.intent_digest != intent.digest:
            _fail("EXECUTION_REQUEST_CONFLICT")
        if record.request_id != "sdk-request-v1:" + self._seed(intent)[:40]:
            _fail("EXECUTION_LOCAL_RECORD_FAILED")

    def _replay(self, record: _ParentRecord | None) -> ExecutionRoutingResult:
        if record is None:
            return self._result(ExecutionRoutingOutcome.EXECUTION_UNKNOWN)
        if record.result is None:
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_UNKNOWN,
                code="EXECUTION_UNKNOWN",
                request_id=record.request_id,
                request_sha256=record.request_sha256,
            )
        return self._stored_result(
            record.result,
            request_id=record.request_id,
            request_sha256=record.request_sha256,
        )

    def _store_and_return(
        self, identity: tuple[str, str, str], result: ExecutionRoutingResult
    ) -> ExecutionRoutingResult:
        value = _canonical(
            {
                "status": result.outcome.value,
                "code": result.code,
                "parent_ref": result.parent_ref,
                "execution_state": result.execution_state,
            }
        )
        db = self._db
        try:
            _begin(db)
            stored = _store_result(db, identity, value)
            _commit(db)
        except ExecutionRoutingError:
            _rollback(db)
            raise ExecutionRoutingError("EXECUTION_UNKNOWN") from None
        except sqlite3.Error:
            _rollback(db)
            raise ExecutionRoutingError("EXECUTION_UNKNOWN") from None
        data = _validate_result(stored)
        return self._result(
            ExecutionRoutingOutcome(data["status"]),
            code=data["code"],
            request_id=result.request_id,
            request_sha256=result.request_sha256,
            parent_ref=data["parent_ref"],
            execution_state=data["execution_state"],
        )

    def _port_callable(self, name: str):
        try:
            method = getattr(self._port, name, None)
        except Exception:
            return None
        return method if callable(method) else None

    def _now(self) -> int:
        try:
            value = self._clock()
        except Exception:
            _fail("EXECUTION_UNAVAILABLE")
        if type(value) is not int or not 0 <= value < _MAX_NS:
            _fail("EXECUTION_UNAVAILABLE")
        return value

    # -- public operations ----------------------------------------------------

    def route(self, intent: object) -> ExecutionRoutingResult:
        """Match one intent; for a match, persist first and submit exactly once."""
        intent = self._validate_intent(intent)
        identity = self._identity(intent)
        request = self._decode_request(intent)
        if not self._scope_matches(intent):
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_REJECTED, code="EXECUTION_SCOPE_DENIED"
            )
        instrument = self._resolve_instrument(request)
        if instrument is None:
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_REJECTED,
                code="EXECUTION_INSTRUMENT_UNRESOLVED",
            )
        facts = _OrderFacts(
            instrument,
            getattr(request.side, "value", None),
            request.offset,
            request.order_type,
            request.quantity,
            request.quantity_unit,
        )
        decision = self._config.rule_set.match(facts)
        if decision.outcome is _Outcome.INDETERMINATE:
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_REJECTED,
                code="EXECUTION_ROUTE_INDETERMINATE",
            )
        if decision.outcome is _Outcome.REJECT_UNSUPPORTED:
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_REJECTED,
                code="EXECUTION_ORDER_TYPE_UNSUPPORTED",
            )
        if decision.outcome is _Outcome.MATCH:
            policy = self._config.policies[decision.rule_index]
            rejection = self._policy_rejection(request, policy)
            if rejection is not None:
                return self._result(ExecutionRoutingOutcome.EXECUTION_REJECTED, code=rejection)
            now = self._now()
            if now >= policy.deadline_ns:
                return self._result(
                    ExecutionRoutingOutcome.EXECUTION_REJECTED,
                    code="EXECUTION_DEADLINE_EXPIRED",
                )

        db = self._db
        _begin(db)
        try:
            existing = _load_record(db, identity)
            if existing is not None:
                self._check_replay(intent, existing)
                _commit(db)
                return self._replay(existing)
            try:
                candidate = load_route_candidate(db, intent)
            except RouteJournalError as error:
                _rollback(db)
                if error.args and error.args[0] == "CANDIDATE_UNAVAILABLE":
                    raise ExecutionRoutingError("EXECUTION_REQUEST_CONFLICT") from None
                raise ExecutionRoutingError("EXECUTION_LOCAL_RECORD_FAILED") from None
            if candidate is None:
                try:
                    candidate = record_route_candidate(
                        db,
                        intent,
                        configuration_id=self._config.configuration_id,
                        instruments=self._config.instruments,
                        rules=self._config.rule_set,
                    )
                except RouteJournalError as error:
                    _rollback(db)
                    if error.args and error.args[0] == "CANDIDATE_CONFLICT":
                        raise ExecutionRoutingError("EXECUTION_ROUTE_CONFLICT") from None
                    raise ExecutionRoutingError("EXECUTION_LOCAL_RECORD_FAILED") from None
            if candidate.outcome is _Outcome.NO_MATCH:
                _commit(db)
                return self._result(ExecutionRoutingOutcome.DIRECT_PATH)
            if candidate.outcome is _Outcome.INDETERMINATE:
                _commit(db)
                return self._result(
                    ExecutionRoutingOutcome.EXECUTION_REJECTED,
                    code="EXECUTION_ROUTE_INDETERMINATE",
                )
            if candidate.outcome is _Outcome.REJECT_UNSUPPORTED:
                _commit(db)
                return self._result(
                    ExecutionRoutingOutcome.EXECUTION_REJECTED,
                    code="EXECUTION_ORDER_TYPE_UNSUPPORTED",
                )
            if _candidate_configuration(candidate) != self._config.configuration_id:
                _rollback(db)
                return self._result(
                    ExecutionRoutingOutcome.EXECUTION_UNKNOWN,
                    code="EXECUTION_ROUTE_POLICY_UNAVAILABLE",
                )
            index = candidate.rule_index
            if type(index) is not int or not 0 <= index < len(self._config.policies):
                _rollback(db)
                return self._result(
                    ExecutionRoutingOutcome.EXECUTION_UNKNOWN,
                    code="EXECUTION_ROUTE_POLICY_UNAVAILABLE",
                )
            policy = self._config.policies[index]
            rejection = self._policy_rejection(request, policy)
            if rejection is not None:
                _rollback(db)
                return self._result(ExecutionRoutingOutcome.EXECUTION_REJECTED, code=rejection)
            now = self._now()
            if now >= policy.deadline_ns:
                _rollback(db)
                return self._result(
                    ExecutionRoutingOutcome.EXECUTION_REJECTED,
                    code="EXECUTION_DEADLINE_EXPIRED",
                )
            if self._port_callable("submit") is None:
                _rollback(db)
                return self._result(
                    ExecutionRoutingOutcome.EXECUTION_UNAVAILABLE,
                    code="EXECUTION_UNAVAILABLE",
                )
            request_id, digest, payload = self._build(intent, request, instrument, policy)
            record, created = _insert_prepared(
                db,
                identity,
                intent.digest,
                request_id,
                digest,
                self._config.configuration_id,
                payload,
                now,
            )
            _commit(db)
        except sqlite3.Error:
            _rollback(db)
            raise ExecutionRoutingError("EXECUTION_LOCAL_RECORD_FAILED") from None
        except BaseException:
            _rollback(db)
            raise
        if not created:
            return self._replay(record)
        return self._call_submit(identity, request_id, digest, payload)

    def _call_submit(
        self,
        identity: tuple[str, str, str],
        request_id: str,
        digest: str,
        payload: str,
    ) -> ExecutionRoutingResult:
        submit = self._port_callable("submit")
        if submit is None:
            return self._result(
                ExecutionRoutingOutcome.EXECUTION_UNAVAILABLE,
                code="EXECUTION_UNAVAILABLE",
                request_id=request_id,
                request_sha256=digest,
            )
        try:
            reply = submit(json.loads(payload))
        except Exception:
            result = self._result(
                ExecutionRoutingOutcome.EXECUTION_UNKNOWN,
                code="CALL_OUTCOME_UNKNOWN",
                request_id=request_id,
                request_sha256=digest,
            )
            return self._store_and_return(identity, result)
        result = self._submit_result(reply, request_id=request_id, request_sha256=digest)
        return self._store_and_return(identity, result)

    def cancel(self, intent: object) -> ExecutionRoutingResult:
        """Route a cancel for a bound parent; never a direct venue cancel."""
        intent = self._validate_intent(intent)
        identity = self._identity(intent)
        operation_id = "sdk-cancel-v1:" + self._seed(intent)[:32]
        db = self._db
        _begin(db)
        try:
            record = _load_record(db, identity)
            if record is None:
                _commit(db)
                return self._result(ExecutionRoutingOutcome.DIRECT_PATH)
            self._check_replay(intent, record)
            if record.cancel_result is not None:
                _commit(db)
                data = _validate_result(record.cancel_result)
                return self._result(
                    ExecutionRoutingOutcome(data["status"]),
                    code=data["code"],
                    request_id=record.request_id,
                    request_sha256=record.request_sha256,
                    parent_ref=data["parent_ref"],
                    execution_state=data["execution_state"],
                )
            if record.cancel_operation_id == operation_id:
                _commit(db)
                return self._result(
                    ExecutionRoutingOutcome.EXECUTION_UNKNOWN,
                    code="EXECUTION_UNKNOWN",
                    request_id=record.request_id,
                    request_sha256=record.request_sha256,
                )
            if record.cancel_operation_id is not None:
                _commit(db)
                raise ExecutionRoutingError("EXECUTION_REQUEST_CONFLICT")
            if self._port_callable("cancel") is None:
                _commit(db)
                return self._result(
                    ExecutionRoutingOutcome.EXECUTION_UNAVAILABLE,
                    code="EXECUTION_CANCEL_UNAVAILABLE",
                    request_id=record.request_id,
                    request_sha256=record.request_sha256,
                )
            _set_cancel_operation(db, identity, operation_id)
            _commit(db)
        except sqlite3.Error:
            _rollback(db)
            raise ExecutionRoutingError("EXECUTION_LOCAL_RECORD_FAILED") from None
        except BaseException:
            _rollback(db)
            raise
        payload_data = json.loads(record.payload)
        remote_parent_ref = None
        if record.result is not None:
            remote_parent_ref = _validate_result(record.result)["parent_ref"]
        payload = {
            "request_id": record.request_id,
            "parent_ref": payload_data["spec"]["parent_ref"],
            "remote_parent_ref": remote_parent_ref,
            "request_sha256": record.request_sha256,
            "operation_id": operation_id,
        }
        try:
            reply = self._port_callable("cancel")(payload)
        except Exception:
            result = self._result(
                ExecutionRoutingOutcome.EXECUTION_UNKNOWN,
                code="CALL_OUTCOME_UNKNOWN",
                request_id=record.request_id,
                request_sha256=record.request_sha256,
            )
            return self._store_cancel_and_return(identity, result)
        result = self._cancel_result(
            reply, request_id=record.request_id, request_sha256=record.request_sha256
        )
        return self._store_cancel_and_return(identity, result)

    def _store_cancel_and_return(
        self, identity: tuple[str, str, str], result: ExecutionRoutingResult
    ) -> ExecutionRoutingResult:
        value = _canonical(
            {
                "status": result.outcome.value,
                "code": result.code,
                "parent_ref": result.parent_ref,
                "execution_state": result.execution_state,
            }
        )
        db = self._db
        try:
            _begin(db)
            stored = _store_cancel_result(db, identity, value)
            _commit(db)
        except Exception:
            _rollback(db)
            raise ExecutionRoutingError("EXECUTION_UNKNOWN") from None
        data = _validate_result(stored)
        return self._result(
            ExecutionRoutingOutcome(data["status"]),
            code=data["code"],
            request_id=result.request_id,
            request_sha256=result.request_sha256,
            parent_ref=data["parent_ref"],
            execution_state=data["execution_state"],
        )

    def status(self, intent: object) -> ExecutionRoutingStatus:
        """Read-only projection of the local identity and the Execution state."""
        intent = self._validate_intent(intent)
        identity = self._identity(intent)
        db = self._db
        _begin_read(db)
        try:
            record = _load_record(db, identity)
        except BaseException:
            _rollback(db)
            raise
        _rollback(db)
        if record is None:
            return ExecutionRoutingStatus(routed=False, outcome=ExecutionRoutingOutcome.DIRECT_PATH)
        self._check_replay(intent, record)
        local = self._replay(record)
        cancel_outcome = None
        cancel_code = None
        if record.cancel_result is not None:
            cancel = _validate_result(record.cancel_result)
            cancel_outcome = cancel["status"]
            cancel_code = cancel["code"]
        status_method = self._port_callable("status")
        if status_method is None:
            return ExecutionRoutingStatus(
                routed=True,
                outcome=local.outcome,
                request_id=record.request_id,
                request_sha256=record.request_sha256,
                parent_ref=local.parent_ref,
                configuration_id=record.configuration_id,
                execution_state=None,
                code="EXECUTION_STATUS_UNAVAILABLE",
                cancel_outcome=cancel_outcome,
                cancel_code=cancel_code,
            )
        try:
            reply = status_method(record.request_id)
        except Exception:
            return ExecutionRoutingStatus(
                routed=True,
                outcome=ExecutionRoutingOutcome.EXECUTION_UNKNOWN,
                request_id=record.request_id,
                request_sha256=record.request_sha256,
                parent_ref=local.parent_ref,
                configuration_id=record.configuration_id,
                code="EXECUTION_STATUS_UNAVAILABLE",
                cancel_outcome=cancel_outcome,
                cancel_code=cancel_code,
            )
        projected = _project_status(reply, record)
        if projected is None:
            return ExecutionRoutingStatus(
                routed=True,
                outcome=ExecutionRoutingOutcome.EXECUTION_UNKNOWN,
                request_id=record.request_id,
                request_sha256=record.request_sha256,
                parent_ref=local.parent_ref,
                configuration_id=record.configuration_id,
                code="EXECUTION_INVALID_REPLY",
                cancel_outcome=cancel_outcome,
                cancel_code=cancel_code,
            )
        return ExecutionRoutingStatus(
            routed=True,
            outcome=local.outcome,
            request_id=record.request_id,
            request_sha256=record.request_sha256,
            parent_ref=projected["parent_ref"] or local.parent_ref,
            configuration_id=record.configuration_id,
            execution_state=projected["state"],
            children=projected["children"],
            unknown_children=projected["unknown_children"],
            code=local.code,
            cancel_outcome=cancel_outcome,
            cancel_code=cancel_code,
        )


def _project_status(reply: object, record: _ParentRecord) -> dict | None:
    if not isinstance(reply, Mapping):
        return None
    request_id = reply.get("request_id")
    if request_id is not None and request_id != record.request_id:
        return None
    request_sha256 = reply.get("request_sha256")
    if request_sha256 is not None and request_sha256 != record.request_sha256:
        return None
    state = reply.get("state", reply.get("status"))
    if state is not None and type(state) is not str:
        return None
    parent_ref = reply.get("parent_ref")
    if parent_ref is not None and type(parent_ref) is not str:
        return None
    raw_children = reply.get("children", ())
    if type(raw_children) not in (list, tuple) or len(raw_children) > _MAX_CHILDREN:
        return None
    children = []
    for child in raw_children:
        if not isinstance(child, Mapping):
            return None
        child_id = child.get("child_id")
        child_state = child.get("state")
        if _text(child_id) is None or _text(child_state) is None:
            return None
        row = {"child_id": child_id, "state": child_state}
        for name in ("quantity", "reservation_id", "last_error"):
            value = child.get(name)
            if value is not None and type(value) is not str:
                return None
            row[name] = value
        attempts = child.get("attempts")
        if attempts is not None and _count(attempts) is None:
            return None
        row["attempts"] = attempts
        children.append(row)
    raw_unknown = reply.get("unknown_children", ())
    if type(raw_unknown) not in (list, tuple) or any(_text(item) is None for item in raw_unknown):
        return None
    return {
        "state": state,
        "parent_ref": parent_ref,
        "children": tuple(children),
        "unknown_children": tuple(raw_unknown),
    }


def route_order(router: ExecutionOrderRouter | None, intent: object, direct):
    """Call ``direct`` only when no Execution route is selected or matched.

    A matched request always returns the routing result, including a finite
    failure; the existing direct path is never a fallback for it.
    """
    if router is None:
        return direct()
    result = router.route(intent)
    if result.outcome is ExecutionRoutingOutcome.DIRECT_PATH:
        return direct()
    return result


__all__ = [
    "ExecutionOrderRouter",
    "ExecutionRoutingConfig",
    "ExecutionRoutingError",
    "ExecutionRoutingOutcome",
    "ExecutionRoutingResult",
    "ExecutionRoutingStatus",
    "RulePolicy",
    "install_execution_routing_tables",
    "parse_execution_routing",
    "route_order",
]
