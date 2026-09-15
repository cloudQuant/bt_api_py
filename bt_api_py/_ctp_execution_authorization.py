"""Independent signed CTP execution approval verification for U1a.

This module deliberately contains no signing or private-key loading path.  A
deployment supplies an independently managed trust root and an already signed
JSON artifact.  Verification produces an immutable result; redemption is
performed by :class:`BtApi` only after the existing execution journal durably
consumes the approval nonce.

The wire format is a small maintained contract rather than a general purpose
JSON signature protocol.  The signed bytes are UTF-8 JSON produced with sorted
keys and compact separators from the exact ``payload`` object.  Ed25519 is
provided by the optional ``security`` extra (``cryptography``); there is no
fallback crypto implementation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from ._contracts.errors import NormalizedApiError

APPROVAL_SCHEMA_VERSION = "ctp-execution-approval-v1"
RECOVERY_APPROVAL_SCHEMA_VERSION = "ctp-execution-recovery-approval-v1"
ENTRY_APPROVAL_SCHEMA_VERSION = "ctp-execution-entry-approval-v1"
TRUST_ROOT_SCHEMA_VERSION = "ctp-execution-trust-root-v1"
APPROVAL_ALGORITHM = "Ed25519"
APPROVAL_PURPOSE = "ctp_execution_approval"
RECOVERY_APPROVAL_PURPOSE = "ctp_execution_recovery"
RECOVERY_APPROVAL_SCOPE_VERSION = "ctp-execution-recovery-v1"
APPROVAL_OPERATION = "verify_ctp_execution_approval"

_APPROVAL_FIELDS = frozenset(
    {
        "schema_version",
        "algorithm",
        "approval_id",
        "nonce",
        "issuer_key_id",
        "issuer_role",
        "purpose",
        "context_source",
        "candidate_id",
        "strategy_id",
        "strategy_identity_sha256",
        "execution_cycle_id",
        "configuration_sha256",
        "backtrader_sha256",
        "bt_api_py_sha256",
        "bt_api_ctp_sha256",
        "bt_api_base_sha256",
        "native_sha256",
        "dependency_hashes_sha256",
        "authorized_instruments",
        "primary_instrument",
        "account_fingerprint",
        "trading_day",
        "connection_generation",
        "environment_profile",
        "preflight_sha256",
        "evidence_sha256",
        "budget_policy_id",
        "budget_limit",
        "future_reservation_id",
        "issued_at",
        "not_before",
        "expires_at",
        "revocation_snapshot_version",
    }
)
_ARTIFACT_FIELDS = frozenset({"schema_version", "algorithm", "payload", "signature"})
_TRUST_ROOT_FIELDS = frozenset({"schema_version", "keys", "revocation_snapshot"})
_TRUST_KEY_FIELDS = frozenset({"public_key", "role", "purposes", "not_before", "expires_at"})
_REVOCATION_FIELDS = frozenset(
    {
        "version",
        "issued_at",
        "expires_at",
        "revoked_approval_ids",
        "revoked_nonces",
    }
)
_INSTRUMENT_FIELDS = frozenset({"instrument_id", "exchange_id"})
_RECOVERY_ACTION_FIELDS = frozenset(
    {
        "action_id",
        "action_kind",
        "instrument_id",
        "exchange_id",
        "side",
        "position_side",
        "offset",
        "quantity",
        "quantity_unit",
        "account_fingerprint",
        "trading_day",
        "connection_generation",
        "environment_profile",
        "candidate_id",
        "execution_cycle_id",
        "expires_at",
    }
)
_RECOVERY_APPROVAL_FIELDS = _APPROVAL_FIELDS | frozenset(
    {
        "recovery_scope_version",
        "recovery_plan_sha256",
        "recovery_token_sha256",
        "recovery_action_sha256",
        "receipt_sha256",
        "source_hashes_sha256",
        "ctp_package_sha256",
        "recovery_actions",
    }
)
# The entry variant keeps the ordinary execution purpose and adds exactly the
# proof-bound material hashes the V2 bundle arm contract requires; a base
# ordinary approval stays audit-only and can never mint an arm token.
_ENTRY_APPROVAL_FIELDS = _APPROVAL_FIELDS | frozenset(
    {
        "receipt_sha256",
        "source_hashes_sha256",
        "ctp_package_sha256",
    }
)
_CONTEXT_FIELDS = frozenset(
    {
        "source",
        *(
            field_name
            for field_name in _APPROVAL_FIELDS
            if field_name
            not in {
                "schema_version",
                "algorithm",
                "approval_id",
                "nonce",
                "issuer_key_id",
                "issuer_role",
                "purpose",
                "issued_at",
                "not_before",
                "expires_at",
                "revocation_snapshot_version",
            }
        ),
    }
)
_HASH_FIELDS = frozenset(
    {
        "strategy_identity_sha256",
        "configuration_sha256",
        "backtrader_sha256",
        "bt_api_py_sha256",
        "bt_api_ctp_sha256",
        "bt_api_base_sha256",
        "native_sha256",
        "dependency_hashes_sha256",
        "preflight_sha256",
        "evidence_sha256",
    }
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TRADING_DAY = re.compile(r"^[0-9]{8}$")
_B64URL = re.compile(r"^[A-Za-z0-9_-]{86}$")
_SAFE_ID = re.compile(r"^[\w][\w.:-]{0,255}$", re.ASCII)
_MAX_APPROVAL_LIFETIME = timedelta(days=366)
_CAPABILITY_SEAL = object()
_CONTEXT_SEAL = object()


class _DuplicateJsonKeyError(ValueError):
    pass


def _reject(operation: str, code: str) -> None:
    raise NormalizedApiError(operation, code, definite_reject=True)


def _duplicate_guard(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def _parse_json_artifact(value: bytes | str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            raw = value.encode("utf-8", "strict")
        except UnicodeEncodeError:
            _reject(APPROVAL_OPERATION, "ctp_approval_ambiguous_encoding")
    elif isinstance(value, bytes):
        raw = value
    else:
        _reject(APPROVAL_OPERATION, "ctp_approval_artifact_encoding_required")
    if raw.startswith(b"\xef\xbb\xbf"):
        _reject(APPROVAL_OPERATION, "ctp_approval_ambiguous_encoding")
    try:
        text = raw.decode("utf-8", "strict")
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_guard,
            parse_constant=_reject_json_constant,
        )
    except _DuplicateJsonKeyError:
        _reject(APPROVAL_OPERATION, "ctp_approval_duplicate_json_key")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_artifact")
    if not isinstance(value, dict):
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_artifact")
    return value


def _strict_mapping(value: Any, operation: str, code: str) -> Mapping:
    if not isinstance(value, Mapping) or type(value) is not dict:
        _reject(operation, code)
    return value


def _strict_string(
    value: Any,
    *,
    operation: str,
    code: str,
    pattern: re.Pattern[str] | None = None,
    max_length: int = 256,
) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        _reject(operation, code)
    if value != value.strip() or value != unicodedata.normalize("NFC", value):
        _reject(operation, code)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        _reject(operation, code)
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        _reject(operation, code)
    if pattern is not None and pattern.fullmatch(value) is None:
        _reject(operation, code)
    return value


def _hash(value: Any, *, operation: str) -> str:
    return _strict_string(
        value,
        operation=operation,
        code="ctp_approval_invalid_hash",
        pattern=_HEX64,
        max_length=64,
    )


def _parse_time(value: Any, *, operation: str, code: str) -> datetime:
    text = _strict_string(value, operation=operation, code=code, max_length=32)
    if not text.endswith("Z") or "+" in text or "-" not in text:
        _reject(operation, code)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except (TypeError, ValueError):
        _reject(operation, code)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _reject(operation, code)
    canonical = parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if canonical != text:
        _reject(operation, code)
    return parsed.astimezone(UTC)


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, UnicodeEncodeError, ValueError):
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_artifact")


def canonical_approval_payload_bytes(payload: Mapping[str, Any]) -> bytes:
    """Return the maintained canonical bytes covered by an Ed25519 signature."""

    return _canonical_json(payload)


def _decode_signature(value: Any) -> bytes:
    text = _strict_string(
        value,
        operation=APPROVAL_OPERATION,
        code="ctp_approval_invalid_signature_encoding",
        pattern=_B64URL,
        max_length=86,
    )
    try:
        result = base64.urlsafe_b64decode(text + "==")
    except (ValueError, base64.binascii.Error):
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_signature_encoding")
    if len(result) != 64:
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_signature_encoding")
    return result


def _instrument(value: Any) -> dict[str, str]:
    value = _strict_mapping(value, APPROVAL_OPERATION, "ctp_approval_invalid_instrument_scope")
    if set(value) != _INSTRUMENT_FIELDS:
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_instrument_scope")
    instrument_id = _strict_string(
        value["instrument_id"],
        operation=APPROVAL_OPERATION,
        code="ctp_approval_invalid_instrument_scope",
        max_length=80,
    )
    exchange_id = _strict_string(
        value["exchange_id"],
        operation=APPROVAL_OPERATION,
        code="ctp_approval_invalid_instrument_scope",
        pattern=re.compile(r"^[A-Z][A-Z0-9_]{1,15}$"),
        max_length=16,
    )
    return {"instrument_id": instrument_id, "exchange_id": exchange_id}


def _instrument_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not 2 <= len(value) <= 3:
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_instrument_scope")
    result = [_instrument(item) for item in value]
    identities = {(item["exchange_id"], item["instrument_id"]) for item in result}
    if len(identities) != len(result):
        _reject(APPROVAL_OPERATION, "ctp_approval_duplicate_instrument_scope")
    return result


def _decimal_string(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_budget_limit")
    if any(char in value for char in ("e", "E", "+")):
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_budget_limit")
    try:
        decimal = Decimal(value)
    except (InvalidOperation, ValueError):
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_budget_limit")
    if not decimal.is_finite() or decimal <= 0:
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_budget_limit")
    canonical = format(decimal, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    if canonical != value:
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_budget_limit")
    return canonical


def _normalize_payload(value: Any) -> dict[str, Any]:
    value = _strict_mapping(value, APPROVAL_OPERATION, "ctp_approval_invalid_artifact")
    if value.get("schema_version") == RECOVERY_APPROVAL_SCHEMA_VERSION:
        return _normalize_recovery_payload(value)
    if value.get("schema_version") == ENTRY_APPROVAL_SCHEMA_VERSION:
        return _normalize_entry_payload(value)
    if set(value) != _APPROVAL_FIELDS:
        unknown = set(value) - _APPROVAL_FIELDS
        _reject(
            APPROVAL_OPERATION,
            ("ctp_approval_unknown_payload_key" if unknown else "ctp_approval_missing_field"),
        )
    if value["schema_version"] != APPROVAL_SCHEMA_VERSION:
        _reject(APPROVAL_OPERATION, "ctp_approval_unknown_schema_version")
    if value["algorithm"] != APPROVAL_ALGORITHM:
        _reject(APPROVAL_OPERATION, "ctp_approval_unknown_algorithm")
    result = dict(value)
    for field_name in (
        "approval_id",
        "nonce",
        "issuer_key_id",
        "issuer_role",
        "candidate_id",
        "strategy_id",
        "execution_cycle_id",
        "environment_profile",
        "budget_policy_id",
        "future_reservation_id",
        "account_fingerprint",
    ):
        result[field_name] = _strict_string(
            value[field_name],
            operation=APPROVAL_OPERATION,
            code="ctp_approval_invalid_identity",
            pattern=_SAFE_ID if field_name not in {"account_fingerprint"} else None,
        )
    if value["purpose"] != APPROVAL_PURPOSE:
        _reject(APPROVAL_OPERATION, "ctp_approval_purpose_unsupported")
    result["context_source"] = _strict_string(
        value["context_source"],
        operation=APPROVAL_OPERATION,
        code="ctp_approval_context_untrusted",
        pattern=re.compile(r"^(?:sdk_runtime|deployment_manifest|synthetic_test)$"),
        max_length=32,
    )
    for field_name in _HASH_FIELDS:
        result[field_name] = _hash(value[field_name], operation=APPROVAL_OPERATION)
    result["authorized_instruments"] = _instrument_list(value["authorized_instruments"])
    result["primary_instrument"] = _instrument(value["primary_instrument"])
    if result["primary_instrument"] not in result["authorized_instruments"]:
        _reject(APPROVAL_OPERATION, "ctp_approval_primary_scope_mismatch")
    result["trading_day"] = _strict_string(
        value["trading_day"],
        operation=APPROVAL_OPERATION,
        code="ctp_approval_invalid_identity",
        pattern=_TRADING_DAY,
        max_length=8,
    )
    generation = value["connection_generation"]
    if isinstance(generation, bool) or type(generation) is not int or generation <= 0:
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_identity")
    result["connection_generation"] = generation
    result["budget_limit"] = _decimal_string(value["budget_limit"])
    for field_name in ("issued_at", "not_before", "expires_at"):
        result[field_name] = _parse_time(
            value[field_name],
            operation=APPROVAL_OPERATION,
            code="ctp_approval_invalid_time",
        )
    version = value["revocation_snapshot_version"]
    if isinstance(version, bool) or type(version) is not int or version <= 0:
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_revocation_version")
    result["revocation_snapshot_version"] = version
    issued_at = result["issued_at"]
    not_before = result["not_before"]
    expires_at = result["expires_at"]
    if not issued_at <= not_before <= expires_at:
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_time_window")
    if expires_at - issued_at > _MAX_APPROVAL_LIFETIME:
        _reject(APPROVAL_OPERATION, "ctp_approval_time_window_too_wide")
    for field_name in ("issued_at", "not_before", "expires_at"):
        result[field_name] = _iso(result[field_name])
    return result


def _recovery_quantity_string(value: Any) -> str:
    """Normalize one finite positive action quantity without unit conversion."""

    if not isinstance(value, str) or not value or value != value.strip():
        _reject(APPROVAL_OPERATION, "ctp_recovery_invalid_action")
    if any(char in value for char in ("e", "E", "+")):
        _reject(APPROVAL_OPERATION, "ctp_recovery_invalid_action")
    try:
        quantity = Decimal(value)
    except (InvalidOperation, ValueError):
        _reject(APPROVAL_OPERATION, "ctp_recovery_invalid_action")
    if not quantity.is_finite() or quantity <= 0:
        _reject(APPROVAL_OPERATION, "ctp_recovery_invalid_action")
    canonical = format(quantity, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    if canonical != value:
        _reject(APPROVAL_OPERATION, "ctp_recovery_invalid_action")
    return canonical


def _normalize_recovery_action(value: Any) -> dict[str, Any]:
    value = _strict_mapping(value, APPROVAL_OPERATION, "ctp_recovery_invalid_action")
    if set(value) != _RECOVERY_ACTION_FIELDS:
        _reject(APPROVAL_OPERATION, "ctp_recovery_invalid_action")
    action_id = _strict_string(
        value["action_id"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
        pattern=_SAFE_ID,
    )
    action_kind = _strict_string(
        value["action_kind"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
        pattern=re.compile(r"^(?:close|cancel)$"),
        max_length=6,
    )
    instrument_id = _strict_string(
        value["instrument_id"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
        max_length=80,
    )
    if "." in instrument_id:
        _reject(APPROVAL_OPERATION, "ctp_recovery_invalid_action")
    exchange_id = _strict_string(
        value["exchange_id"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
        pattern=re.compile(r"^[A-Z][A-Z0-9_]{1,15}$"),
        max_length=16,
    )
    side = _strict_string(
        value["side"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
        pattern=re.compile(r"^(?:buy|sell)$"),
        max_length=4,
    )
    position_side = _strict_string(
        value["position_side"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
        pattern=re.compile(r"^(?:long|short)$"),
        max_length=5,
    )
    offset_pattern = (
        r"^(?:close|close_today|close_yesterday)$"
        if action_kind == "close"
        else r"^(?:open|close|close_today|close_yesterday)$"
    )
    offset = _strict_string(
        value["offset"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
        pattern=re.compile(offset_pattern),
        max_length=16,
    )
    quantity_unit = _strict_string(
        value["quantity_unit"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
        pattern=_SAFE_ID,
        max_length=32,
    )
    account_fingerprint = _strict_string(
        value["account_fingerprint"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
    )
    trading_day = _strict_string(
        value["trading_day"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
        pattern=_TRADING_DAY,
        max_length=8,
    )
    generation = value["connection_generation"]
    if isinstance(generation, bool) or type(generation) is not int or generation <= 0:
        _reject(APPROVAL_OPERATION, "ctp_recovery_invalid_action")
    environment_profile = _strict_string(
        value["environment_profile"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
        pattern=_SAFE_ID,
    )
    candidate_id = _strict_string(
        value["candidate_id"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
        pattern=_SAFE_ID,
    )
    execution_cycle_id = _strict_string(
        value["execution_cycle_id"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
        pattern=_SAFE_ID,
    )
    expires_at = _parse_time(
        value["expires_at"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_action",
    )
    return {
        "action_id": action_id,
        "action_kind": action_kind,
        "instrument_id": instrument_id,
        "exchange_id": exchange_id,
        "side": side,
        "position_side": position_side,
        "offset": offset,
        "quantity": _recovery_quantity_string(value["quantity"]),
        "quantity_unit": quantity_unit,
        "account_fingerprint": account_fingerprint,
        "trading_day": trading_day,
        "connection_generation": generation,
        "environment_profile": environment_profile,
        "candidate_id": candidate_id,
        "execution_cycle_id": execution_cycle_id,
        "expires_at": _iso(expires_at),
    }


def _recovery_action_digest_value(actions: list[Mapping[str, Any]]) -> str:
    return hashlib.sha256(_canonical_json(_jsonable(actions))).hexdigest()


def recovery_action_digest(actions: Any) -> str:
    """Return the digest of a complete, normalized recovery action list.

    This helper is useful to an independently operated approval producer.  It
    validates the same exact action shape as the verifier, so a symbol-only or
    opening action cannot be turned into an apparently valid digest.
    """

    if not isinstance(actions, list) or not actions:
        _reject(APPROVAL_OPERATION, "ctp_recovery_invalid_action")
    normalized = [_normalize_recovery_action(item) for item in actions]
    action_ids = [item["action_id"] for item in normalized]
    if len(action_ids) != len(set(action_ids)):
        _reject(APPROVAL_OPERATION, "ctp_recovery_duplicate_action_id")
    return _recovery_action_digest_value(normalized)


def recovery_plan_digest(plan: Any) -> str:
    """Hash the SDK recovery plan while excluding its one-time bearer token.

    The token is checked separately against the live session.  Every other
    plan field, including the query barrier, journal digest, and per-leg
    allowances, remains part of the signed recovery binding.
    """

    if not isinstance(plan, Mapping) or "recovery_token_sha256" not in plan:
        _reject(APPROVAL_OPERATION, "ctp_recovery_plan_required")
    material = {
        key: _jsonable(value) for key, value in plan.items() if key != "recovery_token_sha256"
    }
    return hashlib.sha256(_canonical_json(material)).hexdigest()


def _normalize_recovery_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != _RECOVERY_APPROVAL_FIELDS:
        unknown = set(value) - _RECOVERY_APPROVAL_FIELDS
        _reject(
            APPROVAL_OPERATION,
            ("ctp_approval_unknown_payload_key" if unknown else "ctp_approval_missing_field"),
        )
    if value["purpose"] != RECOVERY_APPROVAL_PURPOSE:
        _reject(APPROVAL_OPERATION, "ctp_approval_purpose_unsupported")
    base = {field_name: value[field_name] for field_name in _APPROVAL_FIELDS}
    # Reuse the ordinary field validators while keeping the recovery schema and
    # purpose in the signed payload.  The trust-root policy is checked against
    # the recovery purpose by the verifier below.
    base["schema_version"] = APPROVAL_SCHEMA_VERSION
    base["purpose"] = APPROVAL_PURPOSE
    result = _normalize_payload(base)
    result["schema_version"] = RECOVERY_APPROVAL_SCHEMA_VERSION
    result["purpose"] = RECOVERY_APPROVAL_PURPOSE
    scope = _strict_string(
        value["recovery_scope_version"],
        operation=APPROVAL_OPERATION,
        code="ctp_recovery_invalid_scope",
        pattern=_SAFE_ID,
        max_length=64,
    )
    if scope != RECOVERY_APPROVAL_SCOPE_VERSION:
        _reject(APPROVAL_OPERATION, "ctp_recovery_invalid_scope")
    result["recovery_scope_version"] = scope
    for field_name in (
        "recovery_plan_sha256",
        "recovery_token_sha256",
        "recovery_action_sha256",
        "receipt_sha256",
        "source_hashes_sha256",
        "ctp_package_sha256",
    ):
        result[field_name] = _hash(value[field_name], operation=APPROVAL_OPERATION)
    actions = value["recovery_actions"]
    if not isinstance(actions, list) or not 1 <= len(actions) <= 128:
        _reject(APPROVAL_OPERATION, "ctp_recovery_invalid_action")
    normalized_actions = [_normalize_recovery_action(item) for item in actions]
    action_ids = [item["action_id"] for item in normalized_actions]
    if len(action_ids) != len(set(action_ids)):
        _reject(APPROVAL_OPERATION, "ctp_recovery_duplicate_action_id")
    authorized = {
        (item["exchange_id"], item["instrument_id"]) for item in result["authorized_instruments"]
    }
    for action in normalized_actions:
        if (action["exchange_id"], action["instrument_id"]) not in authorized:
            _reject(APPROVAL_OPERATION, "ctp_recovery_action_scope_mismatch")
        if action["account_fingerprint"] != result["account_fingerprint"]:
            _reject(APPROVAL_OPERATION, "ctp_recovery_action_identity_mismatch")
        if action["trading_day"] != result["trading_day"]:
            _reject(APPROVAL_OPERATION, "ctp_recovery_action_identity_mismatch")
        if action["connection_generation"] != result["connection_generation"]:
            _reject(APPROVAL_OPERATION, "ctp_recovery_action_identity_mismatch")
        if action["environment_profile"] != result["environment_profile"]:
            _reject(APPROVAL_OPERATION, "ctp_recovery_action_identity_mismatch")
        if action["candidate_id"] != result["candidate_id"]:
            _reject(APPROVAL_OPERATION, "ctp_recovery_action_identity_mismatch")
        if action["execution_cycle_id"] != result["execution_cycle_id"]:
            _reject(APPROVAL_OPERATION, "ctp_recovery_action_identity_mismatch")
        if datetime.fromisoformat(action["expires_at"][:-1] + "+00:00") > datetime.fromisoformat(
            result["expires_at"][:-1] + "+00:00"
        ):
            _reject(APPROVAL_OPERATION, "ctp_recovery_action_expiry_mismatch")
    if value["recovery_action_sha256"] != _recovery_action_digest_value(normalized_actions):
        _reject(APPROVAL_OPERATION, "ctp_recovery_action_digest_mismatch")
    result["recovery_actions"] = normalized_actions
    return result


def _normalize_entry_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the entry-schema ordinary approval that can arm a V2 bundle."""

    if set(value) != _ENTRY_APPROVAL_FIELDS:
        unknown = set(value) - _ENTRY_APPROVAL_FIELDS
        _reject(
            APPROVAL_OPERATION,
            ("ctp_approval_unknown_payload_key" if unknown else "ctp_approval_missing_field"),
        )
    if value["purpose"] != APPROVAL_PURPOSE:
        _reject(APPROVAL_OPERATION, "ctp_approval_purpose_unsupported")
    base = {field_name: value[field_name] for field_name in _APPROVAL_FIELDS}
    # Reuse the ordinary field validators while keeping the entry schema and
    # purpose in the signed payload.
    base["schema_version"] = APPROVAL_SCHEMA_VERSION
    result = _normalize_payload(base)
    result["schema_version"] = ENTRY_APPROVAL_SCHEMA_VERSION
    for field_name in ("receipt_sha256", "source_hashes_sha256", "ctp_package_sha256"):
        result[field_name] = _hash(value[field_name], operation=APPROVAL_OPERATION)
    return result


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_context(value: Any) -> dict[str, Any]:
    sealed = type(value) is CtpExecutionApprovalContext
    if sealed:
        if value._seal is not _CONTEXT_SEAL:
            _reject(APPROVAL_OPERATION, "ctp_approval_context_untrusted")
        value = _thaw(value.values)
    else:
        value = _strict_mapping(value, APPROVAL_OPERATION, "ctp_approval_context_required")
    if set(value) != _CONTEXT_FIELDS:
        _reject(APPROVAL_OPERATION, "ctp_approval_context_incomplete")
    source = _strict_string(
        value["source"],
        operation=APPROVAL_OPERATION,
        code="ctp_approval_context_untrusted",
        pattern=re.compile(r"^(?:sdk_runtime|deployment_manifest|synthetic_test)$"),
        max_length=32,
    )
    if source != "synthetic_test" and not sealed:
        _reject(APPROVAL_OPERATION, "ctp_approval_context_untrusted")
    result = {"source": source}
    payload_like = {
        field_name: value[field_name] for field_name in _CONTEXT_FIELDS if field_name != "source"
    }
    normalized = _normalize_payload(
        {
            **payload_like,
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "algorithm": APPROVAL_ALGORITHM,
            "approval_id": "context-approval",
            "nonce": "context-nonce",
            "issuer_key_id": "context-issuer",
            "issuer_role": "context-role",
            "purpose": APPROVAL_PURPOSE,
            "context_source": source,
            "issued_at": _iso(datetime.now(UTC) - timedelta(seconds=1)),
            "not_before": _iso(datetime.now(UTC) - timedelta(seconds=1)),
            "expires_at": _iso(datetime.now(UTC) + timedelta(days=1)),
            "revocation_snapshot_version": 1,
        }
    )
    for field_name in _CONTEXT_FIELDS - {"source"}:
        result[field_name] = normalized[field_name]
    return result


