"""Trusted provider binding for the normal Gateway market-hub role."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from bt_api_base.market_protocol import MarketSubscriptionKey

if TYPE_CHECKING:
    from bt_api_gateway.market_hub import MarketProvider as GatewayMarketProvider

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

            object.__setattr__(self, "key_universe", keys)
            object.__setattr__(self, "principal_allowed_keys", MappingProxyType(audience))
        except GatewayOperatorDeploymentError:
            raise
        except Exception:
            raise GatewayOperatorDeploymentError("INVALID_MARKET_BINDING") from None


__all__ = ["GatewayMarketBinding"]
