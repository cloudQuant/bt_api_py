"""Production-only, non-authorizing CTP managed-write approval evidence.

This module verifies two detached Ed25519 signatures: an independently
provisioned trust anchor signs a short-lived issuer trust root, and an issuer
key from that root signs an exact production action scope.  Verification only
returns immutable evidence.  This module has no CTP client imports, signer,
approval redemption, arming, order, or cancel path.

The private context factory is reserved for a future code-owned runtime after
it has collected session identity.  A verified object from this module is not
a write capability and is not accepted by any current route.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, NoReturn
from urllib.parse import urlsplit

from ._contracts.errors import NormalizedApiError

PRODUCTION_APPROVAL_SCHEMA_VERSION = "ctp-production-managed-write-approval-v1"
PRODUCTION_TRUST_ROOT_SCHEMA_VERSION = "ctp-production-write-trust-root-v1"
PRODUCTION_APPROVAL_PURPOSE = "ctp_production_managed_write"
PRODUCTION_APPROVER_ROLE = "independent_production_approver"
_ALGORITHM = "Ed25519"
_OPERATION = "verify_ctp_production_managed_write_approval"
_MAX_ARTIFACT_BYTES = 64 * 1024
_MAX_ROOT_LIFETIME = timedelta(days=90)
_MAX_REVOCATION_LIFETIME = timedelta(hours=24)
_MAX_APPROVAL_LIFETIME = timedelta(hours=24)
_MAX_ORDERS = 16
_MAX_CANCELS = 16
_MAX_TOTAL_ACTIONS = 24
_MAX_ORDER_VOLUME = 1000
_MAX_TOTAL_VOLUME = 5000
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TRADING_DAY = re.compile(r"^[0-9]{8}$")
_B64URL_32 = re.compile(r"^[A-Za-z0-9_-]{43}$")
_B64URL_64 = re.compile(r"^[A-Za-z0-9_-]{86}$")
_EXCHANGE = re.compile(r"^[A-Z][A-Z0-9_]{1,15}$")
_INSTRUMENT = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_ORDER_REF = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")
_CONTEXT_SEAL = object()
_EVIDENCE_SEAL = object()

_ARTIFACT_FIELDS = frozenset({"schema_version", "algorithm", "payload", "signature"})
_ROOT_PAYLOAD_FIELDS = frozenset(
    {"schema_version", "root_id", "issued_at", "expires_at", "keys", "revocation_snapshot"}
)
_ROOT_KEY_FIELDS = frozenset({"public_key", "role", "purposes", "not_before", "expires_at"})
_REVOCATION_FIELDS = frozenset(
    {"version", "issued_at", "expires_at", "revoked_approval_ids", "revoked_nonces"}
)
_APPROVAL_FIELDS = frozenset(
    {
        "schema_version",
        "approval_id",
        "nonce",
        "issuer_key_id",
        "issuer_role",
        "purpose",
        "environment",
        "broker_id",
        "account_fingerprint",
        "md_front",
        "td_front",
        "trading_day",
        "connection_generation",
        "strategy_id",
        "runtime_id",
        "artifact_sha256",
        "config_sha256",
        "issued_at",
        "not_before",
        "expires_at",
        "revocation_snapshot_version",
        "orders",
        "cancellations",
    }
)
_ORDER_FIELDS = frozenset(
    {
        "intent_id",
        "instrument_id",
        "exchange_id",
        "side",
        "offset",
        "hedge_flag",
        "volume",
        "limit_price",
    }
)
_CANCEL_FIELDS = frozenset({"cancel_id", "target_order_ref", "instrument_id", "exchange_id"})
_CONTEXT_FIELDS = frozenset(
    {
        "environment",
        "broker_id",
        "account_fingerprint",
        "md_front",
        "td_front",
        "trading_day",
        "connection_generation",
        "strategy_id",
        "runtime_id",
        "artifact_sha256",
        "config_sha256",
    }
)


def _reject(code: str) -> NoReturn:
    raise NormalizedApiError(_OPERATION, code, definite_reject=True)


class _DuplicateKeyError(ValueError):
    pass


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid JSON constant: {value}")


def _parse_object(value: bytes | str, *, code: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            raw = value.encode("utf-8", "strict")
        except UnicodeEncodeError:
            _reject(code)
    elif isinstance(value, bytes):
        raw = value
    else:
        _reject(code)
    if len(raw) > _MAX_ARTIFACT_BYTES or raw.startswith(b"\xef\xbb\xbf"):
        _reject(code)
    try:
        parsed = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except _DuplicateKeyError:
        _reject("ctp_production_approval_duplicate_json_key")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        _reject(code)
    if type(parsed) is not dict:
        _reject(code)
    return parsed


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, UnicodeEncodeError, ValueError, RecursionError):
        _reject("ctp_production_approval_invalid_artifact")


def _mapping(value: Any, expected: frozenset[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        _reject(code)
    return value


def _string(
    value: Any,
    *,
    code: str,
    pattern: re.Pattern[str] | None = None,
    max_length: int = 256,
) -> str:
    if type(value) is not str or not value or len(value) > max_length:
        _reject(code)
    if value != value.strip() or value != unicodedata.normalize("NFC", value):
        _reject(code)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        _reject(code)
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        _reject(code)
    if pattern is not None and pattern.fullmatch(value) is None:
        _reject(code)
    return value


def _hash(value: Any) -> str:
    return _string(
        value, code="ctp_production_approval_invalid_hash", pattern=_HEX64, max_length=64
    )


def _timestamp(value: Any, *, code: str) -> datetime:
    text = _string(value, code=code, max_length=32)
    if not text.endswith("Z"):
        _reject(code)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        _reject(code)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _reject(code)
    canonical = parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if text != canonical:
        _reject(code)
    return parsed.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _b64url(value: Any, *, length: int, code: str) -> bytes:
    pattern = _B64URL_32 if length == 32 else _B64URL_64
    text = _string(value, code=code, pattern=pattern, max_length=43 if length == 32 else 86)
    try:
        result = base64.urlsafe_b64decode(text + ("=" if length == 32 else "=="))
    except (ValueError, binascii.Error):
        _reject(code)
    if len(result) != length:
        _reject(code)
    return result


def _public_key(value: Any) -> bytes:
    if isinstance(value, bytes):
        if len(value) != 32:
            _reject("ctp_production_approval_invalid_trust_anchor")
        return value
    return _b64url(value, length=32, code="ctp_production_approval_invalid_trust_anchor")


def _signature(value: Any) -> bytes:
    return _b64url(value, length=64, code="ctp_production_approval_invalid_signature")


def _verify_ed25519(signature: bytes, message: bytes, public_key: bytes, *, code: str) -> None:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except (ImportError, ModuleNotFoundError):
        _reject("ctp_production_approval_cryptography_unavailable")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except Exception:
        _reject(code)


def _coerce_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        _reject("ctp_production_approval_clock_untrusted")
    return value.astimezone(UTC)


def _validate_window(
    issued: datetime,
    not_before: datetime,
    expires: datetime,
    current: datetime,
    *,
    max_lifetime: timedelta,
    prefix: str,
) -> None:
    if not issued <= not_before < expires or expires - issued > max_lifetime:
        _reject(f"{prefix}_invalid_time_window")
    if current < not_before:
        _reject(f"{prefix}_not_yet_valid")
    if current >= expires:
        _reject(f"{prefix}_expired")
    if issued > current + timedelta(seconds=1):
        _reject(f"{prefix}_issued_in_future")


def _front(value: Any, *, code: str) -> str:
    text = _string(value, code=code, max_length=256)
    try:
        parsed = urlsplit(text)
        port = parsed.port
        host = parsed.hostname
    except ValueError:
        _reject(code)
    if (
        parsed.scheme not in {"tcp", "ssl"}
        or not host
        or port is None
        or not 1 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not text.isascii()
    ):
        _reject(code)
    return text


def _trading_day(value: Any, *, code: str) -> str:
    text = _string(value, code=code, pattern=_TRADING_DAY, max_length=8)
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        _reject(code)
    return text


def _normalize_context(value: Any) -> Mapping[str, Any]:
    if type(value) is not CtpProductionWriteContext or value._seal is not _CONTEXT_SEAL:
        _reject("ctp_production_approval_context_untrusted")
    raw = value.values
    normalized = {
        "environment": _string(
            raw.get("environment"), code="ctp_production_approval_context_invalid"
        ),
        "broker_id": _string(
            raw.get("broker_id"), code="ctp_production_approval_context_invalid", pattern=_SAFE_ID
        ),
        "account_fingerprint": _hash(raw.get("account_fingerprint")),
        "md_front": _front(raw.get("md_front"), code="ctp_production_approval_context_invalid"),
        "td_front": _front(raw.get("td_front"), code="ctp_production_approval_context_invalid"),
        "trading_day": _trading_day(
            raw.get("trading_day"), code="ctp_production_approval_context_invalid"
        ),
        "connection_generation": _positive_int(
            raw.get("connection_generation"), "ctp_production_approval_context_invalid"
        ),
        "strategy_id": _string(
            raw.get("strategy_id"), code="ctp_production_approval_context_invalid", pattern=_SAFE_ID
        ),
        "runtime_id": _string(
            raw.get("runtime_id"), code="ctp_production_approval_context_invalid", pattern=_SAFE_ID
        ),
        "artifact_sha256": _hash(raw.get("artifact_sha256")),
        "config_sha256": _hash(raw.get("config_sha256")),
    }
    if normalized["environment"] != "production":
        _reject("ctp_production_approval_environment_mismatch")
    return MappingProxyType(normalized)


def _positive_int(value: Any, code: str) -> int:
    if type(value) is not int or value <= 0:
        _reject(code)
    return value


def _normalize_revocations(value: Any, *, now: datetime) -> Mapping[str, Any]:
    value = _mapping(value, _REVOCATION_FIELDS, "ctp_production_approval_invalid_trust_root")
    version = _positive_int(value["version"], "ctp_production_approval_invalid_revocation_version")
    issued = _timestamp(value["issued_at"], code="ctp_production_approval_invalid_trust_root")
    expires = _timestamp(value["expires_at"], code="ctp_production_approval_invalid_trust_root")
    if not issued < expires or expires - issued > _MAX_REVOCATION_LIFETIME:
        _reject("ctp_production_approval_invalid_trust_root")
    if now < issued or now >= expires:
        _reject("ctp_production_approval_revocation_snapshot_stale")
    result: dict[str, Any] = {
        "version": version,
        "issued_at": _timestamp_text(issued),
        "expires_at": _timestamp_text(expires),
    }
    for name in ("revoked_approval_ids", "revoked_nonces"):
        entries = value[name]
        if type(entries) is not list:
            _reject("ctp_production_approval_invalid_trust_root")
        normalized = [
            _string(item, code="ctp_production_approval_invalid_trust_root", pattern=_SAFE_ID)
            for item in entries
        ]
        if normalized != sorted(set(normalized)):
            _reject("ctp_production_approval_invalid_trust_root")
        result[name] = normalized
    return MappingProxyType(result)


def _normalize_root_payload(value: Any, *, now: datetime) -> Mapping[str, Any]:
    value = _mapping(value, _ROOT_PAYLOAD_FIELDS, "ctp_production_approval_invalid_trust_root")
    if value["schema_version"] != PRODUCTION_TRUST_ROOT_SCHEMA_VERSION:
        _reject("ctp_production_approval_unknown_trust_root_schema")
    root_id = _string(
        value["root_id"], code="ctp_production_approval_invalid_trust_root", pattern=_SAFE_ID
    )
    issued = _timestamp(value["issued_at"], code="ctp_production_approval_invalid_trust_root")
    expires = _timestamp(value["expires_at"], code="ctp_production_approval_invalid_trust_root")
    _validate_window(
        issued,
        issued,
        expires,
        now,
        max_lifetime=_MAX_ROOT_LIFETIME,
        prefix="ctp_production_approval_trust_root",
    )
    keys = value["keys"]
    if type(keys) is not dict or not keys:
        _reject("ctp_production_approval_invalid_trust_root")
    normalized_keys: dict[str, Any] = {}
    for key_id, entry in keys.items():
        key_id = _string(
            key_id, code="ctp_production_approval_invalid_trust_root", pattern=_SAFE_ID
        )
        entry = _mapping(entry, _ROOT_KEY_FIELDS, "ctp_production_approval_invalid_trust_root")
        role = _string(
            entry["role"], code="ctp_production_approval_invalid_trust_root", pattern=_SAFE_ID
        )
        purposes = entry["purposes"]
        if type(purposes) is not list or not purposes:
            _reject("ctp_production_approval_invalid_trust_root")
        normalized_purposes = [
            _string(item, code="ctp_production_approval_invalid_trust_root", pattern=_SAFE_ID)
            for item in purposes
        ]
        if len(normalized_purposes) != len(set(normalized_purposes)):
            _reject("ctp_production_approval_invalid_trust_root")
        key_not_before = _timestamp(
            entry["not_before"], code="ctp_production_approval_invalid_trust_root"
        )
        key_expires = _timestamp(
            entry["expires_at"], code="ctp_production_approval_invalid_trust_root"
        )
        if not key_not_before < key_expires or now < key_not_before or now >= key_expires:
            _reject("ctp_production_approval_issuer_key_expired")
        normalized_keys[key_id] = {
            "public_key": _string(
                entry["public_key"],
                code="ctp_production_approval_invalid_trust_root",
                pattern=_B64URL_32,
                max_length=43,
            ),
            "role": role,
            "purposes": tuple(normalized_purposes),
            "not_before": _timestamp_text(key_not_before),
            "expires_at": _timestamp_text(key_expires),
        }
    revocations = _normalize_revocations(value["revocation_snapshot"], now=now)
    return MappingProxyType(
        {
            "schema_version": PRODUCTION_TRUST_ROOT_SCHEMA_VERSION,
            "root_id": root_id,
            "issued_at": _timestamp_text(issued),
            "expires_at": _timestamp_text(expires),
            "keys": MappingProxyType(normalized_keys),
            "revocation_snapshot": revocations,
        }
    )


def _normalize_order(value: Any) -> Mapping[str, Any]:
    value = _mapping(value, _ORDER_FIELDS, "ctp_production_approval_invalid_order_scope")
    result = {
        "intent_id": _string(
            value["intent_id"], code="ctp_production_approval_invalid_order_scope", pattern=_SAFE_ID
        ),
        "instrument_id": _string(
            value["instrument_id"],
            code="ctp_production_approval_invalid_order_scope",
            pattern=_INSTRUMENT,
        ),
        "exchange_id": _string(
            value["exchange_id"],
            code="ctp_production_approval_invalid_order_scope",
            pattern=_EXCHANGE,
        ),
        "side": _string(value["side"], code="ctp_production_approval_invalid_order_scope"),
        "offset": _string(value["offset"], code="ctp_production_approval_invalid_order_scope"),
        "hedge_flag": _string(
            value["hedge_flag"], code="ctp_production_approval_invalid_order_scope"
        ),
        "volume": value["volume"],
        "limit_price": value["limit_price"],
    }
    if (
        result["side"] not in {"buy", "sell"}
        or result["offset"] not in {"open", "close", "close_today", "close_yesterday"}
        or result["hedge_flag"] not in {"1", "2", "3"}
    ):
        _reject("ctp_production_approval_invalid_order_scope")
    if type(result["volume"]) is not int or not 1 <= result["volume"] <= _MAX_ORDER_VOLUME:
        _reject("ctp_production_approval_order_volume_out_of_bounds")
    price_text = _string(
        result["limit_price"], code="ctp_production_approval_invalid_order_scope", max_length=32
    )
    try:
        price = Decimal(price_text)
    except InvalidOperation:
        _reject("ctp_production_approval_invalid_order_scope")
    if (
        not price.is_finite()
        or price <= 0
        or price > Decimal("1000000000")
        or price.as_tuple().exponent < -8
    ):
        _reject("ctp_production_approval_invalid_order_scope")
    canonical_price = format(price, "f")
    if "." in canonical_price:
        canonical_price = canonical_price.rstrip("0").rstrip(".")
    if canonical_price != price_text:
        _reject("ctp_production_approval_invalid_order_scope")
    result["limit_price"] = canonical_price
    return MappingProxyType(result)


def _normalize_cancel(value: Any) -> Mapping[str, Any]:
    value = _mapping(value, _CANCEL_FIELDS, "ctp_production_approval_invalid_cancel_scope")
    return MappingProxyType(
        {
            "cancel_id": _string(
                value["cancel_id"],
                code="ctp_production_approval_invalid_cancel_scope",
                pattern=_SAFE_ID,
            ),
            "target_order_ref": _string(
                value["target_order_ref"],
                code="ctp_production_approval_invalid_cancel_scope",
                pattern=_ORDER_REF,
            ),
            "instrument_id": _string(
                value["instrument_id"],
                code="ctp_production_approval_invalid_cancel_scope",
                pattern=_INSTRUMENT,
            ),
            "exchange_id": _string(
                value["exchange_id"],
                code="ctp_production_approval_invalid_cancel_scope",
                pattern=_EXCHANGE,
            ),
        }
    )


def _normalize_approval_payload(value: Any) -> Mapping[str, Any]:
    value = _mapping(value, _APPROVAL_FIELDS, "ctp_production_approval_invalid_payload")
    if value["schema_version"] != PRODUCTION_APPROVAL_SCHEMA_VERSION:
        _reject("ctp_production_approval_unknown_schema")
    result: dict[str, Any] = {
        "schema_version": PRODUCTION_APPROVAL_SCHEMA_VERSION,
        "approval_id": _string(
            value["approval_id"], code="ctp_production_approval_invalid_payload", pattern=_SAFE_ID
        ),
        "nonce": _string(
            value["nonce"], code="ctp_production_approval_invalid_payload", pattern=_SAFE_ID
        ),
        "issuer_key_id": _string(
            value["issuer_key_id"], code="ctp_production_approval_invalid_payload", pattern=_SAFE_ID
        ),
        "issuer_role": _string(
            value["issuer_role"], code="ctp_production_approval_invalid_payload", pattern=_SAFE_ID
        ),
        "purpose": _string(
            value["purpose"], code="ctp_production_approval_invalid_payload", pattern=_SAFE_ID
        ),
        "environment": _string(
            value["environment"], code="ctp_production_approval_invalid_payload"
        ),
        "broker_id": _string(
            value["broker_id"], code="ctp_production_approval_invalid_payload", pattern=_SAFE_ID
        ),
        "account_fingerprint": _hash(value["account_fingerprint"]),
        "md_front": _front(value["md_front"], code="ctp_production_approval_invalid_front"),
        "td_front": _front(value["td_front"], code="ctp_production_approval_invalid_front"),
        "trading_day": _trading_day(
            value["trading_day"], code="ctp_production_approval_invalid_payload"
        ),
        "connection_generation": _positive_int(
            value["connection_generation"], "ctp_production_approval_invalid_payload"
        ),
        "strategy_id": _string(
            value["strategy_id"], code="ctp_production_approval_invalid_payload", pattern=_SAFE_ID
        ),
        "runtime_id": _string(
            value["runtime_id"], code="ctp_production_approval_invalid_payload", pattern=_SAFE_ID
        ),
        "artifact_sha256": _hash(value["artifact_sha256"]),
        "config_sha256": _hash(value["config_sha256"]),
        "issued_at": _timestamp_text(
            _timestamp(value["issued_at"], code="ctp_production_approval_invalid_payload")
        ),
        "not_before": _timestamp_text(
            _timestamp(value["not_before"], code="ctp_production_approval_invalid_payload")
        ),
        "expires_at": _timestamp_text(
            _timestamp(value["expires_at"], code="ctp_production_approval_invalid_payload")
        ),
        "revocation_snapshot_version": _positive_int(
            value["revocation_snapshot_version"], "ctp_production_approval_invalid_payload"
        ),
    }
    if result["environment"] != "production":
        _reject("ctp_production_approval_environment_mismatch")
    if result["purpose"] != PRODUCTION_APPROVAL_PURPOSE:
        _reject("ctp_production_approval_purpose_mismatch")
    orders = value["orders"]
    cancellations = value["cancellations"]
    if type(orders) is not list or not 1 <= len(orders) <= _MAX_ORDERS:
        _reject("ctp_production_approval_order_scope_out_of_bounds")
    if type(cancellations) is not list or len(cancellations) > _MAX_CANCELS:
        _reject("ctp_production_approval_cancel_scope_out_of_bounds")
    if len(orders) + len(cancellations) > _MAX_TOTAL_ACTIONS:
        _reject("ctp_production_approval_action_scope_out_of_bounds")
    normalized_orders = [_normalize_order(item) for item in orders]
    normalized_cancels = [_normalize_cancel(item) for item in cancellations]
    intent_ids = [item["intent_id"] for item in normalized_orders]
    cancel_ids = [item["cancel_id"] for item in normalized_cancels]
    if len(intent_ids) != len(set(intent_ids)) or len(cancel_ids) != len(set(cancel_ids)):
        _reject("ctp_production_approval_duplicate_action_id")
    if sum(item["volume"] for item in normalized_orders) > _MAX_TOTAL_VOLUME:
        _reject("ctp_production_approval_total_volume_out_of_bounds")
    order_refs = {item["target_order_ref"] for item in normalized_cancels}
    if len(order_refs) != len(normalized_cancels):
        _reject("ctp_production_approval_duplicate_cancel_target")
    result["orders"] = tuple(normalized_orders)
    result["cancellations"] = tuple(normalized_cancels)
    return MappingProxyType(result)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True, init=False)
class CtpProductionWriteContext:
    """Private sealed snapshot of locally collected production identity."""

    values: Mapping[str, Any] = field(repr=False)
    _seal: object = field(repr=False, compare=False)
    _owner: object = field(repr=False, compare=False)

    def __init__(self, *, values: Mapping[str, Any], owner: object, _seal: object) -> None:
        if _seal is not _CONTEXT_SEAL:
            raise TypeError("SDK-collected production CTP context required")
        if not isinstance(values, Mapping) or set(values) != _CONTEXT_FIELDS:
            raise TypeError("complete production CTP identity required")
        object.__setattr__(self, "values", _freeze(dict(values)))
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_seal", _seal)


def _new_runtime_context(values: Mapping[str, Any], *, owner: object) -> CtpProductionWriteContext:
    """Internal constructor for the future code-owned runtime and local tests."""

    return CtpProductionWriteContext(values=values, owner=owner, _seal=_CONTEXT_SEAL)


@dataclass(frozen=True, slots=True, init=False)
class CtpProductionWriteApprovalEvidence:
    """Immutable signature evidence with no arming or provider interface."""

    payload: Mapping[str, Any] = field(repr=False)
    payload_sha256: str
    trust_root_sha256: str
    trust_anchor_key_sha256: str
    revocation_snapshot: Mapping[str, Any]
    _seal: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        payload: Mapping[str, Any],
        payload_sha256: str,
        trust_root_sha256: str,
        trust_anchor_key_sha256: str,
        revocation_snapshot: Mapping[str, Any],
        _seal: object,
    ) -> None:
        if _seal is not _EVIDENCE_SEAL:
            raise TypeError("verified production CTP approval evidence required")
        object.__setattr__(self, "payload", _freeze(payload))
        object.__setattr__(self, "payload_sha256", payload_sha256)
        object.__setattr__(self, "trust_root_sha256", trust_root_sha256)
        object.__setattr__(self, "trust_anchor_key_sha256", trust_anchor_key_sha256)
        object.__setattr__(self, "revocation_snapshot", _freeze(revocation_snapshot))
        object.__setattr__(self, "_seal", _seal)

    @property
    def approval_id(self) -> str:
        return str(self.payload["approval_id"])

    @property
    def environment(self) -> str:
        return str(self.payload["environment"])

    @property
    def expires_at(self) -> str:
        return str(self.payload["expires_at"])

    def as_dict(self) -> dict[str, Any]:
        """Return detached audit data; this result grants no write authority."""

        return {
            "approval_id": self.approval_id,
            "environment": self.environment,
            "payload_sha256": self.payload_sha256,
            "trust_root_sha256": self.trust_root_sha256,
            "trust_anchor_key_sha256": self.trust_anchor_key_sha256,
            "expires_at": self.expires_at,
            "payload": _thaw(self.payload),
            "revocation_snapshot": _thaw(self.revocation_snapshot),
            "authorizes_write": False,
        }


def verify_ctp_production_managed_write_approval(
    artifact: bytes | str,
    *,
    trust_root_artifact: bytes | str,
    trust_anchor_public_key: bytes | str,
    context: CtpProductionWriteContext,
    owner: object,
    minimum_revocation_snapshot_version: int,
    _now: datetime | None = None,
) -> CtpProductionWriteApprovalEvidence:
    """Verify production-only approval and exact locally collected context.

    The trust anchor is supplied out-of-band and signs the issuer root. The
    issuer root signs the bounded approval. The runtime must supply its
    durably retained revocation-version floor; an older signed snapshot is
    rejected. A SimNow/demo artifact, context, purpose, or issuer trust root
    cannot satisfy the schema/environment gates. The returned result is
    evidence only and cannot arm, submit, or cancel.
    """

    if type(context) is not CtpProductionWriteContext or context._seal is not _CONTEXT_SEAL:
        _reject("ctp_production_approval_context_untrusted")
    if context._owner is not owner:
        _reject("ctp_production_approval_context_owner_mismatch")
    current = _coerce_now(_now)

    root_artifact = _parse_object(
        trust_root_artifact, code="ctp_production_approval_invalid_trust_root"
    )
    if set(root_artifact) != _ARTIFACT_FIELDS:
        _reject("ctp_production_approval_invalid_trust_root")
    if (
        root_artifact["schema_version"] != PRODUCTION_TRUST_ROOT_SCHEMA_VERSION
        or root_artifact["algorithm"] != _ALGORITHM
    ):
        _reject("ctp_production_approval_unknown_trust_root_schema")
    root_payload = _normalize_root_payload(root_artifact["payload"], now=current)
    anchor = _public_key(trust_anchor_public_key)
    root_signature = _signature(root_artifact["signature"])
    root_bytes = _canonical(_thaw(root_payload))
    _verify_ed25519(
        root_signature,
        root_bytes,
        anchor,
        code="ctp_production_approval_trust_root_signature_invalid",
    )

    approval_artifact = _parse_object(artifact, code="ctp_production_approval_invalid_artifact")
    if set(approval_artifact) != _ARTIFACT_FIELDS:
        _reject("ctp_production_approval_invalid_artifact")
    if (
        approval_artifact["schema_version"] != PRODUCTION_APPROVAL_SCHEMA_VERSION
        or approval_artifact["algorithm"] != _ALGORITHM
    ):
        _reject("ctp_production_approval_unknown_schema")
    payload = _normalize_approval_payload(approval_artifact["payload"])
    issued = _timestamp(payload["issued_at"], code="ctp_production_approval_invalid_payload")
    not_before = _timestamp(payload["not_before"], code="ctp_production_approval_invalid_payload")
    expires = _timestamp(payload["expires_at"], code="ctp_production_approval_invalid_payload")
    _validate_window(
        issued,
        not_before,
        expires,
        current,
        max_lifetime=_MAX_APPROVAL_LIFETIME,
        prefix="ctp_production_approval",
    )
    snapshot = root_payload["revocation_snapshot"]
    minimum_version = _positive_int(
        minimum_revocation_snapshot_version,
        "ctp_production_approval_invalid_revocation_version_floor",
    )
    if snapshot["version"] < minimum_version:
        _reject("ctp_production_approval_revocation_version_rollback")
    if payload["revocation_snapshot_version"] != snapshot["version"]:
        _reject("ctp_production_approval_revocation_version_stale")
    if (
        payload["approval_id"] in snapshot["revoked_approval_ids"]
        or payload["nonce"] in snapshot["revoked_nonces"]
    ):
        _reject("ctp_production_approval_revoked")
    key = root_payload["keys"].get(payload["issuer_key_id"])
    if key is None:
        _reject("ctp_production_approval_issuer_untrusted")
    if (
        key["role"] != PRODUCTION_APPROVER_ROLE
        or payload["issuer_role"] != PRODUCTION_APPROVER_ROLE
        or PRODUCTION_APPROVAL_PURPOSE not in key["purposes"]
    ):
        _reject("ctp_production_approval_issuer_policy_mismatch")
    issuer_public_key = _b64url(
        key["public_key"],
        length=32,
        code="ctp_production_approval_invalid_trust_root",
    )
    if issuer_public_key == anchor:
        _reject("ctp_production_approval_issuer_key_must_differ_from_trust_anchor")
    approval_signature = _signature(approval_artifact["signature"])
    payload_bytes = _canonical(_thaw(payload))
    _verify_ed25519(
        approval_signature,
        payload_bytes,
        issuer_public_key,
        code="ctp_production_approval_signature_invalid",
    )

    normalized_context = _normalize_context(context)
    for field_name in _CONTEXT_FIELDS:
        if payload[field_name] != normalized_context[field_name]:
            _reject("ctp_production_approval_context_mismatch")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    root_hash = hashlib.sha256(_canonical(_thaw(root_payload))).hexdigest()
    return CtpProductionWriteApprovalEvidence(
        payload=payload,
        payload_sha256=payload_hash,
        trust_root_sha256=root_hash,
        trust_anchor_key_sha256=hashlib.sha256(anchor).hexdigest(),
        revocation_snapshot=snapshot,
        _seal=_EVIDENCE_SEAL,
    )


__all__ = [
    "PRODUCTION_APPROVAL_PURPOSE",
    "PRODUCTION_APPROVER_ROLE",
    "PRODUCTION_APPROVAL_SCHEMA_VERSION",
    "PRODUCTION_TRUST_ROOT_SCHEMA_VERSION",
    "CtpProductionWriteApprovalEvidence",
    "CtpProductionWriteContext",
    "verify_ctp_production_managed_write_approval",
]