def _normalize_revocation(value: Any) -> dict[str, Any]:
    value = _strict_mapping(value, APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
    if set(value) != _REVOCATION_FIELDS:
        _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
    version = value["version"]
    if isinstance(version, bool) or type(version) is not int or version <= 0:
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_revocation_version")
    issued_at = _parse_time(
        value["issued_at"],
        operation=APPROVAL_OPERATION,
        code="ctp_approval_trust_root_invalid",
    )
    expires_at = _parse_time(
        value["expires_at"],
        operation=APPROVAL_OPERATION,
        code="ctp_approval_trust_root_invalid",
    )
    if not issued_at <= expires_at or expires_at - issued_at > _MAX_APPROVAL_LIFETIME:
        _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
    result = {
        "version": version,
        "issued_at": _iso(issued_at),
        "expires_at": _iso(expires_at),
    }
    for field_name in ("revoked_approval_ids", "revoked_nonces"):
        values = value[field_name]
        if not isinstance(values, list):
            _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
        normalized = [
            _strict_string(
                item,
                operation=APPROVAL_OPERATION,
                code="ctp_approval_trust_root_invalid",
                pattern=_SAFE_ID,
            )
            for item in values
        ]
        if len(normalized) != len(set(normalized)) or normalized != sorted(normalized):
            _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
        result[field_name] = normalized
    return result


def _decode_public_key(value: Any) -> bytes:
    text = _strict_string(
        value,
        operation=APPROVAL_OPERATION,
        code="ctp_approval_trust_root_invalid",
        max_length=43,
    )
    if not re.fullmatch(r"^[A-Za-z0-9_-]{43}$", text):
        _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
    try:
        key = base64.urlsafe_b64decode(text + "=")
    except (ValueError, base64.binascii.Error):
        _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
    if len(key) != 32:
        _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
    return key


def _normalize_trust_root(value: Any) -> dict[str, Any]:
    if value is None:
        _reject(APPROVAL_OPERATION, "BLOCKED_OPERATOR_TRUST_ROOT")
    value = _strict_mapping(value, APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
    if set(value) != _TRUST_ROOT_FIELDS:
        _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
    if value["schema_version"] != TRUST_ROOT_SCHEMA_VERSION:
        _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
    keys = value["keys"]
    if not isinstance(keys, dict) or not keys:
        _reject(APPROVAL_OPERATION, "BLOCKED_OPERATOR_TRUST_ROOT")
    normalized_keys = {}
    for key_id, entry in keys.items():
        key_id = _strict_string(
            key_id,
            operation=APPROVAL_OPERATION,
            code="ctp_approval_trust_root_invalid",
            pattern=_SAFE_ID,
        )
        entry = _strict_mapping(entry, APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
        if set(entry) != _TRUST_KEY_FIELDS:
            _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
        role = _strict_string(
            entry["role"],
            operation=APPROVAL_OPERATION,
            code="ctp_approval_trust_root_invalid",
            pattern=_SAFE_ID,
        )
        purposes = entry["purposes"]
        if (
            not isinstance(purposes, list)
            or not purposes
            or any(not isinstance(item, str) or item != item.strip() for item in purposes)
        ):
            _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
        if len(purposes) != len(set(purposes)):
            _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
        normalized_keys[key_id] = {
            "public_key": _decode_public_key(entry["public_key"]),
            "role": role,
            "purposes": tuple(purposes),
            "not_before": _parse_time(
                entry["not_before"],
                operation=APPROVAL_OPERATION,
                code="ctp_approval_trust_root_invalid",
            ),
            "expires_at": _parse_time(
                entry["expires_at"],
                operation=APPROVAL_OPERATION,
                code="ctp_approval_trust_root_invalid",
            ),
        }
        if not normalized_keys[key_id]["not_before"] <= normalized_keys[key_id]["expires_at"]:
            _reject(APPROVAL_OPERATION, "ctp_approval_trust_root_invalid")
    revocation = _normalize_revocation(value["revocation_snapshot"])
    result = {
        "schema_version": TRUST_ROOT_SCHEMA_VERSION,
        "keys": normalized_keys,
        "revocation_snapshot": revocation,
    }
    return result


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, bytes):
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
    return value


@dataclass(frozen=True, slots=True, init=False)
class CtpExecutionApprovalContext:
    """SDK-sealed context whose runtime identity was collected locally.

    A plain mapping is intentionally accepted only for ``synthetic_test``
    verification.  Production-shaped sources must be created by the SDK after
    it has inspected the loaded packages, native module, and live CTP session;
    copying those values into a mapping cannot establish runtime provenance.
    """

    values: Mapping[str, Any]
    _seal: object = field(repr=False, compare=False)
    _owner: object = field(repr=False, compare=False)
    _refresh: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        values: Mapping[str, Any],
        _seal: object,
        _owner: object = None,
        _refresh: object = None,
    ) -> None:
        if _seal is not _CONTEXT_SEAL:
            raise TypeError("SDK-collected CTP approval context required")
        object.__setattr__(self, "values", _freeze(values))
        object.__setattr__(self, "_seal", _seal)
        object.__setattr__(self, "_owner", _owner)
        object.__setattr__(self, "_refresh", _refresh)

    @property
    def source(self) -> str:
        return str(self.values["source"])

    def as_dict(self) -> dict[str, Any]:
        """Return a detached view for signing or audit inspection."""

        return _thaw(self.values)


def _new_runtime_context(
    values: Mapping[str, Any], *, owner: object = None, refresh: object = None
) -> CtpExecutionApprovalContext:
    return CtpExecutionApprovalContext(
        values=values,
        _seal=_CONTEXT_SEAL,
        _owner=owner,
        _refresh=refresh,
    )


def _refresh_runtime_context(
    value: CtpExecutionApprovalContext, owner: object
) -> CtpExecutionApprovalContext:
    """Recollect one SDK-owned context before a durable approval transition."""
    if (
        type(value) is not CtpExecutionApprovalContext
        or value._seal is not _CONTEXT_SEAL
        or value._owner is not owner
        or not callable(value._refresh)
    ):
        _reject("redeem_ctp_execution_approval", "ctp_approval_context_untrusted")
    try:
        refreshed = value._refresh()
    except NormalizedApiError:
        raise
    except Exception:
        _reject(
            "redeem_ctp_execution_approval",
            "ctp_approval_context_refresh_unavailable",
        )
    if (
        type(refreshed) is not CtpExecutionApprovalContext
        or refreshed._seal is not _CONTEXT_SEAL
        or refreshed._owner is not owner
    ):
        _reject(
            "redeem_ctp_execution_approval",
            "ctp_approval_context_refresh_unavailable",
        )
    return refreshed


@dataclass(frozen=True, slots=True, init=False)
class CtpExecutionApproval:
    """Immutable, cryptographically verified approval evidence."""

    payload: Mapping[str, Any]
    payload_sha256: str
    trust_root_sha256: str
    signature: bytes = field(repr=False, compare=False)
    revocation_snapshot: Mapping[str, Any]
    _seal: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        payload: Mapping[str, Any],
        payload_sha256: str,
        trust_root_sha256: str,
        signature: bytes,
        revocation_snapshot: Mapping[str, Any],
        _seal: object,
    ) -> None:
        if _seal is not _CAPABILITY_SEAL:
            raise TypeError("verified CTP approval result required")
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "payload_sha256", payload_sha256)
        object.__setattr__(self, "trust_root_sha256", trust_root_sha256)
        object.__setattr__(self, "signature", signature)
        object.__setattr__(self, "revocation_snapshot", revocation_snapshot)
        object.__setattr__(self, "_seal", _seal)

    @property
    def approval_id(self) -> str:
        return str(self.payload["approval_id"])

    @property
    def nonce(self) -> str:
        return str(self.payload["nonce"])

    @property
    def purpose(self) -> str:
        return str(self.payload["purpose"])

    @property
    def schema_version(self) -> str:
        return str(self.payload["schema_version"])

    @property
    def expires_at(self) -> str:
        return str(self.payload["expires_at"])

    @property
    def not_before(self) -> str:
        return str(self.payload["not_before"])

    @property
    def revocation_snapshot_version(self) -> int:
        return int(self.payload["revocation_snapshot_version"])

    @property
    def recovery_plan_sha256(self) -> str | None:
        value = self.payload.get("recovery_plan_sha256")
        return str(value) if value is not None else None

    @property
    def recovery_scope_version(self) -> str | None:
        value = self.payload.get("recovery_scope_version")
        return str(value) if value is not None else None

    @property
    def recovery_token_sha256(self) -> str | None:
        value = self.payload.get("recovery_token_sha256")
        return str(value) if value is not None else None

    @property
    def recovery_action_sha256(self) -> str | None:
        value = self.payload.get("recovery_action_sha256")
        return str(value) if value is not None else None

    @property
    def recovery_actions(self) -> tuple[Mapping[str, Any], ...]:
        value = self.payload.get("recovery_actions", ())
        return tuple(value)

    @property
    def bindings(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                field_name: self.payload[field_name]
                for field_name in _CONTEXT_FIELDS
                if field_name != "source" and field_name in self.payload
            }
        )

    def journal_record(self) -> dict[str, Any]:
        """Return only JSON-safe audit fields for the existing execution journal."""

        result = {
            key: _jsonable(value)
            for key, value in self.payload.items()
            if key not in {"schema_version", "algorithm"}
        }
        result.update(
            {
                "approval_payload": _jsonable(self.payload),
                "approval_sha256": self.payload_sha256,
                "trust_root_sha256": self.trust_root_sha256,
                "revocation_snapshot_sha256": hashlib.sha256(
                    _canonical_json(_jsonable(self.revocation_snapshot))
                ).hexdigest(),
                "revocation_snapshot": _jsonable(self.revocation_snapshot),
            }
        )
        return result


