"""Explicitly scoped read-only CTP instrument-status observation."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway.instrument_status_forwarding import project_instrument_status


def _ref(value):
    try:
        return (
            type(value) is str
            and bool(value)
            and value == value.strip()
            and value.isprintable()
            and len(value.encode("utf-8", "strict")) <= 256
        )
    except UnicodeError:
        return False


@dataclass(frozen=True, slots=True)
class GatewayInstrumentStatusReadSettings:
    principal_instruments: Mapping[str, tuple[MarketInstrumentKey, ...]] = field(repr=False)
    owner_wait_seconds: float

    def __post_init__(self):
        try:
            if (
                not isinstance(self.principal_instruments, Mapping)
                or not 1 <= len(self.principal_instruments) <= 256
                or type(self.owner_wait_seconds) not in (int, float)
                or not math.isfinite(self.owner_wait_seconds)
                or not 0 < self.owner_wait_seconds <= 5
            ):
                raise ValueError
            copied = {}
            total = 0
            for principal, instruments in self.principal_instruments.items():
                if (
                    not _ref(principal)
                    or type(instruments) is not tuple
                    or not instruments
                    or any(type(key) is not MarketInstrumentKey for key in instruments)
                ):
                    raise ValueError
                normalized = tuple(
                    MarketInstrumentKey.from_dict(key.to_dict()) for key in instruments
                )
                if len(set(normalized)) != len(normalized):
                    raise ValueError
                total += len(normalized)
                if total > 4096:
                    raise ValueError
                copied[principal] = normalized
            object.__setattr__(self, "principal_instruments", MappingProxyType(copied))
        except Exception:
            raise ValueError("INVALID_INSTRUMENT_STATUS_SETTINGS") from None


class GatewayInstrumentStatusService:
    """Borrow exactly one existing owner, session and provider; no writes."""

    def __init__(self, owner, session, provider, journal, settings, *, session_is_original):
        from bt_api_ctp.direct_write_provider import CtpDirectWriteProvider
        from bt_api_ctp.gateway_session import CtpGatewaySession
        from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

        from bt_api_py.gateway_direct_owner import GatewayDirectOwner

        if (
            type(owner) is not GatewayDirectOwner
            or type(session) is not CtpGatewaySession
            or type(provider) is not CtpDirectWriteProvider
            or type(journal) is not GatewayDirectCommandJournal
            or type(settings) is not GatewayInstrumentStatusReadSettings
            or not callable(session_is_original)
            or not owner.is_bound_to(journal)
        ):
            raise ValueError("INVALID_INSTRUMENT_STATUS_BINDING")
        self._owner, self._session, self._provider = owner, session, provider
        self._journal, self._settings, self._session_is_original = (
            journal,
            settings,
            session_is_original,
        )
        self._original = (owner, session, provider, journal, settings, session_is_original)
        self._generation = journal.generation

    def handle(self, context, arguments, grant):
        from bt_api_ctp.gateway_instrument_status_snapshot import CtpGatewayInstrumentStatusSnapshot

        from bt_api_py.gateway_instrument_status_query import decode_query, encode_observation

        try:
            instrument = decode_query(arguments)
        except Exception:
            return {"status": "REQUEST_REJECTED", "code": "INVALID_REQUEST"}
        if (
            context.principal_ref not in self._settings.principal_instruments
            or instrument not in self._settings.principal_instruments[context.principal_ref]
            or instrument.native_symbol not in grant.symbols
        ):
            return {"status": "REQUEST_REJECTED", "code": "SCOPE_DENIED"}
        if not _ref(context.registration_id) or not _ref(context.service_epoch):
            return {"status": "REQUEST_REJECTED", "code": "INSTRUMENT_STATUS_UNAVAILABLE"}
        owner, session, provider, journal = (
            self._owner,
            self._session,
            self._provider,
            self._journal,
        )

        def intact():
            return all(
                value is original
                for value, original in zip(
                    (
                        self._owner,
                        self._session,
                        self._provider,
                        self._journal,
                        self._settings,
                        self._session_is_original,
                    ),
                    self._original,
                    strict=True,
                )
            )

        def read():
            if (
                not intact()
                or self._owner is not owner
                or self._session is not session
                or self._provider is not provider
                or self._journal is not journal
                or not self._session_is_original()
                or not owner.is_bound_to(journal)
                or journal.generation != self._generation
                or session._direct is not provider
            ):
                raise ValueError
            snapshot = session.capture_instrument_status(instrument.venue, instrument.native_symbol)
            if (
                type(snapshot) is not CtpGatewayInstrumentStatusSnapshot
                or snapshot.venue != instrument.venue
                or snapshot.native_symbol != instrument.native_symbol
            ):
                raise ValueError
            projection = project_instrument_status(
                snapshot.view,
                venue=instrument.venue,
                instrument=instrument.native_symbol,
                session_generation=snapshot.session_generation,
                trading_day=snapshot.trading_day,
            )
            if (
                not intact()
                or not self._session_is_original()
                or session._direct is not provider
                or not owner.is_bound_to(journal)
                or journal.generation != self._generation
                or session.snapshot_is_current(snapshot) is not True
            ):
                raise ValueError
            return encode_observation(
                instrument=instrument,
                registration_id=context.registration_id,
                service_epoch=context.service_epoch,
                owner_generation=self._generation,
                projection=projection,
            )

        try:
            return owner.submit_service_call(read).result(
                timeout_seconds=self._settings.owner_wait_seconds
            )
        except Exception:
            return {"status": "REQUEST_REJECTED", "code": "INSTRUMENT_STATUS_UNAVAILABLE"}
