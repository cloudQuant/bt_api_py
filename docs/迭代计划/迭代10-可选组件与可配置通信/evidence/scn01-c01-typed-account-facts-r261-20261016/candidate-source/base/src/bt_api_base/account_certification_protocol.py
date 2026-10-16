"""Typed, bounded provider facts needed by read-only venue certification.

These contracts keep three identities separate: the authorized wire scope,
the account owner commitment, and the provider session generation. The
exchange's server time is a provider fact; ``collected_at_utc`` is the local
Gateway receipt time and must never be substituted for it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from bt_api_base.service_protocol import MAX_REFERENCE_BYTES

_ACCOUNT_KEY_ID = re.compile(r"account-[0-9a-f]{64}\Z", re.ASCII)
_UTC_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z\Z",
    re.ASCII,
)


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} is invalid")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} is invalid") from exc
    if len(encoded) > MAX_REFERENCE_BYTES or not value.isprintable():
        raise ValueError(f"{field} is invalid")
    return value


def _account_key_id(value: object) -> str:
    candidate = _text(value, "account_key_id")
    if _ACCOUNT_KEY_ID.fullmatch(candidate) is None:
        raise ValueError("account_key_id is invalid")
    return candidate


def _utc_timestamp(value: object) -> str:
    if type(value) is not str or len(value) > 32 or _UTC_TIMESTAMP.fullmatch(value) is None:
        raise ValueError("collected_at_utc must be an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("collected_at_utc must be a valid UTC timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("collected_at_utc must be UTC")
    return value


def _exact_fields(value: object, fields: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise TypeError(f"{name} must be a string-keyed mapping")
    if set(value) != fields:
        raise ValueError(f"{name} has an invalid field set")
    return value


@dataclass(frozen=True, slots=True)
class AccountConfigurationObservation:
    """One allowlisted, read-only account-configuration sample.

    Missing provider facts remain ``None``. They are never converted to false
    or inferred from another snapshot. ``account_key_id`` is a stable owner
    commitment, not a provider account alias or credential.
    """

    account_key_id: str
    session_generation: str
    observation_id: str
    collected_at_utc: str
    can_trade: bool | None
    can_deposit: bool | None
    can_withdraw: bool | None
    dual_side_position: bool | None

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "account_key_id",
            "session_generation",
            "observation_id",
            "collected_at_utc",
            "can_trade",
            "can_deposit",
            "can_withdraw",
            "dual_side_position",
        }
    )

    def __post_init__(self) -> None:
        _account_key_id(self.account_key_id)
        _text(self.session_generation, "session_generation")
        _text(self.observation_id, "observation_id")
        _utc_timestamp(self.collected_at_utc)
        for name in ("can_trade", "can_deposit", "can_withdraw", "dual_side_position"):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise TypeError(f"{name} must be bool or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_key_id": self.account_key_id,
            "session_generation": self.session_generation,
            "observation_id": self.observation_id,
            "collected_at_utc": self.collected_at_utc,
            "can_trade": self.can_trade,
            "can_deposit": self.can_deposit,
            "can_withdraw": self.can_withdraw,
            "dual_side_position": self.dual_side_position,
        }

    @classmethod
    def from_dict(cls, mapping: Mapping[str, Any]) -> AccountConfigurationObservation:
        raw = _exact_fields(mapping, cls._FIELDS, "account_configuration_observation")
        return cls(**{name: raw[name] for name in cls._FIELDS})


@dataclass(frozen=True, slots=True)
class ExchangeServerTimeObservation:
    """A provider-returned exchange time plus its Gateway receipt metadata."""

    account_key_id: str
    session_generation: str
    observation_id: str
    collected_at_utc: str
    server_time_ms: int

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "account_key_id",
            "session_generation",
            "observation_id",
            "collected_at_utc",
            "server_time_ms",
        }
    )

    def __post_init__(self) -> None:
        _account_key_id(self.account_key_id)
        _text(self.session_generation, "session_generation")
        _text(self.observation_id, "observation_id")
        _utc_timestamp(self.collected_at_utc)
        if type(self.server_time_ms) is not int or not 0 <= self.server_time_ms <= 253402300799999:
            raise ValueError("server_time_ms is outside the supported timestamp range")

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_key_id": self.account_key_id,
            "session_generation": self.session_generation,
            "observation_id": self.observation_id,
            "collected_at_utc": self.collected_at_utc,
            "server_time_ms": self.server_time_ms,
        }

    @classmethod
    def from_dict(cls, mapping: Mapping[str, Any]) -> ExchangeServerTimeObservation:
        raw = _exact_fields(mapping, cls._FIELDS, "exchange_server_time_observation")
        return cls(**{name: raw[name] for name in cls._FIELDS})


__all__ = ["AccountConfigurationObservation", "ExchangeServerTimeObservation"]