class CtpExecutionApprovalCapability:
    """Opaque process-local result of a durably consumed approval."""

    __slots__ = (
        "_seal",
        "_owner",
        "_approval",
        "_approval_id",
        "_nonce",
        "_purpose",
        "_bindings",
        "_recovery_used",
        "_entry_used",
        "_settlement_used",
        "_context",
    )

    def __init__(
        self,
        *,
        seal: object,
        owner: object,
        approval: CtpExecutionApproval,
        context: Mapping[str, Any] | CtpExecutionApprovalContext | None = None,
    ):
        if seal is not _CAPABILITY_SEAL:
            raise TypeError("opaque CTP approval capability required")
        self._seal = seal
        self._owner = owner
        self._approval = approval
        self._approval_id = approval.approval_id
        self._nonce = approval.nonce
        self._purpose = approval.purpose
        self._bindings = approval.bindings
        self._recovery_used = False
        self._entry_used = False
        self._settlement_used = False
        # Keep the SDK-sealed collector object alive with the capability.  A
        # sealed context retains the deployment-owned Path/configuration
        # references in its private refresh closure; recovery re-collects
        # those references before native arm and each managed write.
        self._context = context

    @property
    def approval_id(self) -> str:
        return self._approval_id

    @property
    def nonce(self) -> str:
        return self._nonce

    @property
    def purpose(self) -> str:
        return self._purpose

    @property
    def bindings(self) -> Mapping[str, Any]:
        return self._bindings

    @property
    def schema_version(self) -> str:
        return self._approval.schema_version

    @property
    def expires_at(self) -> str:
        return self._approval.expires_at

    @property
    def revocation_snapshot_version(self) -> int:
        return self._approval.revocation_snapshot_version

    @property
    def recovery_plan_sha256(self) -> str | None:
        return self._approval.recovery_plan_sha256

    @property
    def recovery_scope_version(self) -> str | None:
        return self._approval.recovery_scope_version

    @property
    def recovery_token_sha256(self) -> str | None:
        return self._approval.recovery_token_sha256

    @property
    def recovery_action_sha256(self) -> str | None:
        return self._approval.recovery_action_sha256

    @property
    def recovery_actions(self) -> tuple[Mapping[str, Any], ...]:
        return self._approval.recovery_actions


