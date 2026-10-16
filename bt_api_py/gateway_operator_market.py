"""Trusted provider binding for the normal Gateway market-hub role."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from bt_api_base.market_protocol import MarketSubscriptionKey

if TYPE_CHECKING:
    from bt_api_gateway.market_hub import (
        MarketHistoryProvider as GatewayMarketHistoryProvider,
    )
    from bt_api_gateway.market_hub import (
        MarketProvider as GatewayMarketProvider,
    )

_SAFE_PRINCIPAL_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class GatewayMarketBinding:
    """Server-trusted key universe and lazy typed provider factory.

    Provider factories are trusted in-process deployment code. They must be
    lazy: constructing a provider must not connect to a venue; the Market Hub
    calls ``subscribe`` only after an authorized registration is accepted.
    Client manifests and requests never supply endpoint or credential values.
    """

    key_universe: tuple[MarketSubscriptionKey, ...]
    principal_allowed_keys: Mapping[str, frozenset[MarketSubscriptionKey]] = field(
        repr=False
    )
    provider_factory: Callable[[], GatewayMarketProvider] = field(
        repr=False, compare=False
    )
    max_provider_events_per_maintain: int = 32
    history_key_universe: tuple[MarketSubscriptionKey, ...] = ()
    principal_allowed_history_keys: Mapping[
        str, frozenset[MarketSubscriptionKey]
    ] = field(default_factory=dict, repr=False)
    history_provider_factory: Callable[[], GatewayMarketHistoryProvider] | None = field(
        default=None, repr=False, compare=False
    )
    history_timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        from .gateway_operator import GatewayOperatorDeploymentError

        try:
            keys = tuple(self.key_universe)
            if (
                not keys
                or len(keys) > 1_024
                or any(type(key) is not MarketSubscriptionKey for key in keys)
                or len(set(keys)) != len(keys)
                or not callable(self.provider_factory)
                or type(self.max_provider_events_per_maintain) is not int
                or not 1 <= self.max_provider_events_per_maintain <= 256
                or not isinstance(self.principal_allowed_keys, Mapping)
            ):
                raise ValueError

            universe = frozenset(keys)
            audience: dict[str, frozenset[MarketSubscriptionKey]] = {}
            for principal_ref, allowed in self.principal_allowed_keys.items():
                if (
                    type(principal_ref) is not str
                    or _SAFE_PRINCIPAL_REF.fullmatch(principal_ref) is None
                ):
                    raise ValueError
                frozen_allowed = frozenset(allowed)
                if (
                    not frozen_allowed
                    or any(type(key) is not MarketSubscriptionKey for key in frozen_allowed)
                    or not frozen_allowed <= universe
                ):
                    raise ValueError
                audience[principal_ref] = frozen_allowed
            if not audience:
                raise ValueError

            history_keys = tuple(self.history_key_universe)
            history_factory = self.history_provider_factory
            history_timeout = self.history_timeout_seconds
            history_acl_input = self.principal_allowed_history_keys
            if history_factory is None:
                if history_keys or history_acl_input or history_timeout is not None:
                    raise ValueError
                history_audience: dict[str, frozenset[MarketSubscriptionKey]] = {}
            else:
                if (
                    not callable(history_factory)
                    or not history_keys
                    or len(history_keys) > 1_024
                    or any(type(key) is not MarketSubscriptionKey for key in history_keys)
                    or len(set(history_keys)) != len(history_keys)
                    or any(key.channel != "bar" for key in history_keys)
                    or not frozenset(history_keys).isdisjoint(universe)
                    or isinstance(history_timeout, bool)
                    or not isinstance(history_timeout, (int, float))
                    or not math.isfinite(float(history_timeout))
                    or not 0 < float(history_timeout) <= 300.0
                    or not isinstance(history_acl_input, Mapping)
                    or set(history_acl_input) != set(audience)
                ):
                    raise ValueError
                history_universe = frozenset(history_keys)
                history_audience = {}
                for principal_ref, allowed in history_acl_input.items():
                    frozen_allowed = frozenset(allowed)
                    if (
                        any(type(key) is not MarketSubscriptionKey for key in frozen_allowed)
                        or not frozen_allowed <= history_universe
                    ):
                        raise ValueError
                    history_audience[principal_ref] = frozen_allowed
                object.__setattr__(self, "history_timeout_seconds", float(history_timeout))

            object.__setattr__(self, "key_universe", keys)
            object.__setattr__(self, "principal_allowed_keys", MappingProxyType(audience))
            object.__setattr__(self, "history_key_universe", history_keys)
            object.__setattr__(
                self,
                "principal_allowed_history_keys",
                MappingProxyType(history_audience),
            )
        except GatewayOperatorDeploymentError:
            raise
        except Exception:
            raise GatewayOperatorDeploymentError("INVALID_MARKET_BINDING") from None


__all__ = ["GatewayMarketBinding"]
