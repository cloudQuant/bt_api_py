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
                 generation, exchange, closing):
        if (type(owner) is not GatewayDirectOwner
                or type(journal) is not GatewayDirectCommandJournal
                or type(guard) is not LocalAccountSessionGuard
                or type(dispatcher) is not GatewayDirectDispatcher
                or type(closing) is not threading.Event
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
        self.__pid, self.__thread = os.getpid(), threading.current_thread()

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
