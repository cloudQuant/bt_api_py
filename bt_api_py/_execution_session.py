"""Private execution state owned by BtApi, independent of any trading engine.

The optional session persists intent before dispatch, reconciles without retrying
placement, and merges REST and stream reports using cumulative watermarks.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
import unicodedata
import uuid
from collections import defaultdict, deque
from collections.abc import Mapping
from contextlib import contextmanager, suppress
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from threading import RLock

from ._contracts.errors import NormalizedApiError
from ._contracts.models import QueryOrderRequest

_TERMINAL = {"completed", "canceled", "expired", "rejected"}
_STATUSES = _TERMINAL | {"submitted", "accepted", "partial"}
_NONTERMINAL_PROGRESS = {"submitted": 0, "accepted": 1, "partial": 2}
_IDENTITY = (
    "exchange_id",
    "front_id",
    "session_id",
    "order_ref",
    "position_id",
    "position_side",
    "offset",
    "quantity_unit",
    "position_mode",
    "trading_day",
    "execution_cycle_id",
    "execution_role",
    "strategy_identity_sha256",
)
_LEDGER_SEMANTIC_IDENTITY = frozenset(
    {
        "side",
        "position_side",
        "offset",
        "quantity_unit",
        "position_mode",
        "execution_cycle_id",
        "execution_role",
        "strategy_identity_sha256",
    }
)
_EXPLICIT_IDENTITY_FIELDS = "_explicit_identity_fields"
_CONFIG = {
    "order_journal": None,
    "account_risk_state": None,
    "require_order_journal": True,
    "market_data_only": False,
    "order_poll_interval": 0.2,
    "account_currency": None,
    "account_currencies": {},
    "account_ids": {},
    "required_environments": {},
    "strategy_id": "default",
    "strategy_identity_sha256": None,
    "account_maximum_loss_bps": None,
    "account_risk_max_age_seconds": "2",
}

_JOURNAL_SCHEMA_VERSION = 2
_CRYPTO_PROVIDERS = {"OKX", "BINANCE"}
_RISK_TRANSITION_EVENTS = {
    "risk_breach",
    "risk_reset_prepared",
    "risk_reset_committed",
}

_EXECUTION_ARM_FIELDS = (
    "account_fingerprint",
    "trading_day",
    "instrument",
    "connection_generation",
    "environment_profile",
    "receipt_sha256",
    "native_sha256",
    "ctp_package_sha256",
    "source_hashes_sha256",
    "dependency_hashes_sha256",
    "preflight_sha256",
)
_EXECUTION_ARM_CONTEXT_FIELDS = (
    "account_fingerprint",
    "trading_day",
    "connection_generation",
    "environment_profile",
    "native_sha256",
    "ctp_package_sha256",
)
_EXECUTION_ARM_HASH_FIELDS = (
    "receipt_sha256",
    "native_sha256",
    "ctp_package_sha256",
    "source_hashes_sha256",
    "dependency_hashes_sha256",
    "preflight_sha256",
)
_CTP_EXCHANGES = {"CFFEX", "CZCE", "DCE", "GFEX", "INE", "SHFE"}
_CTP_EXCHANGE_ALIASES = {"ZCE": "CZCE"}
_CTP_INSTRUMENT_RE = re.compile(r"^[A-Z]{1,3}[0-9]{3,4}$")
_ARM_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_EXECUTION_ROLES = frozenset({"entry", "exit", "recovery_exit"})
_RECOVERY_POSITION_KEYS = (
    "long_today",
    "long_yesterday",
    "short_today",
    "short_yesterday",
)


def _canonical_ctp_exchange(value):
    exchange = str(value or "").strip().upper()
    exchange = _CTP_EXCHANGE_ALIASES.get(exchange, exchange)
    return exchange if exchange in _CTP_EXCHANGES else ""


def _canonical_ctp_instrument(value, exchange_id=None):
    """Return ``EXCHANGE.INSTRUMENT`` for one strictly scoped CTP contract.

    Public write requests may use either prefix/suffix notation or a bare
    instrument paired with ``exchange_id``.  The arm proof itself must already
    equal the returned canonical representation.  CZCE's four-digit alias is
    collapsed to the native three-digit year/month representation.
    """
    text = str(value or "").strip().upper()
    supplied_exchange = _canonical_ctp_exchange(exchange_id)
    if not text or (exchange_id not in (None, "") and not supplied_exchange):
        return ""
    parts = text.split(".")
    if len(parts) == 1:
        exchange = supplied_exchange
        instrument = parts[0]
    elif len(parts) == 2:
        first_exchange = _canonical_ctp_exchange(parts[0])
        last_exchange = _canonical_ctp_exchange(parts[1])
        if bool(first_exchange) == bool(last_exchange):
            return ""
        exchange = first_exchange or last_exchange
        instrument = parts[1] if first_exchange else parts[0]
        if supplied_exchange and supplied_exchange != exchange:
            return ""
    else:
        return ""
    if not exchange or not _CTP_INSTRUMENT_RE.fullmatch(instrument):
        return ""
    letters = instrument.rstrip("0123456789")
    digits = instrument[len(letters) :]
    if exchange == "CZCE" and len(digits) == 4:
        digits = digits[-3:]
    return f"{exchange}.{letters}{digits}"


def _arm_revocation_reason(value):
    reason = str(value or "execution_arm_revoked").strip().lower()
    return reason if _ARM_REASON_RE.fullmatch(reason) else "execution_arm_revoked"


def _execution_arm_proof(value):
    """Validate and hash the closed Iteration 22 execution-arm contract."""
    operation = "arm_execution_from_preflight"
    if not isinstance(value, Mapping) or set(value) != set(_EXECUTION_ARM_FIELDS):
        raise NormalizedApiError(
            operation, "invalid_execution_arm_proof", definite_reject=True
        )
    proof = {field: value[field] for field in _EXECUTION_ARM_FIELDS}
    for field in (
        "account_fingerprint",
        "trading_day",
        "instrument",
        "environment_profile",
    ):
        item = proof[field]
        if not isinstance(item, str) or not item or item != item.strip():
            raise NormalizedApiError(
                operation, "invalid_execution_arm_proof", definite_reject=True
            )
    canonical_instrument = _canonical_ctp_instrument(proof["instrument"])
    if not canonical_instrument or proof["instrument"] != canonical_instrument:
        raise NormalizedApiError(
            operation, "invalid_execution_arm_proof", definite_reject=True
        )
    account_fingerprint = proof["account_fingerprint"]
    account_digest = account_fingerprint.removeprefix("acct_")
    if (
        account_fingerprint != account_fingerprint.lower()
        or not account_fingerprint.startswith("acct_")
        or len(account_digest) != 16
        or any(char not in "0123456789abcdef" for char in account_digest)
    ):
        raise NormalizedApiError(
            operation, "invalid_execution_arm_proof", definite_reject=True
        )
    generation = proof["connection_generation"]
    if type(generation) is not int or generation <= 0:
        raise NormalizedApiError(
            operation, "invalid_execution_arm_proof", definite_reject=True
        )
    trading_day = proof["trading_day"]
    try:
        parsed_day = time.strptime(trading_day, "%Y%m%d")
    except ValueError:
        raise NormalizedApiError(
            operation, "invalid_execution_arm_proof", definite_reject=True
        ) from None
    if time.strftime("%Y%m%d", parsed_day) != trading_day:
        raise NormalizedApiError(
            operation, "invalid_execution_arm_proof", definite_reject=True
        )
    for field in _EXECUTION_ARM_HASH_FIELDS:
        digest = proof[field]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or digest != digest.lower()
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise NormalizedApiError(
                operation, "invalid_execution_arm_proof", definite_reject=True
            )
    encoded = json.dumps(
        proof,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return proof, hashlib.sha256(encoded).hexdigest()


def _normalize_label(value):
    """Return the stable, non-secret identity label used by crypto ledgers."""
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _identity_core(identity):
    return {
        "provider": str(identity.get("provider") or "").upper(),
        "environment": str(identity.get("environment") or "").strip().lower(),
        "account_id": _normalize_label(identity.get("account_id")),
    }


def _normalized_ledger_identity(identity):
    result = _identity_core(identity)
    fingerprint = str(identity.get("credential_fingerprint") or "").lower()
    if fingerprint:
        result["credential_fingerprint"] = fingerprint
    account_fingerprint = str(identity.get("account_fingerprint") or "").lower()
    if account_fingerprint:
        result["account_fingerprint"] = account_fingerprint
    return result


def _recorded_identity_matches(recorded, expected):
    """Allow a one-way upgrade from a legacy label-only registry record."""
    if not isinstance(recorded, dict):
        return False
    recorded = _normalized_ledger_identity(recorded)
    expected = _normalized_ledger_identity(expected)
    if _identity_core(recorded) != _identity_core(expected):
        return False
    recorded_fingerprint = recorded.get("credential_fingerprint")
    expected_fingerprint = expected.get("credential_fingerprint")
    if recorded_fingerprint is not None and (
        expected_fingerprint is None or recorded_fingerprint != expected_fingerprint
    ):
        return False
    recorded_account = recorded.get("account_fingerprint")
    expected_account = expected.get("account_fingerprint")
    if recorded_account is None:
        return True
    if expected_account is None:
        return False
    return recorded_account == expected_account


def _identity_registry_digests(identity):
    """Return the authoritative scopes for one physical writer.

    Authenticated crypto identities use an SDK-derived public-key fingerprint,
    so a caller-controlled account label cannot create another writer.  A key
    rotation cannot be linked to the old physical account without a venue-
    authenticated immutable account identifier; callers must reconcile and
    explicitly migrate that boundary.
    """
    core = _identity_core(identity)
    fingerprint = str(identity.get("credential_fingerprint") or "").lower()
    account_fingerprint = str(identity.get("account_fingerprint") or "").lower()
    if account_fingerprint:
        materials = [("ctp_account", core["provider"], account_fingerprint)]
    else:
        materials = [
            (
                "credential" if fingerprint else "account",
                core["provider"],
                core["environment"],
                fingerprint or core["account_id"],
            )
        ]
    return tuple(
        hashlib.sha256("\0".join(material).encode("utf-8")).hexdigest()
        for material in materials
    )


def _ledger_registry_root():
    """Return the process-independent registry for authenticated execution ledgers."""
    return Path.home() / ".bt_api_py" / "execution-ledgers"


def _identity_digest(identity):
    """Return the authoritative registry digest for an execution identity."""
    return _identity_registry_digests(identity)[0]


def _credential_account_id(provider, fingerprint):
    """Derive an opaque stable account authority from non-secret key material."""
    return f"{str(provider).strip().casefold()}-credential-{str(fingerprint).lower()}"


def _default_journal_path(identities):
    """Return one strategy-independent path for a configured identity bundle."""
    normalized = [_normalized_ledger_identity(identity) for identity in identities]
    normalized.sort(
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))
    )
    material = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    return (
        _ledger_registry_root().parent / "execution-journals" / f"{digest}.jsonl"
    ).resolve()


def _lock_file(path, operation, code):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(fd, "r+b", buffering=0)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                handle.write(b"\0")
            handle.seek(0)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except Exception:
        handle.close()
        raise NormalizedApiError(operation, code, definite_reject=True) from None


def _read_locked_json(handle, operation):
    handle.seek(0)
    raw = handle.read()
    if not raw or raw == b"\0":
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        raise NormalizedApiError(
            operation, "unreadable_ledger_registry", definite_reject=True
        ) from None


def _write_locked_json(handle, value):
    payload = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    handle.seek(0)
    handle.truncate()
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())


def _number(value, default=0.0):
    result = default if value in (None, "") else float(value)
    if not math.isfinite(result):
        raise NormalizedApiError("execution", "non_finite_number")
    return result


def _semantic_identity_value(key, value, exchange_name=None):
    """Canonicalize order-intent identity before comparing remote evidence."""
    if value is None or value == "":
        return value
    value = getattr(value, "value", value)
    if key == "position_mode" and isinstance(value, bool):
        return "dual_side" if value else "net"
    text = str(value).strip().casefold().replace("-", "_").replace(" ", "_")
    if key == "position_side":
        return {"both": "net", "buy": "long", "sell": "short"}.get(text, text)
    if key == "offset":
        return {
            "closetoday": "close_today",
            "closeyesterday": "close_yesterday",
        }.get(text, text)
    if key == "position_mode":
        return {
            "net_mode": "net",
            "oneway": "net",
            "one_way": "net",
            "long_short_mode": "dual_side",
            "hedge": "dual_side",
            "hedge_mode": "dual_side",
        }.get(text, text)
    if key == "quantity_unit":
        text = {"contract": "contracts", "lot": "lots"}.get(text, text)
        if text == "native":
            venue = str(exchange_name or "").strip().upper()
            if venue.startswith("MT5___"):
                return "lots"
            if venue.startswith("CTP___") or venue == "OKX___SWAP":
                return "contracts"
            if venue:
                return "base"
        return text
    return text


def _explicit_identity_fields(row):
    """Return semantic fields backed by this row rather than mapper defaults."""
    fields = row.get(_EXPLICIT_IDENTITY_FIELDS)
    if fields is None:
        return {
            key for key in _LEDGER_SEMANTIC_IDENTITY if row.get(key) not in (None, "")
        }
    if not isinstance(fields, (list, tuple, set, frozenset)):
        return set()
    return {str(key) for key in fields if key in _LEDGER_SEMANTIC_IDENTITY}


def session_config(config):
    if not isinstance(config, dict) or set(config) - set(_CONFIG):
        raise NormalizedApiError("configure_execution", "invalid_execution_config")
    result = {**_CONFIG, **config}
    if any(
        type(result[key]) is not bool
        for key in ("market_data_only", "require_order_journal")
    ):
        raise NormalizedApiError("configure_execution", "invalid_execution_config")
    result["account_currencies"] = dict(result["account_currencies"] or {})
    account_ids = result["account_ids"]
    if not isinstance(account_ids, dict):
        raise NormalizedApiError("configure_execution", "invalid_execution_config")
    normalized_accounts = {}
    for venue, account_id in account_ids.items():
        if (
            not isinstance(venue, str)
            or not venue.strip()
            or not isinstance(account_id, str)
        ):
            raise NormalizedApiError("configure_execution", "invalid_execution_config")
        normalized_venue = venue.strip()
        normalized_account = (
            _normalize_label(account_id)
            if normalized_venue.partition("___")[0].upper() in _CRYPTO_PROVIDERS
            else account_id.strip()
        )
        if not normalized_account or normalized_venue in normalized_accounts:
            raise NormalizedApiError("configure_execution", "invalid_execution_config")
        normalized_accounts[normalized_venue] = normalized_account
    result["account_ids"] = normalized_accounts
    required_environments = result["required_environments"]
    if not isinstance(required_environments, dict):
        raise NormalizedApiError("configure_execution", "invalid_execution_config")
    normalized_environments = {}
    for venue, environment in required_environments.items():
        if (
            not isinstance(venue, str)
            or not venue.strip()
            or not isinstance(environment, str)
            or environment.strip().lower() not in {"production", "demo", "testnet"}
        ):
            raise NormalizedApiError("configure_execution", "invalid_execution_config")
        normalized_venue = venue.strip()
        if normalized_venue in normalized_environments:
            raise NormalizedApiError("configure_execution", "invalid_execution_config")
        normalized_environments[normalized_venue] = environment.strip().lower()
    result["required_environments"] = normalized_environments
    default_currency = result["account_currency"]
    if (
        default_currency is not None
        and (not isinstance(default_currency, str) or not default_currency.strip())
    ) or any(
        not isinstance(currency, str) or not currency.strip()
        for currency in result["account_currencies"].values()
    ):
        raise NormalizedApiError("configure_execution", "invalid_execution_config")
    strategy_id = result["strategy_id"]
    if not isinstance(strategy_id, str) or not strategy_id.strip():
        raise NormalizedApiError("configure_execution", "invalid_execution_config")
    result["strategy_id"] = strategy_id.strip()
    strategy_identity = result["strategy_identity_sha256"]
    if strategy_identity is not None and (
        not isinstance(strategy_identity, str)
        or len(strategy_identity) != 64
        or strategy_identity != strategy_identity.lower()
        or any(character not in "0123456789abcdef" for character in strategy_identity)
    ):
        raise NormalizedApiError("configure_execution", "invalid_execution_config")
    maximum_loss_bps = result["account_maximum_loss_bps"]
    if maximum_loss_bps is not None:
        if result["require_order_journal"] is not True:
            raise NormalizedApiError("configure_execution", "invalid_execution_config")
        if isinstance(maximum_loss_bps, bool):
            raise NormalizedApiError("configure_execution", "invalid_execution_config")
        try:
            maximum_loss_bps = Decimal(str(maximum_loss_bps))
        except (InvalidOperation, TypeError, ValueError):
            raise NormalizedApiError(
                "configure_execution", "invalid_execution_config"
            ) from None
        if not maximum_loss_bps.is_finite() or maximum_loss_bps <= 0:
            raise NormalizedApiError("configure_execution", "invalid_execution_config")
        # Decimal.normalize() applies the ambient decimal context and can round
        # a caller's configured threshold. Formatting the finite Decimal
        # directly preserves every supplied digit before canonical zero trim.
        maximum_loss_bps = format(maximum_loss_bps, "f")
        if "." in maximum_loss_bps:
            maximum_loss_bps = maximum_loss_bps.rstrip("0").rstrip(".")
        result["account_maximum_loss_bps"] = maximum_loss_bps
    risk_max_age = result["account_risk_max_age_seconds"]
    if isinstance(risk_max_age, bool):
        raise NormalizedApiError("configure_execution", "invalid_execution_config")
    try:
        risk_max_age = Decimal(str(risk_max_age))
    except (InvalidOperation, TypeError, ValueError):
        raise NormalizedApiError(
            "configure_execution", "invalid_execution_config"
        ) from None
    if (
        not risk_max_age.is_finite()
        or risk_max_age <= 0
        or int(risk_max_age * Decimal("1000000000")) <= 0
    ):
        raise NormalizedApiError("configure_execution", "invalid_execution_config")
    risk_max_age = format(risk_max_age, "f")
    if "." in risk_max_age:
        risk_max_age = risk_max_age.rstrip("0").rstrip(".")
    result["account_risk_max_age_seconds"] = risk_max_age
    result["order_poll_interval"] = max(0.05, _number(result["order_poll_interval"]))
    for path_key in ("order_journal", "account_risk_state"):
        if result[path_key] is not None:
            result[path_key] = str(Path(result[path_key]).expanduser().resolve())
    return result


def _claim_identity(value):
    if not isinstance(value, dict):
        raise NormalizedApiError(
            "migrate_journal", "invalid_claim", definite_reject=True
        )
    provider = str(value.get("provider") or "").upper()
    environment = str(value.get("environment") or "").lower()
    account_id = _normalize_label(value.get("account_id"))
    strategy_id = unicodedata.normalize(
        "NFKC", str(value.get("strategy_id") or "default")
    ).strip()
    credential_fingerprint = str(value.get("credential_fingerprint") or "").lower()
    if (
        not provider
        or environment not in {"production", "demo", "testnet"}
        or not account_id
        or not strategy_id
        or (
            credential_fingerprint
            and (
                len(credential_fingerprint) != 64
                or any(
                    char not in "0123456789abcdef" for char in credential_fingerprint
                )
            )
        )
    ):
        raise NormalizedApiError(
            "migrate_journal", "invalid_claim", definite_reject=True
        )
    if provider in _CRYPTO_PROVIDERS and not credential_fingerprint:
        raise NormalizedApiError(
            "migrate_journal", "credential_fingerprint_required", definite_reject=True
        )
    identity = {
        "provider": provider,
        "environment": environment,
        "account_id": (
            _credential_account_id(provider, credential_fingerprint)
            if provider in _CRYPTO_PROVIDERS and credential_fingerprint
            else account_id
        ),
        "strategy_id": strategy_id,
    }
    if credential_fingerprint:
        identity["credential_fingerprint"] = credential_fingerprint
    return identity


def _lock_existing_journal(path):
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(fd, "r+b", buffering=0)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                handle.write(b"\0")
            handle.seek(0)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except Exception:
        handle.close()
        raise NormalizedApiError(
            "migrate_journal", "source_journal_in_use", definite_reject=True
        ) from None


def _atomic_write_jsonl(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            for record in records:
                stream.write(json.dumps(record, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise NormalizedApiError(
                "migrate_journal", "destination_exists", definite_reject=True
            )
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(
                path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _migration_blocked(
    migration_id,
    source,
    destination,
    source_hash,
    source_epoch,
    reason,
    *,
    migrated=0,
    quarantined=0,
    quarantine=None,
):
    return {
        "status": "BLOCKED",
        "reason": reason,
        "migration_id": migration_id,
        "source": str(source),
        "source_hash": source_hash,
        "source_epoch": source_epoch,
        "migrated_records": migrated,
        "quarantined_records": quarantined,
        "destination": None,
        "quarantine": str(quarantine) if quarantine is not None else None,
        "lock_copied": False,
    }


def _fsync_directory(path):
    if os.name == "nt":
        return
    directory_fd = os.open(str(Path(path)), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _publish_no_replace(staging, destination):
    """Publish a same-directory validated file without an exists/replace race."""
    try:
        os.link(staging, destination)
    except FileExistsError:
        raise NormalizedApiError(
            "migrate_journal", "destination_exists", definite_reject=True
        ) from None
    _fsync_directory(Path(destination).parent)
    Path(staging).unlink()
    _fsync_directory(Path(destination).parent)


def _migration_records_hash(records):
    payload = json.dumps(
        records,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _acquire_identity_registry_leases(identity, operation="migrate_journal"):
    leases = []
    try:
        for digest in sorted(set(_identity_registry_digests(identity))):
            handle = _lock_file(
                _ledger_registry_root() / f"{digest}.lock",
                operation,
                "authenticated_account_execution_session_locked",
            )
            manifest = _read_locked_json(handle, operation)
            recorded = manifest.get("ledger_identity")
            if recorded and not _recorded_identity_matches(recorded, identity):
                raise NormalizedApiError(
                    operation, "ledger_registry_identity_mismatch", definite_reject=True
                )
            leases.append((digest, handle, manifest))
        return leases
    except Exception:
        for _digest, handle, _manifest in leases:
            handle.close()
        raise


def _close_registry_leases(leases):
    for _digest, handle, _manifest in leases:
        handle.close()


def _migration_transaction_path(destination):
    return Path(str(destination) + ".cutover.transaction.json")


def _migration_receipt_path(destination):
    return Path(str(destination) + ".cutover.json")


def _complete_migration_files(transaction, reconciliation):
    source = Path(transaction["source"])
    destination = Path(transaction["destination"])
    freeze = Path(str(source) + ".freeze")
    freeze_record = {
        "schema_version": _JOURNAL_SCHEMA_VERSION,
        "status": "COMPLETE",
        "migration_id": transaction["migration_id"],
        "source": str(source),
        "destination": str(destination),
        "source_hash": transaction["source_hash"],
        "source_epoch": transaction["source_epoch"],
        "claims_hash": transaction["claims_hash"],
        "destination_epoch": transaction["destination_epoch"],
        "sealed_source": transaction["sealed_source"],
        "records_hash": transaction["records_hash"],
    }
    _atomic_write_json(freeze, freeze_record)
    receipt = _migration_receipt_path(destination)
    _atomic_write_json(
        receipt,
        {
            **freeze_record,
            "ledger_identity": transaction["ledger_identity"],
            "remote_reconcile": {
                key: value
                for key, value in dict(reconciliation or {}).items()
                if key not in {"credentials", "secret", "api_key"}
            },
        },
    )
    return receipt


def _migration_report(transaction, receipt):
    return {
        "status": "COMPLETE",
        "migration_id": transaction["migration_id"],
        "source": transaction["source"],
        "source_hash": transaction["source_hash"],
        "source_epoch": transaction["source_epoch"],
        "migrated_records": transaction["migrated_records"],
        "quarantined_records": 0,
        "destination": transaction["destination"],
        "destination_epoch": transaction["destination_epoch"],
        "sealed_source": transaction["sealed_source"],
        "quarantine": None,
        "cutover_receipt": str(receipt),
        "lock_copied": False,
    }


def _recover_migration_transaction(source, destination):
    """Finish an authoritative commit or roll an incomplete prepared cutover back."""
    transaction_path = _migration_transaction_path(destination)
    if not transaction_path.exists():
        return None
    try:
        transaction = json.loads(transaction_path.read_text())
        if (
            transaction.get("source") != str(source)
            or transaction.get("destination") != str(destination)
            or transaction.get("status") not in {"PREPARED", "COMMITTED"}
        ):
            raise ValueError
        identity = _normalized_ledger_identity(transaction["ledger_identity"])
    except Exception:
        raise NormalizedApiError(
            "migrate_journal", "unreadable_cutover_transaction", definite_reject=True
        ) from None

    source_lease = _lock_existing_journal(source)
    registry_leases = _acquire_identity_registry_leases(identity)
    try:
        destination_is_authority = bool(destination.exists()) and all(
            Path(str(manifest.get("active_journal") or "")).resolve() == destination
            and int(manifest.get("fencing_epoch", -1))
            == int(transaction["destination_epoch"])
            for _digest, _handle, manifest in registry_leases
        )
        if transaction["status"] == "COMMITTED" or destination_is_authority:
            if not destination_is_authority:
                raise NormalizedApiError(
                    "migrate_journal",
                    "committed_cutover_authority_mismatch",
                    definite_reject=True,
                )
            destination_records = [
                json.loads(line) for line in destination.read_text().splitlines()
            ]
            if (
                _migration_records_hash(destination_records)
                != transaction["destination_hash"]
            ):
                raise NormalizedApiError(
                    "migrate_journal",
                    "committed_destination_hash_mismatch",
                    definite_reject=True,
                )
            transaction["status"] = "COMMITTED"
            _atomic_write_json(transaction_path, transaction)
            receipt = _complete_migration_files(
                transaction, transaction.get("remote_reconcile")
            )
            return _migration_report(transaction, receipt)

        previous = transaction.get("previous_manifests") or {}
        for digest, handle, _manifest in registry_leases:
            _write_locked_json(handle, dict(previous.get(digest) or {}))
        with suppress(FileNotFoundError):
            destination.unlink()
        with suppress(FileNotFoundError):
            Path(transaction["staging"]).unlink()
        sealed = Path(transaction["sealed_source"])
        if sealed.exists():
            with suppress(FileNotFoundError):
                source.unlink()
            os.replace(sealed, source)
            _fsync_directory(source.parent)
        transaction["status"] = "ROLLED_BACK"
        _atomic_write_json(transaction_path, transaction)
        transaction_path.unlink()
        _fsync_directory(transaction_path.parent)
        return None
    finally:
        _close_registry_leases(registry_leases)
        source_lease.close()


def migrate_execution_journal(
    source,
    destination,
    claims,
    *,
    quarantine_path=None,
    remote_reconcile=None,
):
    """Claim, reconcile and atomically transfer one journal authority.

    A durable PREPARED transaction is written before the source is sealed.  A
    later invocation rolls a partial prepare back, or completes publication if
    every registry scope already points at the validated destination.
    """
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    recovered = _recover_migration_transaction(source, destination)
    if recovered is not None:
        return recovered
    quarantine = (
        Path(quarantine_path).expanduser().resolve()
        if quarantine_path is not None
        else destination.with_suffix(destination.suffix + ".quarantine")
    )
    if source == destination or not source.is_file() or destination.exists():
        raise NormalizedApiError(
            "migrate_journal", "invalid_source_or_destination", definite_reject=True
        )
    if not isinstance(claims, dict):
        raise NormalizedApiError(
            "migrate_journal", "invalid_claim", definite_reject=True
        )
    normalized_claims = {
        str(key): _claim_identity(value) for key, value in claims.items()
    }
    source_lease = _lock_existing_journal(source)
    registry_leases = []
    staging = None
    transaction = None
    transaction_path = _migration_transaction_path(destination)
    migrated = []
    quarantined = []
    migration_id = uuid.uuid4().hex
    freeze = Path(str(source) + ".freeze")
    try:
        source_bytes = source.read_bytes()
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        try:
            source_epoch = int(
                _read_locked_json(source_lease, "migrate_journal").get(
                    "fencing_epoch", 0
                )
            )
        except NormalizedApiError:
            raise
        claims_hash = hashlib.sha256(
            json.dumps(
                normalized_claims, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        freeze_record = {
            "schema_version": _JOURNAL_SCHEMA_VERSION,
            "status": "FROZEN",
            "migration_id": migration_id,
            "source": str(source),
            "destination": str(destination),
            "source_hash": source_hash,
            "source_epoch": source_epoch,
            "claims_hash": claims_hash,
        }
        _atomic_write_json(freeze, freeze_record)

        for line_number, line in enumerate(
            source_bytes.decode("utf-8").splitlines(), 1
        ):
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not row.get("event"):
                    raise ValueError("invalid_record")
                client_id = str(row.get("client_order_id") or "")
                native_id = str(row.get("order_id") or row.get("venue_order_id") or "")
                identity = normalized_claims.get(client_id) or normalized_claims.get(
                    f"order:{native_id}"
                )
                if identity is None:
                    raise ValueError("unclaimed_identity")
                ledger_identity = {
                    key: identity[key]
                    for key in (
                        "provider",
                        "environment",
                        "account_id",
                        "credential_fingerprint",
                    )
                    if key in identity
                }
                embedded = row.get("ledger_identity")
                if isinstance(embedded, dict) and not _recorded_identity_matches(
                    embedded, ledger_identity
                ):
                    raise ValueError("embedded_identity_mismatch")
                row_provider = (
                    str(row.get("exchange_name") or "").partition("___")[0].upper()
                )
                if (
                    row_provider in _CRYPTO_PROVIDERS
                    and row_provider != identity["provider"]
                ):
                    raise ValueError("claim_provider_mismatch")
                migrated.append(
                    {
                        **row,
                        "schema_version": _JOURNAL_SCHEMA_VERSION,
                        "account_id": identity["account_id"],
                        "strategy_id": identity["strategy_id"],
                        "ledger_identity": ledger_identity,
                        "migration_id": migration_id,
                        "source_hash": source_hash,
                        "source_epoch": source_epoch,
                        "source_record_index": line_number,
                    }
                )
            except Exception as exc:
                quarantined.append(
                    {
                        "schema_version": _JOURNAL_SCHEMA_VERSION,
                        "migration_id": migration_id,
                        "source_record_index": line_number,
                        "reason": str(exc) or type(exc).__name__,
                        "raw_line": line,
                    }
                )
        if quarantined:
            _atomic_write_jsonl(quarantine, quarantined)
            freeze_record.update(status="BLOCKED", reason="quarantined_records")
            _atomic_write_json(freeze, freeze_record)
            return _migration_blocked(
                migration_id,
                source,
                destination,
                source_hash,
                source_epoch,
                "quarantined_records",
                migrated=len(migrated),
                quarantined=len(quarantined),
                quarantine=quarantine,
            )

        identities = {
            json.dumps(row["ledger_identity"], sort_keys=True, separators=(",", ":"))
            for row in migrated
        }
        if len(identities) != 1:
            freeze_record.update(
                status="BLOCKED", reason="single_ledger_identity_required"
            )
            _atomic_write_json(freeze, freeze_record)
            return _migration_blocked(
                migration_id,
                source,
                destination,
                source_hash,
                source_epoch,
                "single_ledger_identity_required",
                migrated=len(migrated),
            )
        identity = json.loads(next(iter(identities)))
        records_hash = _migration_records_hash(migrated)
        if not callable(remote_reconcile):
            freeze_record.update(status="BLOCKED", reason="remote_reconcile_required")
            _atomic_write_json(freeze, freeze_record)
            return _migration_blocked(
                migration_id,
                source,
                destination,
                source_hash,
                source_epoch,
                "remote_reconcile_required",
                migrated=len(migrated),
            )
        try:
            reconciliation = remote_reconcile(
                ledger_identity=dict(identity),
                records=tuple(dict(row) for row in migrated),
                records_hash=records_hash,
                record_count=len(migrated),
                source_hash=source_hash,
                source_epoch=source_epoch,
                migration_id=migration_id,
            )
        except Exception as exc:
            reconciliation = {"verified": False, "reason": type(exc).__name__}
        echoed_identity = (
            _normalized_ledger_identity(reconciliation.get("ledger_identity", {}))
            if isinstance(reconciliation, dict)
            else {}
        )
        reconciliation_verified = (
            isinstance(reconciliation, dict)
            and reconciliation.get("verified") is True
            and not reconciliation.get("unknown_ids")
            and echoed_identity == _normalized_ledger_identity(identity)
            and reconciliation.get("source_hash") == source_hash
            and int(reconciliation.get("source_epoch", -1)) == source_epoch
            and reconciliation.get("migration_id") == migration_id
            and reconciliation.get("records_hash") == records_hash
            and int(reconciliation.get("record_count", -1)) == len(migrated)
        )
        if not reconciliation_verified:
            freeze_record.update(status="BLOCKED", reason="remote_reconcile_unverified")
            _atomic_write_json(freeze, freeze_record)
            return _migration_blocked(
                migration_id,
                source,
                destination,
                source_hash,
                source_epoch,
                "remote_reconcile_unverified",
                migrated=len(migrated),
            )

        registry_leases = _acquire_identity_registry_leases(identity)
        for _digest, _handle, manifest in registry_leases:
            active = manifest.get("active_journal")
            if (
                active
                and Path(active).resolve() not in {source, destination}
                and Path(active).exists()
            ):
                freeze_record.update(
                    status="BLOCKED", reason="ledger_has_other_authority"
                )
                _atomic_write_json(freeze, freeze_record)
                return _migration_blocked(
                    migration_id,
                    source,
                    destination,
                    source_hash,
                    source_epoch,
                    "ledger_has_other_authority",
                    migrated=len(migrated),
                )

        if source.read_bytes() != source_bytes:
            freeze_record.update(status="BLOCKED", reason="source_changed_while_frozen")
            _atomic_write_json(freeze, freeze_record)
            return _migration_blocked(
                migration_id,
                source,
                destination,
                source_hash,
                source_epoch,
                "source_changed_while_frozen",
                migrated=len(migrated),
            )

        destination_epoch = (
            max(
                source_epoch,
                *(
                    int(manifest.get("fencing_epoch", 0))
                    for _d, _h, manifest in registry_leases
                ),
            )
            + 1
        )
        cutover_owner = f"cutover:{migration_id}"
        finalized = [
            {
                **row,
                "owner_token": cutover_owner,
                "owner_pid": os.getpid(),
                "fencing_epoch": destination_epoch,
                "cutover_id": migration_id,
            }
            for row in migrated
        ]
        destination_hash = _migration_records_hash(finalized)
        staging = destination.with_name(f".{destination.name}.{migration_id}.validated")
        _atomic_write_jsonl(staging, finalized)
        validated = [json.loads(line) for line in staging.read_text().splitlines()]
        if (
            validated != finalized
            or _migration_records_hash(validated) != destination_hash
        ):
            raise NormalizedApiError(
                "migrate_journal", "staging_validation_failed", definite_reject=True
            )
        sealed_source = source.with_name(f"{source.name}.{migration_id}.sealed")
        transaction = {
            "schema_version": _JOURNAL_SCHEMA_VERSION,
            "status": "PREPARED",
            "migration_id": migration_id,
            "source": str(source),
            "destination": str(destination),
            "staging": str(staging),
            "sealed_source": str(sealed_source),
            "source_hash": source_hash,
            "source_epoch": source_epoch,
            "claims_hash": claims_hash,
            "records_hash": records_hash,
            "destination_hash": destination_hash,
            "destination_epoch": destination_epoch,
            "migrated_records": len(migrated),
            "ledger_identity": identity,
            "previous_manifests": {
                digest: manifest for digest, _handle, manifest in registry_leases
            },
        }
        _atomic_write_json(transaction_path, transaction)

        os.replace(source, sealed_source)
        _fsync_directory(source.parent)
        if sealed_source.read_bytes() != source_bytes:
            raise NormalizedApiError(
                "migrate_journal", "source_changed_while_frozen", definite_reject=True
            )
        tombstone = {
            "schema_version": _JOURNAL_SCHEMA_VERSION,
            "event": "cutover_tombstone",
            "migration_id": migration_id,
            "source_hash": source_hash,
            "destination": str(destination),
            "destination_epoch": destination_epoch,
            "sealed_source": str(sealed_source),
        }
        _atomic_write_json(source, tombstone)
        _publish_no_replace(staging, destination)
        staging = None
        for digest, handle, _manifest in registry_leases:
            _write_locked_json(
                handle,
                {
                    "schema_version": _JOURNAL_SCHEMA_VERSION,
                    "ledger_identity": identity,
                    "active_journal": str(destination),
                    "owner_token": cutover_owner,
                    "owner_pid": os.getpid(),
                    "fencing_epoch": destination_epoch,
                    "source_hash": source_hash,
                    "registry_scope": digest,
                    "cutover_status": "COMMITTED",
                },
            )
        transaction["status"] = "COMMITTED"
        transaction["remote_reconcile"] = {
            key: value
            for key, value in reconciliation.items()
            if key not in {"credentials", "secret", "api_key"}
        }
        _atomic_write_json(transaction_path, transaction)
        receipt = _complete_migration_files(transaction, reconciliation)
        return _migration_report(transaction, receipt)
    except Exception:
        if transaction is not None and transaction.get("status") != "COMMITTED":
            for digest, handle, _manifest in registry_leases:
                previous = transaction.get("previous_manifests", {}).get(digest) or {}
                with suppress(Exception):
                    _write_locked_json(handle, previous)
            with suppress(FileNotFoundError):
                destination.unlink()
            if staging is not None:
                with suppress(FileNotFoundError):
                    Path(staging).unlink()
            sealed_source = Path(transaction["sealed_source"])
            if sealed_source.exists():
                with suppress(FileNotFoundError):
                    source.unlink()
                os.replace(sealed_source, source)
                _fsync_directory(source.parent)
            freeze_record = {
                "schema_version": _JOURNAL_SCHEMA_VERSION,
                "status": "BLOCKED",
                "reason": "cutover_failed_rolled_back",
                "migration_id": migration_id,
                "source": str(source),
                "destination": str(destination),
            }
            with suppress(Exception):
                _atomic_write_json(freeze, freeze_record)
            with suppress(FileNotFoundError):
                transaction_path.unlink()
        raise
    finally:
        _close_registry_leases(registry_leases)
        source_lease.close()


# Public compatibility name used by the iteration-21 design documents.
migrate_legacy_execution_journal = migrate_execution_journal


class _ExecutionSession:
    def __init__(self, config, exchange_names=(), credential_fingerprints=None):
        self.config = session_config(config)
        self.exchange_names = tuple(exchange_names or ())
        self.credential_fingerprints = {
            str(venue).strip(): str(fingerprint).lower()
            for venue, fingerprint in dict(credential_fingerprints or {}).items()
        }
        identities = (
            []
            if self.config["market_data_only"]
            else self._configured_crypto_identities()
        )
        if (
            not self.config["market_data_only"]
            and self.config["require_order_journal"]
            and self.config["order_journal"] is None
            and identities
        ):
            self.config["order_journal"] = str(_default_journal_path(identities))
        path = self.config["order_journal"]
        self.path = Path(path) if path else None
        risk_path = self.config["account_risk_state"]
        if risk_path is None and self.path is not None:
            risk_path = str(self.path) + ".account-risk.json"
            self.config["account_risk_state"] = risk_path
        self.risk_path = Path(risk_path) if risk_path else None
        self.lock_file = None
        self.ledger_lock_files = []
        self.ledger_registry_digests = set()
        self.owner_token = uuid.uuid4().hex
        self.owner_pid = os.getpid()
        self.fencing_epoch = 0
        self.mutex = RLock()
        self.risk_collection_mutex = RLock()
        self.orders = {}
        self.used_ids = set()
        self.reserved_ids = set()
        self.historical_unknown = set()
        self.pending = defaultdict(deque)
        self.trade_ids = set()
        self.accounts = {}
        self.submit_calls = 0
        self.cancel_calls = 0
        self.persistence_failed = False
        self.risk_record = None
        self.risk_error = None
        self.risk_measurement_error = None
        self.risk_transition_error = None
        self.risk_check_in_progress = False
        self.risk_last_verified_monotonic_ns = 0
        self._journal_loss_state = None
        self._journal_loss_breached_at = None
        self._pending_loss_reset_id = None
        self._pending_loss_reset_at = None
        self._committed_loss_reset_id = None
        self._arm_managed = False
        self._arm_proof = None
        self._arm_proof_sha256 = None
        self._last_arm_proof_sha256 = None
        self._arm_state_reader = None
        self._arm_revoked_reason = None
        self._arm_revoked_error_code = None
        self._arm_revoked_generation = None
        self._arm_venue = None
        self._ctp_execution_identity = None
        self._recovery_plan = None
        self._recovery_mode = False
        self._recovery_dispatch_in_progress = False
        self._recovery_arm_capability = object()
        self._recovery_refresh_in_progress = False
        self._recovery_journal_error = None
        self._recovery_used_tokens = set()
        self._recovery_completed_preflight_sha256 = None
        self._recovery_completed = False
        self._recovery_event_revision = 0
        self._recovery_private_event_revision = 0
        self._recovery_private_ingress_revision = 0
        self._arm_submit_calls = 0
        self._arm_cancel_calls = 0
        self.closed = False
        try:
            if not self.config["market_data_only"]:
                self._validate_crypto_configuration(self.exchange_names)
                self._acquire_lock()
                self._load_journal()
                self._load_risk_state()
        except Exception:
            self.close()
            raise

    @staticmethod
    def _provider(venue):
        return str(venue or "").partition("___")[0].upper()

    def _validate_crypto_configuration(self, venues):
        for venue in venues:
            if self._provider(venue) not in _CRYPTO_PROVIDERS:
                continue
            if venue not in self.config["required_environments"]:
                raise NormalizedApiError(
                    "configure_execution",
                    "required_environment_missing",
                    definite_reject=True,
                )
            if (
                venue not in self.credential_fingerprints
                and venue not in self.config["account_ids"]
            ):
                raise NormalizedApiError(
                    "configure_execution",
                    "authenticated_execution_identity_missing",
                    definite_reject=True,
                )

    def _configured_crypto_identities(self):
        venues = set(self.exchange_names)
        venues.update(self.config["required_environments"])
        venues.update(self.config["account_ids"])
        result = {}
        for venue in sorted(venues):
            if self._provider(venue) not in _CRYPTO_PROVIDERS:
                continue
            self._validate_crypto_configuration((venue,))
            fingerprint = self.credential_fingerprints.get(venue)
            identity = {
                "provider": self._provider(venue),
                "environment": self.config["required_environments"][venue],
                "account_id": (
                    _credential_account_id(self._provider(venue), fingerprint)
                    if fingerprint
                    else self.config["account_ids"][venue]
                ),
            }
            if fingerprint:
                identity["credential_fingerprint"] = fingerprint
            result[tuple(sorted(identity.items()))] = identity
        return list(result.values())

    def _configured_execution_identities(self):
        identities = list(self._configured_crypto_identities())
        if isinstance(self._ctp_execution_identity, Mapping):
            identities.append(dict(self._ctp_execution_identity))
        return identities

    def _acquire_ledger_locks(self):
        identities = self._configured_execution_identities()
        if not identities or self.path is None:
            return 0
        scopes = {}
        for identity in identities:
            for digest in _identity_registry_digests(identity):
                previous = scopes.get(digest)
                if previous is not None and previous != identity:
                    raise NormalizedApiError(
                        "journal",
                        "authenticated_account_identity_conflict",
                        definite_reject=True,
                    )
                scopes[digest] = identity
        acquired = []
        try:
            for digest, identity in sorted(scopes.items()):
                registry_path = _ledger_registry_root() / f"{digest}.lock"
                handle = _lock_file(
                    registry_path,
                    "journal",
                    "authenticated_account_execution_session_locked",
                )
                manifest = _read_locked_json(handle, "journal")
                recorded_identity = manifest.get("ledger_identity")
                if recorded_identity and not _recorded_identity_matches(
                    recorded_identity, identity
                ):
                    raise NormalizedApiError(
                        "journal",
                        "ledger_registry_identity_mismatch",
                        definite_reject=True,
                    )
                active = manifest.get("active_journal")
                if (
                    active
                    and Path(active).resolve() != self.path
                    and Path(active).exists()
                ):
                    raise NormalizedApiError(
                        "journal",
                        "authenticated_account_journal_conflict",
                        definite_reject=True,
                    )
                acquired.append((digest, identity, handle, manifest))
            epoch = (
                max(
                    (int(item[3].get("fencing_epoch", 0)) for item in acquired),
                    default=0,
                )
                + 1
            )
            for digest, identity, handle, _manifest in acquired:
                _write_locked_json(
                    handle,
                    {
                        "schema_version": _JOURNAL_SCHEMA_VERSION,
                        "ledger_identity": identity,
                        "active_journal": str(self.path),
                        "owner_token": self.owner_token,
                        "owner_pid": self.owner_pid,
                        "fencing_epoch": epoch,
                        "registry_scope": digest,
                    },
                )
            self.ledger_lock_files = [item[2] for item in acquired]
            self.ledger_registry_digests = {item[0] for item in acquired}
            return epoch
        except Exception:
            for _digest, _identity, handle, _manifest in acquired:
                handle.close()
            raise

    def _acquire_lock(self):
        if self.path is None:
            return
        handle = None
        try:
            freeze = Path(str(self.path) + ".freeze")
            if freeze.exists():
                raise NormalizedApiError(
                    "journal", "journal_frozen_for_cutover", definite_reject=True
                )
            global_epoch = self._acquire_ledger_locks()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = _lock_file(
                Path(str(self.path) + ".lock"),
                "journal",
                "execution_session_locked_or_unavailable",
            )
            fd = handle.fileno()
            handle.seek(0)
            previous = handle.read()
            if previous:
                try:
                    lease = json.loads(previous.decode("utf-8"))
                    self.fencing_epoch = int(lease.get("fencing_epoch", 0))
                except (
                    UnicodeDecodeError,
                    ValueError,
                    TypeError,
                    json.JSONDecodeError,
                ):
                    # The OS lock is authoritative. An old/empty sidecar has no
                    # transferable ownership and starts the durable epoch anew.
                    self.fencing_epoch = 0
            self.fencing_epoch = max(self.fencing_epoch + 1, global_epoch)
            for ledger_handle in self.ledger_lock_files:
                manifest = _read_locked_json(ledger_handle, "journal")
                manifest.update(
                    owner_token=self.owner_token,
                    owner_pid=self.owner_pid,
                    fencing_epoch=self.fencing_epoch,
                )
                _write_locked_json(ledger_handle, manifest)
            lease = json.dumps(
                {
                    "schema_version": _JOURNAL_SCHEMA_VERSION,
                    "owner_token": self.owner_token,
                    "owner_pid": self.owner_pid,
                    "fencing_epoch": self.fencing_epoch,
                    "journal": str(self.path),
                },
                separators=(",", ":"),
            ).encode("utf-8")
            handle.seek(0)
            handle.truncate()
            handle.write(lease)
            handle.flush()
            os.fsync(fd)
            self.lock_file = handle
        except Exception as exc:
            if handle is not None:
                handle.close()
            for ledger_handle in self.ledger_lock_files:
                ledger_handle.close()
            self.ledger_lock_files = []
            self.ledger_registry_digests.clear()
            if isinstance(exc, NormalizedApiError):
                raise
            raise NormalizedApiError(
                "journal",
                "execution_session_locked_or_unavailable",
                definite_reject=True,
            ) from None

    def _assert_writer_lease(self, operation="journal"):
        # Buffered file objects carry a shared cursor.  Keep every seek/read of
        # the journal and registry lease handles under one process-local lock;
        # otherwise concurrent event/order threads can observe an empty or
        # partial JSON document even though the durable owner never changed.
        with self.mutex:
            if os.getpid() != self.owner_pid:
                raise NormalizedApiError(
                    operation, "execution_session_forked_process", definite_reject=True
                )
            if self.path is None:
                return
            if self.lock_file is None or self.lock_file.closed:
                raise NormalizedApiError(
                    operation, "writer_lease_lost", definite_reject=True
                )
            lease = _read_locked_json(self.lock_file, operation)
            if (
                lease.get("owner_token") != self.owner_token
                or int(lease.get("fencing_epoch", -1)) != self.fencing_epoch
                or int(lease.get("owner_pid", self.owner_pid)) != self.owner_pid
            ):
                raise NormalizedApiError(
                    operation, "writer_lease_fenced", definite_reject=True
                )
            for handle in self.ledger_lock_files:
                if handle.closed:
                    raise NormalizedApiError(
                        operation, "writer_lease_lost", definite_reject=True
                    )
                manifest = _read_locked_json(handle, operation)
                if (
                    manifest.get("owner_token") != self.owner_token
                    or int(manifest.get("fencing_epoch", -1)) != self.fencing_epoch
                    or int(manifest.get("owner_pid", self.owner_pid)) != self.owner_pid
                    or Path(str(manifest.get("active_journal") or "")).resolve()
                    != self.path
                ):
                    raise NormalizedApiError(
                        operation, "writer_lease_fenced", definite_reject=True
                    )

    def _release_writer_leases(self):
        handle, self.lock_file = self.lock_file, None
        if handle is not None:
            # Closing releases the OS advisory lock, including on a crash.
            # Never unlink the inode: another process may be waiting on it.
            handle.close()
        ledger_handles, self.ledger_lock_files = self.ledger_lock_files, []
        self.ledger_registry_digests.clear()
        for ledger_handle in ledger_handles:
            ledger_handle.close()

    def close(self):
        with self.mutex:
            self.closed = True
            self._arm_state_reader = None
            self._release_writer_leases()

    def bind_credential_identity(self, venue, credential_fingerprint):
        """Bind a dynamically added crypto venue before any authenticated I/O."""
        fingerprint = str(credential_fingerprint or "").lower()
        if len(fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in fingerprint
        ):
            raise NormalizedApiError(
                "configure_execution",
                "invalid_credential_fingerprint",
                definite_reject=True,
            )
        with self.mutex:
            self._assert_writer_lease("configure_execution")
            current = self.credential_fingerprints.get(venue)
            if current is not None:
                if current != fingerprint:
                    raise NormalizedApiError(
                        "configure_execution",
                        "credential_identity_already_bound",
                        definite_reject=True,
                    )
                return
            if venue not in self.config["required_environments"]:
                raise NormalizedApiError(
                    "configure_execution",
                    "required_environment_missing",
                    definite_reject=True,
                )
            identity = {
                "provider": self._provider(venue),
                "environment": self.config["required_environments"][venue],
                "account_id": _credential_account_id(
                    self._provider(venue), fingerprint
                ),
                "credential_fingerprint": fingerprint,
            }
            credential_digest = _identity_registry_digests(identity)[0]
            if credential_digest in self.ledger_registry_digests:
                self.credential_fingerprints[venue] = fingerprint
                return
            registry_path = _ledger_registry_root() / f"{credential_digest}.lock"
            handle = _lock_file(
                registry_path,
                "configure_execution",
                "authenticated_account_execution_session_locked",
            )
            try:
                manifest = _read_locked_json(handle, "configure_execution")
                recorded = manifest.get("ledger_identity")
                if recorded and not _recorded_identity_matches(recorded, identity):
                    raise NormalizedApiError(
                        "configure_execution",
                        "ledger_registry_identity_mismatch",
                        definite_reject=True,
                    )
                active = manifest.get("active_journal")
                if (
                    active
                    and Path(active).resolve() != self.path
                    and Path(active).exists()
                ):
                    raise NormalizedApiError(
                        "configure_execution",
                        "authenticated_account_journal_conflict",
                        definite_reject=True,
                    )
                prior_epoch = int(manifest.get("fencing_epoch", 0))
                if prior_epoch >= self.fencing_epoch:
                    new_epoch = prior_epoch + 1
                    for existing in self.ledger_lock_files:
                        existing_manifest = _read_locked_json(
                            existing, "configure_execution"
                        )
                        existing_manifest.update(
                            owner_token=self.owner_token,
                            owner_pid=self.owner_pid,
                            fencing_epoch=new_epoch,
                        )
                        _write_locked_json(existing, existing_manifest)
                    if self.lock_file is not None:
                        _write_locked_json(
                            self.lock_file,
                            {
                                "schema_version": _JOURNAL_SCHEMA_VERSION,
                                "owner_token": self.owner_token,
                                "owner_pid": self.owner_pid,
                                "fencing_epoch": new_epoch,
                                "journal": str(self.path),
                            },
                        )
                    self.fencing_epoch = new_epoch
                _write_locked_json(
                    handle,
                    {
                        "schema_version": _JOURNAL_SCHEMA_VERSION,
                        "ledger_identity": identity,
                        "active_journal": str(self.path),
                        "owner_token": self.owner_token,
                        "owner_pid": self.owner_pid,
                        "fencing_epoch": self.fencing_epoch,
                        "registry_scope": credential_digest,
                    },
                )
            except Exception:
                handle.close()
                raise
            self.credential_fingerprints[venue] = fingerprint
            self.ledger_lock_files.append(handle)
            self.ledger_registry_digests.add(credential_digest)

    def _ledger_identity(self, venue, account_id=None, row=None):
        row = row or {}
        provider = self._provider(venue)
        if provider in _CRYPTO_PROVIDERS:
            self._validate_crypto_configuration((venue,))
            fingerprint = self.credential_fingerprints.get(venue)
            alias = self.config["account_ids"].get(venue)
            canonical_account = (
                _credential_account_id(provider, fingerprint) if fingerprint else alias
            )
            identity = {
                "provider": provider,
                "environment": self.config["required_environments"][venue],
                "account_id": canonical_account,
            }
            if fingerprint:
                identity["credential_fingerprint"] = fingerprint
            supplied_account = account_id or row.get("account_id")
            accepted_accounts = {canonical_account}
            if alias:
                accepted_accounts.add(alias)
            if (
                supplied_account not in (None, "")
                and _normalize_label(supplied_account) not in accepted_accounts
            ):
                raise NormalizedApiError(
                    "journal", "authenticated_account_id_mismatch", definite_reject=True
                )
            embedded = row.get("ledger_identity")
            if isinstance(embedded, dict):
                normalized_embedded = _normalized_ledger_identity(embedded)
                if not _recorded_identity_matches(normalized_embedded, identity):
                    raise NormalizedApiError(
                        "journal", "ledger_identity_mismatch", definite_reject=True
                    )
            return identity
        if provider == "CTP" and isinstance(self._ctp_execution_identity, Mapping):
            if venue != self._arm_venue:
                raise NormalizedApiError(
                    "journal", "execution_arm_venue_mismatch", definite_reject=True
                )
            identity = dict(self._ctp_execution_identity)
            supplied_account = account_id or row.get("account_id")
            if (
                supplied_account not in (None, "")
                and _normalize_label(supplied_account) != identity["account_id"]
            ):
                raise NormalizedApiError(
                    "journal", "authenticated_account_id_mismatch", definite_reject=True
                )
            embedded = row.get("ledger_identity")
            if isinstance(embedded, dict) and not _recorded_identity_matches(
                embedded, identity
            ):
                raise NormalizedApiError(
                    "journal", "ledger_identity_mismatch", definite_reject=True
                )
            return identity
        embedded = row.get("ledger_identity")
        if isinstance(embedded, dict):
            provider = str(embedded.get("provider") or "").upper()
            environment = str(embedded.get("environment") or "").lower()
            ledger_account = str(embedded.get("account_id") or "")
            if provider and environment and ledger_account:
                return {
                    "provider": provider,
                    "environment": environment,
                    "account_id": ledger_account,
                }
        provider = self._provider(venue)
        environment = str(
            row.get("environment")
            or self.config["required_environments"].get(venue)
            or "unverified"
        ).lower()
        ledger_account = str(
            account_id
            or row.get("account_id")
            or self.config["account_ids"].get(venue)
            or venue
            or ""
        )
        if not provider or not ledger_account:
            raise NormalizedApiError(
                "journal", "missing_ledger_identity", definite_reject=True
            )
        return {
            "provider": provider,
            "environment": environment,
            "account_id": ledger_account,
        }

    @staticmethod
    def _ledger_key(identity):
        return (
            identity["provider"],
            identity["environment"],
            identity["account_id"],
        )

    def _client_key(self, venue, account_id, client_id, row=None):
        identity = self._ledger_identity(venue, account_id, row)
        return (*self._ledger_key(identity), str(client_id))

    def _trade_key(self, venue, row):
        """Scope venue trade IDs to account, trading day, exchange and symbol."""
        identity = self._ledger_identity(venue, row.get("account_id"), row)
        return (
            *self._ledger_key(identity),
            str(row.get("trading_day") or ""),
            str(row.get("exchange_id") or ""),
            str(row.get("symbol") or ""),
            str(row.get("trade_id") or ""),
        )

    def _load_journal(self):
        if self.path is None or not self.path.exists():
            return
        try:
            epoch_owners = {}
            previous_epoch = -1
            for line in self.path.read_text().splitlines():
                row = json.loads(line)
                event = row["event"]
                if event not in {
                    "client_id_reservation",
                    "intent",
                    "cancel_intent",
                    "order_update",
                    "trade",
                    "trade_update",
                    *_RISK_TRANSITION_EVENTS,
                }:
                    raise ValueError("unknown_journal_record")
                embedded_identity = row.get("ledger_identity")
                provider = (
                    str(embedded_identity.get("provider") or "").upper()
                    if isinstance(embedded_identity, dict)
                    else self._provider(row.get("exchange_name"))
                )
                if (
                    provider in _CRYPTO_PROVIDERS or event in _RISK_TRANSITION_EVENTS
                ) and int(row.get("schema_version", 0) or 0) >= _JOURNAL_SCHEMA_VERSION:
                    row_epoch = int(row.get("fencing_epoch", -1))
                    row_owner = str(row.get("owner_token") or "")
                    if (
                        row_epoch < 0
                        or not row_owner
                        or row_epoch < previous_epoch
                        or row_epoch > self.fencing_epoch
                        or (
                            row_epoch in epoch_owners
                            and epoch_owners[row_epoch] != row_owner
                        )
                    ):
                        raise ValueError("invalid_journal_fencing")
                    epoch_owners[row_epoch] = row_owner
                    previous_epoch = row_epoch
                if event in _RISK_TRANSITION_EVENTS:
                    if (
                        row.get("loss_limit_bps")
                        != self.config["account_maximum_loss_bps"]
                    ):
                        raise ValueError("risk transition limit mismatch")
                    transition_time = row.get("transition_time")
                    if (
                        isinstance(transition_time, bool)
                        or not isinstance(transition_time, (int, float))
                        or not math.isfinite(transition_time)
                        or transition_time <= 0
                    ):
                        raise ValueError("invalid risk transition time")
                    transition_id = str(row.get("transition_id") or "")
                    if not transition_id:
                        raise ValueError("missing risk transition id")
                    if event == "risk_breach":
                        self._journal_loss_state = "breached"
                        self._journal_loss_breached_at = transition_time
                        self._pending_loss_reset_id = None
                        self._pending_loss_reset_at = None
                        self._committed_loss_reset_id = None
                    elif event == "risk_reset_prepared":
                        self._pending_loss_reset_id = transition_id
                        self._pending_loss_reset_at = transition_time
                    else:
                        if transition_id != self._pending_loss_reset_id:
                            raise ValueError("unmatched risk reset commit")
                        self._journal_loss_state = "reset"
                        self._committed_loss_reset_id = transition_id
                        self._pending_loss_reset_id = None
                        self._pending_loss_reset_at = None
                    continue
                if event == "client_id_reservation":
                    client_id = str(row.get("client_order_id") or "")
                    venue = row.get("exchange_name")
                    if not client_id or not venue:
                        raise ValueError("missing_reservation_identity")
                    self.reserved_ids.add(
                        self._client_key(
                            venue,
                            row.get("account_id"),
                            client_id,
                            row,
                        )
                    )
                    continue
                if event in {"trade", "trade_update"} and row.get("trade_id"):
                    self.trade_ids.add(self._trade_key(row.get("exchange_name"), row))
                client_id = str(row.get("client_order_id") or "")
                venue = row.get("exchange_name")
                if not client_id and not (
                    row.get("order_id") or row.get("venue_order_id")
                ):
                    if event in {"intent", "cancel_intent", "order_update"}:
                        raise ValueError("missing_order_identity")
                    continue
                if client_id:
                    client_key = self._client_key(
                        venue,
                        row.get("account_id"),
                        client_id,
                        row,
                    )
                    self.used_ids.add(client_key)
                    self.reserved_ids.discard(client_key)
                if not venue or not row.get("symbol"):
                    raise ValueError("missing_order_identity")
                state = self._state(venue, row, create=True)
                self._identity(
                    state,
                    {
                        **row,
                        "order_id": row.get("order_id") or row.get("venue_order_id"),
                    },
                )
                identity = self._identifier(state)
                state.setdefault("recovery_ids", set()).add(identity)
                if event == "intent":
                    state["_intent_persisted"] = True
                    state["strategy_id"] = row.get("strategy_id")
                    state["strategy_identity_sha256"] = row.get(
                        "strategy_identity_sha256"
                    )
                    state["execution_arm_proof_sha256"] = row.get(
                        "execution_arm_proof_sha256"
                    )
                    state["connection_generation"] = row.get("connection_generation")
                    state["fencing_epoch"] = row.get("fencing_epoch")
                    self.historical_unknown.add(identity)
                    state["terminal"] = False
                elif event == "cancel_intent":
                    self.historical_unknown.add(identity)
                elif event == "order_update":
                    # Old journals may contain engine references. Whitelist
                    # SDK execution fields; never restore consumer objects.
                    state["last_update"] = {
                        k: v
                        for k, v in row.items()
                        if k
                        not in {
                            "event",
                            "bt_order_ref",
                            "data_name",
                            "external_order_id",
                            _EXPLICIT_IDENTITY_FIELDS,
                        }
                    }
                    state["terminal"] = bool(row.get("terminal_confirmed"))
                    if state["terminal"]:
                        self.historical_unknown.difference_update(state["recovery_ids"])
                if row.get("fee_unresolved"):
                    state["fee_unresolved"] = True
            for state in self.orders.values():
                if not state.get("terminal"):
                    self.historical_unknown.add(self._identifier(state))
                    state["last_update"] = self._unknown(
                        state, "restart_reconciliation"
                    )
        except Exception:
            raise NormalizedApiError(
                "journal", "unreadable_journal", definite_reject=True
            ) from None

    def _journal(self, event, row, *, allow_read_only=False):
        if self.closed:
            raise NormalizedApiError(
                "journal", "execution_session_closed", definite_reject=True
            )
        if self.path is None or (
            self.config["market_data_only"] and not allow_read_only
        ):
            return
        try:
            self._assert_writer_lease("journal")
            created = False
            try:
                fd = os.open(
                    str(self.path),
                    os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                created = True
            except FileExistsError:
                fd = os.open(str(self.path), os.O_APPEND | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "a") as stream:
                venue = row.get("exchange_name")
                ledger_identity = (
                    self._ledger_identity(venue, row.get("account_id"), row)
                    if venue
                    else None
                )
                envelope = {
                    **row,
                    "schema_version": _JOURNAL_SCHEMA_VERSION,
                    "owner_token": self.owner_token,
                    "owner_pid": self.owner_pid,
                    "fencing_epoch": self.fencing_epoch,
                    "strategy_id": self.config["strategy_id"],
                    "strategy_identity_sha256": self.config["strategy_identity_sha256"],
                    "execution_arm_proof_sha256": (
                        self._arm_proof_sha256
                        or (self._last_arm_proof_sha256 if allow_read_only else None)
                    ),
                    "connection_generation": (
                        self._arm_proof.get("connection_generation")
                        if isinstance(self._arm_proof, Mapping)
                        else None
                    ),
                    "ledger_identity": ledger_identity,
                    "event": event,
                    "timestamp": time.time(),
                }
                if ledger_identity is not None:
                    envelope["account_id"] = ledger_identity["account_id"]
                if (
                    venue
                    and self._provider(venue) == "CTP"
                    and isinstance(self._arm_proof, Mapping)
                ):
                    envelope["trading_day"] = self._arm_proof["trading_day"]
                stream.write(json.dumps(envelope, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            if created and os.name != "nt":
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                directory_fd = os.open(str(self.path.parent), flags)
                try:
                    # The file fsync above persists its contents. A separate
                    # directory fsync makes the first journal name durable.
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except Exception:
            self.persistence_failed = True
            raise NormalizedApiError(
                "journal", "persistence_failed", definite_reject=True
            ) from None

    def _journal_risk_transition(self, event, transition_id=None):
        """Append and apply one fenced account-loss transition."""
        transition_id = str(transition_id or uuid.uuid4().hex)
        transition_time = time.time()
        self._journal(
            event,
            {
                "transition_id": transition_id,
                "transition_time": transition_time,
                "loss_limit_bps": self.config["account_maximum_loss_bps"],
            },
        )
        if event == "risk_breach":
            self._journal_loss_state = "breached"
            self._journal_loss_breached_at = transition_time
            self._pending_loss_reset_id = None
            self._pending_loss_reset_at = None
            self._committed_loss_reset_id = None
        elif event == "risk_reset_prepared":
            self._pending_loss_reset_id = transition_id
            self._pending_loss_reset_at = transition_time
        elif event == "risk_reset_committed":
            if transition_id != self._pending_loss_reset_id:
                raise NormalizedApiError(
                    "journal", "unmatched_risk_reset_commit", definite_reject=True
                )
            self._journal_loss_state = "reset"
            self._committed_loss_reset_id = transition_id
            self._pending_loss_reset_id = None
            self._pending_loss_reset_at = None
        else:  # pragma: no cover - private caller invariant
            raise ValueError("unknown risk transition")
        return transition_id

    @staticmethod
    def _arm_context_error(proof, context, *, require_account_stream=False):
        if not isinstance(context, Mapping):
            return "execution_arm_state_unavailable"
        for field in _EXECUTION_ARM_CONTEXT_FIELDS:
            if context.get(field) != proof[field]:
                return f"execution_arm_{field}_mismatch"
        if require_account_stream and context.get("account_stream_ready") is not True:
            return "execution_arm_account_stream_unavailable"
        return None

    def _revoke_arm(self, reason, *, generation=None):
        reason = _arm_revocation_reason(reason)
        if generation is None and isinstance(self._arm_proof, Mapping):
            generation = self._arm_proof.get("connection_generation")
        if type(generation) is int and generation > 0:
            self._arm_revoked_generation = max(
                int(self._arm_revoked_generation or 0), generation
            )
        self.config["market_data_only"] = True
        self._arm_proof_sha256 = None
        self._arm_state_reader = None
        self._recovery_mode = False
        if self._arm_revoked_reason is None:
            self._arm_revoked_reason = reason
            self._arm_revoked_error_code = (
                reason
                if reason.startswith("execution_arm_")
                else "execution_arm_revoked"
            )

    def disarm_execution(self, reason="execution_arm_revoked", *, generation=None):
        """Revoke execution for the current CTP connection generation.

        The first bounded reason is retained so repeated Store rollback calls
        are idempotent and same-generation writes fail with a deterministic
        code. A strictly newer connection generation still requires a fresh
        proof and can be armed after a new read-only preflight.
        """
        with self.mutex:
            self._arm_managed = True
            self._revoke_arm(reason, generation=generation)
            return {
                "armed": False,
                "market_data_only": True,
                "reason": self._arm_revoked_reason,
                "revocation_reason": self._arm_revoked_reason,
                "revoked_generation": self._arm_revoked_generation,
            }

    def prepare_execution_authorization(
        self, reason="execution_authorization_prepared"
    ):
        """Return to reusable read-only state before a fresh preflight.

        Preparing a never-armed session is idempotent and does not create a
        revocation fence. If an arm existed, its connection generation remains
        fenced; only a proof from a strictly newer generation may supersede it.
        """
        with self.mutex:
            if self.closed:
                raise NormalizedApiError(
                    "prepare_execution_authorization",
                    "execution_session_closed",
                    definite_reject=True,
                )
            normalized_reason = _arm_revocation_reason(reason)
            prior_generation = (
                self._arm_proof.get("connection_generation")
                if isinstance(self._arm_proof, Mapping)
                else None
            )
            had_arm = bool(
                self._arm_proof_sha256 or not self.config["market_data_only"]
            )
            self._arm_managed = True
            if had_arm:
                self._revoke_arm(normalized_reason, generation=prior_generation)
            else:
                self.config["market_data_only"] = True
                self._arm_proof_sha256 = None
                self._arm_state_reader = None
                self._recovery_mode = False
                if self._arm_revoked_generation is None:
                    self._arm_revoked_reason = None
                    self._arm_revoked_error_code = None
            return {
                "prepared": True,
                "armed": False,
                "market_data_only": True,
                "reusable": True,
                "minimum_next_generation": (
                    self._arm_revoked_generation + 1
                    if self._arm_revoked_generation is not None
                    else None
                ),
                "reason": normalized_reason,
                "revoked_generation": self._arm_revoked_generation,
            }

    def _allow_new_generation_arm(self, normalized):
        """Clear a prior generation fence only for a strictly newer proof."""
        if self._arm_revoked_reason is None:
            return
        generation = normalized["connection_generation"]
        revoked_generation = self._arm_revoked_generation
        if revoked_generation is None or generation <= revoked_generation:
            raise NormalizedApiError(
                "arm_execution_from_preflight",
                self._arm_revoked_error_code or "execution_arm_revoked",
                definite_reject=True,
            )
        self._arm_revoked_reason = None
        self._arm_revoked_error_code = None
        self._arm_proof = None
        self._arm_proof_sha256 = None
        self._arm_state_reader = None

    def _current_arm_error(self):
        reader = self._arm_state_reader
        proof = self._arm_proof
        if not callable(reader) or not isinstance(proof, Mapping):
            return "execution_arm_state_unavailable"
        try:
            context = reader()
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "")
            if code.startswith("execution_arm_"):
                return code
            return "execution_arm_state_unavailable"
        return self._arm_context_error(proof, context, require_account_stream=True)

    def _require_arm_scope(self, operation, venue, symbol, exchange_id):
        if not self._arm_managed or self.config["market_data_only"]:
            return
        if venue != self._arm_venue:
            raise NormalizedApiError(
                operation, "execution_arm_venue_mismatch", definite_reject=True
            )
        expected = _canonical_ctp_instrument(self._arm_proof.get("instrument"))
        observed = (
            _canonical_ctp_instrument(symbol, exchange_id)
            if _canonical_ctp_exchange(exchange_id)
            else ""
        )
        if not expected or observed != expected:
            raise NormalizedApiError(
                operation, "execution_arm_instrument_mismatch", definite_reject=True
            )

    def _require_ctp_order_identity(self, operation, request):
        """Require caller-owned strategy/cycle metadata on managed CTP orders."""
        if not self._arm_managed or self._provider(self._arm_venue) != "CTP":
            return
        cycle_id = getattr(request, "execution_cycle_id", None)
        role = getattr(request, "execution_role", None)
        strategy_identity = getattr(request, "strategy_identity_sha256", None)
        if (
            not isinstance(cycle_id, str)
            or not cycle_id
            or role not in _EXECUTION_ROLES
            or not self.config["strategy_identity_sha256"]
            or strategy_identity != self.config["strategy_identity_sha256"]
        ):
            raise NormalizedApiError(
                operation,
                "execution_identity_missing_or_mismatch",
                definite_reject=True,
            )
        closing = getattr(request, "offset", None) in {
            "close",
            "close_today",
            "close_yesterday",
        }
        if (role == "entry") != (not closing):
            raise NormalizedApiError(
                operation, "execution_role_offset_mismatch", definite_reject=True
            )
        if role == "recovery_exit" and not self._recovery_mode:
            raise NormalizedApiError(
                operation, "execution_recovery_not_armed", definite_reject=True
            )

    @staticmethod
    def _recovery_cancel_matches(request, allowed):
        request_values = {
            "client_order_id": request.client_order_id,
            "order_id": request.order_id,
            "order_ref": request.order_ref,
            "front_id": request.front_id,
            "session_id": request.session_id,
        }
        compared = False
        for field, value in request_values.items():
            if value in (None, ""):
                continue
            compared = True
            expected = allowed.get(field)
            if expected in (None, "") or str(value) != str(expected):
                return False
        return compared

    def _require_recovery_action(self, operation, request, *, tracked=None):
        """Return the one bounded recovery allowance consumed by this request."""
        if not self._recovery_mode:
            return None
        plan = self._recovery_plan
        if not isinstance(plan, Mapping) or plan.get("status") != "RECOVERABLE":
            raise NormalizedApiError(
                operation, "execution_recovery_plan_unavailable", definite_reject=True
            )
        if operation == "make_order":
            self._require_ctp_order_identity(operation, request)
            if request.execution_role != "recovery_exit":
                raise NormalizedApiError(
                    operation, "execution_recovery_open_forbidden", definite_reject=True
                )
            if plan.get("allowed_cancels"):
                raise NormalizedApiError(
                    operation,
                    "execution_recovery_cancel_required",
                    definite_reject=True,
                )
            for index, allowed in enumerate(plan.get("allowed_closes") or ()):
                observed_instrument = _canonical_ctp_instrument(
                    request.symbol, request.exchange_id
                )
                if (
                    request.execution_cycle_id == allowed.get("execution_cycle_id")
                    and observed_instrument
                    == _canonical_ctp_instrument(
                        allowed.get("symbol"), allowed.get("exchange_id")
                    )
                    and request.side.value == allowed.get("side")
                    and request.position_side == allowed.get("position_side")
                    and request.offset == allowed.get("offset")
                    and request.quantity_unit == allowed.get("quantity_unit")
                    and request.quantity <= Decimal(str(allowed.get("quantity") or "0"))
                ):
                    return ("close", index, format(request.quantity, "f"))
            raise NormalizedApiError(
                operation,
                "execution_recovery_close_exceeds_proof",
                definite_reject=True,
            )
        if operation == "cancel_order":
            if tracked is None or tracked.get("execution_cycle_id") != plan.get(
                "execution_cycle_id"
            ):
                raise NormalizedApiError(
                    operation,
                    "execution_recovery_foreign_cancel",
                    definite_reject=True,
                )
            for index, allowed in enumerate(plan.get("allowed_cancels") or ()):
                if self._recovery_cancel_matches(request, allowed):
                    return ("cancel", index, None)
            raise NormalizedApiError(
                operation, "execution_recovery_foreign_cancel", definite_reject=True
            )
        raise NormalizedApiError(
            operation, "execution_recovery_operation_forbidden", definite_reject=True
        )

    def _consume_recovery_action(self, allowance):
        if allowance is None or not isinstance(self._recovery_plan, dict):
            return
        kind, index, quantity = allowance
        key = "allowed_closes" if kind == "close" else "allowed_cancels"
        values = list(self._recovery_plan.get(key) or ())
        if not 0 <= index < len(values):
            raise NormalizedApiError(
                "execution_recovery",
                "execution_recovery_plan_changed",
                definite_reject=True,
            )
        if kind == "close":
            remaining = Decimal(str(values[index]["quantity"])) - Decimal(quantity)
            if remaining > 0:
                values[index] = {**values[index], "quantity": format(remaining, "f")}
            else:
                values.pop(index)
        else:
            values.pop(index)
        self._recovery_plan[key] = values

    def _arm_load_state(self):
        names = (
            "orders",
            "used_ids",
            "reserved_ids",
            "historical_unknown",
            "pending",
            "trade_ids",
            "accounts",
            "fencing_epoch",
            "persistence_failed",
            "risk_record",
            "risk_error",
            "risk_measurement_error",
            "risk_transition_error",
            "risk_check_in_progress",
            "risk_last_verified_monotonic_ns",
            "_journal_loss_state",
            "_journal_loss_breached_at",
            "_pending_loss_reset_id",
            "_pending_loss_reset_at",
            "_committed_loss_reset_id",
        )
        return {name: deepcopy(getattr(self, name)) for name in names}

    def _restore_arm_load_state(self, state):
        for name, value in state.items():
            setattr(self, name, value)

    def prepare_recovery(self, proof, state_reader, *, venue=None):
        """Bind a new connection proof while keeping every write path closed."""
        operation = "prepare_execution_recovery"
        acquired_here = False
        load_state = None
        with self.mutex:
            previous_identity = deepcopy(self._ctp_execution_identity)
            previous_venue = self._arm_venue
            try:
                normalized, proof_sha256 = _execution_arm_proof(proof)
                if self.closed:
                    raise NormalizedApiError(
                        operation, "execution_session_closed", definite_reject=True
                    )
                if not callable(state_reader):
                    raise NormalizedApiError(
                        operation,
                        "execution_arm_state_unavailable",
                        definite_reject=True,
                    )
                if not self.config["strategy_identity_sha256"]:
                    raise NormalizedApiError(
                        operation,
                        "strategy_identity_required",
                        definite_reject=True,
                    )
                ctp_venues = tuple(
                    item
                    for item in self.exchange_names
                    if self._provider(item) == "CTP"
                )
                arm_venue = str(
                    venue or (ctp_venues[0] if len(ctp_venues) == 1 else "")
                )
                if (
                    not arm_venue
                    or self._provider(arm_venue) != "CTP"
                    or set(self.exchange_names) != {arm_venue}
                ):
                    raise NormalizedApiError(
                        operation, "single_ctp_session_required", definite_reject=True
                    )
                if not self.config["market_data_only"] and not self._recovery_mode:
                    raise NormalizedApiError(
                        operation, "execution_already_armed", definite_reject=True
                    )
                self._allow_new_generation_arm(normalized)
                context = state_reader()
                error = self._arm_context_error(normalized, context)
                if error is not None:
                    raise NormalizedApiError(operation, error, definite_reject=True)
                if (
                    self.config["require_order_journal"] is not True
                    or self.path is None
                ):
                    raise NormalizedApiError(
                        operation, "order_journal_required", definite_reject=True
                    )
                environment = (
                    str(
                        self.config["required_environments"].get(arm_venue)
                        or normalized["environment_profile"]
                    )
                    .strip()
                    .lower()
                )
                bound_identity = {
                    "provider": "CTP",
                    "environment": environment,
                    "account_id": normalized["account_fingerprint"],
                    "account_fingerprint": normalized["account_fingerprint"],
                }
                if previous_identity is not None and not _recorded_identity_matches(
                    previous_identity, bound_identity
                ):
                    raise NormalizedApiError(
                        operation,
                        "execution_arm_account_fingerprint_mismatch",
                        definite_reject=True,
                    )
                self._arm_venue = arm_venue
                self._ctp_execution_identity = bound_identity
                self._arm_managed = True
                self.config["market_data_only"] = True
                self._arm_proof_sha256 = None
                self._arm_state_reader = None
                self._recovery_mode = False
                self._recovery_refresh_in_progress = True
                self._recovery_plan = None
                self._recovery_completed = False
                self._recovery_journal_error = None

                acquired_here = self.path is not None and self.lock_file is None
                if acquired_here:
                    load_state = self._arm_load_state()
                    self._acquire_lock()
                    context = state_reader()
                    error = self._arm_context_error(normalized, context)
                    if error is not None:
                        raise NormalizedApiError(operation, error, definite_reject=True)
                    try:
                        self._load_journal()
                    except NormalizedApiError as exc:
                        if load_state is not None:
                            acquired_epoch = self.fencing_epoch
                            self._restore_arm_load_state(load_state)
                            self.fencing_epoch = acquired_epoch
                        self._recovery_journal_error = exc.code
                    self._load_risk_state()

                context = state_reader()
                error = self._arm_context_error(normalized, context)
                if error is not None:
                    raise NormalizedApiError(operation, error, definite_reject=True)
                self._arm_proof = normalized
                self._last_arm_proof_sha256 = proof_sha256
                self._arm_state_reader = state_reader
                return {
                    "prepared": True,
                    "armed": False,
                    "market_data_only": True,
                    "proof_sha256": proof_sha256,
                    "connection_generation": normalized["connection_generation"],
                    "fencing_epoch": self.fencing_epoch,
                }
            except Exception:
                self._recovery_refresh_in_progress = False
                if acquired_here and self.lock_file is not None:
                    self._release_writer_leases()
                    if load_state is not None:
                        self._restore_arm_load_state(load_state)
                self._ctp_execution_identity = previous_identity
                self._arm_venue = previous_venue
                self.config["market_data_only"] = True
                raise

    def require_bound_read(self, operation, *, venue=None):
        """Fence a recovery query without opening any execution capability."""
        with self.mutex:
            if self.closed:
                raise NormalizedApiError(
                    operation, "execution_session_closed", definite_reject=True
                )
            if (
                not self._arm_managed
                or not isinstance(self._arm_proof, Mapping)
                or not callable(self._arm_state_reader)
            ):
                raise NormalizedApiError(
                    operation,
                    "execution_recovery_not_prepared",
                    definite_reject=True,
                )
            if venue not in (None, self._arm_venue):
                raise NormalizedApiError(
                    operation, "execution_arm_venue_mismatch", definite_reject=True
                )
            try:
                context = self._arm_state_reader()
            except Exception:
                raise NormalizedApiError(
                    operation,
                    "execution_arm_state_unavailable",
                    definite_reject=True,
                ) from None
            error = self._arm_context_error(self._arm_proof, context)
            if error is not None:
                self._revoke_arm(
                    error,
                    generation=self._arm_proof.get("connection_generation"),
                )
                raise NormalizedApiError(operation, error, definite_reject=True)
            self._assert_writer_lease(operation)

    def recovery_event_revision(self):
        """Return the process-local order/trade event fence for query barriers."""
        with self.mutex:
            return self._recovery_event_revision

    def recovery_private_event_revision(self):
        """Return the private-event fence used by SDK queue query barriers."""
        with self.mutex:
            return self._recovery_private_event_revision

    def recovery_private_ingress_revision(self):
        """Return the producer-side private queue ingress fence."""
        with self.mutex:
            return self._recovery_private_ingress_revision

    def note_private_ingress(self, venue, ordered_after_write=False):
        """Record a producer ingress and revoke a pre-write execution arm."""
        with self.mutex:
            self._recovery_private_ingress_revision += 1
            should_revoke = bool(
                self._arm_managed
                and self._arm_venue == venue
                and not self.config["market_data_only"]
                and not ordered_after_write
                and self.submit_calls == self._arm_submit_calls
                and self.cancel_calls == self._arm_cancel_calls
            )
            if should_revoke:
                # An order/trade callback before this arm has dispatched any
                # write belongs to pre-arm state.  Close the SDK lease now;
                # the queue owner closes the native gate under its transition lock.
                self.config["market_data_only"] = True
                self._arm_proof_sha256 = None
                self._recovery_mode = False
                self._recovery_refresh_in_progress = True
            return should_revoke

    @staticmethod
    def _recovery_value(row, *names):
        for name in names:
            value = row.get(name)
            if value not in (None, ""):
                return value
        return None

    @classmethod
    def _recovery_quantity(cls, row, *names):
        value = cls._recovery_value(row, *names)
        if isinstance(value, bool) or value in (None, ""):
            raise ValueError("missing recovery quantity")
        quantity = Decimal(str(value))
        if (
            not quantity.is_finite()
            or quantity < 0
            or quantity != quantity.to_integral_value()
        ):
            raise ValueError("invalid recovery quantity")
        return quantity

    @classmethod
    def _recovery_row_instrument(cls, row):
        symbol = cls._recovery_value(
            row, "symbol", "instrument", "instrument_id", "InstrumentID"
        )
        exchange_id = cls._recovery_value(row, "exchange_id", "ExchangeID")
        return _canonical_ctp_instrument(symbol, exchange_id)

    @classmethod
    def _recovery_row_side(cls, row, *, position=False):
        names = (
            ("position_side", "position_direction", "PosiDirection", "direction")
            if position
            else ("side", "direction", "Direction")
        )
        value = str(cls._recovery_value(row, *names) or "").strip().lower()
        if position:
            return {
                "2": "long",
                "3": "short",
                "buy": "long",
                "sell": "short",
            }.get(value, value)
        return {"0": "buy", "1": "sell"}.get(value, value)

    @staticmethod
    def _canonical_recovery_offset(value):
        value = str(value or "").strip().lower().replace("-", "_")
        return {
            "0": "open",
            "1": "close",
            "3": "close_today",
            "4": "close_yesterday",
            "closetoday": "close_today",
            "closeyesterday": "close_yesterday",
        }.get(value, value)

    @staticmethod
    def _canonical_recovery_number(value):
        value = Decimal(str(value))
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text in {"", "-0"} else text

    def _recovery_record_identity_error(self, row, *, proof=None):
        proof = proof or self._arm_proof
        if not isinstance(proof, Mapping):
            return "recovery_proof_missing"
        if row.get("schema_version") != _JOURNAL_SCHEMA_VERSION:
            return "recovery_journal_schema_mismatch"
        if row.get("strategy_id") != self.config["strategy_id"]:
            return "recovery_strategy_id_mismatch"
        strategy_identity = row.get("strategy_identity_sha256")
        if strategy_identity != self.config["strategy_identity_sha256"]:
            return "recovery_strategy_identity_mismatch"
        if self._provider(row.get("exchange_name")) != "CTP":
            return "recovery_venue_mismatch"
        if row.get("account_id") != proof["account_fingerprint"]:
            return "recovery_account_mismatch"
        if str(row.get("trading_day") or "") != proof["trading_day"]:
            return "recovery_trading_day_mismatch"
        if self._recovery_row_instrument(row) != proof["instrument"]:
            return "recovery_instrument_mismatch"
        cycle_id = row.get("execution_cycle_id")
        if not isinstance(cycle_id, str) or not cycle_id or len(cycle_id) > 128:
            return "recovery_cycle_identity_missing"
        if row.get("execution_role") not in _EXECUTION_ROLES:
            return "recovery_execution_role_missing"
        arm_hash = str(row.get("execution_arm_proof_sha256") or "")
        if (
            len(arm_hash) != 64
            or arm_hash != arm_hash.lower()
            or any(character not in "0123456789abcdef" for character in arm_hash)
        ):
            return "recovery_arm_proof_missing"
        generation = row.get("connection_generation")
        if (
            type(generation) is not int
            or generation <= 0
            or generation > proof["connection_generation"]
        ):
            return "recovery_generation_invalid"
        epoch = row.get("fencing_epoch")
        if type(epoch) is not int or epoch <= 0 or epoch > self.fencing_epoch:
            return "recovery_fencing_invalid"
        embedded = row.get("ledger_identity")
        if not isinstance(embedded, Mapping) or not _recorded_identity_matches(
            embedded, self._ctp_execution_identity
        ):
            return "recovery_ledger_identity_mismatch"
        return None

    @staticmethod
    def _is_prior_recovery_trading_day(value, current):
        """Return whether ``value`` is one canonical trading day before ``current``."""
        value = str(value or "")
        current = str(current or "")
        try:
            parsed_value = time.strptime(value, "%Y%m%d")
            parsed_current = time.strptime(current, "%Y%m%d")
        except ValueError:
            return False
        return bool(
            time.strftime("%Y%m%d", parsed_value) == value
            and time.strftime("%Y%m%d", parsed_current) == current
            and value < current
        )

    def _recovery_journal_records(
        self,
        *,
        proof=None,
        include_prior_trading_days=False,
    ):
        if self._recovery_journal_error:
            return (), (), (self._recovery_journal_error,)
        if self.path is None or not self.path.exists():
            return (), (), ("recovery_journal_missing",)
        intents = []
        trades = []
        errors = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError
                event = row.get("event")
                if event not in {
                    "intent",
                    "cancel_intent",
                    "order_update",
                    "trade",
                    "trade_update",
                }:
                    continue
                record_proof = proof
                if include_prior_trading_days and isinstance(proof, Mapping):
                    row_day = str(row.get("trading_day") or "")
                    proof_day = str(proof.get("trading_day") or "")
                    if self._is_prior_recovery_trading_day(row_day, proof_day):
                        # Native connection generations are process-local.  A
                        # prior day's positive generation must not be compared
                        # with a fresh process's generation counter.
                        record_proof = {
                            **proof,
                            "trading_day": row_day,
                            "connection_generation": row.get("connection_generation"),
                        }
                error = self._recovery_record_identity_error(
                    row,
                    proof=record_proof,
                )
                if error is not None:
                    errors.append(error)
                    continue
                if event == "intent":
                    intents.append(row)
                elif event == "trade":
                    trades.append(row)
        except Exception:
            errors.append("unreadable_journal")
        return tuple(intents), tuple(trades), tuple(sorted(set(errors)))

    def _journal_requires_recovery(self, proof):
        """Detect durable CTP exposure before any ordinary arm opens the gate."""
        if self._recovery_completed and not self._recovery_plan_private_fence_current(
            self._recovery_plan
        ):
            return True
        if (
            self.path is None
            or not self.path.is_file()
            or self.path.stat().st_size == 0
        ):
            return False
        intents, trades, errors = self._recovery_journal_records(
            proof=proof,
            include_prior_trading_days=True,
        )
        if (
            errors
            or self.historical_unknown
            or any(not state.get("terminal") for state in self.orders.values())
        ):
            return True
        try:
            for state in self.orders.values():
                if not state.get("terminal"):
                    continue
                last_update = state.get("last_update") or {}
                filled = self._recovery_quantity(last_update, "filled")
                durable_fills = sum(
                    (
                        self._recovery_quantity(
                            trade, "size", "quantity", "volume", "Volume"
                        )
                        for trade in trades
                        if str(trade.get("trading_day") or "")
                        == str(
                            last_update.get("trading_day")
                            or state.get("trading_day")
                            or ""
                        )
                        and self._recovery_order_matches(state, trade)
                    ),
                    Decimal(0),
                )
                if filled != durable_fills:
                    return True
        except (InvalidOperation, TypeError, ValueError):
            return True
        intent_cycles = {
            (str(intent.get("trading_day") or ""), intent.get("execution_cycle_id"))
            for intent in intents
        }
        exposure = defaultdict(lambda: {"long": Decimal(0), "short": Decimal(0)})
        try:
            for trade in trades:
                cycle = (
                    str(trade.get("trading_day") or ""),
                    trade["execution_cycle_id"],
                )
                if (
                    cycle not in intent_cycles
                    or sum(
                        str(intent.get("trading_day") or "") == cycle[0]
                        and intent.get("execution_cycle_id") == cycle[1]
                        and self._recovery_order_matches(intent, trade)
                        for intent in intents
                    )
                    != 1
                ):
                    return True
                quantity = self._recovery_quantity(
                    trade, "size", "quantity", "volume", "Volume"
                )
                side = self._recovery_row_side(trade)
                offset = self._canonical_recovery_offset(
                    self._recovery_value(trade, "offset", "trade_offset", "OffsetFlag")
                )
                role = trade["execution_role"]
                if role == "entry" and offset == "open" and side in {"buy", "sell"}:
                    exposure[cycle]["long" if side == "buy" else "short"] += quantity
                    continue
                if role not in {"exit", "recovery_exit"} or offset not in {
                    "close",
                    "close_today",
                    "close_yesterday",
                }:
                    return True
                position_side = self._recovery_row_side(trade, position=True)
                if position_side not in {"long", "short"}:
                    position_side = "long" if side == "sell" else "short"
                if (position_side == "long" and side != "sell") or (
                    position_side == "short" and side != "buy"
                ):
                    return True
                exposure[cycle][position_side] -= quantity
        except (InvalidOperation, KeyError, OSError, TypeError, ValueError):
            return True
        return any(
            amount != 0
            for cycle_exposure in exposure.values()
            for amount in cycle_exposure.values()
        )

    @staticmethod
    def _recovery_order_active(row):
        status = (
            str(
                row.get("status")
                or row.get("OrderStatus")
                or row.get("order_status")
                or ""
            )
            .strip()
            .lower()
        )
        if status in {
            "0",
            "2",
            "4",
            "5",
            "filled",
            "completed",
            "canceled",
            "cancelled",
            "expired",
            "rejected",
        }:
            return False
        remaining = _ExecutionSession._recovery_value(
            row, "remaining", "VolumeTotal", "volume_total"
        )
        if remaining not in (None, ""):
            try:
                return Decimal(str(remaining)) > 0
            except InvalidOperation:
                return True
        return True

    @staticmethod
    def _recovery_order_matches(left, right):
        fields = (
            ("client_order_id", "OrderRef", "order_ref"),
            ("order_id", "OrderSysID", "venue_order_id"),
        )
        compared = False
        for names in fields:
            left_value = _ExecutionSession._recovery_value(left, *names)
            right_value = _ExecutionSession._recovery_value(right, *names)
            if left_value in (None, "") or right_value in (None, ""):
                continue
            compared = True
            if str(left_value).strip() != str(right_value).strip():
                return False
        return compared

    def _recovery_remote_identity_error(self, row):
        proof = self._arm_proof
        if not isinstance(proof, Mapping):
            return "recovery_proof_missing"
        account = self._recovery_value(row, "account_id", "AccountID", "InvestorID")
        day = self._recovery_value(row, "trading_day", "TradingDay")
        if str(account or "") != proof["account_fingerprint"]:
            return "recovery_remote_account_missing_or_mismatch"
        if str(day or "") != proof["trading_day"]:
            return "recovery_remote_trading_day_missing_or_mismatch"
        if row.get("connection_generation") != proof["connection_generation"]:
            return "recovery_remote_generation_missing_or_mismatch"
        if row.get("evidence_complete") is not True:
            return "recovery_remote_evidence_incomplete"
        if self._recovery_row_instrument(row) != proof["instrument"]:
            return "recovery_remote_instrument_mismatch"
        return None

    def _recovery_active_order_matches_intent(self, intent, order):
        if not self._recovery_order_matches(intent, order):
            return False
        try:
            intent_side = self._recovery_row_side(intent)
            order_side = self._recovery_row_side(order)
            intent_offset = self._canonical_recovery_offset(
                self._recovery_value(intent, "offset", "CombOffsetFlag")
            )
            order_offset = self._canonical_recovery_offset(
                self._recovery_value(
                    order,
                    "offset",
                    "CombOffsetFlag",
                    "OffsetFlag",
                )
            )
            intent_quantity = self._recovery_quantity(intent, "quantity", "size")
            order_quantity = self._recovery_quantity(
                order,
                "quantity",
                "size",
                "VolumeTotalOriginal",
                "volume_total_original",
            )
        except (InvalidOperation, TypeError, ValueError):
            return False
        hedge_flag = str(
            self._recovery_value(order, "hedge_flag", "CombHedgeFlag", "HedgeFlag")
            or ""
        ).strip()
        return bool(
            intent_side in {"buy", "sell"}
            and order_side == intent_side
            and intent_offset
            in {
                "open",
                "close",
                "close_today",
                "close_yesterday",
            }
            and order_offset == intent_offset
            and intent_quantity == order_quantity
            and hedge_flag == "1"
        )

    @classmethod
    def _recovery_trade_key(cls, row):
        return (
            str(cls._recovery_value(row, "trading_day", "TradingDay") or ""),
            cls._recovery_row_instrument(row),
            str(cls._recovery_value(row, "trade_id", "TradeID") or ""),
        )

    def _recovery_barrier_errors(self, snapshot, barrier):
        proof = self._arm_proof
        if not isinstance(proof, Mapping) or not isinstance(barrier, Mapping):
            return ["recovery_query_barrier_missing"]
        if barrier.get("schema_version") != "bt-api-py.ctp-recovery-query-barrier.v1":
            return ["recovery_query_barrier_schema_invalid"]
        material = dict(barrier)
        reported_hash = material.pop("barrier_sha256", None)
        try:
            expected_hash = hashlib.sha256(
                json.dumps(
                    material,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            snapshot_hash = hashlib.sha256(
                json.dumps(
                    snapshot,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError):
            return ["recovery_query_barrier_not_canonical"]
        errors = []
        if reported_hash != expected_hash:
            errors.append("recovery_query_barrier_hash_mismatch")
        if barrier.get("stable") is not True:
            errors.append("recovery_snapshot_not_stable")
        rounds = barrier.get("rounds")
        if not isinstance(rounds, (list, tuple)) or len(rounds) != 2:
            errors.append("recovery_query_rounds_incomplete")
            return errors
        request_ids = []
        account_hashes = []
        full_hashes = []
        for round_result in rounds:
            if not isinstance(round_result, Mapping):
                errors.append("recovery_query_round_invalid")
                continue
            ids = round_result.get("request_ids")
            if not isinstance(ids, Mapping) or set(ids) != {
                "account",
                "positions",
                "orders",
                "trades",
            }:
                errors.append("recovery_query_ids_incomplete")
                continue
            request_ids.extend(ids.values())
            account_hashes.append(round_result.get("account_snapshot_sha256"))
            full_hashes.append(round_result.get("full_snapshot_sha256"))
            if (
                round_result.get("account_fingerprint") != proof["account_fingerprint"]
                or round_result.get("trading_day") != proof["trading_day"]
                or round_result.get("connection_generation")
                != proof["connection_generation"]
                or round_result.get("snapshot_sha256") != snapshot_hash
            ):
                errors.append("recovery_query_identity_mismatch")
        if (
            len(request_ids) != 8
            or any(type(value) is not int or value <= 0 for value in request_ids)
            or len(set(request_ids)) != 8
        ):
            errors.append("recovery_query_id_reused")

        def valid_sha256(value):
            return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))

        if (
            len(account_hashes) != 2
            or any(not valid_sha256(value) for value in account_hashes)
            or len(set(account_hashes)) != 1
            or len(full_hashes) != 2
            or any(not valid_sha256(value) for value in full_hashes)
            or len(set(full_hashes)) != 1
        ):
            errors.append("recovery_query_full_snapshot_not_stable")
        revisions = barrier.get("event_revisions")
        if (
            not isinstance(revisions, Mapping)
            or set(revisions) != {"start", "middle", "end"}
            or any(type(value) is not int or value < 0 for value in revisions.values())
            or len(set(revisions.values())) != 1
            or revisions.get("end") != self._recovery_event_revision
        ):
            errors.append("recovery_event_fence_invalid")
        private_revisions = barrier.get("private_event_revisions")
        if (
            not isinstance(private_revisions, Mapping)
            or set(private_revisions) != {"start", "end"}
            or any(
                type(value) is not int or value < 0
                for value in private_revisions.values()
            )
            or private_revisions.get("start") != private_revisions.get("end")
            or private_revisions.get("end") != self._recovery_private_event_revision
        ):
            errors.append("recovery_private_event_fence_invalid")
        ingress_revisions = barrier.get("private_ingress_revisions")
        if (
            not isinstance(ingress_revisions, Mapping)
            or set(ingress_revisions) != {"start", "end"}
            or any(
                type(value) is not int or value < 0
                for value in ingress_revisions.values()
            )
            or ingress_revisions.get("start") != ingress_revisions.get("end")
            or ingress_revisions.get("end") != self._recovery_private_ingress_revision
        ):
            errors.append("recovery_private_ingress_fence_invalid")
        ingress_epochs = barrier.get("private_ingress_epochs")
        if (
            not isinstance(ingress_epochs, Mapping)
            or set(ingress_epochs) != {"start", "end"}
            or any(
                type(value) is not int or value < 0 for value in ingress_epochs.values()
            )
            or ingress_epochs.get("start") != ingress_epochs.get("end")
        ):
            errors.append("recovery_private_ingress_epoch_invalid")
        ingress_pending = barrier.get("private_ingress_pending")
        if (
            not isinstance(ingress_pending, Mapping)
            or set(ingress_pending) != {"start", "end"}
            or any(type(value) is not int for value in ingress_pending.values())
            or any(value != 0 for value in ingress_pending.values())
        ):
            errors.append("recovery_private_ingress_pending")
        return errors

    def _recovery_plan_private_fence_current(self, plan):
        """Return whether no private order/trade event arrived after ``plan``."""
        barrier = plan.get("query_barrier") if isinstance(plan, Mapping) else None
        event_revisions = (
            barrier.get("private_event_revisions")
            if isinstance(barrier, Mapping)
            else None
        )
        ingress_revisions = (
            barrier.get("private_ingress_revisions")
            if isinstance(barrier, Mapping)
            else None
        )
        return bool(
            isinstance(event_revisions, Mapping)
            and type(event_revisions.get("end")) is int
            and event_revisions.get("end") == self._recovery_private_event_revision
            and isinstance(ingress_revisions, Mapping)
            and type(ingress_revisions.get("end")) is int
            and ingress_revisions.get("end") == self._recovery_private_ingress_revision
        )

    def build_recovery_plan(
        self,
        snapshot,
        *,
        stable=None,
        barrier=None,
        failure_reason=None,
    ):
        """Return the closed recovery decision derived only from SDK journal evidence."""
        operation = "prepare_execution_recovery"
        with self.mutex:
            self.require_bound_read(operation, venue=self._arm_venue)
            proof = self._arm_proof
            proof_sha256 = self._last_arm_proof_sha256
            zero = dict.fromkeys(_RECOVERY_POSITION_KEYS, "0")
            remote_position = dict(zero)
            owned_position = dict(zero)
            reasons = []
            if failure_reason not in (None, ""):
                reason = str(failure_reason).strip().lower()
                reasons.append(
                    reason
                    if _ARM_REASON_RE.fullmatch(reason)
                    else "recovery_query_failed"
                )
            intents, journal_trades, journal_errors = self._recovery_journal_records()
            reasons.extend(journal_errors)
            journal_sha256 = None
            if self.path is not None and self.path.is_file():
                try:
                    journal_sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
                except OSError:
                    reasons.append("recovery_journal_unreadable")
            if not isinstance(snapshot, Mapping) or set(snapshot) != {
                "positions",
                "orders",
                "trades",
            }:
                reasons.append("recovery_snapshot_invalid")
                positions = orders = trades = ()
            else:
                positions = snapshot["positions"]
                orders = snapshot["orders"]
                trades = snapshot["trades"]
                if not all(
                    isinstance(value, (list, tuple)) for value in snapshot.values()
                ):
                    reasons.append("recovery_snapshot_invalid")
                    positions = orders = trades = ()
            del stable  # Compatibility only; callers cannot self-certify stability.
            reasons.extend(self._recovery_barrier_errors(snapshot, barrier))

            frozen_position = {key: Decimal(0) for key in _RECOVERY_POSITION_KEYS}
            frozen_by_side = {"long": Decimal(0), "short": Decimal(0)}
            try:
                for row in positions:
                    if not isinstance(row, Mapping):
                        raise ValueError
                    quantity = self._recovery_quantity(
                        row, "quantity", "position_volume", "Position", "volume", "size"
                    )
                    if quantity == 0:
                        continue
                    identity_error = self._recovery_remote_identity_error(row)
                    if identity_error is not None:
                        reasons.append(identity_error)
                        continue
                    side = self._recovery_row_side(row, position=True)
                    if side not in {"long", "short"}:
                        reasons.append("ambiguous_position_side")
                        continue
                    today_value = self._recovery_value(
                        row, "today", "today_position", "TodayPosition"
                    )
                    yesterday_value = self._recovery_value(
                        row, "yesterday", "yd_position", "YdPosition"
                    )
                    if today_value is None or yesterday_value is None:
                        reasons.append("position_bucket_evidence_missing")
                        continue
                    today = self._recovery_quantity({"value": today_value}, "value")
                    yesterday = self._recovery_quantity(
                        {"value": yesterday_value}, "value"
                    )
                    if today < 0 or yesterday < 0 or today + yesterday != quantity:
                        reasons.append("position_bucket_mismatch")
                        continue
                    remote_position[f"{side}_today"] = self._canonical_recovery_number(
                        Decimal(remote_position[f"{side}_today"]) + today
                    )
                    remote_position[f"{side}_yesterday"] = (
                        self._canonical_recovery_number(
                            Decimal(remote_position[f"{side}_yesterday"]) + yesterday
                        )
                    )
                    frozen_names = (
                        ("long_frozen", "LongFrozen")
                        if side == "long"
                        else ("short_frozen", "ShortFrozen")
                    )
                    frozen_value = self._recovery_value(row, *frozen_names)
                    if frozen_value is None:
                        reasons.append("position_frozen_evidence_missing")
                        continue
                    frozen = self._recovery_quantity({"value": frozen_value}, "value")
                    if frozen > quantity:
                        reasons.append("position_frozen_exceeds_position")
                    else:
                        frozen_by_side[side] += frozen
                    exchange = proof["instrument"].partition(".")[0]
                    if frozen and today and yesterday and exchange in {"SHFE", "INE"}:
                        # CTP exposes a side total here; without a dated frozen
                        # bucket an automatic close cannot choose a safe offset.
                        reasons.append("position_frozen_bucket_ambiguous")
                    elif frozen:
                        bucket = "today" if today else "yesterday"
                        frozen_position[f"{side}_{bucket}"] += frozen
            except (InvalidOperation, ValueError, TypeError):
                reasons.append("ctp_position_schema_invalid")

            intent_by_cycle = defaultdict(list)
            intent_order_keys = set()
            for intent in intents:
                intent_by_cycle[intent["execution_cycle_id"]].append(intent)
                client_identity = str(
                    self._recovery_value(
                        intent,
                        "client_order_id",
                        "order_ref",
                        "OrderRef",
                    )
                    or ""
                ).strip()
                if not client_identity or client_identity in intent_order_keys:
                    reasons.append("recovery_intent_identity_invalid")
                intent_order_keys.add(client_identity)
            journal_trade_by_key = {}
            cycle_exposure = defaultdict(
                lambda: {"long": Decimal(0), "short": Decimal(0)}
            )
            try:
                for trade in journal_trades:
                    key = self._recovery_trade_key(trade)
                    if not key[2] or key in journal_trade_by_key:
                        reasons.append("recovery_trade_identity_invalid")
                        continue
                    journal_trade_by_key[key] = trade
                    quantity = self._recovery_quantity(
                        trade, "size", "quantity", "volume", "Volume"
                    )
                    side = self._recovery_row_side(trade)
                    offset = self._canonical_recovery_offset(
                        self._recovery_value(
                            trade, "offset", "trade_offset", "OffsetFlag"
                        )
                    )
                    cycle = trade["execution_cycle_id"]
                    role = trade["execution_role"]
                    if side not in {"buy", "sell"}:
                        reasons.append("recovery_trade_side_invalid")
                    elif role == "entry" and offset == "open":
                        cycle_exposure[cycle][
                            "long" if side == "buy" else "short"
                        ] += quantity
                    elif role in {"exit", "recovery_exit"} and offset in {
                        "close",
                        "close_today",
                        "close_yesterday",
                    }:
                        position_side = self._recovery_row_side(trade, position=True)
                        if position_side not in {"long", "short"}:
                            position_side = "long" if side == "sell" else "short"
                        if (position_side == "long" and side != "sell") or (
                            position_side == "short" and side != "buy"
                        ):
                            reasons.append("recovery_trade_close_direction_invalid")
                        else:
                            cycle_exposure[cycle][position_side] -= quantity
                    else:
                        reasons.append("recovery_trade_role_offset_invalid")
            except (InvalidOperation, ValueError, TypeError, KeyError):
                reasons.append("ctp_trade_schema_invalid")

            remote_trade_keys = set()
            for trade in trades:
                if not isinstance(trade, Mapping):
                    reasons.append("ctp_trade_schema_invalid")
                    continue
                identity_error = self._recovery_remote_identity_error(trade)
                if identity_error is not None:
                    reasons.append(identity_error)
                    continue
                key = self._recovery_trade_key(trade)
                if not key[2] or key in remote_trade_keys:
                    reasons.append("recovery_remote_trade_identity_invalid")
                    continue
                remote_trade_keys.add(key)
                if key not in journal_trade_by_key:
                    reasons.append("external_or_unowned_trade_present")
            if set(journal_trade_by_key) != remote_trade_keys:
                reasons.append("recovery_trade_ledger_mismatch")

            active_orders = []
            order_cycles = set()
            for order in orders:
                if not isinstance(order, Mapping):
                    reasons.append("ctp_order_schema_invalid")
                    continue
                if not self._recovery_order_active(order):
                    continue
                identity_error = self._recovery_remote_identity_error(order)
                if identity_error is not None:
                    reasons.append(identity_error)
                    continue
                matches = [
                    intent
                    for intent in intents
                    if self._recovery_active_order_matches_intent(intent, order)
                ]
                if len(matches) != 1:
                    reasons.append("external_or_unowned_active_order")
                    continue
                intent = matches[0]
                cycle = intent["execution_cycle_id"]
                order_cycles.add(cycle)
                active_orders.append((order, intent))

            nonzero_cycles = {
                cycle
                for cycle, exposure in cycle_exposure.items()
                if exposure["long"] != 0 or exposure["short"] != 0
            }
            cycles = nonzero_cycles | order_cycles
            if len(cycles) > 1:
                reasons.append("multiple_execution_cycles_present")
            cycle_id = next(iter(cycles), None) if len(cycles) == 1 else None
            if cycle_id is not None:
                exposure = cycle_exposure[cycle_id]
                if exposure["long"] < 0 or exposure["short"] < 0:
                    reasons.append("recovery_owned_position_negative")
                remote_long = Decimal(remote_position["long_today"]) + Decimal(
                    remote_position["long_yesterday"]
                )
                remote_short = Decimal(remote_position["short_today"]) + Decimal(
                    remote_position["short_yesterday"]
                )
                if exposure["long"] != remote_long or exposure["short"] != remote_short:
                    reasons.append("recovery_owned_position_mismatch")
                else:
                    owned_position = dict(remote_position)
            elif any(Decimal(value) != 0 for value in remote_position.values()):
                reasons.append("unowned_position_present")

            remote_long = Decimal(remote_position["long_today"]) + Decimal(
                remote_position["long_yesterday"]
            )
            remote_short = Decimal(remote_position["short_today"]) + Decimal(
                remote_position["short_yesterday"]
            )
            if remote_long > 0 and remote_short > 0:
                reasons.append("dual_side_position_present")

            allowed_cancels = []
            for order, intent in active_orders:
                allowed_cancels.append(
                    {
                        "execution_cycle_id": intent["execution_cycle_id"],
                        "symbol": self._recovery_value(
                            intent, "symbol", "InstrumentID", "instrument_id"
                        ),
                        "exchange_id": self._recovery_value(
                            intent, "exchange_id", "ExchangeID"
                        ),
                        "client_order_id": self._recovery_value(
                            order, "client_order_id", "OrderRef", "order_ref"
                        ),
                        "order_id": self._recovery_value(
                            order, "order_id", "OrderSysID", "venue_order_id"
                        ),
                        "order_ref": self._recovery_value(
                            order, "order_ref", "OrderRef", "client_order_id"
                        ),
                        "front_id": self._recovery_value(order, "front_id", "FrontID"),
                        "session_id": self._recovery_value(
                            order, "session_id", "SessionID"
                        ),
                    }
                )
            allowed_closes = []
            if not allowed_cancels and cycle_id is not None and not reasons:
                exchange_id, instrument_id = proof["instrument"].split(".", 1)
                if exchange_id in {"SHFE", "INE"}:
                    close_buckets = (
                        ("long", "today", "close_today"),
                        ("long", "yesterday", "close_yesterday"),
                        ("short", "today", "close_today"),
                        ("short", "yesterday", "close_yesterday"),
                    )
                    for position_side, bucket, offset in close_buckets:
                        key = f"{position_side}_{bucket}"
                        available = Decimal(remote_position[key]) - frozen_position[key]
                        if available <= 0:
                            continue
                        allowed_closes.append(
                            {
                                "execution_cycle_id": cycle_id,
                                "symbol": instrument_id,
                                "exchange_id": exchange_id,
                                "position_side": position_side,
                                "side": "sell" if position_side == "long" else "buy",
                                "offset": offset,
                                "quantity": self._canonical_recovery_number(available),
                                "quantity_unit": "contracts",
                            }
                        )
                else:
                    # CTP's exchange contract has dated close instructions only for
                    # SHFE/INE. CZCE (Iteration 22) and the remaining exchanges use
                    # the generic close flag while today/yesterday stays in evidence.
                    for position_side, total in (
                        ("long", remote_long),
                        ("short", remote_short),
                    ):
                        available = total - frozen_by_side[position_side]
                        if available <= 0:
                            continue
                        allowed_closes.append(
                            {
                                "execution_cycle_id": cycle_id,
                                "symbol": instrument_id,
                                "exchange_id": exchange_id,
                                "position_side": position_side,
                                "side": "sell" if position_side == "long" else "buy",
                                "offset": "close",
                                "quantity": self._canonical_recovery_number(available),
                                "quantity_unit": "contracts",
                            }
                        )

            if (
                not reasons
                and cycle_id is not None
                and not allowed_cancels
                and not allowed_closes
                and (remote_long > 0 or remote_short > 0)
            ):
                reasons.append("recovery_no_safe_action")

            if reasons:
                status = "MANUAL_INTERVENTION"
                allowed_cancels = []
                allowed_closes = []
                allowed_actions = []
                cycle_id = None
            elif (
                cycle_id is None
                and not active_orders
                and remote_long == remote_short == 0
            ):
                status = "FLAT"
                allowed_actions = ["complete"]
            else:
                status = "RECOVERABLE"
                allowed_actions = []
                if allowed_cancels:
                    allowed_actions.append("cancel")
                elif allowed_closes:
                    allowed_actions.append("close")

            material = {
                "schema_version": "bt_api.execution-recovery.v1",
                "status": status,
                "recovery_required": status != "FLAT",
                "can_arm_execution": status == "FLAT",
                "can_arm_recovery": status == "RECOVERABLE",
                "account_fingerprint": proof["account_fingerprint"],
                "strategy_id": self.config["strategy_id"],
                "strategy_identity_sha256": self.config["strategy_identity_sha256"],
                "instrument": proof["instrument"],
                "trading_day": proof["trading_day"],
                "connection_generation": proof["connection_generation"],
                "fencing_epoch": self.fencing_epoch,
                "proof_sha256": proof_sha256,
                "execution_cycle_id": cycle_id,
                "remote_position": remote_position,
                "owned_position": owned_position,
                "allowed_closes": allowed_closes,
                "allowed_cancels": allowed_cancels,
                "allowed_actions": allowed_actions,
                "unknown_ids": sorted(self._unknown_ids()) if reasons else [],
                "evidence_errors": sorted(set(reasons)),
                "journal_sha256": journal_sha256,
                "query_barrier": deepcopy(barrier) if barrier is not None else None,
            }
            token = None
            if status != "MANUAL_INTERVENTION":
                token_material = {
                    **material,
                    "owner_token": self.owner_token,
                    "nonce": uuid.uuid4().hex,
                }
                token = hashlib.sha256(
                    json.dumps(
                        token_material,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest()
            plan = {**material, "recovery_token_sha256": token}
            self._recovery_plan = deepcopy(plan)
            self._recovery_refresh_in_progress = False
            return deepcopy(plan)

    def arm_recovery_from_preflight(
        self,
        proof,
        recovery_token_sha256,
        state_reader,
        *,
        venue=None,
        prepare_execution=None,
        rollback_execution=None,
    ):
        """Consume one recovery token and arm only its bounded exposure reduction."""
        operation = "arm_execution_recovery"
        normalized, proof_sha256 = _execution_arm_proof(proof)
        with self.mutex:
            plan = self._recovery_plan
            if (
                not isinstance(plan, Mapping)
                or plan.get("status") != "RECOVERABLE"
                or plan.get("proof_sha256") != proof_sha256
                or self._arm_proof != normalized
                or recovery_token_sha256 != plan.get("recovery_token_sha256")
                or recovery_token_sha256 in self._recovery_used_tokens
                or not self._recovery_plan_private_fence_current(plan)
            ):
                raise NormalizedApiError(
                    operation,
                    "invalid_or_consumed_recovery_token",
                    definite_reject=True,
                )
            result = self._arm_from_preflight(
                normalized,
                state_reader,
                venue=venue,
                prepare_execution=prepare_execution,
                rollback_execution=rollback_execution,
                _recovery_capability=self._recovery_arm_capability,
            )
            self._recovery_used_tokens.add(recovery_token_sha256)
            self._recovery_mode = True
            return {
                **result,
                "recovery_only": True,
                "recovery_token_sha256": recovery_token_sha256,
                "execution_cycle_id": self._recovery_plan["execution_cycle_id"],
            }

    def complete_recovery(self, recovery_token_sha256, snapshot, barrier):
        """Close recovery after the SDK-owned query barrier proves flatness."""
        operation = "complete_execution_recovery"
        with self.mutex:
            plan = self._recovery_plan
            if (
                not isinstance(plan, Mapping)
                or recovery_token_sha256 != plan.get("recovery_token_sha256")
                or plan.get("status") == "MANUAL_INTERVENTION"
                or not self._recovery_plan_private_fence_current(plan)
            ):
                raise NormalizedApiError(
                    operation, "invalid_recovery_token", definite_reject=True
                )
            if (
                plan.get("status") == "RECOVERABLE"
                and recovery_token_sha256 not in self._recovery_used_tokens
            ):
                raise NormalizedApiError(
                    operation, "recovery_token_not_armed", definite_reject=True
                )
            verified = self.build_recovery_plan(snapshot, barrier=barrier)
            if verified.get("status") != "FLAT":
                # Preserve the original token and recovery permissions on failure.
                self._recovery_plan = dict(plan)
                raise NormalizedApiError(
                    operation, "execution_recovery_not_flat", definite_reject=True
                )
            # Stable account/order/trade/position absence is the only path that
            # converts restart UNKNOWN intents into a durable terminal outcome.
            for state in self.orders.values():
                if state.get("terminal"):
                    continue
                update = self._unknown(state, "recovery_query_barrier")
                update.update(
                    status="reconciled_absent",
                    filled="0",
                    avg_price=None,
                    execution_unknown=False,
                    terminal_confirmed=True,
                    recovery_query_barrier_sha256=barrier.get("barrier_sha256"),
                )
                self._record(
                    state,
                    update,
                    origin="complete_execution_recovery",
                    allow_read_only_journal=True,
                )
            self._recovery_completed_preflight_sha256 = self._arm_proof[
                "preflight_sha256"
            ]
            self.config["market_data_only"] = True
            self._arm_proof_sha256 = None
            self._arm_proof = None
            self._arm_state_reader = None
            self._recovery_mode = False
            self._recovery_refresh_in_progress = False
            self._recovery_plan = verified
            self._recovery_completed = True
            return {
                "completed": True,
                "armed": False,
                "market_data_only": True,
                "recovery_only": False,
                "requires_new_preflight": True,
                "recovery_token_sha256": recovery_token_sha256,
            }

    def pause_recovery(self):
        """Close recovery writes without fencing a same-generation refresh."""
        with self.mutex:
            self.config["market_data_only"] = True
            self._arm_proof_sha256 = None
            self._recovery_mode = False
            self._recovery_refresh_in_progress = True

    def arm_from_preflight(
        self,
        proof,
        state_reader,
        *,
        venue=None,
        prepare_execution=None,
        rollback_execution=None,
        prepare_execution_outside_mutex=False,
    ):
        """Atomically convert one read-only session to durable execution.

        The caller supplies a local, non-network state reader for the active
        CTP transport. The same reader remains attached to the session so every
        later write rechecks the connection-bound fields before persistence or
        transport dispatch.
        """
        return self._arm_from_preflight(
            proof,
            state_reader,
            venue=venue,
            prepare_execution=prepare_execution,
            rollback_execution=rollback_execution,
            prepare_execution_outside_mutex=prepare_execution_outside_mutex,
        )

    def _arm_from_preflight(
        self,
        proof,
        state_reader,
        *,
        venue=None,
        prepare_execution=None,
        rollback_execution=None,
        prepare_execution_outside_mutex=False,
        _recovery_capability=None,
    ):
        recovery_arm = _recovery_capability is self._recovery_arm_capability
        operation = "arm_execution_from_preflight"
        prepared = False
        acquired_here = False
        load_state = None
        with self.mutex:
            previous_identity = deepcopy(self._ctp_execution_identity)
            previous_venue = self._arm_venue
            try:
                normalized, proof_sha256 = _execution_arm_proof(proof)
                if self.closed:
                    raise NormalizedApiError(
                        operation, "execution_session_closed", definite_reject=True
                    )
                if not callable(state_reader):
                    raise NormalizedApiError(
                        operation,
                        "execution_arm_state_unavailable",
                        definite_reject=True,
                    )
                ctp_venues = tuple(
                    item
                    for item in self.exchange_names
                    if self._provider(item) == "CTP"
                )
                arm_venue = str(
                    venue or (ctp_venues[0] if len(ctp_venues) == 1 else "")
                )
                if (
                    not arm_venue
                    or self._provider(arm_venue) != "CTP"
                    or set(self.exchange_names) != {arm_venue}
                ):
                    raise NormalizedApiError(
                        operation, "single_ctp_session_required", definite_reject=True
                    )
                self._allow_new_generation_arm(normalized)
                if (
                    self._recovery_plan is not None
                    and not self._recovery_completed
                    and not recovery_arm
                ):
                    status = self._recovery_plan.get("status")
                    code = {
                        "RECOVERABLE": "execution_recovery_arm_required",
                        "FLAT": "execution_recovery_completion_required",
                    }.get(status, "execution_recovery_manual_intervention")
                    raise NormalizedApiError(operation, code, definite_reject=True)
                if (
                    self._recovery_completed_preflight_sha256 is not None
                    and normalized["preflight_sha256"]
                    == self._recovery_completed_preflight_sha256
                    and not recovery_arm
                ):
                    raise NormalizedApiError(
                        operation,
                        "fresh_execution_preflight_required",
                        definite_reject=True,
                    )
                if not self.config["market_data_only"]:
                    if (
                        self._arm_managed
                        and self._arm_proof_sha256 == proof_sha256
                        and self._arm_proof == normalized
                    ):
                        error = self._current_arm_error()
                        if error is not None:
                            self._revoke_arm(error)
                            raise NormalizedApiError(
                                operation, error, definite_reject=True
                            )
                        return {
                            "armed": True,
                            "market_data_only": False,
                            "proof_sha256": proof_sha256,
                        }
                    raise NormalizedApiError(
                        operation, "execution_already_armed", definite_reject=True
                    )

                context = state_reader()
                error = self._arm_context_error(normalized, context)
                if error is not None:
                    raise NormalizedApiError(operation, error, definite_reject=True)
                if (
                    self.config["require_order_journal"] is not True
                    or self.path is None
                ):
                    raise NormalizedApiError(
                        operation, "order_journal_required", definite_reject=True
                    )
                if not self.config["strategy_identity_sha256"]:
                    raise NormalizedApiError(
                        operation,
                        "strategy_identity_required",
                        definite_reject=True,
                    )

                environment = (
                    str(
                        self.config["required_environments"].get(arm_venue)
                        or normalized["environment_profile"]
                    )
                    .strip()
                    .lower()
                )
                bound_identity = {
                    "provider": "CTP",
                    "environment": environment,
                    "account_id": normalized["account_fingerprint"],
                    "account_fingerprint": normalized["account_fingerprint"],
                }
                if previous_identity is not None and not _recorded_identity_matches(
                    previous_identity, bound_identity
                ):
                    raise NormalizedApiError(
                        operation,
                        "execution_arm_account_fingerprint_mismatch",
                        definite_reject=True,
                    )
                self._arm_venue = arm_venue
                self._ctp_execution_identity = bound_identity

                acquired_here = self.path is not None and self.lock_file is None
                if acquired_here:
                    load_state = self._arm_load_state()
                    self._acquire_lock()
                    context = state_reader()
                    error = self._arm_context_error(normalized, context)
                    if error is not None:
                        raise NormalizedApiError(operation, error, definite_reject=True)
                    self._load_journal()
                    self._load_risk_state()
                    load_error = self.risk_error or self.risk_transition_error
                    if self.persistence_failed or load_error:
                        raise NormalizedApiError(
                            operation,
                            load_error or "execution_persistence_failed",
                            definite_reject=True,
                        )

                if not recovery_arm and self._journal_requires_recovery(normalized):
                    # Crash recovery owns all uncertain orders and net durable
                    # exposure.  Ordinary arming must fail before the native
                    # execution gate is opened.
                    raise NormalizedApiError(
                        operation,
                        "execution_recovery_required",
                        definite_reject=True,
                    )

                context = state_reader()
                error = self._arm_context_error(normalized, context)
                if error is not None:
                    raise NormalizedApiError(operation, error, definite_reject=True)
                if callable(prepare_execution):
                    prepared = True
                    if prepare_execution_outside_mutex:
                        # Account-stream start/wait and any producer callback
                        # must never run while the journal mutex is held.
                        self.mutex.release()
                        try:
                            prepare_execution()
                        finally:
                            self.mutex.acquire()
                    else:
                        prepare_execution()
                    context = state_reader()
                    error = self._arm_context_error(
                        normalized, context, require_account_stream=True
                    )
                    if error is not None:
                        raise NormalizedApiError(operation, error, definite_reject=True)
                else:
                    error = self._arm_context_error(
                        normalized, context, require_account_stream=True
                    )
                    if error is not None:
                        raise NormalizedApiError(operation, error, definite_reject=True)

                if not recovery_arm and self._journal_requires_recovery(normalized):
                    raise NormalizedApiError(
                        operation,
                        "execution_recovery_required",
                        definite_reject=True,
                    )

                self._arm_managed = True
                self._arm_proof = normalized
                self._arm_proof_sha256 = proof_sha256
                self._last_arm_proof_sha256 = proof_sha256
                self._arm_state_reader = state_reader
                self._arm_revoked_reason = None
                self._arm_revoked_error_code = None
                self._arm_submit_calls = self.submit_calls
                self._arm_cancel_calls = self.cancel_calls
                self.config["market_data_only"] = False
                if not recovery_arm:
                    self._recovery_plan = None
                    self._recovery_completed = False
                return {
                    "armed": True,
                    "market_data_only": False,
                    "proof_sha256": proof_sha256,
                }
            except Exception as exc:
                if prepared and callable(rollback_execution):
                    with suppress(Exception):
                        rollback_execution()
                if acquired_here:
                    self._release_writer_leases()
                    if load_state is not None:
                        self._restore_arm_load_state(load_state)
                code = str(getattr(exc, "code", "") or "")
                reusable_read_only_rejections = {
                    "execution_recovery_required",
                    "execution_recovery_arm_required",
                    "execution_recovery_completion_required",
                    "execution_recovery_manual_intervention",
                    "fresh_execution_preflight_required",
                }
                if self._arm_managed and code not in reusable_read_only_rejections:
                    self._revoke_arm(
                        code or type(exc).__name__,
                        generation=(
                            normalized.get("connection_generation")
                            if "normalized" in locals()
                            else None
                        ),
                    )
                elif not self._arm_managed:
                    self._ctp_execution_identity = previous_identity
                    self._arm_venue = previous_venue
                    self.config["market_data_only"] = True
                raise

    def require_write(
        self,
        operation,
        *,
        placement=False,
        venue=None,
        recovery_action=False,
    ):
        with self.mutex:
            code = None
            if self.closed:
                code = "execution_session_closed"
            elif self._arm_managed:
                if self._arm_revoked_reason is not None:
                    code = self._arm_revoked_error_code or "execution_arm_revoked"
                elif self.config["market_data_only"]:
                    code = "market_data_only"
                elif venue not in (None, self._arm_venue):
                    code = "execution_arm_venue_mismatch"
                else:
                    code = self._current_arm_error()
                    if code is not None:
                        self._revoke_arm(code)
            if code is None:
                if self.config["market_data_only"]:
                    code = "market_data_only"
                elif placement and (
                    self.persistence_failed
                    or (not recovery_action and self._unknown_ids())
                ):
                    code = "unresolved_or_undurable_journal"
                elif (
                    placement
                    and self.config["require_order_journal"]
                    and self.path is None
                ):
                    code = "order_journal_required"
                elif (
                    placement
                    and not recovery_action
                    and self.config["account_maximum_loss_bps"] is not None
                ):
                    if self.risk_record is None:
                        code = "account_risk_baseline_required"
                    elif self.risk_record.get("loss_limit_breached") is not False:
                        code = "account_maximum_loss_breached"
                    elif self.risk_transition_error:
                        code = self.risk_transition_error
                    elif self.risk_measurement_error:
                        code = self.risk_measurement_error
                    else:
                        code = self._risk_freshness_error()
            if code:
                raise NormalizedApiError(operation, code, definite_reject=True)
            self._assert_writer_lease(operation)

    def execution_identity(self, venue):
        """Return the configured ledger identity for one authenticated venue."""
        with self.mutex:
            identity = self._ledger_identity(venue)
            is_ctp = self._provider(venue) == "CTP" and bool(
                identity.get("account_fingerprint")
            )
            account_alias = (
                identity["account_id"]
                if is_ctp
                else self.config["account_ids"].get(venue)
            )
            return {
                **identity,
                "exchange_name": venue,
                "account_alias": account_alias,
                "account_authority": (
                    "account_fingerprint"
                    if is_ctp
                    else (
                        "credential_fingerprint"
                        if identity.get("credential_fingerprint")
                        else "declared_account_id"
                    )
                ),
                "physical_account_id_verified": False,
                "credential_rotation_requires_reconciled_migration": bool(
                    identity.get("credential_fingerprint")
                ),
                "strategy_id": self.config["strategy_id"],
                "strategy_identity_sha256": self.config["strategy_identity_sha256"],
                "trading_day": (
                    self._arm_proof.get("trading_day")
                    if is_ctp and isinstance(self._arm_proof, Mapping)
                    else None
                ),
                "connection_generation": (
                    self._arm_proof.get("connection_generation")
                    if is_ctp and isinstance(self._arm_proof, Mapping)
                    else None
                ),
                "session_generation": (
                    self._arm_proof.get("connection_generation")
                    if is_ctp and isinstance(self._arm_proof, Mapping)
                    else None
                ),
                "fencing_epoch": self.fencing_epoch,
                "journal_path": str(self.path) if self.path is not None else None,
            }

    def risk_venues(self):
        """Return the authenticated venues covered by this execution ledger."""
        venues = {
            venue
            for venue in set(self.exchange_names)
            | set(self.config["required_environments"])
            if self._provider(venue) in _CRYPTO_PROVIDERS
        }
        if isinstance(self._ctp_execution_identity, Mapping) and self._arm_venue:
            venues.add(self._arm_venue)
        return tuple(sorted(venues))

    def requires_risk_baseline(self):
        """Return whether this ledger still needs its first durable risk baseline."""
        with self.mutex:
            return self.risk_record is None

    def _risk_max_age_ns(self):
        return int(
            Decimal(self.config["account_risk_max_age_seconds"]) * Decimal("1000000000")
        )

    def _risk_freshness_error(self, now_monotonic_ns=None):
        """Return the current placement blocker for an unverified risk snapshot."""
        if self.config["account_maximum_loss_bps"] is None:
            return None
        if self.risk_check_in_progress:
            return "account_risk_refresh_in_progress"
        if self.risk_record is None:
            return None
        verified_at = self.risk_last_verified_monotonic_ns
        if type(verified_at) is not int or verified_at <= 0:
            return "account_risk_snapshot_refresh_required"
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        if type(now) is not int or now < verified_at:
            return "account_risk_snapshot_clock_invalid"
        if now - verified_at > self._risk_max_age_ns():
            return "account_risk_snapshot_stale"
        return None

    @contextmanager
    def risk_collection(self):
        """Serialize risk reads while making their incomplete interval fail closed.

        The verified timestamp is intentionally process local. A process exit
        therefore cannot turn an interrupted read into fresh evidence on the
        next owner: the reopened session starts with refresh-required state.
        """
        with self.risk_collection_mutex:
            with self.mutex:
                if self.closed:
                    raise NormalizedApiError(
                        "get_account_risk_snapshot",
                        "execution_session_closed",
                        definite_reject=True,
                    )
                self.risk_check_in_progress = True
            try:
                yield
            except BaseException:
                with self.mutex:
                    if (
                        self.config["account_maximum_loss_bps"] is not None
                        and self.risk_record is not None
                    ):
                        self.risk_measurement_error = "account_risk_evidence_incomplete"
                raise
            finally:
                with self.mutex:
                    self.risk_check_in_progress = False

    def _risk_identities(self):
        return sorted(
            self._configured_execution_identities(),
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )

    def _configured_risk_currency(self, venue):
        currency = self.config["account_currencies"].get(
            venue, self.config["account_currency"]
        )
        return str(currency).strip().upper() if currency not in (None, "") else None

    def _validated_risk_baseline(self, baseline, expected_venues):
        """Return a canonical baseline only when its complete contract is intact."""
        if not isinstance(baseline, Mapping) or set(baseline) != set(expected_venues):
            raise ValueError("risk baseline venue mismatch")
        validated = {}
        for venue in expected_venues:
            row = baseline[venue]
            if not isinstance(row, Mapping) or set(row) != {"currency", "equity"}:
                raise ValueError("invalid risk baseline row")
            currency = row.get("currency")
            if (
                not isinstance(currency, str)
                or not currency
                or currency != currency.strip().upper()
            ):
                raise ValueError("invalid risk baseline currency")
            configured_currency = self._configured_risk_currency(venue)
            if configured_currency is not None and currency != configured_currency:
                raise ValueError("risk baseline currency mismatch")
            equity = row.get("equity")
            canonical_equity = self._canonical_equity(equity)
            if not isinstance(equity, str) or equity != canonical_equity:
                raise ValueError("noncanonical risk baseline equity")
            validated[venue] = {"currency": currency, "equity": canonical_equity}
        return validated

    def _validated_loss_state(self, value):
        """Validate the persisted account-loss contract without silently weakening it."""
        configured_limit = self.config["account_maximum_loss_bps"]
        recorded_limit = value.get("loss_limit_bps")
        if recorded_limit != configured_limit:
            raise ValueError("account maximum loss limit mismatch")

        if configured_limit is None and "loss_limit_breached" not in value:
            return {
                "loss_limit_bps": None,
                "loss_limit_breached": False,
                "loss_breached_at": None,
                "loss_amount": None,
                "loss_limit_amount": None,
                "loss_bps_observed": None,
                "peak_loss_bps": None,
            }

        breached = value.get("loss_limit_breached")
        if type(breached) is not bool:
            raise ValueError("invalid account loss latch")
        breached_at = value.get("loss_breached_at")
        if breached:
            if (
                isinstance(breached_at, bool)
                or not isinstance(breached_at, (int, float))
                or not math.isfinite(breached_at)
                or breached_at <= 0
            ):
                raise ValueError("invalid account loss breach time")
        elif breached_at is not None:
            raise ValueError("unexpected account loss breach time")

        result = {
            "loss_limit_bps": configured_limit,
            "loss_limit_breached": breached,
            "loss_breached_at": breached_at,
        }
        for key in (
            "loss_amount",
            "loss_limit_amount",
            "loss_bps_observed",
            "peak_loss_bps",
        ):
            item = value.get(key)
            if item is None:
                result[key] = None
                continue
            canonical = self._canonical_equity(item)
            if not isinstance(item, str) or item != canonical or Decimal(canonical) < 0:
                raise ValueError(f"invalid {key}")
            result[key] = canonical
        if configured_limit is not None:
            peak = result["peak_loss_bps"]
            limit = Decimal(configured_limit)
            if breached and (peak is None or Decimal(peak) < limit):
                raise ValueError("breached loss latch lacks threshold evidence")
            if not breached and peak is not None and Decimal(peak) >= limit:
                raise ValueError("unlatched loss state exceeds threshold")
        return result

    def _load_risk_state(self):
        if self.risk_path is None or not self.risk_path.exists():
            return
        try:
            value = json.loads(self.risk_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("schema_version") != 1:
                raise ValueError("invalid risk schema")
            recorded = value.get("ledger_identities")
            expected = self._risk_identities()
            if not isinstance(recorded, list) or len(recorded) != len(expected):
                raise ValueError("risk identity count mismatch")
            if any(
                not _recorded_identity_matches(old, new)
                for old, new in zip(recorded, expected, strict=False)
            ):
                raise ValueError("risk identity mismatch")
            expected_venues = self.risk_venues()
            if tuple(value.get("configured_venues") or ()) != expected_venues:
                raise ValueError("risk venue mismatch")
            baseline = self._validated_risk_baseline(
                value.get("baseline_equity_by_venue"), expected_venues
            )
            value["baseline_equity_by_venue"] = baseline
            value.update(self._validated_loss_state(value))
            configured_limit = self.config["account_maximum_loss_bps"]
            if configured_limit is not None:
                transition_mismatch = None
                if self._pending_loss_reset_id:
                    transition_mismatch = "account_loss_reset_incomplete"
                elif value.get("loss_limit_breached") is not True and (
                    self._journal_loss_state == "breached"
                    or (
                        self._journal_loss_state == "reset"
                        and value.get("last_loss_reset_id")
                        != self._committed_loss_reset_id
                    )
                ):
                    transition_mismatch = "account_loss_transition_mismatch"
                if transition_mismatch:
                    # An interrupted reset or a state file that claims an
                    # unjournaled unlock is conservatively restored as latched.
                    value["loss_limit_breached"] = True
                    value["loss_breached_at"] = (
                        self._journal_loss_breached_at
                        or self._pending_loss_reset_at
                        or value.get("as_of_wall_time")
                        or time.time()
                    )
                    peak = Decimal(value.get("peak_loss_bps") or "0")
                    value["peak_loss_bps"] = self._canonical_equity(
                        max(peak, Decimal(configured_limit))
                    )
                    self.risk_transition_error = transition_mismatch
            self.risk_record = value
        except Exception:
            self.risk_error = "unreadable_or_mismatched_account_risk_state"
            self.persistence_failed = True

    @staticmethod
    def _canonical_equity(value):
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise NormalizedApiError(
                "get_account_risk_snapshot",
                "invalid_account_equity",
                definite_reject=True,
            ) from None
        if not decimal_value.is_finite():
            raise NormalizedApiError(
                "get_account_risk_snapshot",
                "invalid_account_equity",
                definite_reject=True,
            )
        text = format(decimal_value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text in {"", "-0"} else text

    @staticmethod
    def _decimal_work_precision(*values):
        """Return enough precision for exact finite sums/products plus a ratio."""
        decimals = [
            value if isinstance(value, Decimal) else Decimal(str(value))
            for value in values
        ]
        integer_digits = max(
            (max(value.adjusted() + 1, 1) for value in decimals), default=1
        )
        fractional_digits = max(
            (max(-value.as_tuple().exponent, 0) for value in decimals), default=0
        )
        product_digits = sum(len(value.as_tuple().digits) for value in decimals)
        return max(64, integer_digits + fractional_digits + 32, product_digits + 32)

    def _write_risk_state(self, value):
        if self.risk_path is None:
            raise NormalizedApiError(
                "get_account_risk_snapshot",
                "account_risk_state_path_missing",
                definite_reject=True,
            )
        self._assert_writer_lease("get_account_risk_snapshot")
        self.risk_path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.risk_path.with_name(
            f".{self.risk_path.name}.{self.owner_token}.tmp"
        )
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        fd = os.open(staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, self.risk_path)
            _fsync_directory(self.risk_path.parent)
        finally:
            with suppress(FileNotFoundError):
                staging.unlink()

    def account_risk_snapshot(
        self,
        observations,
        *,
        evidence_errors=None,
        initialize_baseline=False,
        baseline_commit_check=None,
        reset_loss_latch=False,
    ):
        """Validate and durably update account-equity evidence for this ledger.

        The commit check runs under the session mutex immediately before a new
        or reset baseline becomes writable, closing the local execution TOCTOU
        window. A breached account-loss limit remains latched until this method
        receives an explicit reset backed by flat positions and empty orders.
        """
        with self.mutex:
            now_monotonic = time.monotonic_ns()
            now_wall = time.time()
            errors = dict(evidence_errors or {})
            expected_venues = self.risk_venues()
            if not expected_venues:
                errors.setdefault("venues", "account_risk_venues_missing")
            configured_loss_limit = self.config["account_maximum_loss_bps"]
            prior_record = dict(self.risk_record or {})
            prior_loss_breached = prior_record.get("loss_limit_breached") is True
            was_loss_breached = prior_loss_breached
            prior_breached_at = prior_record.get("loss_breached_at")
            normalized = {}
            for venue in expected_venues:
                row = observations.get(venue)
                if not isinstance(row, dict):
                    errors.setdefault(venue, "account_evidence_missing")
                    continue
                currency = str(row.get("currency") or "").strip().upper()
                if not currency:
                    errors.setdefault(venue, "account_currency_missing")
                    continue
                configured_currency = self._configured_risk_currency(venue)
                if configured_currency is not None and currency != configured_currency:
                    errors.setdefault(venue, "account_currency_mismatch")
                    continue
                try:
                    equity = self._canonical_equity(row.get("equity"))
                except NormalizedApiError as error:
                    errors.setdefault(venue, error.code)
                    continue
                normalized[venue] = {
                    "currency": currency,
                    "equity": equity,
                    "positions_known": row.get("positions_known") is True,
                    "positions_flat": row.get("positions_flat") is True,
                    "open_orders_known": row.get("open_orders_known") is True,
                    "open_orders_empty": row.get("open_orders_empty") is True,
                    "authenticated_account_id": (
                        str(row["authenticated_account_id"])
                        if row.get("authenticated_account_id") not in (None, "")
                        else None
                    ),
                }
                if row.get("positions_known") is not True:
                    errors.setdefault(venue, "positions_not_proven")
                if self._provider(venue) == "CTP":
                    expected_account_id = self._ledger_identity(venue)["account_id"]
                    if (
                        _normalize_label(normalized[venue]["authenticated_account_id"])
                        != expected_account_id
                    ):
                        errors.setdefault(venue, "authenticated_account_id_mismatch")

            persisted_baseline = (
                dict(prior_record.get("baseline_equity_by_venue") or {})
                if prior_record
                else None
            )
            baseline = (
                dict(persisted_baseline) if persisted_baseline is not None else None
            )
            baseline_created = False
            baseline_reset = False
            if (baseline is None and initialize_baseline) or reset_loss_latch:
                for venue in expected_venues:
                    row = normalized.get(venue)
                    error_key = f"{venue}:open_orders"
                    if row is None or not row["open_orders_known"]:
                        errors.setdefault(error_key, "open_orders_not_proven")
                    elif not row["open_orders_empty"]:
                        errors.setdefault(error_key, "open_orders_present")

            current_evidence_complete = not errors and set(normalized) == set(
                expected_venues
            )
            safe_to_replace_baseline = (
                current_evidence_complete
                and all(row["positions_flat"] for row in normalized.values())
                and all(row["open_orders_empty"] for row in normalized.values())
                and not self.persistence_failed
                and not self._unknown_ids()
                and not any(not state["terminal"] for state in self.orders.values())
            )
            if baseline is None and initialize_baseline:
                if not safe_to_replace_baseline:
                    errors.setdefault("baseline", "unsafe_baseline_initialization")
                    current_evidence_complete = False
                else:
                    baseline = {
                        venue: {
                            "currency": row["currency"],
                            "equity": row["equity"],
                        }
                        for venue, row in normalized.items()
                    }
                    baseline_created = True

            if reset_loss_latch:
                if configured_loss_limit is None:
                    errors.setdefault(
                        "loss_reset", "account_maximum_loss_limit_disabled"
                    )
                elif not prior_loss_breached:
                    errors.setdefault("loss_reset", "account_maximum_loss_not_breached")
                elif baseline is None:
                    errors.setdefault("loss_reset", "account_risk_baseline_missing")
                elif not safe_to_replace_baseline:
                    errors.setdefault("loss_reset", "unsafe_account_loss_reset")
                else:
                    baseline = {
                        venue: {
                            "currency": row["currency"],
                            "equity": row["equity"],
                        }
                        for venue, row in normalized.items()
                    }
                    baseline_reset = True

            current = {
                venue: {"currency": row["currency"], "equity": row["equity"]}
                for venue, row in normalized.items()
            }
            baseline_contract_valid = False
            if baseline is not None:
                try:
                    baseline = self._validated_risk_baseline(baseline, expected_venues)
                except (NormalizedApiError, TypeError, ValueError):
                    errors.setdefault("baseline", "baseline_contract_mismatch")
                    baseline = None
                else:
                    baseline_contract_valid = set(current) == set(
                        expected_venues
                    ) and all(
                        current[venue]["currency"] == baseline[venue]["currency"]
                        for venue in expected_venues
                    )
                    if not baseline_contract_valid:
                        errors.setdefault(
                            "baseline", "baseline_current_contract_mismatch"
                        )

            if baseline_created or baseline_reset:
                try:
                    commit_errors = baseline_commit_check()
                    if not isinstance(commit_errors, Mapping):
                        raise ValueError("baseline commit check result missing")
                except Exception:
                    errors.setdefault(
                        "baseline_commit", "open_orders_commit_sweep_not_proven"
                    )
                else:
                    errors.update(commit_errors)
                if errors:
                    baseline = dict(persisted_baseline) if baseline_reset else None
                    baseline_contract_valid = False
                    current_evidence_complete = False
                    if baseline is not None:
                        try:
                            baseline = self._validated_risk_baseline(
                                baseline, expected_venues
                            )
                        except (NormalizedApiError, TypeError, ValueError):
                            baseline = None
                        else:
                            baseline_contract_valid = set(current) == set(
                                expected_venues
                            ) and all(
                                current[venue]["currency"]
                                == baseline[venue]["currency"]
                                for venue in expected_venues
                            )

            unknown_ids = self._unknown_ids()
            active_execution = any(
                not state["terminal"] for state in self.orders.values()
            )
            currencies = {row["currency"] for row in current.values()}
            aggregate_currency = (
                next(iter(currencies)) if len(currencies) == 1 else None
            )
            aggregate_baseline = aggregate_current = None
            if (
                aggregate_currency
                and baseline
                and set(baseline) == set(current)
                and all(
                    row.get("currency") == aggregate_currency
                    for row in baseline.values()
                )
            ):
                baseline_values = [Decimal(row["equity"]) for row in baseline.values()]
                current_values = [Decimal(row["equity"]) for row in current.values()]
                with localcontext() as context:
                    context.prec = self._decimal_work_precision(
                        *baseline_values, *current_values
                    )
                    baseline_total = sum(baseline_values, Decimal("0"))
                    current_total = sum(current_values, Decimal("0"))
                aggregate_baseline = self._canonical_equity(baseline_total)
                aggregate_current = self._canonical_equity(current_total)

            loss_limit_breached = bool(configured_loss_limit and prior_loss_breached)
            loss_breached_at = prior_breached_at if loss_limit_breached else None
            loss_amount = loss_limit_amount = loss_bps_observed = None
            peak_loss_bps = (
                prior_record.get("peak_loss_bps")
                if configured_loss_limit is not None
                else None
            )
            loss_measurement_complete = configured_loss_limit is None
            if configured_loss_limit is not None:
                if aggregate_baseline is None or aggregate_current is None:
                    errors.setdefault(
                        "account_maximum_loss", "aggregate_equity_not_comparable"
                    )
                    current_evidence_complete = False
                elif Decimal(aggregate_baseline) <= 0:
                    errors.setdefault(
                        "account_maximum_loss", "nonpositive_account_risk_baseline"
                    )
                    current_evidence_complete = False
                else:
                    baseline_decimal = Decimal(aggregate_baseline)
                    current_decimal = Decimal(aggregate_current)
                    limit_decimal = Decimal(configured_loss_limit)
                    if baseline_reset and not errors:
                        prior_loss_breached = False
                        prior_breached_at = None
                        prior_peak = Decimal("0")
                    else:
                        prior_peak_text = prior_record.get("peak_loss_bps")
                        prior_peak = (
                            Decimal(prior_peak_text)
                            if prior_peak_text is not None
                            else Decimal("0")
                        )
                    with localcontext() as context:
                        context.prec = self._decimal_work_precision(
                            baseline_decimal,
                            current_decimal,
                            limit_decimal,
                            prior_peak,
                            Decimal("10000"),
                        )
                        current_loss = max(
                            baseline_decimal - current_decimal, Decimal("0")
                        )
                        scaled_loss = current_loss * Decimal("10000")
                        scaled_limit = baseline_decimal * limit_decimal
                        current_loss_bps = scaled_loss / baseline_decimal
                        limit_amount = scaled_limit / Decimal("10000")
                        breached_now = scaled_loss >= scaled_limit
                    loss_amount = self._canonical_equity(current_loss)
                    loss_limit_amount = self._canonical_equity(limit_amount)
                    loss_bps_observed = self._canonical_equity(current_loss_bps)
                    peak_loss_bps = self._canonical_equity(
                        max(prior_peak, current_loss_bps)
                    )
                    loss_limit_breached = bool(prior_loss_breached or breached_now)
                    if loss_limit_breached:
                        loss_breached_at = prior_breached_at or now_wall
                    else:
                        loss_breached_at = None
                    loss_measurement_complete = True

            risk_measurement_complete = bool(
                configured_loss_limit is not None
                and loss_measurement_complete
                and current_evidence_complete
                and baseline_contract_valid
                and not errors
            )
            if risk_measurement_complete:
                self.risk_measurement_error = None
            elif configured_loss_limit is not None and self.risk_record is not None:
                self.risk_measurement_error = "account_risk_evidence_incomplete"

            if errors:
                current_evidence_complete = False
            execution_evidence_complete = bool(
                not self.persistence_failed and not unknown_ids and not active_execution
            )
            evidence_complete = bool(
                current_evidence_complete
                and baseline_contract_valid
                and execution_evidence_complete
            )
            if risk_measurement_complete and execution_evidence_complete:
                self.risk_last_verified_monotonic_ns = now_monotonic

            reasons = []
            if self.risk_error:
                reasons.append(self.risk_error)
            if baseline is None:
                reasons.append("baseline_missing")
            if not evidence_complete:
                reasons.append("account_evidence_incomplete")
            if self.persistence_failed:
                reasons.append("execution_persistence_failed")
            if unknown_ids:
                reasons.append("execution_unknown")
            if active_execution:
                reasons.append("active_execution")
            if loss_limit_breached:
                reasons.append("account_maximum_loss_breached")
            durable = bool(
                baseline is not None
                and evidence_complete
                and not self.risk_error
                and not self.persistence_failed
            )
            trading_blocked = bool(reasons)
            result = {
                "schema_version": 1,
                "ledger_identities": self._risk_identities(),
                "configured_venues": list(expected_venues),
                "baseline_equity_by_venue": baseline,
                "current_equity_by_venue": current,
                "baseline_equity": aggregate_baseline,
                "current_equity": aggregate_current,
                "currency": aggregate_currency,
                "evidence_complete": evidence_complete,
                "durable": durable,
                "trading_blocked": trading_blocked,
                "blocked_reasons": sorted(set(reasons)),
                "evidence_errors": errors,
                "loss_limit_bps": configured_loss_limit,
                "loss_limit_breached": loss_limit_breached,
                "loss_breached_at": loss_breached_at,
                "loss_amount": loss_amount,
                "loss_limit_amount": loss_limit_amount,
                "loss_bps_observed": loss_bps_observed,
                "peak_loss_bps": peak_loss_bps,
                "last_loss_reset_id": prior_record.get("last_loss_reset_id"),
                # A positive numeric generation lets consumers apply monotonic
                # restart checks without interpreting the opaque owner token.
                # The multi-identity writer lease advances this fence once per
                # execution session and applies the same value to every venue.
                "generation": self.fencing_epoch,
                "session_generation": self.owner_token,
                "owner_pid": self.owner_pid,
                "fencing_epoch": self.fencing_epoch,
                "as_of_monotonic_ns": now_monotonic,
                "clock_domain_id": f"process:{self.owner_pid}:monotonic",
                "as_of_wall_time": now_wall,
                "journal_path": str(self.path) if self.path else None,
            }
            newly_breached = bool(loss_limit_breached and not was_loss_breached)
            successful_reset = bool(
                baseline_reset and not errors and not loss_limit_breached
            )
            should_persist = bool(evidence_complete or newly_breached)
            if baseline is not None and should_persist and not self.risk_error:
                reset_id = None
                try:
                    if newly_breached:
                        self._journal_risk_transition("risk_breach")
                    if successful_reset:
                        reset_id = uuid.uuid4().hex
                        self._journal_risk_transition("risk_reset_prepared", reset_id)
                        result["last_loss_reset_id"] = reset_id
                    self._write_risk_state(result)
                    if successful_reset:
                        self._journal_risk_transition("risk_reset_committed", reset_id)
                except Exception:
                    self.persistence_failed = True
                    self.risk_error = "account_risk_state_persistence_failed"
                    if newly_breached:
                        # The append-only breach transition is the conservative
                        # in-process authority even if the replace was uncertain.
                        self.risk_record = dict(result)
                    elif successful_reset:
                        # A reset is not observable until both the state replace
                        # and its fenced commit receipt are durable.
                        result.update(
                            baseline_equity_by_venue=prior_record.get(
                                "baseline_equity_by_venue"
                            ),
                            baseline_equity=prior_record.get("baseline_equity"),
                            loss_limit_breached=True,
                            loss_breached_at=prior_record.get("loss_breached_at"),
                            loss_amount=prior_record.get("loss_amount"),
                            loss_limit_amount=prior_record.get("loss_limit_amount"),
                            loss_bps_observed=prior_record.get("loss_bps_observed"),
                            peak_loss_bps=prior_record.get("peak_loss_bps"),
                            last_loss_reset_id=prior_record.get("last_loss_reset_id"),
                        )
                    result.update(
                        durable=False,
                        trading_blocked=True,
                        blocked_reasons=sorted(
                            set(result["blocked_reasons"] + [self.risk_error])
                        ),
                    )
                else:
                    if successful_reset:
                        self.risk_transition_error = None
                    self.risk_record = dict(result)
            return result

    def new_client_order_id(self, venue, account_id=None, strategy_id=None):
        with self.mutex:
            self.require_write("new_client_order_id", placement=True, venue=venue)
            identity = self._ledger_identity(venue, account_id)
            account_id = identity["account_id"]
            partition = strategy_id or self.config["strategy_id"]
            if not isinstance(partition, str) or not partition.strip():
                raise NormalizedApiError(
                    "new_client_order_id", "invalid_strategy_id", definite_reject=True
                )
            if partition.strip() != self.config["strategy_id"]:
                raise NormalizedApiError(
                    "new_client_order_id",
                    "strategy_partition_mismatch",
                    definite_reject=True,
                )
            candidate = time.time_ns() % 10**12
            result = f"{candidate:012d}"
            key = self._client_key(venue, account_id, result)
            while key in self.used_ids | self.reserved_ids:
                candidate = (candidate + 1) % 10**12
                result = f"{candidate:012d}"
                key = self._client_key(venue, account_id, result)
            self._journal(
                "client_id_reservation",
                {
                    "exchange_name": venue,
                    "account_id": account_id,
                    "client_order_id": result,
                    "strategy_id": partition.strip(),
                },
            )
            self.reserved_ids.add(key)
            return result

    def _state(self, venue, row, *, create=False):
        client_id = str(row.get("client_order_id") or row.get("order_ref") or "")
        account_id = self._ledger_identity(venue, row.get("account_id"), row)[
            "account_id"
        ]
        key = (venue, client_id)
        state = self.orders.get(key) if client_id else None
        if state is None and client_id:
            # A native-ID-only query can learn its client ID in a later reply.
            state = next(
                (
                    item
                    for item in self.orders.values()
                    if item["exchange_name"] == venue
                    and item.get("client_order_id") == client_id
                    and item.get("account_id") == account_id
                ),
                None,
            )
        order_id = str(row.get("order_id") or row.get("venue_order_id") or "")
        symbol = row.get("symbol")
        exchange_id = row.get("exchange_id")
        if state is None and order_id:
            candidates = [
                item
                for item in self.orders.values()
                if item["exchange_name"] == venue
                and (not row.get("account_id") or item.get("account_id") == account_id)
                and item.get("order_id") == order_id
                and item.get("symbol") == symbol
                and (
                    exchange_id in (None, "")
                    or item.get("exchange_id") in (None, "")
                    or str(item["exchange_id"]) == str(exchange_id)
                )
            ]
            if len(candidates) > 1:
                raise NormalizedApiError(
                    "execution", "ambiguous_order_identity", execution_unknown=True
                )
            state = candidates[0] if candidates else None
        if state is None:
            state = {
                "symbol": row.get("symbol"),
                "exchange_name": venue,
                "account_id": account_id,
                "client_order_id": client_id,
                "order_id": order_id or None,
                "side": row.get("side"),
                "size": row.get("size", row.get("quantity")),
                "terminal": False,
                "next_poll": 0.0,
                "strategy_id": row.get("strategy_id"),
                "strategy_identity_sha256": row.get("strategy_identity_sha256"),
                "execution_arm_proof_sha256": row.get("execution_arm_proof_sha256"),
                "connection_generation": row.get("connection_generation"),
                "fencing_epoch": row.get("fencing_epoch"),
                _EXPLICIT_IDENTITY_FIELDS: _explicit_identity_fields(row),
                **{key: row[key] for key in _IDENTITY if row.get(key) is not None},
            }
            if create and (client_id or order_id):
                storage_key = (
                    key
                    if client_id
                    else (
                        venue,
                        account_id,
                        "native",
                        symbol,
                        str(exchange_id or ""),
                        order_id,
                    )
                )
                self.orders[storage_key] = state
        return state

    @staticmethod
    def _identity_conflict_code(state, row):
        reported_explicit = _explicit_identity_fields(row)
        state_explicit = _explicit_identity_fields(state)
        for key in (
            "symbol",
            "exchange_name",
            "client_order_id",
            "order_id",
            "exchange_id",
            "front_id",
            "session_id",
            "order_ref",
            "position_id",
            "side",
            "position_side",
            "offset",
            "position_mode",
            "quantity_unit",
            "trading_day",
        ):
            if key in _LEDGER_SEMANTIC_IDENTITY and (
                key not in state_explicit or key not in reported_explicit
            ):
                continue
            existing, reported = state.get(key), row.get(key)
            if key in _LEDGER_SEMANTIC_IDENTITY:
                exchange_name = state.get("exchange_name") or row.get("exchange_name")
                existing = _semantic_identity_value(key, existing, exchange_name)
                reported = _semantic_identity_value(key, reported, exchange_name)
            if (
                existing not in (None, "")
                and reported not in (None, "")
                and str(existing) != str(reported)
            ):
                return (
                    "ledger_mismatch"
                    if key in _LEDGER_SEMANTIC_IDENTITY
                    else "order_identity_mismatch"
                )
        return None

    @classmethod
    def _identity_conflicts(cls, state, row):
        return cls._identity_conflict_code(state, row) is not None

    @staticmethod
    def _identity(state, row):
        conflict_code = _ExecutionSession._identity_conflict_code(state, row)
        if conflict_code is not None:
            raise NormalizedApiError("execution", conflict_code, execution_unknown=True)
        result = dict(row)
        result.pop(_EXPLICIT_IDENTITY_FIELDS, None)
        reported_explicit = _explicit_identity_fields(row)
        state_explicit = _explicit_identity_fields(state)
        for key in ("order_id", "client_order_id", "side", *_IDENTITY):
            if key in _LEDGER_SEMANTIC_IDENTITY and key not in reported_explicit:
                if state.get(key) is not None:
                    result[key] = state[key]
                continue
            if row.get(key) not in (None, ""):
                state[key] = row[key]
                if key in _LEDGER_SEMANTIC_IDENTITY:
                    state_explicit.add(key)
            elif state.get(key) is not None:
                result[key] = state[key]
        state[_EXPLICIT_IDENTITY_FIELDS] = state_explicit
        result.update(
            symbol=state["symbol"],
            exchange_name=state["exchange_name"],
            account_id=state["account_id"],
        )
        return result

    @classmethod
    def _unknown(cls, state, code):
        result = {
            **cls._identity(state, {}),
            "kind": "order",
            "status": "submitted",
            "execution_unknown": True,
            "terminal_confirmed": False,
            "error_code": code,
        }
        previous = state.get("last_update", {})
        for key in (
            "filled",
            "avg_price",
            "cumulative_commission",
            "unbooked_cumulative_commission",
            "commission_currency",
            "fee_currency",
            "commission_normalized",
            "fee_in_account_currency",
            "commission_source",
        ):
            if key in previous:
                result[key] = previous[key]
        return result

    def currency(self, venue):
        return (
            self.accounts.get(venue, {}).get("currency")
            or self.config["account_currencies"].get(venue)
            or self.config["account_currency"]
        )

    def _fee(self, state, row, *, trade=False):
        result = dict(row)
        field = "fee" if trade else "cumulative_commission"
        currency = result.get("commission_currency") or result.get("fee_currency")
        value = result.get(field)
        if (
            value is not None
            and self.currency(state["exchange_name"]) == currency
            and currency
        ):
            result[field] = _number(value)
            result["commission_currency"] = currency
            result["commission_normalized"] = True
            result["fee_in_account_currency"] = True
            result["commission_source"] = "exchange"
            if trade:
                result["commission"] = result.pop("fee")
        else:
            if value is not None:
                result[
                    "unbooked_fee" if trade else "unbooked_cumulative_commission"
                ] = value
                result.pop(field, None)
            result["commission_source"] = "unresolved"
            result["fee_in_account_currency"] = False
            if _number(result.get("size" if trade else "filled")) > 0:
                state["fee_unresolved"] = True
        return result

    def _order_update(self, state, row):
        conflict_code = self._identity_conflict_code(state, row)
        if conflict_code is not None:
            return self._unknown(state, conflict_code)
        previous = state.get("last_update", {})
        filled = _number(row.get("filled"))
        average = _number(row.get("avg_price"))
        status = row.get("status")
        previous_filled = _number(previous.get("filled"))
        previous_average = _number(previous.get("avg_price"))
        if filled == previous_filled and not average and previous_average:
            average = previous_average
        if previous and (
            filled < previous_filled
            or (
                not previous.get("execution_unknown")
                and previous.get("terminal_confirmed")
                and status not in _TERMINAL
                and filled == previous_filled
            )
            or (
                not previous.get("execution_unknown")
                and not row.get("execution_unknown")
                and status in _NONTERMINAL_PROGRESS
                and previous.get("status") in _NONTERMINAL_PROGRESS
                and filled == previous_filled
                and _NONTERMINAL_PROGRESS[status]
                < _NONTERMINAL_PROGRESS[previous["status"]]
            )
        ):
            return dict(previous)
        result = self._identity(state, row)
        trade_source = row.get("execution_source") == "trades"
        confirmed = (
            status in _STATUSES
            and not row.get("execution_unknown")
            and (not filled or average > 0 or trade_source)
            and (status not in _TERMINAL or row.get("terminal_confirmed") is True)
            and not (status == "completed" and not filled)
        )
        result.update(
            kind="order",
            status=status if confirmed else "submitted",
            filled=filled,
            avg_price=average or None,
            execution_unknown=not confirmed,
            terminal_confirmed=bool(confirmed and status in _TERMINAL),
        )
        # An order's price is its requested limit, never its fill price.
        result.pop("price", None)
        result.pop("commission", None)
        result = self._fee(state, result)
        if (
            "cumulative_commission" not in result
            and "cumulative_commission" in previous
            and filled == previous.get("filled")
            and average == previous.get("avg_price")
        ):
            for key in (
                "cumulative_commission",
                "commission_currency",
                "fee_currency",
                "commission_normalized",
                "fee_in_account_currency",
                "commission_source",
            ):
                if key in previous:
                    result[key] = previous[key]
        return result

    def _record(
        self,
        state,
        update,
        *,
        origin=None,
        allow_read_only_journal=False,
    ):
        state["next_poll"] = time.monotonic() + self.config["order_poll_interval"]
        try:
            self._journal(
                "order_update",
                {
                    **update,
                    _EXPLICIT_IDENTITY_FIELDS: sorted(_explicit_identity_fields(state)),
                    "fee_unresolved": state.get("fee_unresolved", False),
                },
                allow_read_only=allow_read_only_journal,
            )
        except NormalizedApiError:
            update = {
                **update,
                "execution_unknown": True,
                "terminal_confirmed": False,
                "journal_error": True,
            }
        state["terminal"] = bool(update.get("terminal_confirmed"))
        state["last_update"] = dict(update)
        state["_last_update_origin"] = origin
        state["_revision"] = state.get("_revision", 0) + 1
        if state["terminal"]:
            self.historical_unknown.discard(self._identifier(state))
            self.historical_unknown.difference_update(state.get("recovery_ids", ()))
        return update

    def _begin_invoke(self, operation, venue, request, *, preauthorize=None):
        """Persist intent and capture merge state before a transport call."""
        request_row = asdict(request)
        emergency_cancel = False
        with self.mutex:
            if self.closed:
                raise NormalizedApiError(
                    operation, "execution_session_closed", definite_reject=True
                )
            if operation == "make_order":
                self._require_arm_scope(
                    operation, venue, request.symbol, request.exchange_id
                )
                self._require_ctp_order_identity(operation, request)
                recovery_allowance = self._require_recovery_action(operation, request)
                if (
                    recovery_allowance is not None
                    and self._recovery_dispatch_in_progress
                ):
                    raise NormalizedApiError(
                        operation,
                        "execution_recovery_action_in_progress",
                        definite_reject=True,
                    )
                self.require_write(
                    operation,
                    placement=True,
                    venue=venue,
                    recovery_action=recovery_allowance is not None,
                )
                client_key = self._client_key(
                    venue,
                    request.account_id,
                    request.client_order_id,
                )
                if client_key in self.used_ids:
                    raise NormalizedApiError(
                        operation, "duplicate_client_order_id", definite_reject=True
                    )
                row = asdict(request)
                row.update(
                    side=request.side.value,
                    order_type=request.order_type.value,
                    quantity=format(request.quantity, "f"),
                    price=(
                        format(request.price, "f")
                        if request.price is not None
                        else None
                    ),
                    size=format(request.quantity, "f"),
                    exchange_name=venue,
                    client_id_reserved=True,
                    strategy_id=self.config["strategy_id"],
                )
                if preauthorize is not None:
                    preauthorize()
                self._journal("intent", row)
                self._consume_recovery_action(recovery_allowance)
                self.used_ids.add(client_key)
                self.reserved_ids.discard(client_key)
                state = self._state(venue, row, create=True)
                state["_intent_persisted"] = True
                state["strategy_id"] = self.config["strategy_id"]
                self.submit_calls += 1
            else:
                recovery_allowance = None
                if operation == "cancel_order":
                    self.require_write(operation, venue=venue)
                    self._require_arm_scope(
                        operation, venue, request.symbol, request.exchange_id
                    )
                    if self._arm_managed:
                        tracked = self._state(venue, request_row, create=False)
                        if not any(tracked is item for item in self.orders.values()):
                            raise NormalizedApiError(
                                operation,
                                "execution_arm_untracked_cancel",
                                definite_reject=True,
                            )
                        recovery_allowance = self._require_recovery_action(
                            operation, request, tracked=tracked
                        )
                        if (
                            recovery_allowance is not None
                            and self._recovery_dispatch_in_progress
                        ):
                            raise NormalizedApiError(
                                operation,
                                "execution_recovery_action_in_progress",
                                definite_reject=True,
                            )
                state = self._state(
                    venue,
                    request_row,
                    create=operation == "cancel_order",
                )
                if operation == "cancel_order":
                    emergency_cancel = bool(
                        self.persistence_failed
                        or (self.config["require_order_journal"] and self.path is None)
                    )
                    if preauthorize is not None:
                        preauthorize()
                    try:
                        self._journal(
                            "cancel_intent", self._identity(state, request_row)
                        )
                    except NormalizedApiError as exc:
                        if exc.code != "persistence_failed":
                            raise
                        # Once durable state is unavailable, placements remain
                        # blocked but a cancel request is still a necessary
                        # exposure-reduction action. Its result remains marked
                        # degraded and the session stays trading-blocked.
                        emergency_cancel = True
                    self._consume_recovery_action(recovery_allowance)
                    self.cancel_calls += 1
            state_is_tracked = any(state is tracked for tracked in self.orders.values())
            start_revision = state.get("_revision", 0)
            if recovery_allowance is not None:
                self._recovery_dispatch_in_progress = True
        return {
            "operation": operation,
            "venue": venue,
            "request_row": request_row,
            "emergency_cancel": emergency_cancel,
            "state": state,
            "state_is_tracked": state_is_tracked,
            "start_revision": start_revision,
            "recovery_action": recovery_allowance is not None,
        }

    def _finish_invoke(self, context, result, failure):
        """Merge a sync or async transport outcome through one state machine."""
        operation = context["operation"]
        venue = context["venue"]
        request_row = context["request_row"]
        emergency_cancel = context["emergency_cancel"]
        state = context["state"]
        state_is_tracked = context["state_is_tracked"]
        start_revision = context["start_revision"]
        recovery_action = context["recovery_action"]
        with self.mutex:
            if operation == "query_order" and failure is not None:
                raise failure

            if operation == "query_order" and not state_is_tracked and failure is None:
                # A stream event may have created the queried order while REST
                # was in flight. Merge into that authoritative object instead
                # of inserting a detached snapshot over it.
                for lookup in (request_row, result):
                    candidate = self._state(venue, lookup)
                    if any(candidate is tracked for tracked in self.orders.values()):
                        state = candidate
                        state_is_tracked = True
                        break

            current = state.get("last_update")
            if failure is None:
                update = self._order_update(state, result)
                if operation == "query_order" and not state_is_tracked:
                    client_id = state.get("client_order_id")
                    order_id = state.get("order_id")
                    if client_id:
                        key = (
                            (venue, client_id)
                            if state.get("account_id") == venue
                            else (venue, state.get("account_id"), client_id)
                        )
                    else:
                        key = (
                            venue,
                            state.get("account_id"),
                            "native",
                            state.get("symbol"),
                            str(state.get("exchange_id") or ""),
                            str(order_id or ""),
                        )
                    self.orders[key] = state
            else:
                # A matching stream update received during placement is newer
                # evidence than a later transport failure. For cancellation,
                # only a terminal stream update resolves the cancel outcome.
                changed_during_call = state.get("_revision", 0) > start_revision
                if changed_during_call and (
                    operation == "make_order"
                    or (current and current.get("terminal_confirmed"))
                ):
                    if recovery_action:
                        self.pause_recovery()
                    return dict(current)
                update = self._unknown(
                    state, getattr(failure, "code", type(failure).__name__)
                )
                if operation == "make_order" and getattr(
                    failure, "definite_reject", False
                ):
                    update.update(
                        status="rejected",
                        execution_unknown=False,
                        terminal_confirmed=True,
                        definite_reject=True,
                    )
            if operation == "cancel_order" and emergency_cancel:
                update = {**update, "emergency_cancel": True, "journal_degraded": True}
            if current is not None and update == current:
                if recovery_action and failure is not None:
                    self.pause_recovery()
                return dict(current)
            recorded = self._record(state, update, origin=operation)
            if recovery_action and (
                failure is not None
                or recorded.get("execution_unknown") is True
                or str(recorded.get("status") or "").lower()
                in {"error", "failed", "rejected"}
            ):
                # The allowance was consumed before transport dispatch.  A
                # failed or unresolved action closes the SDK lease so callers
                # must obtain a fresh two-round recovery plan before retrying.
                self.pause_recovery()
            return recorded

    def invoke(self, operation, venue, request, call, *, preauthorize=None):
        context = self._begin_invoke(
            operation,
            venue,
            request,
            preauthorize=preauthorize,
        )
        try:
            failure = None
            result = None
            try:
                result = call()
            except Exception as exc:
                failure = exc
            return self._finish_invoke(context, result, failure)
        finally:
            if context["recovery_action"]:
                with self.mutex:
                    self._recovery_dispatch_in_progress = False

    async def async_invoke(
        self,
        operation,
        venue,
        request,
        call,
        *,
        preauthorize=None,
    ):
        """Await transport I/O while preserving the synchronous WAL semantics."""
        context = self._begin_invoke(
            operation,
            venue,
            request,
            preauthorize=preauthorize,
        )
        try:
            failure = None
            result = None
            try:
                result = await call()
            except asyncio.CancelledError as exc:
                # Cancellation after a durable write does not prove that the venue
                # did not receive the request. Persist the uncertain outcome before
                # preserving asyncio cancellation semantics for the caller.
                if operation != "query_order":
                    try:
                        self._finish_invoke(context, None, exc)
                    except BaseException:
                        self.persistence_failed = True
                raise
            except Exception as exc:
                failure = exc
            return self._finish_invoke(context, result, failure)
        finally:
            if context["recovery_action"]:
                with self.mutex:
                    self._recovery_dispatch_in_progress = False

    def event(self, venue, event):
        if self.closed:
            raise NormalizedApiError("poll_event", "execution_session_closed")
        if event.get("kind") not in {"order", "trade"}:
            return event
        with self.mutex:
            self._recovery_event_revision += 1
            self._recovery_private_event_revision += 1
            state = self._state(venue, event, create=True)
            if state.get("client_order_id"):
                self.used_ids.add(
                    self._client_key(
                        venue,
                        state.get("account_id"),
                        state["client_order_id"],
                        event,
                    )
                )
            conflict_code = self._identity_conflict_code(state, event)
            if conflict_code is not None:
                return self._record(
                    state, self._unknown(state, conflict_code), origin="event"
                )
            if event["kind"] == "order":
                return self._record(
                    state, self._order_update(state, event), origin="event"
                )
            trade_key = self._trade_key(venue, event)
            if event.get("trade_id") and trade_key in self.trade_ids:
                return None
            update = self._fee(state, self._identity(state, event), trade=True)
            if event.get("trade_id"):
                self.trade_ids.add(trade_key)
            try:
                self._journal(
                    "trade",
                    {
                        **update,
                        _EXPLICIT_IDENTITY_FIELDS: sorted(
                            _explicit_identity_fields(state)
                        ),
                    },
                )
            except NormalizedApiError:
                # A real fill remains deliverable after disk failure. Publish
                # uncertainty first, stop future writes, then deliver the fill.
                unknown = self._unknown(state, "persistence_failed")
                unknown["journal_error"] = True
                state["last_update"] = unknown
                state["terminal"] = False
                self.pending[venue].append(update)
                return unknown
            return update

    def poll_due(self, api, venue):
        with self.mutex:
            if self.closed or self.config["market_data_only"]:
                return
            now = time.monotonic()
            for state in list(self.orders.values()):
                if (
                    state["exchange_name"] != venue
                    or state["terminal"]
                    or state["next_poll"] > now
                    or state.get("_poll_inflight")
                ):
                    continue
                request = QueryOrderRequest(
                    symbol=state["symbol"],
                    account_id=state["account_id"],
                    client_order_id=state.get("client_order_id") or None,
                    order_id=state.get("order_id") or None,
                    **{
                        key: state[key]
                        for key in (
                            "exchange_id",
                            "front_id",
                            "session_id",
                            "order_ref",
                        )
                        if state.get(key) is not None
                    },
                )
                before = dict(state.get("last_update") or {})
                state["_poll_inflight"] = True
                state["next_poll"] = now + self.config["order_poll_interval"]
                break
            else:
                return

        failure = None
        completed = False
        update = None
        try:
            try:
                update = api.query_order(venue, request, normalized=True)
                completed = True
            except Exception as exc:
                failure = exc
        finally:
            with self.mutex:
                if failure is not None:
                    # Missing orders and unsupported remote queries never prove
                    # rejection and cannot release a historical unknown intent.
                    state["reconciliation_error"] = getattr(
                        failure, "code", type(failure).__name__
                    )
                elif completed:
                    current = state.get("last_update")
                    if (
                        state.get("_last_update_origin") == "query_order"
                        and current == update
                        and current != before
                    ):
                        self.pending[venue].append(dict(current))
                    state.pop("reconciliation_error", None)
                state["_poll_inflight"] = False
                state["next_poll"] = (
                    time.monotonic() + self.config["order_poll_interval"]
                )

    @staticmethod
    def _identifier(state):
        return (
            state["client_order_id"]
            or f"{state['exchange_name']}:{state['symbol']}:{state.get('exchange_id') or ''}:order:{state.get('order_id', '')}"
        )

    def _unknown_ids(self):
        return self.historical_unknown | {
            self._identifier(state)
            for state in self.orders.values()
            if state.get("last_update", {}).get("execution_unknown")
        }

    def summary(self):
        with self.mutex:
            fees = sorted(
                {
                    self._identifier(s)
                    for s in self.orders.values()
                    if s.get("fee_unresolved")
                }
            )
            funding = sorted(
                {
                    self._identifier(state)
                    for state in self.orders.values()
                    if state.get("funding_unresolved")
                }
            )
            reconciliation_errors = {
                self._identifier(state): state["reconciliation_error"]
                for state in self.orders.values()
                if state.get("reconciliation_error")
            }
            evidence_errors = {
                f"{identifier}:{error}"
                for identifier, error in reconciliation_errors.items()
            }
            if self.persistence_failed:
                evidence_errors.add("execution_persistence_failed")
            if self.risk_error:
                evidence_errors.add(str(self.risk_error))
            if self.risk_measurement_error:
                evidence_errors.add(str(self.risk_measurement_error))
            if self.risk_transition_error:
                evidence_errors.add(str(self.risk_transition_error))
            if self._arm_revoked_reason:
                evidence_errors.add(
                    self._arm_revoked_error_code or "execution_arm_revoked"
                )
            loss_limit_configured = self.config["account_maximum_loss_bps"] is not None
            risk_freshness_error = self._risk_freshness_error()
            if risk_freshness_error:
                evidence_errors.add(risk_freshness_error)
            loss_limit_breached = bool(
                self.risk_record and self.risk_record.get("loss_limit_breached") is True
            )
            if loss_limit_configured and self.risk_record is None:
                evidence_errors.add("account_risk_baseline_required")
            if loss_limit_breached:
                evidence_errors.add("account_maximum_loss_breached")
            armed = bool(
                self._arm_managed
                and not self.config["market_data_only"]
                and self._arm_proof_sha256
                and self._arm_revoked_reason is None
            )
            arm_revoked = bool(self._arm_revoked_reason)
            arm_generation = (
                self._arm_proof.get("connection_generation")
                if isinstance(self._arm_proof, Mapping)
                else None
            )
            trading_blocked = bool(
                self.persistence_failed
                or self._unknown_ids()
                or funding
                or evidence_errors
                or (self._arm_managed and self.config["market_data_only"])
            )
            return {
                "armed": armed,
                "market_data_only": bool(self.config["market_data_only"]),
                "arm_managed": self._arm_managed,
                "arm_revoked": arm_revoked,
                "revocation_reason": self._arm_revoked_reason,
                "arm_proof_sha256": self._arm_proof_sha256,
                "proof_sha256": self._arm_proof_sha256,
                "last_arm_proof_sha256": self._last_arm_proof_sha256,
                "generation": arm_generation,
                "session_generation": arm_generation,
                "fencing_epoch": self.fencing_epoch,
                "submit_calls": self.submit_calls,
                "cancel_calls": self.cancel_calls,
                "unknown_ids": sorted(self._unknown_ids()),
                "active_orders": sum(not s["terminal"] for s in self.orders.values()),
                "fee_unresolved_orders": fees,
                # Compatibility alias records incomplete SDK fees, never claims
                # that a particular engine actually posted an estimated cost.
                "estimated_fee_orders": fees,
                "funding_unresolved_orders": funding,
                # The session does not yet ingest authenticated funding-ledger
                # cashflows. Empty unresolved-order state therefore cannot be
                # interpreted as proof of zero funding.
                "funding_evidence_status": "unavailable",
                "signed_funding_cashflow": None,
                "evidence_errors": sorted(evidence_errors),
                "loss_limit_bps": self.config["account_maximum_loss_bps"],
                "loss_limit_breached": loss_limit_breached,
                "trading_blocked": trading_blocked,
                "evidence_complete": not trading_blocked,
                "reconciliation_errors": reconciliation_errors,
            }
