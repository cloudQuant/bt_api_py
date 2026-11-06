"""Gateway MarketHub tick runtime for ``BtApi`` (iteration 62, 62.08).

A trusted host passes :class:`GatewayMarketTickRuntimeFactory` to
``BtApi(market_runtime_factory=...)``. The facade's market stream then serves
``tick`` subscriptions from the Gateway MarketHub through an exact
``ServiceMarketClient`` per instrument, instead of opening an in-process
provider market session (``ServiceMarketStream``; no silent fallback to the
direct provider).

The factory implements the SDK's ``MarketTickRuntimeFactory`` port. It owns
nothing but its configuration: the caller supplies an already constructed
``ServiceClientRuntime`` (its identity, scope/peer resolvers and CURVE keys are
trusted host inputs), the authorized market mount, the profile's source and
entitlement references and the market subscriber factory. The provider adapter
selected by the SDK route builds the subscription key; this module invents no
source, entitlement, clock, freshness or authorization fact.

Runtime lifecycle: the caller may hand over a started or an unstarted runtime
and closes it after the facade. The runtime renews its registration lease only
when its owner thread calls ``maintain()``, which market clients do while they
are polled; before the first subscription nothing does. A host may spend longer
than the lease between starting the runtime and subscribing (Cerebro starts the
store and broker first). So, on the owner thread and before each new client,
the factory makes the runtime fit for it: it starts a new runtime, performs any
due renewal, and, only while it has not yet created any client, replaces a
registration whose required lease already expired with a fresh registration
session (``ServiceClientRuntime.recover()``; nothing was bound to the expired
registration and no business call is replayed). Once a client exists an
expired lease is never recovered here: it stays a reported failure that every
existing stream observes. Every other runtime failure propagates unchanged.
"""

from __future__ import annotations

import threading
from typing import Any

from bt_api_base.market_protocol import MarketSubscriptionKey
from bt_api_base.market_tick_provider_protocol import (
    MarketTickSourceBinding,
    SelectedMarketTickRoute,
)

__all__ = ("GatewayMarketRuntimeError", "GatewayMarketTickRuntimeFactory")


