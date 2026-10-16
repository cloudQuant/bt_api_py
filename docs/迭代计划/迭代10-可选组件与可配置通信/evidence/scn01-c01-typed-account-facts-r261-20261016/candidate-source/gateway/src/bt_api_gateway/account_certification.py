"""Bounded, code-owned collection of read-only venue certification facts.

Provider values cross this boundary only through two fixed methods. The
collector projects a small allowlist into typed, owner-bound observations and
keeps the Gateway receipt clock separate from exchange server time.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Callable

from bt_api_base.account_certification_protocol import (
    AccountConfigurationObservation,
    ExchangeServerTimeObservation,
)


class GatewayCertificationFactsError(RuntimeError):
    """Finite, value-free failure from the account-facts collection boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_CONFIG_FIELDS = {
    "can_trade": "canTrade",
    "can_deposit": "canDeposit",
    "can_withdraw": "canWithdraw",
    "dual_side_position": "dualSidePosition",
}


def collect_account_configuration(
    provider: object,
    *,
    account_key_id: str,
    generation: str,
    timeout: float,
    assert_owned: Callable[[], None],
    monotonic_clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
) -> AccountConfigurationObservation:
    """Read one typed allowlist from ``provider.read_account_configuration``."""

    raw = _call_provider(
        provider,
        "read_account_configuration",
        generation=generation,
        timeout=timeout,
        assert_owned=assert_owned,
        monotonic_clock=monotonic_clock,
    )
    if not isinstance(raw, Mapping) or len(raw) > 64 or any(type(key) is not str for key in raw):
        raise GatewayCertificationFactsError("PROVIDER_CONFIGURATION_INVALID")
    facts: dict[str, bool | None] = {}
    for target, source in _CONFIG_FIELDS.items():
        value = raw.get(source)
        if value is not None and type(value) is not bool:
            raise GatewayCertificationFactsError("PROVIDER_CONFIGURATION_INVALID")
        facts[target] = value
    return AccountConfigurationObservation(
        account_key_id=account_key_id,
        session_generation=generation,
        observation_id="account-config-" + uuid.uuid4().hex,
        collected_at_utc=_capture_utc(wall_clock),
        **facts,
    )


def collect_exchange_server_time(
    provider: object,
    *,
    account_key_id: str,
    generation: str,
    timeout: float,
    assert_owned: Callable[[], None],
    monotonic_clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
) -> ExchangeServerTimeObservation:
    """Read the provider's native ``serverTime`` field without clock fallback."""

    raw = _call_provider(
        provider,
        "read_exchange_server_time",
        generation=generation,
        timeout=timeout,
        assert_owned=assert_owned,
        monotonic_clock=monotonic_clock,
    )
    if not isinstance(raw, Mapping) or set(raw) != {"serverTime"}:
        raise GatewayCertificationFactsError("PROVIDER_SERVER_TIME_INVALID")
    server_time = raw["serverTime"]
    if type(server_time) is not int:
        raise GatewayCertificationFactsError("PROVIDER_SERVER_TIME_INVALID")
    try:
        return ExchangeServerTimeObservation(
            account_key_id=account_key_id,
            session_generation=generation,
            observation_id="exchange-time-" + uuid.uuid4().hex,
            collected_at_utc=_capture_utc(wall_clock),
            server_time_ms=server_time,
        )
    except (TypeError, ValueError):
        raise GatewayCertificationFactsError("PROVIDER_SERVER_TIME_INVALID") from None


def _call_provider(
    provider: object,
    method_name: str,
    *,
    generation: str,
    timeout: float,
    assert_owned: Callable[[], None],
    monotonic_clock: Callable[[], float],
) -> object:
    method = getattr(provider, method_name, None)
    if not callable(method):
        raise GatewayCertificationFactsError("PROVIDER_FACT_UNAVAILABLE")
    start = _clock_value(monotonic_clock)
    assert_owned()
    try:
        raw = method(generation=generation, timeout=timeout)
    except Exception:
        raise GatewayCertificationFactsError("PROVIDER_FACT_READ_FAILED") from None
    end = _clock_value(monotonic_clock)
    assert_owned()
    if end < start:
        raise GatewayCertificationFactsError("MONOTONIC_CLOCK_REGRESSED")
    if end - start > timeout:
        raise GatewayCertificationFactsError("PROVIDER_RESPONSE_LATE")
    return raw


def _clock_value(clock: Callable[[], float]) -> float:
    try:
        value = clock()
    except Exception:
        raise GatewayCertificationFactsError("CLOCK_UNAVAILABLE") from None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise GatewayCertificationFactsError("CLOCK_INVALID")
    return float(value)


def _capture_utc(wall_clock: Callable[[], float]) -> str:
    timestamp = _clock_value(wall_clock)
    try:
        return (
            datetime.fromtimestamp(timestamp, tz=timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError):
        raise GatewayCertificationFactsError("CLOCK_INVALID") from None


__all__ = [
    "GatewayCertificationFactsError",
    "collect_account_configuration",
    "collect_exchange_server_time",
]
