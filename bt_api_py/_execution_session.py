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
import time
import unicodedata
import uuid
from collections import defaultdict, deque
from collections.abc import Mapping
from contextlib import contextmanager, suppress
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
)
_LEDGER_SEMANTIC_IDENTITY = frozenset(
    {"side", "position_side", "offset", "quantity_unit", "position_mode"}
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
    if recorded_fingerprint is None:
        return True
    if expected_fingerprint is None:
        return False
    return recorded_fingerprint == expected_fingerprint


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

    def _acquire_ledger_locks(self):
        identities = self._configured_crypto_identities()
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

    def close(self):
        with self.mutex:
            self.closed = True
            handle, self.lock_file = self.lock_file, None
            if handle is not None:
                # Closing releases the OS advisory lock, including on a crash.
                # Never unlink the inode: another process may be waiting on it.
                handle.close()
            ledger_handles, self.ledger_lock_files = self.ledger_lock_files, []
            self.ledger_registry_digests.clear()
            for ledger_handle in ledger_handles:
                ledger_handle.close()

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
                    self.trade_ids.add(
                        (row.get("exchange_name"), row.get("symbol"), row["trade_id"])
                    )
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

    def _journal(self, event, row):
        if self.closed:
            raise NormalizedApiError(
                "journal", "execution_session_closed", definite_reject=True
            )
        if self.path is None or self.config["market_data_only"]:
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
                    "ledger_identity": ledger_identity,
                    "event": event,
                    "timestamp": time.time(),
                }
                if ledger_identity is not None:
                    envelope["account_id"] = ledger_identity["account_id"]
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

    def require_write(self, operation, *, placement=False):
        with self.mutex:
            code = None
            if self.closed:
                code = "execution_session_closed"
            elif self.config["market_data_only"]:
                code = "market_data_only"
            elif placement and (self.persistence_failed or self._unknown_ids()):
                code = "unresolved_or_undurable_journal"
            elif (
                placement and self.config["require_order_journal"] and self.path is None
            ):
                code = "order_journal_required"
            elif placement and self.config["account_maximum_loss_bps"] is not None:
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
            return {
                **identity,
                "exchange_name": venue,
                "account_alias": self.config["account_ids"].get(venue),
                "account_authority": (
                    "credential_fingerprint"
                    if identity.get("credential_fingerprint")
                    else "declared_account_id"
                ),
                "physical_account_id_verified": False,
                "credential_rotation_requires_reconciled_migration": bool(
                    identity.get("credential_fingerprint")
                ),
                "strategy_id": self.config["strategy_id"],
                "fencing_epoch": self.fencing_epoch,
                "journal_path": str(self.path) if self.path is not None else None,
            }

    def risk_venues(self):
        """Return the authenticated venues covered by this execution ledger."""
        return tuple(
            sorted(
                venue
                for venue in set(self.exchange_names)
                | set(self.config["required_environments"])
                if self._provider(venue) in _CRYPTO_PROVIDERS
            )
        )

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
            self._configured_crypto_identities(),
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
            self.require_write("new_client_order_id", placement=True)
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

    def _record(self, state, update, *, origin=None):
        state["next_poll"] = time.monotonic() + self.config["order_poll_interval"]
        try:
            self._journal(
                "order_update",
                {
                    **update,
                    _EXPLICIT_IDENTITY_FIELDS: sorted(_explicit_identity_fields(state)),
                    "fee_unresolved": state.get("fee_unresolved", False),
                },
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

    def _begin_invoke(self, operation, venue, request):
        """Persist intent and capture merge state before a transport call."""
        request_row = asdict(request)
        emergency_cancel = False
        with self.mutex:
            if self.closed:
                raise NormalizedApiError(
                    operation, "execution_session_closed", definite_reject=True
                )
            if operation == "make_order":
                self.require_write(operation, placement=True)
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
                )
                self._journal("intent", row)
                self.used_ids.add(client_key)
                self.reserved_ids.discard(client_key)
                state = self._state(venue, row, create=True)
                self.submit_calls += 1
            else:
                state = self._state(
                    venue,
                    request_row,
                    create=operation == "cancel_order",
                )
                if operation == "cancel_order":
                    self.require_write(operation)
                    emergency_cancel = bool(
                        self.persistence_failed
                        or (self.config["require_order_journal"] and self.path is None)
                    )
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
                    self.cancel_calls += 1
            state_is_tracked = any(state is tracked for tracked in self.orders.values())
            start_revision = state.get("_revision", 0)
        return {
            "operation": operation,
            "venue": venue,
            "request_row": request_row,
            "emergency_cancel": emergency_cancel,
            "state": state,
            "state_is_tracked": state_is_tracked,
            "start_revision": start_revision,
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
                return dict(current)
            return self._record(state, update, origin=operation)

    def invoke(self, operation, venue, request, call):
        context = self._begin_invoke(operation, venue, request)
        failure = None
        result = None
        try:
            result = call()
        except Exception as exc:
            failure = exc
        return self._finish_invoke(context, result, failure)

    async def async_invoke(self, operation, venue, request, call):
        """Await transport I/O while preserving the synchronous WAL semantics."""
        context = self._begin_invoke(operation, venue, request)
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

    def event(self, venue, event):
        if self.closed:
            raise NormalizedApiError("poll_event", "execution_session_closed")
        if event.get("kind") not in {"order", "trade"}:
            return event
        with self.mutex:
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
            trade_key = (venue, event.get("symbol"), event.get("trade_id"))
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
            return {
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
                "trading_blocked": bool(
                    self.persistence_failed
                    or self._unknown_ids()
                    or funding
                    or evidence_errors
                ),
                "reconciliation_errors": reconciliation_errors,
            }