class GatewayMarketRuntimeError(RuntimeError):
    """Finite, value-free configuration failure of the Gateway tick runtime."""

    _CODES = frozenset(
        {
            "GATEWAY_MARKET_RUNTIME_INVALID",
            "GATEWAY_MARKET_REFERENCE_INVALID",
            "GATEWAY_MARKET_BINDING_INVALID",
            "GATEWAY_MARKET_OWNER_THREAD_MISMATCH",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._CODES else "GATEWAY_MARKET_RUNTIME_INVALID"
        super().__init__(self.code)


def _reference(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 256
        and value == value.strip()
        and value.isprintable()
    )


class GatewayMarketTickRuntimeFactory:
    """Create exact Gateway market clients for SDK-selected tick routes.

    ``create_market_client`` runs on the owner thread and first makes the
    runtime fit for one new client (see the module docstring): ``"new"`` is
    started, ``"registered"`` is maintained (due renewals only), and an
    expired required lease (``maintain()`` raising ``REQUIRED_LEASE_EXPIRED``,
    or a ``"failed"`` runtime) is recovered with ``recover()`` only before the
    factory's first client. Afterwards the runtime error propagates; any other
    state or runtime error propagates as well and no client is created.
    """

    def __init__(
        self,
        runtime: Any,
        *,
        mount: str,
        source_ref: str,
        entitlement_ref: str,
        subscriber_factory: Any,
        stream_scope: Any,
        head_check_interval_seconds: float = 1.0,
        queue_capacity: int = 4_096,
    ) -> None:
        from bt_api_base.service_protocol import WireScope

        from bt_api_py.service_runtime import ServiceClientRuntime

        if type(runtime) is not ServiceClientRuntime:
            raise GatewayMarketRuntimeError("GATEWAY_MARKET_RUNTIME_INVALID")
        if not all(_reference(value) for value in (mount, source_ref, entitlement_ref)):
            raise GatewayMarketRuntimeError("GATEWAY_MARKET_REFERENCE_INVALID")
        if subscriber_factory is None or type(stream_scope) is not WireScope:
            raise GatewayMarketRuntimeError("GATEWAY_MARKET_RUNTIME_INVALID")
        self._runtime = runtime
        self._mount = mount
        self._source_ref = source_ref
        self._entitlement_ref = entitlement_ref
        self._subscriber_factory = subscriber_factory
        self._stream_scope = stream_scope
        self._head_check_interval_seconds = head_check_interval_seconds
        self._queue_capacity = queue_capacity
        self._owner_thread_id = threading.get_ident()
        self._clients: dict[int, tuple[Any, MarketSubscriptionKey]] = {}
        # Set once the first client is built and never cleared: from then on
        # an expired registration may back a stream and must not be replaced.
        self._client_created = False

    @property
    def owner_thread_id(self) -> int:
        return self._owner_thread_id

    @property
    def source_ref(self) -> str:
        return self._source_ref

    @property
    def entitlement_ref(self) -> str:
        return self._entitlement_ref

    def create_market_client(
        self, route: SelectedMarketTickRoute, binding: MarketTickSourceBinding, /
    ) -> object:
        from bt_api_py.service_market import ServiceMarketClient

        self._check_owner()
        if (
            type(route) is not SelectedMarketTickRoute
            or type(binding) is not MarketTickSourceBinding
            or binding.instrument != route.instrument
        ):
            raise GatewayMarketRuntimeError("GATEWAY_MARKET_BINDING_INVALID")
        key = binding.provider_adapter.create_subscription_key(
            route.instrument,
            source_ref=self._source_ref,
            entitlement_ref=self._entitlement_ref,
            channel=binding.channel,
        )
        if type(key) is not MarketSubscriptionKey or key.instrument != route.instrument:
            raise GatewayMarketRuntimeError("GATEWAY_MARKET_BINDING_INVALID")
        self._prepare_runtime()
        client = ServiceMarketClient(
            self._runtime,
            self._mount,
            key,
            subscriber_factory=self._subscriber_factory,
            stream_scope=self._stream_scope,
            queue_capacity=self._queue_capacity,
            head_check_interval_seconds=self._head_check_interval_seconds,
            require_source_descriptor=True,
        )
        self._client_created = True
        self._clients[id(client)] = (client, key)
        return client

    def _prepare_runtime(self) -> None:
        """Make the runtime fit for one new client on the owner thread.

        ``recover()`` releases the expired registration (an expired one is
        acknowledged by the host's ``NOT_REGISTERED``), closes the transports
        and registers a new session; it replays no business call. It is used
        only before the first client, when nothing can be bound to the expired
        registration. ``CLOSE_FAILED`` or a failed re-registration propagates.
        """

        from bt_api_py.service_runtime import ServiceRuntimeError

        runtime = self._runtime
        state = runtime.state
        if state == "new":
            runtime.start()
            return
        if state == "failed" and not self._client_created:
            runtime.recover()
            return
        try:
            # Renews when due; raises SESSION_NOT_REGISTERED for any other
            # state and REQUIRED_LEASE_EXPIRED (state -> "failed") on expiry.
            runtime.maintain()
        except ServiceRuntimeError as error:
            if self._client_created or error.code != "REQUIRED_LEASE_EXPIRED":
                raise
            runtime.recover()

    def validate_market_client_binding(
        self,
        route: SelectedMarketTickRoute,
        binding: MarketTickSourceBinding,
        client: object,
        /,
    ) -> bool:
        if threading.get_ident() != self._owner_thread_id:
            return False
        created = self._clients.get(id(client))
        return bool(
            created is not None
            and created[0] is client
            and type(route) is SelectedMarketTickRoute
            and type(binding) is MarketTickSourceBinding
            and created[1].instrument == route.instrument == binding.instrument
            and getattr(client, "key", None) == created[1]
            and getattr(client, "mount", None) == self._mount
        )

    def _check_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise GatewayMarketRuntimeError("GATEWAY_MARKET_OWNER_THREAD_MISMATCH")
