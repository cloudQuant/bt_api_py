"""Test-only helpers: run synthetic Gateway compositions on the decision-B path.

Decision B (iteration 63) refuses dispatch without a claim port, and a claim
port requires prepared placement: legacy ``place`` could reach a provider
without the single claim. Tests whose subject is the Gateway journal, owner,
client or stream behaviour around one native send use these helpers so they
exercise the production composition instead of the retired legacy path.

``PreparedFromLegacy`` maps prepared placement onto a synthetic provider's
existing ``reserve_order``/``submit_order``/``release_reservation`` methods, so
the provider's own send assertions (DISPATCHING marker present, persisted
identity, one send) keep running unchanged. It is not a provider contract.
"""

from __future__ import annotations

from bt_api_base.direct_place_protocol import PreparedPlaceRequest
from bt_api_base.direct_write_protocol import DirectSubmitResult

DEFAULT_TRADING_DAY = "20300101"


class PreparedFromLegacy:
    """Mixin exposing a synthetic legacy provider through prepared placement."""

    def _adapter_state(self):
        state = self.__dict__.get("_prepared_from_legacy")
        if state is None:
            state = {"entries": {}, "day": DEFAULT_TRADING_DAY}
            self.__dict__["_prepared_from_legacy"] = state
        return state

    def trading_day(self):
        return self._adapter_state()["day"]

    def prepare_order(self, request):
        reservation = self.reserve_order(request)
        identity = reservation.identity
        day = identity.fields.get("trading_day", DEFAULT_TRADING_DAY)
        state = self._adapter_state()
        state["day"] = day
        prepared = PreparedPlaceRequest(
            "prep-" + reservation.reservation_id,
            identity,
            request.symbol,
            day,
            identity.session_generation,
            {"synthetic": True},
            ("place-" + identity.client_order_id,),
            {"source": "synthetic-legacy-adapter"},
        )
        state["entries"][prepared.reservation_id] = (reservation, request)
        return prepared

    def submit_prepared_order(self, prepared, grant, verifier, *, native_market_recheck=None):
        # Decision B: the claim check runs immediately before the native send.
        if native_market_recheck is not None and native_market_recheck() is False:
            return DirectSubmitResult(False, prepared.identity, "synthetic_not_sent")
        reservation, request = self._adapter_state()["entries"][prepared.reservation_id]
        return self.submit_order(reservation, request, grant, verifier)

    def release_prepared_order(self, prepared):
        entry = self._adapter_state()["entries"].pop(prepared.reservation_id, None)
        release = getattr(self, "release_reservation", None)
        if entry is not None and callable(release):
            release(entry[0])


def seal_claims(journal, guard):
    """A sealed production claim coordinator wired to ``journal``."""
    from bt_api_py.gateway_dispatch_claim import (
        GATEWAY_DISPATCH_COVERAGE,
        GatewayDispatchClaimCoordinator,
        GatewayJournalDispatchChangePort,
        seal_gateway_dispatch_coverage,
    )

    claims, sealer = GatewayDispatchClaimCoordinator.with_coverage_sealer(
        journal.account_scope, journal.generation
    )
    journal.install_dispatch_change_port(guard=guard, port=GatewayJournalDispatchChangePort(claims))
    wired = frozenset(label for labels in GATEWAY_DISPATCH_COVERAGE.values() for label in labels)
    seal_gateway_dispatch_coverage(claims, sealer, wired=wired)
    return claims


def compose_claimed_dispatcher(coordinator, exchange_name, journal, guard, assert_admitted, *,
                               expected_local_provider, journal_id="journal-test"):
    """Compose exactly like production: prepared placement plus the claim port."""
    from bt_api_py.gateway_direct_dispatch import compose_gateway_direct_dispatcher
    from bt_api_py.gateway_dispatch_claim import GatewayNativeDispatchClaimPort

    claims = seal_claims(journal, guard)
    return compose_gateway_direct_dispatcher(
        coordinator,
        exchange_name,
        journal,
        guard,
        assert_admitted,
        expected_local_provider=expected_local_provider,
        prepared_place=True,
        claim_port=GatewayNativeDispatchClaimPort(claims, journal_id=journal_id),
    )
