"""Authenticated, journal-bound strategy execution stream for Gateway owners."""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Mapping
from types import MappingProxyType

from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.gateway_execution_stream_protocol import (
    GatewayExecutionStreamEvent,
    GatewayExecutionStreamPage,
    GatewayExecutionStreamProgress,
    gateway_execution_consumer_ref,
    project_gateway_execution_observation,
)
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
)
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger

from bt_api_py.service_registry import ServiceCallContext, ServiceResultTooLargeError

STREAM_OPERATIONS = frozenset(
    {"direct.events.read_after", "direct.events.progress", "direct.events.ack"}
)


def _ref(value):
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 256


def _epoch(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _reject(code):
    return {"status": "REQUEST_REJECTED", "code": code}


class GatewayExecutionStreamService:
    """One lifecycle-locked view; it never invokes provider or guard APIs."""

    def __init__(self, ledger, journal, grants, *, execution_domain_ref):
        if (
            type(ledger) is not GatewayDirectExecutionLedger
            or type(journal) is not GatewayDirectCommandJournal
            or not ledger.is_bound_to(journal)
            or not isinstance(grants, Mapping)
            or not grants
            or any(not _ref(key) or not _ref(value) for key, value in grants.items())
            or not _ref(execution_domain_ref)
        ):
            raise ValueError("INVALID_EXECUTION_STREAM_CONFIGURATION")
        self._pid = os.getpid()
        self._ledger, self._journal = ledger, journal
        self._grants = MappingProxyType(dict(grants))
        self._domain = execution_domain_ref
        self._condition = threading.Condition()
        self._closed = False
        self._inflight = 0

    def _check_pid(self):
        if os.getpid() != self._pid:
            raise RuntimeError("PROCESS_BOUNDARY")

    def matches(self, journal, grants, execution_domain_ref):
        self._check_pid()
        return (
            self._journal is journal
            and self._grants == grants
            and self._domain == execution_domain_ref
        )

    def begin_close(self):
        self._check_pid()
        with self._condition:
            self._closed = True

    def close(self, timeout_seconds=5.0):
        self._check_pid()
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or not 0 <= timeout_seconds <= 3600
        ):
            raise ValueError("INVALID_CLOSE_TIMEOUT")
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            self._closed = True
            while self._inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def handle(self, operation, *, context, arguments):
        self._check_pid()
        if type(context) is not ServiceCallContext or not isinstance(arguments, Mapping):
            return _reject("INVALID_REQUEST")
        identity, scope = context.identity, context.scope
        strategy = self._grants.get(context.principal_ref)
        if (
            identity.role is not WireRole.STRATEGY_CLIENT
            or identity.principal_ref != context.principal_ref
            or identity.actor_ref != strategy
            or identity.delegate_ref is not None
            or strategy is None
            or scope.execution_domain_ref != self._domain
            or scope.account_ref != self._journal.account_scope
            or scope.strategy_ref != strategy
            or not _ref(scope.run_id)
            or scope.account_refs
            or scope.portfolio_ref is not None
            or scope.manual_book_ref is not None
            or scope.group_id is not None
            or scope.leg_id is not None
        ):
            return _reject("SCOPE_DENIED")
        fields = {
            "direct.events.read_after": {"after_sequence", "expected_epoch", "limit"},
            "direct.events.progress": set(),
            "direct.events.ack": {"sequence", "expected_epoch"},
        }.get(operation)
        if fields is None or set(arguments) != fields:
            return _reject("INVALID_REQUEST")
        if operation == "direct.events.read_after":
            seq, epoch, limit = (
                arguments["after_sequence"],
                arguments["expected_epoch"],
                arguments["limit"],
            )
            if (
                type(seq) is not int
                or not 0 <= seq < 2**63
                or (epoch is not None and not _epoch(epoch))
                or (seq > 0 and epoch is None)
                or type(limit) is not int
                or not 1 <= limit <= 256
            ):
                return _reject("INVALID_REQUEST")
        if operation == "direct.events.ack" and (
            type(arguments["sequence"]) is not int
            or not 0 <= arguments["sequence"] < 2**63
            or not _epoch(arguments["expected_epoch"])
        ):
            return _reject("INVALID_REQUEST")
        if not callable(context.result_fits):
            raise RuntimeError("REPLY_BUDGET_UNAVAILABLE")
        principal = context.principal_ref
        binding = GatewayCommandBinding(
            self._journal.journal_id, self._journal.account_scope, strategy, principal
        )
        consumer = gateway_execution_consumer_ref(
            principal_ref=principal,
            actor_ref=identity.actor_ref,
            execution_domain_ref=self._domain,
            account_scope=self._journal.account_scope,
            strategy_scope=strategy,
        )

        def progress_value(progress):
            return GatewayExecutionStreamProgress(
                binding,
                consumer,
                progress.epoch,
                progress.offered_sequence,
                progress.acknowledged_sequence,
            )

        def page_value(page):
            events = tuple(
                GatewayExecutionStreamEvent(
                    event.sequence,
                    event.request_key,
                    event.reservation_id,
                    event.command_digest,
                    event.command_principal,
                    event.fact_digest,
                    project_gateway_execution_observation(event.fact),
                    cancel_attribution=event.cancel_attribution,
                )
                for event in page.events
            )
            return GatewayExecutionStreamPage(
                binding,
                consumer,
                page.epoch,
                page.offered_sequence,
                page.acknowledged_sequence,
                page.has_more,
                events,
            )

        def fits_page(page):
            return context.result_fits({"status": "OK", "page": page_value(page).to_mapping()})

        def fits_progress(progress):
            return context.result_fits(
                {"status": "OK", "progress": progress_value(progress).to_mapping()}
            )

        with self._condition:
            if self._closed:
                return _reject("EVENTS_UNAVAILABLE")
            self._inflight += 1
        try:
            try:
                if operation == "direct.events.read_after":
                    page = self._ledger.read_for_consumer(
                        strategy,
                        principal_id=principal,
                        consumer_ref=consumer,
                        after_sequence=seq,
                        expected_epoch=epoch,
                        limit=limit,
                        result_fits=fits_page,
                    )
                    return {"status": "OK", "page": page_value(page).to_mapping()}
                if operation == "direct.events.ack":
                    progress = self._ledger.acknowledge(
                        strategy,
                        principal_id=principal,
                        consumer_ref=consumer,
                        sequence=arguments["sequence"],
                        expected_epoch=arguments["expected_epoch"],
                        result_fits=fits_progress,
                    )
                else:
                    progress = self._ledger.consumer_progress(
                        strategy, principal_id=principal, consumer_ref=consumer
                    )
                    fits = fits_progress(progress)
                    if type(fits) is not bool:
                        raise RuntimeError("INVALID_REPLY_BUDGET")
                    if not fits:
                        raise ServiceResultTooLargeError()
                return {"status": "OK", "progress": progress_value(progress).to_mapping()}
            except DirectCommandJournalError as error:
                if error.code == "PAGE_TOO_LARGE":
                    raise ServiceResultTooLargeError() from None
                if error.code in {
                    "INVALID_SEQUENCE",
                    "EPOCH_MISMATCH",
                    "CONSUMER_CAPACITY_EXCEEDED",
                }:
                    return _reject(error.code)
                raise
        finally:
            with self._condition:
                self._inflight -= 1
                self._condition.notify_all()
