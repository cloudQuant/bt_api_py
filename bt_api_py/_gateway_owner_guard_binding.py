"""Private, local consistency check for a Gateway owner's existing resources.

This object conveys neither account authority nor permission to dispatch.
"""

import os
import threading

from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_direct_owner import GatewayDirectOwner


class _GatewayOwnerGuardBinding:
    """Retain the exact factory resources for owner-thread checks only."""

    def __init__(self, owner, journal, guard, dispatcher, *, account, journal_id,
                 generation, exchange, closing, instance_id):
        if (type(owner) is not GatewayDirectOwner
                or type(journal) is not GatewayDirectCommandJournal
                or type(guard) is not LocalAccountSessionGuard
                or type(dispatcher) is not GatewayDirectDispatcher
                or type(closing) is not threading.Event
                or type(instance_id) is not str or not instance_id
                or instance_id != instance_id.strip()
                or owner._pid != os.getpid()
                or owner._thread is not threading.current_thread()
                or closing.is_set()
                or journal.account_scope != account
                or journal.journal_id != journal_id
                or journal.generation != generation):
            raise RuntimeError("GATEWAY_OWNER_GUARD_BINDING_INVALID")
        with owner._condition:
            if (owner._closing or owner._done.is_set() or owner._binding is not None
                    or owner._journal is not None or owner._dispatcher is not None):
                raise RuntimeError("GATEWAY_OWNER_GUARD_BINDING_INVALID")
        journal._assert_account_guard(guard)
        dispatcher.assert_owned()
        if not dispatcher.is_bound_to(journal, guard, exchange):
            raise RuntimeError("GATEWAY_OWNER_GUARD_BINDING_INVALID")
        self.__owner, self.__journal, self.__guard = owner, journal, guard
        self.__dispatcher, self.__closing = dispatcher, closing
        self.__account, self.__journal_id = account, journal_id
        self.__generation, self.__exchange = generation, exchange
        self.__instance_id = instance_id
        self.__pid, self.__thread = os.getpid(), threading.current_thread()

    def assert_instance(self, instance_id):
        """Compare the factory's selected instance on the native owner thread."""
        if (os.getpid() != self.__pid or threading.current_thread() is not self.__thread
                or type(instance_id) is not str or instance_id != self.__instance_id
                or self.__closing.is_set()):
            raise RuntimeError("GATEWAY_OWNER_GUARD_BINDING_INVALID")

    def assert_bound(self, owner, journal):
        """Check the published owner still holds this exact local composition."""
        if (os.getpid() != self.__pid or threading.current_thread() is not self.__thread
                or owner is not self.__owner or journal is not self.__journal
                or self.__closing.is_set()):
            raise RuntimeError("GATEWAY_OWNER_GUARD_BINDING_INVALID")
        # Hold the owner's condition while checking publication and resources,
        # so close cannot change the owner's state during the check.
        with owner._condition:
            binding = owner.binding()
            if (self.__closing.is_set() or binding is None or owner._done.is_set()
                    or owner._thread is not self.__thread
                    or owner._dispatcher is not self.__dispatcher
                    or not owner.is_bound_to(journal)
                    or binding.account_scope != self.__account
                    or binding.journal_id != self.__journal_id
                    or binding.generation != self.__generation
                    or journal.account_scope != self.__account
                    or journal.journal_id != self.__journal_id
                    or journal.generation != self.__generation):
                raise RuntimeError("GATEWAY_OWNER_GUARD_BINDING_INVALID")
            journal._assert_account_guard(self.__guard)
            self.__dispatcher.assert_owned()
            if not self.__dispatcher.is_bound_to(journal, self.__guard, self.__exchange):
                raise RuntimeError("GATEWAY_OWNER_GUARD_BINDING_INVALID")
            if self.__closing.is_set():
                raise RuntimeError("GATEWAY_OWNER_GUARD_BINDING_INVALID")

    def reserve_first_risk(self, owner, journal, request, payload, *, permit, trading_fence):
        """Use the factory-owned guard on its owner thread; return no guard capability."""
        from contextlib import nullcontext

        self._assert_bound_nonblocking(owner, journal)
        command = request.command
        result = journal.reserve_first_delegated(
            command.strategy_scope, command.request_key, command.actor_principal, payload,
            guard=self.__guard, reservation_fence=nullcontext, delegation_permit=permit,
            ttl_seconds=request.requested_ttl_ms / 1000,
            origin_trading_fence=trading_fence, risk_v2_request=request,
        )
        self._assert_bound_nonblocking(owner, journal)
        return result

    def _assert_bound_nonblocking(self, owner, journal):
        """Never wait for the owner condition while Registry admission is held."""
        if not owner._condition.acquire(blocking=False):
            raise RuntimeError("GATEWAY_OWNER_GUARD_BINDING_INVALID")
        try:
            self.assert_bound(owner, journal)
        finally:
            owner._condition.release()

    def first_risk_remaining(self, owner, journal, first):
        """Resample the same guarded journal clock before projecting the result."""
        from bt_api_gateway.direct_command_journal import (
            FirstDelegatedReservation, _bounded_remaining_ttl, _first_reservation_clock_scope,
        )

        if type(first) is not FirstDelegatedReservation:
            raise RuntimeError("GATEWAY_FIRST_RESERVATION_INVALID")
        self._assert_bound_nonblocking(owner, journal)
        with _first_reservation_clock_scope(self.__guard, journal.generation, journal._clock) as clock:
            clock.sample(journal._clock)
            self._assert_bound_nonblocking(owner, journal)
            now = clock.sample(journal._clock)
            self._assert_bound_nonblocking(owner, journal)
            return min(first.remaining_ttl_seconds, _bounded_remaining_ttl(first.expires_at, now))