def _new_capability(
    approval: CtpExecutionApproval,
    owner: object,
    context: Mapping[str, Any] | CtpExecutionApprovalContext | None = None,
) -> CtpExecutionApprovalCapability:
    return CtpExecutionApprovalCapability(
        seal=_CAPABILITY_SEAL, owner=owner, approval=approval, context=context
    )


def _verify_signature(signature: bytes, payload_bytes: bytes, public_key: bytes) -> None:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except (ImportError, ModuleNotFoundError):
        _reject(APPROVAL_OPERATION, "ctp_approval_cryptography_unavailable")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload_bytes)
    except Exception:
        _reject(APPROVAL_OPERATION, "ctp_approval_signature_invalid")


def _coerce_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        _reject(APPROVAL_OPERATION, "ctp_approval_clock_untrusted")
    return value.astimezone(UTC)


def _validate_time_window(payload: Mapping[str, Any], now: datetime) -> None:
    issued_at = datetime.fromisoformat(payload["issued_at"][:-1] + "+00:00")
    not_before = datetime.fromisoformat(payload["not_before"][:-1] + "+00:00")
    expires_at = datetime.fromisoformat(payload["expires_at"][:-1] + "+00:00")
    if now < not_before:
        _reject(APPROVAL_OPERATION, "ctp_approval_not_yet_valid")
    if now >= expires_at:
        _reject(APPROVAL_OPERATION, "ctp_approval_expired")
    if issued_at > now + timedelta(seconds=1):
        _reject(APPROVAL_OPERATION, "ctp_approval_issued_in_future")


