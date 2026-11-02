"""SDK-owned, route-bound market-stream subscription session."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Dict, Optional, Tuple


class MarketStreamSession:
    """Own one consumer's exact subscription handles and their lifecycle.

    Provider sessions and channel queues remain with the selected stream port.
    The caller supplies route, lifecycle, diagnostics and projection callbacks.
    Each session holds only its own handle registry and queue observations.
    """

    _UNAVAILABLE_CHANNEL_CODES = frozenset(
        {
            "MARKET_STREAM_CHANNEL_UNAVAILABLE",
            "MARKET_STREAM_PROVIDER_CAPABILITY_UNAVAILABLE",
            "MARKET_STREAM_PROVIDER_CHANNEL_UNAVAILABLE",
        }
    )

    def __init__(
        self,
        queue_capacity: int,
        *,
        configured_orderbook_depth: Optional[int],
        error_type: type,
        missing_dependency_error_type: type,
        resolve_route_context: Callable[[Any, str], Tuple[str, Any]],
        assert_route_current: Callable[[Any], None],
        mark_unavailable: Callable[[str, str, str], None],
        observe_queue_depth: Callable[[str, str, int, bool], None],
        on_binding_closed: Callable[[str, str], None],
        on_close_failure: Callable[[str, str, str], None],
        sanitize_exception: Callable[[BaseException], Any],
        safe_exception_code: Callable[[BaseException, str], str],
    ) -> None:
        if type(queue_capacity) is not int or queue_capacity < 1:
            raise ValueError("market stream queue capacity must be positive")
        self.queue_capacity = queue_capacity
        self._configured_orderbook_depth = configured_orderbook_depth
        self._error_type = error_type
        self._missing_dependency_error_type = missing_dependency_error_type
        self._resolve_route_context = resolve_route_context
        self._assert_route_current = assert_route_current
        self._mark_unavailable = mark_unavailable
        self._observe_queue_depth = observe_queue_depth
        self._on_binding_closed = on_binding_closed
        self._on_close_failure = on_close_failure
        self._sanitize_exception = sanitize_exception
        self._safe_exception_code = safe_exception_code
        self._bindings: Dict[Tuple[str, str, Optional[int]], Dict[str, Any]] = {}

    @property
    def has_subscriptions(self) -> bool:
        return bool(self._bindings)

    def symbols(self):
        """Return data names with retained SDK subscription handles."""
        return frozenset(key[0] for key in self._bindings)

    def has_binding(
        self,
        dataname: str,
        channel: str,
        depth: Optional[int] = None,
    ) -> bool:
        """Check a retained exact route without exposing its SDK handle."""
        return self._find_binding(dataname, channel, depth) is not None

    def observed_queue_depth(self, dataname: str, channel: str) -> int:
        """Return the sum of the last exact SDK depth observations."""
        symbol = str(dataname)
        return sum(
            int(binding.get("last_queue_depth", 0) or 0)
            for (bound_symbol, bound_channel, _depth), binding in self._bindings.items()
            if bound_symbol == symbol and bound_channel == channel
        )

    @staticmethod
    def _binding_key(
        dataname: str,
        channel: str,
        depth: Optional[int],
    ) -> Tuple[str, str, Optional[int]]:
        return (str(dataname), str(channel), depth)

    def _get_binding(
        self,
        dataname: str,
        channel: str,
        depth: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        return self._bindings.get(self._binding_key(dataname, channel, depth))

    def _find_binding(
        self,
        dataname: str,
        channel: str,
        depth: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        symbol = str(dataname)
        if channel == "orderbook" and depth is None:
            depth = self._configured_orderbook_depth
        if depth is not None:
            return self._get_binding(symbol, channel, depth)
        matches = [
            binding
            for (bound_symbol, bound_channel, _bound_depth), binding in self._bindings.items()
            if bound_symbol == symbol and bound_channel == channel
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def sdk_contracts(missing_dependency_error_type: type):
        """Load SDK neutral identities only when a typed stream is used."""
        try:
            from bt_api_py.market_stream_contracts import market_stream_consumer_contracts

            return market_stream_consumer_contracts()
        except (ImportError, AttributeError) as exc:
            raise missing_dependency_error_type(
                "bt_api_py market-stream contracts are required"
            ) from exc

    def contracts(self):
        return self.sdk_contracts(self._missing_dependency_error_type)

    def identity_matches(self, stream: Any, subscription: Any, channel: str) -> bool:
        try:
            from bt_api_py.market_stream_contracts import market_stream_port_identity_matches
        except ImportError as exc:
            raise self._missing_dependency_error_type(
                "bt_api_py market-stream identity contract is required"
            ) from exc

        self.contracts()
        return market_stream_port_identity_matches(stream, subscription, channel)

    def assert_binding(
        self,
        dataname: str,
        binding: Mapping[str, Any],
        api: Any,
        route: str,
        instrument: Any,
        channel: Optional[str] = None,
        depth: Optional[int] = None,
    ) -> None:
        """Require a retained token to match the current neutral route facts."""
        self.contracts()
        from bt_api_py.market_stream_contracts import market_stream_retained_binding_matches

        channel = binding.get("channel", "tick") if channel is None else channel
        depth = binding.get("depth") if depth is None else depth
        if not market_stream_retained_binding_matches(
            binding, api, route, instrument, channel, depth
        ):
            self._mark_unavailable(
                str(dataname), channel, "MARKET_STREAM_BINDING_CHANGED"
            )
            raise self._error_type(
                "SDK market-stream route or instrument binding changed"
            )

    def start_subscription(
        self,
        dataname: str,
        *,
        channel: str = "tick",
        depth: Optional[int] = None,
        api: Any,
    ) -> bool:
        """Create or validate one exact consumer-to-SDK subscription binding."""
        if channel not in {"tick", "orderbook"}:
            raise self._error_type("unsupported SDK market stream channel")
        if channel == "tick" and depth is not None:
            raise self._error_type("tick market streams cannot request depth")
        if channel == "orderbook" and depth is None:
            self._mark_unavailable(
                str(dataname), channel, "MARKET_STREAM_DEPTH_UNCONFIGURED"
            )
            return False

        symbol = str(dataname)
        route, instrument = self._resolve_route_context(api, symbol)
        self._assert_route_current(api)
        contracts = self.contracts()
        stream_error_type = contracts[2]
        request_type = contracts[5]
        selector_type = contracts[6]
        subscription_type = contracts[8]
        if not isinstance(api, selector_type):
            raise self._error_type(
                "SDK facade lacks the optional market-stream selector"
            )

        key = self._binding_key(symbol, channel, depth)
        try:
            from bt_api_py.market_stream_contracts import (
                market_stream_subscription_matches_request,
                read_market_stream_queue_depth,
                select_market_stream,
                subscribe_market_stream,
            )

            stream = select_market_stream(api, error_type=self._error_type)
            existing = self._bindings.get(key)
            if existing is not None:
                if (
                    existing.get("api") is api
                    and existing.get("stream") is stream
                    and existing.get("route") == route
                    and existing.get("instrument") == instrument
                    and existing.get("channel") == channel
                    and existing.get("depth") == depth
                    and type(existing.get("subscription")) is subscription_type
                ):
                    self.assert_binding(
                        symbol, existing, api, route, instrument, channel, depth
                    )
                    if not self.identity_matches(
                        stream, existing["subscription"], channel
                    ):
                        raise self._error_type(
                            "SDK market-stream identity is unavailable or mismatched"
                        )
                    queue_depth = read_market_stream_queue_depth(
                        stream, existing["subscription"], error_type=self._error_type
                    )
                    existing["last_queue_depth"] = queue_depth
                    self._observe_queue_depth(symbol, channel, queue_depth, False)
                    self._assert_route_current(api)
                    return True
                raise self._error_type(
                    "market-stream subscription has a conflicting consumer binding"
                )

            request = request_type(
                exchange_name=route,
                instrument=instrument,
                channel=channel,
                depth=depth,
                queue_capacity=self.queue_capacity,
            )
            subscription = subscribe_market_stream(stream, request, error_type=self._error_type)
            binding = {
                "api": api,
                "route": route,
                "instrument": instrument,
                "stream": stream,
                "subscription": subscription,
                "symbol": symbol,
                "channel": channel,
                "depth": depth,
                "last_dropped_events": 0,
            }
            # Retain the exact token before validating post-start facts so stop
            # can still attempt its one exact unsubscribe.
            self._bindings[key] = binding
            if not market_stream_subscription_matches_request(
                subscription, request, instrument, channel, depth
            ):
                raise self._error_type(
                    "SDK market-stream handle is bound to a different request"
                )
            if not self.identity_matches(stream, subscription, channel):
                raise self._error_type(
                    "SDK market-stream identity is unavailable or mismatched"
                )
            queue_depth = read_market_stream_queue_depth(
                stream, subscription, error_type=self._error_type
            )
            binding["last_queue_depth"] = queue_depth
            self._observe_queue_depth(symbol, channel, queue_depth, True)
            self._assert_route_current(api)
            return True
        except stream_error_type as exc:
            code = getattr(exc, "code", "MARKET_STREAM_PROJECTOR_INVALID")
            self._mark_unavailable(symbol, channel, code)
            if channel == "orderbook" and code in self._UNAVAILABLE_CHANNEL_CODES:
                return False
            raise self._error_type(
                f"SDK market stream is unavailable ({code})"
            ) from None
        except Exception as exc:
            self._sanitize_exception(exc)
            self._mark_unavailable(
                symbol, channel, "MARKET_STREAM_UNAVAILABLE"
            )
            if isinstance(exc, self._error_type):
                raise
            code = self._safe_exception_code(exc, "sdk_market_stream_unavailable")
            raise self._error_type(
                f"SDK market stream is unavailable ({code})"
            ) from None

    def identity(
        self,
        dataname: str,
        *,
        channel: str = "tick",
        depth: Optional[int] = None,
        api: Any,
    ):
        """Return the exact SDK identity receipt for one bound consumer route."""
        contracts = self.contracts()
        identity_type = contracts[1] if channel == "tick" else contracts[14]
        unavailable_identity = contracts[12]
        binding = self._find_binding(dataname, channel, depth)
        if binding is None:
            return unavailable_identity("PROJECTOR_NOT_CREATED") if channel == "tick" else None
        if api is None or binding.get("api") is not api:
            raise self._error_type(
                "market stream identity is not bound to the active consumer API"
            )
        route, instrument = self._resolve_route_context(api, dataname)
        try:
            self._assert_route_current(api)
            self.assert_binding(
                dataname, binding, api, route, instrument, channel, binding["depth"]
            )
            identity = binding["stream"].stream_identity(binding["subscription"])
            if type(identity) is not identity_type:
                raise self._error_type(
                    "SDK market-stream identity has an invalid type"
                )
            return identity
        except Exception as exc:
            self._sanitize_exception(exc)
            if isinstance(exc, self._error_type):
                raise
            raise self._error_type(
                "market stream identity route or instrument binding changed"
            ) from None

    def queue_depth(
        self,
        dataname: str,
        channel: str = "tick",
        depth: Optional[int] = None,
    ) -> int:
        """Observe the SDK queue without consuming or mirroring its events."""
        binding = self._find_binding(dataname, channel, depth)
        if binding is None:
            return 0
        try:
            from bt_api_py.market_stream_contracts import read_market_stream_queue_depth

            observed = read_market_stream_queue_depth(
                binding["stream"], binding["subscription"], error_type=self._error_type
            )
            binding["last_queue_depth"] = observed
            return observed
        except Exception as exc:
            self._sanitize_exception(exc)
            if isinstance(exc, self._error_type):
                raise
            code = self._safe_exception_code(
                exc, "market_stream_queue_depth_unavailable"
            )
            raise self._error_type(
                f"SDK market-stream queue depth is unavailable ({code})"
            ) from None

    def supports_channel(
        self,
        dataname: str,
        api: Any,
        *,
        channel: str,
        depth: Optional[int] = None,
    ) -> bool:
        """Check a retained route-bound port's advertised neutral channels."""
        binding = self._find_binding(dataname, channel, depth)
        if binding is None or binding.get("api") is not api:
            return False
        route, instrument = self._resolve_route_context(api, str(dataname))
        self.assert_binding(
            str(dataname), binding, api, route, instrument, channel, binding["depth"]
        )
        port_type = self.contracts()[4]
        stream = binding.get("stream")
        channels = getattr(stream, "supported_channels", None)
        return (
            isinstance(stream, port_type)
            and type(channels) is tuple
            and channel in channels
        )

    def accept_resync(
        self,
        dataname: str,
        api: Any,
        *,
        channel: str,
        depth: Optional[int],
        acknowledge_data_loss: bool,
    ):
        """Call the SDK's explicit acknowledgement and validate its receipt."""
        binding = self._find_binding(dataname, channel, depth)
        if binding is None or binding.get("api") is not api:
            raise self._error_type(
                "market-stream recovery subscription is unavailable"
            )
        stream = binding["stream"]
        subscription = binding["subscription"]
        try:
            from bt_api_py.market_stream_contracts import accept_market_stream_resync

            self.contracts()
            result = accept_market_stream_resync(
                stream, subscription, channel,
                acknowledge_data_loss=acknowledge_data_loss,
                error_type=self._error_type,
            )
            previous_dropped = binding.get("last_dropped_events", 0)
            if type(previous_dropped) is not int:
                raise self._error_type(
                    "SDK market-stream drop count is invalid"
                )
            binding["last_dropped_events"] = result.dropped_events
            binding["last_queue_depth"] = result.queue_depth
            return result, previous_dropped
        except Exception as exc:
            self._sanitize_exception(exc)
            if isinstance(exc, self._error_type):
                raise
            code = self._safe_exception_code(
                exc, "market_stream_resync_unverified"
            )
            raise self._error_type(
                f"SDK market-stream recovery is unavailable ({code})"
            ) from None

    def poll_result(
        self,
        dataname: str,
        api: Any = None,
        *,
        channel: str = "tick",
        depth: Optional[int] = None,
        pump_only: bool = False,
    ):
        """Call and validate the neutral SDK poll protocol for one binding."""
        symbol = str(dataname)
        binding = self._find_binding(symbol, channel, depth)
        if binding is None:
            return None, None, False, False, 0
        contracts = self.contracts()
        (
            _identity_status_type,
            _identity_type,
            stream_error_type,
            _poll_result_type,
            _stream_port_type,
            _request_type,
            _selector_type,
            stream_state_type,
            _subscription_type,
            _tick_event_type,
            _source_type,
            _subscription_key_type,
            _unavailable_identity,
            _book_event_type,
            _identity_receipt_type,
            _identity_status_type,
        ) = contracts
        try:
            from bt_api_py.market_stream_contracts import poll_market_stream

            route, instrument = self._resolve_route_context(api, symbol)
            self._assert_route_current(api)
            self.assert_binding(
                symbol, binding, api, route, instrument, channel, binding["depth"]
            )
            stream = binding["stream"]
            subscription = binding["subscription"]
            result = poll_market_stream(
                stream, subscription, channel,
                pump_only=pump_only, error_type=self._error_type,
            )
            previous_dropped = binding.get("last_dropped_events", 0)
            if type(previous_dropped) is not int:
                raise self._error_type(
                    "SDK market-stream drop count is invalid"
                )
            binding["last_dropped_events"] = result.dropped_events
            binding["last_queue_depth"] = result.queue_depth
            return (
                result,
                route,
                result.state is stream_state_type.ACTIVE,
                result.state is stream_state_type.CLEANUP_UNVERIFIED,
                previous_dropped,
            )
        except stream_error_type as exc:
            code = getattr(exc, "code", "MARKET_STREAM_PROJECTOR_INVALID")
            self._mark_unavailable(
                symbol, channel, "sdk_market_stream_poll_unavailable"
            )
            if code == "MARKET_STREAM_OWNER_THREAD_MISMATCH":
                raise self._error_type(
                    "SDK market stream must be used on its owner thread"
                ) from None
            raise self._error_type(
                f"SDK market stream is unavailable ({code})"
            ) from None
        except Exception as exc:
            self._sanitize_exception(exc)
            self._mark_unavailable(
                symbol, channel, "sdk_market_stream_poll_unavailable"
            )
            if isinstance(exc, self._error_type):
                raise
            code = self._safe_exception_code(
                exc, "sdk_market_stream_poll_unavailable"
            )
            raise self._error_type(
                f"SDK market stream is unavailable ({code})"
            ) from None

    def close_all(self) -> bool:
        """Unsubscribe exact retained tokens, preserving any uncertain handle."""
        for binding_key, binding in tuple(self._bindings.items()):
            symbol, channel, _depth = binding_key
            try:
                from bt_api_py.market_stream_contracts import unsubscribe_market_stream

                unsubscribe_market_stream(
                    binding["stream"], binding["subscription"], error_type=self._error_type
                )
            except Exception as exc:
                self._sanitize_exception(exc)
                code = self._safe_exception_code(
                    exc, "market_stream_unsubscribe_unverified"
                )
                self._on_close_failure(symbol, channel, code)
                return False
            self._bindings.pop(binding_key, None)
            self._on_binding_closed(symbol, channel)
        return True
