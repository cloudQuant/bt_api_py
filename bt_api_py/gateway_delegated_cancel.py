"""Durable strategy cancellation of a command created by a delegated actor."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_py.gateway_direct_client import GatewayClientRoute


@dataclass(frozen=True, slots=True)
class GatewayDelegatedCancelPlan:
    binding: GatewayCommandBinding
    route: GatewayClientRoute
    operation_id: str
    request_key: str
    reservation_id: str
    digest: str
    target_actor_principal: str


@dataclass(frozen=True, slots=True)
class GatewayDelegatedCancelObservation:
    account_scope: str
    strategy_scope: str
    requester_principal: str
    target_actor_principal: str
    request_key: str
    operation_id: str
    attempt_id: str
    target_reservation_id: str
    target_digest: str
    state: str
    version: int
    owner_generation: str
    attempt_generation: str


@dataclass(frozen=True, slots=True)
class GatewayDelegatedCancelResult:
    outcome: str
    observation: GatewayDelegatedCancelObservation | None = None
    code: str | None = None


class GatewayDelegatedCancelHost(Protocol):
    """Persist detached plans before requests, and exact observed results afterwards."""

    def save_delegated_cancel_plan(self, plan: GatewayDelegatedCancelPlan) -> GatewayDelegatedCancelPlan: ...
    def load_delegated_cancel_plan(self, operation_id: str) -> GatewayDelegatedCancelPlan | None: ...
    def save_delegated_cancel_observation(self, operation_id: str, observation: GatewayDelegatedCancelObservation) -> GatewayDelegatedCancelObservation: ...
    def load_delegated_cancel_observation(self, operation_id: str) -> GatewayDelegatedCancelObservation | None: ...