def _compare_context(payload: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    for field_name in _CONTEXT_FIELDS - {"source"}:
        left = payload[field_name]
        right = context[field_name]
        if field_name == "authorized_instruments":
            left = {(item["exchange_id"], item["instrument_id"]) for item in left}
            right = {(item["exchange_id"], item["instrument_id"]) for item in right}
        if left != right:
            _reject(APPROVAL_OPERATION, "ctp_approval_context_mismatch")


def verify_ctp_execution_approval(
    artifact: bytes | str,
    *,
    trust_root: Mapping[str, Any] | None,
    context: Mapping[str, Any] | CtpExecutionApprovalContext,
    _now: datetime | None = None,
) -> CtpExecutionApproval:
    """Verify an independently signed approval against deployment state.

    ``trust_root`` and ``context`` are mandatory deployment inputs.  A plain
    context mapping is accepted only for ``synthetic_test``; runtime and
    deployment sources must be the SDK-sealed context produced after local
    identity collection.  The payload cannot supply either a public key or an
    identity shortcut.  The leading underscore on ``_now`` marks deterministic
    test injection; normal callers always use the SDK's UTC clock.
    """

    normalized_root = _normalize_trust_root(trust_root)
    artifact_value = _parse_json_artifact(artifact)
    if set(artifact_value) != _ARTIFACT_FIELDS:
        _reject(APPROVAL_OPERATION, "ctp_approval_invalid_artifact")
    payload_value = artifact_value.get("payload")
    payload_schema = (
        payload_value.get("schema_version") if isinstance(payload_value, Mapping) else None
    )
    if (
        artifact_value["schema_version"] != payload_schema
        or artifact_value["schema_version"]
        not in {
            APPROVAL_SCHEMA_VERSION,
            RECOVERY_APPROVAL_SCHEMA_VERSION,
            ENTRY_APPROVAL_SCHEMA_VERSION,
        }
        or artifact_value["algorithm"] != APPROVAL_ALGORITHM
    ):
        _reject(APPROVAL_OPERATION, "ctp_approval_unknown_schema_version")
    normalized_payload = _normalize_payload(artifact_value["payload"])
    signature = _decode_signature(artifact_value["signature"])
    current = _coerce_now(_now)
    snapshot = normalized_root["revocation_snapshot"]
    snapshot_expires = datetime.fromisoformat(snapshot["expires_at"][:-1] + "+00:00")
    snapshot_issued = datetime.fromisoformat(snapshot["issued_at"][:-1] + "+00:00")
    if current < snapshot_issued or current >= snapshot_expires:
        _reject(APPROVAL_OPERATION, "ctp_approval_revocation_snapshot_stale")
    if normalized_payload["revocation_snapshot_version"] != snapshot["version"]:
        _reject(APPROVAL_OPERATION, "ctp_approval_revocation_version_stale")
    _validate_time_window(normalized_payload, current)
    key_id = normalized_payload["issuer_key_id"]
    key = normalized_root["keys"].get(key_id)
    if key is None:
        _reject(APPROVAL_OPERATION, "ctp_approval_issuer_untrusted")
    key_not_before = key["not_before"]
    key_expires = key["expires_at"]
    if current < key_not_before or current >= key_expires:
        _reject(APPROVAL_OPERATION, "ctp_approval_issuer_key_expired")
    if (
        normalized_payload["issuer_role"] != key["role"]
        or normalized_payload["purpose"] not in key["purposes"]
    ):
        _reject(APPROVAL_OPERATION, "ctp_approval_issuer_policy_mismatch")
    if (
        normalized_payload["approval_id"] in snapshot["revoked_approval_ids"]
        or normalized_payload["nonce"] in snapshot["revoked_nonces"]
    ):
        _reject(APPROVAL_OPERATION, "ctp_approval_revoked")
    payload_bytes = canonical_approval_payload_bytes(normalized_payload)
    _verify_signature(signature, payload_bytes, key["public_key"])
    normalized_context = _normalize_context(context)
    _compare_context(normalized_payload, normalized_context)
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    root_sha256 = hashlib.sha256(_canonical_json(_jsonable(normalized_root))).hexdigest()
    return CtpExecutionApproval(
        payload=_freeze(normalized_payload),
        payload_sha256=payload_sha256,
        trust_root_sha256=root_sha256,
        signature=signature,
        revocation_snapshot=_freeze(snapshot),
        _seal=_CAPABILITY_SEAL,
    )


def verify_ctp_execution_recovery_approval(
    artifact: bytes | str,
    *,
    trust_root: Mapping[str, Any] | None,
    context: Mapping[str, Any] | CtpExecutionApprovalContext,
    _now: datetime | None = None,
) -> CtpExecutionApproval:
    """Verify a versioned recovery-purpose artifact through the same verifier.

    The returned object is still an ordinary opaque approval result; callers
    must durably redeem it before the SDK can consider it for recovery arming.
    This named facade makes the purpose boundary explicit for integrations.
    """

    approval = verify_ctp_execution_approval(
        artifact,
        trust_root=trust_root,
        context=context,
        _now=_now,
    )
    if approval.purpose != RECOVERY_APPROVAL_PURPOSE:
        _reject(APPROVAL_OPERATION, "ctp_recovery_purpose_required")
    return approval


def revalidate_ctp_execution_approval(
    approval: CtpExecutionApproval,
    *,
    trust_root: Mapping[str, Any] | None,
    context: Mapping[str, Any] | CtpExecutionApprovalContext,
    _now: datetime | None = None,
) -> CtpExecutionApproval:
    """Recheck a verified result immediately before durable redemption."""

    if type(approval) is not CtpExecutionApproval or approval._seal is not _CAPABILITY_SEAL:
        _reject("redeem_ctp_execution_approval", "ctp_approval_opaque_required")
    artifact = {
        "schema_version": approval.payload["schema_version"],
        "algorithm": APPROVAL_ALGORITHM,
        "payload": _thaw(approval.payload),
        "signature": base64.urlsafe_b64encode(approval.signature).decode("ascii").rstrip("="),
    }
    return verify_ctp_execution_approval(
        _canonical_json(artifact), trust_root=trust_root, context=context, _now=_now
    )


def current_ctp_execution_revocation_snapshot(
    trust_root: Mapping[str, Any] | None,
    *,
    _now: datetime | None = None,
) -> tuple[Mapping[str, Any], str]:
    """Validate and return the deployment's current revocation snapshot.

    This helper performs no I/O or fetching.  Persisting the returned snapshot
    is the caller's responsibility and uses the existing execution journal.
    """

    normalized_root = _normalize_trust_root(trust_root)
    current = _coerce_now(_now)
    snapshot = normalized_root["revocation_snapshot"]
    issued_at = datetime.fromisoformat(snapshot["issued_at"][:-1] + "+00:00")
    expires_at = datetime.fromisoformat(snapshot["expires_at"][:-1] + "+00:00")
    if current < issued_at or current >= expires_at:
        _reject(APPROVAL_OPERATION, "ctp_approval_revocation_snapshot_stale")
    frozen = _freeze(snapshot)
    snapshot_hash = hashlib.sha256(_canonical_json(_jsonable(frozen))).hexdigest()
    return frozen, snapshot_hash


__all__ = [
    "APPROVAL_ALGORITHM",
    "APPROVAL_PURPOSE",
    "APPROVAL_SCHEMA_VERSION",
    "RECOVERY_APPROVAL_PURPOSE",
    "RECOVERY_APPROVAL_SCHEMA_VERSION",
    "RECOVERY_APPROVAL_SCOPE_VERSION",
    "CtpExecutionApproval",
    "CtpExecutionApprovalCapability",
    "CtpExecutionApprovalContext",
    "canonical_approval_payload_bytes",
    "current_ctp_execution_revocation_snapshot",
    "recovery_action_digest",
    "recovery_plan_digest",
    "revalidate_ctp_execution_approval",
    "verify_ctp_execution_approval",
    "verify_ctp_execution_recovery_approval",
    "_new_capability",
]
