"""R21 offline CTP profile over a spawned CURVE Gateway.

Only native TD/MD are synthetic. This is not evidence of a venue connection,
account execution, or Backtrader Broker/Store integration.
"""
from __future__ import annotations

import multiprocessing
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

import pytest
import zmq
from bt_api_py import _win32_security as win32_security
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_py._contracts.models import OrderRequest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.gateway_strategy_market import GatewayMarketClientSettings, GatewayMarketSubscriptionSettings
from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_runtime import ClientCurveKeys
from tests.integration.test_iter63_gateway_direct_service import (
    CLIENT_A, CLIENT_B, DOMAIN, OPS, SERVICE, _client_config, _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_direct_client import MemoryHost
from tests.integration.test_iter63_gateway_cancel_rpc import CancelHost, OPS as WRITE_OPS
from bt_api_py.gateway_execution_stream_service import STREAM_OPERATIONS
DIRECT_OPS = WRITE_OPS | STREAM_OPERATIONS
from tests.unit.test_iter63_gateway_direct_service import _order

CLIENT_C = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-c", "strategy-c")
ACCOUNT = AccountKey("ctp", "simnow", "simnow", "account", "9999")
SCOPE = ACCOUNT.account_key_id
SOURCE = "ctp-native-market-r1"
ENTITLEMENT = "ctp-quote-v2-entitlement-r1"
RESOURCE = AccountResourceKey("future", "futures")
MARKET_OPS = frozenset({"market.subscribe", "market.unsubscribe", "market.snapshot", "market.head", "market.read_after", "market.bootstrap_begin", "market.bootstrap_status", "market.bootstrap_cancel"})
ACCOUNT_OPS = frozenset({"account.snapshot.typed"})


def _key(symbol):
    return MarketSubscriptionKey(SOURCE, ENTITLEMENT, MarketInstrumentKey(
        "ctp", "CFFEX", "simnow", "future", symbol, symbol), "tick")


def _gateway(root, rpc, market, server_keys, client_keys, journal_id, issuer_id, events, controls, blocked_start=None, blocked_poll=None, blocked_stop=None, blocked_return_poll=None):
    """Runs the actual profile and CTP Python providers in the spawned process."""
    root = Path(root)
    deployment = None
    try:
        ctp_tests = Path(__file__).resolve().parents[2] / "bt_api" / "bt_api_ctp" / "tests"
        sys.path.insert(0, str(ctp_tests))
        from test_iter62_settlement_readback_on_write import _NativeApi, _login
        from test_market_provider import _SyntheticMdClient
        from tests.integration.test_iter63_ctp_gateway_return_maintenance import _relogin_same_spi
        from bt_api_ctp.ctp.client import TraderClient, CtpNativeStopReceipt
        from bt_api_ctp.feeds import live_ctp_feed
        from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream, CtpRequestDataFuture
        from bt_api_ctp.trusted_flow import flow_binding, trusted_flow
        from bt_api_ctp.market_provider import CtpQuoteV2MarketProvider
        from bt_api_gateway.market_hub import GatewayMarketHub
        from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
        from bt_api_py.gateway_owner_market import GatewayOwnerMarketProvider
        from dataclasses import replace

        owner_thread_id = threading.get_ident()
        owner_phase_lock = threading.Lock()
        owner_phase_capture = [False]
        owner_service_context = threading.local()
        owner_instances = {}
        owner_service_sequence = [0]
        owner_service_latest = {}
        owner_service_live = {}
        owner_service_counts = {}
        owner_service_events = []
        owner_maintenance = {"started": 0, "completed": 0, "active": 0,
                             "thread_is_gateway": None, "last_error": None}

        def owner_error_code(error):
            try:
                code = error.code
            except Exception:
                code = None
            if type(code) is str and 1 <= len(code) <= 64 and code.isascii():
                return code
            return type(error).__name__

        def record_owner_event(event, meta, *, same_thread=None, error=None):
            key = (meta["kind"], meta["symbol"])
            with owner_phase_lock:
                counts = owner_service_counts.setdefault(key, [0, 0, 0, 0])
                index = {"submitted": 0, "started": 1, "returned": 2, "error": 3}.get(event)
                if index is not None:
                    counts[index] += 1
                if len(owner_service_events) < 32:
                    owner_service_events.append((meta["point"], meta["kind"], meta["symbol"],
                                                 event, same_thread, error))

        original_owner_init = GatewayDirectOwner.__init__
        original_owner_submit = GatewayDirectOwner.submit_service_call

        def observe_owner_init(self, factory, *args, **kwargs):
            with owner_phase_lock:
                owner_instances[id(self)] = self

            def observed_factory():
                resources = factory()
                if (type(resources) is GatewayDirectOwnerResources
                        and callable(resources.maintain)):
                    original_maintain = resources.maintain

                    def observed_maintain():
                        with owner_phase_lock:
                            owner_maintenance["started"] += 1
                            owner_maintenance["active"] += 1
                            owner_maintenance["thread_is_gateway"] = (
                                threading.get_ident() == owner_thread_id
                            )
                        try:
                            return original_maintain()
                        except BaseException as error:
                            with owner_phase_lock:
                                owner_maintenance["last_error"] = owner_error_code(error)
                            raise
                        finally:
                            with owner_phase_lock:
                                owner_maintenance["completed"] += 1
                                owner_maintenance["active"] -= 1

                    resources = replace(resources, maintain=observed_maintain)
                return resources

            return original_owner_init(self, observed_factory, *args, **kwargs)

        def observe_owner_submit(self, callback):
            with owner_phase_lock:
                owner_instances[id(self)] = self
            if not owner_phase_capture[0]:
                return original_owner_submit(self, callback)
            tag = getattr(owner_service_context, "tag", None)
            kind, symbol = tag if type(tag) is tuple and len(tag) == 2 else ("other", None)
            with owner_phase_lock:
                owner_service_sequence[0] += 1
                token = owner_service_sequence[0]
                meta = {"token": token, "point": "recovery_window", "kind": kind, "symbol": symbol,
                        "pre_fault": not recovery_fault_started[0], "handle": None,
                        "started": False, "returned": False, "same_thread": None, "error": None}
                owner_service_latest[(kind, symbol)] = meta
                owner_service_live[token] = meta
            record_owner_event("submitted", meta)

            def observed_callback():
                with owner_phase_lock:
                    meta["started"] = True
                    meta["same_thread"] = threading.get_ident() == owner_thread_id
                record_owner_event("started", meta, same_thread=meta["same_thread"])
                try:
                    return callback()
                except BaseException as error:
                    meta["error"] = owner_error_code(error)
                    record_owner_event("error", meta, same_thread=meta["same_thread"],
                                       error=meta["error"])
                    raise
                finally:
                    with owner_phase_lock:
                        meta["returned"] = True
                        owner_service_live.pop(token, None)
                    record_owner_event("returned", meta, same_thread=meta["same_thread"],
                                       error=meta["error"])

            try:
                handle = original_owner_submit(self, observed_callback)
            except BaseException as error:
                with owner_phase_lock:
                    meta["error"] = owner_error_code(error)
                    meta["returned"] = True
                    owner_service_live.pop(token, None)
                record_owner_event("error", meta, error=meta["error"])
                raise
            with owner_phase_lock:
                meta["handle"] = handle
            return handle

        GatewayDirectOwner.__init__ = observe_owner_init
        GatewayDirectOwner.submit_service_call = observe_owner_submit

        def with_owner_service_tag(kind, symbol, callback):
            previous = getattr(owner_service_context, "tag", None)
            owner_service_context.tag = (kind, symbol)
            try:
                return callback()
            finally:
                owner_service_context.tag = previous

        def owner_phase_snapshot(point):
            with owner_phase_lock:
                owners = tuple(owner_instances.values())
                latest = tuple(owner_service_latest.values())
                live = tuple(owner_service_live.values())
                counts = tuple((key, tuple(value)) for key, value in owner_service_counts.items())
                events_snapshot = tuple(owner_service_events)
                maintenance = tuple(owner_maintenance[key] for key in
                                    ("started", "completed", "active", "thread_is_gateway", "last_error"))
            statuses = []
            for index, owner in enumerate(owners, 1):
                try:
                    status = owner.status()
                    statuses.append(("owner_" + str(index), status.thread_alive, status.running,
                                     status.accepting, status.failure_category, status.queue_depth,
                                     status.service_queue_depth))
                except BaseException as error:
                    statuses.append(("owner_" + str(index), "status_error", owner_error_code(error)))

            def task_row(meta):
                handle = meta["handle"]
                try:
                    done = bool(handle is not None and handle.done)
                except BaseException:
                    done = False
                state = ("done" if done else "returned" if meta["returned"] else
                         "running" if meta["started"] else "queued")
                return (meta["kind"], meta["symbol"], "pre_fault" if meta["pre_fault"] else "post_fault",
                        state, meta["same_thread"], meta["error"])

            live_rows = tuple(task_row(meta) for meta in live)
            latest_rows = tuple(task_row(meta) for meta in latest)
            return (point, tuple(statuses), maintenance, tuple(sorted(counts, key=repr)),
                    live_rows, latest_rows, events_snapshot)

        recovery_phase_enabled = [False]
        recovery_fault_started = [False]
        recovery_phase_lock = threading.Lock()
        recovery_phase_counts = {}
        recovery_phase_hub_totals = {}
        recovery_phase_first_events = []
        recovery_phase_post_fault_first = {}
        recovery_phase_first_failures = {}
        recovery_phase_events = []
        gateway_handle_labels = {}
        gateway_handle_sequences = [0]
        observed_market_hub = [None]
        observed_market_proxy = [None]
        pending_call_ordinals = {}
        pending_call_history = []
        pending_call_sequence = [0]
        owner_proxy_pre_fault = [None]

        def market_hub_stream_snapshot(point):
            hub = observed_market_hub[0]
            if hub is None:
                return (point, "unavailable")
            rows = []
            for stream in hub._streams.values():
                symbol = stream.key.instrument.native_symbol
                has_registration = bool(stream.registrations or stream.pending_refs)
                eligible = (
                    stream.handle is not None
                    and has_registration
                    and not stream.starting
                    and not stream.releasing
                    and not stream.pending_release
                    and stream.generation is not None
                    and not stream.source_reset_failed
                )
                rows.append((symbol, len(stream.registrations), len(stream.pending_refs),
                             len(stream.ref_owners), stream.handle is not None,
                             stream.generation is not None, stream.starting, stream.releasing,
                             stream.pending_release, stream.source_reset_failed,
                             stream.pending_source_reset_epoch is not None, eligible))
            return (point, tuple(hub.readiness.items()), tuple(sorted(rows)))

        def owner_proxy_pending_snapshot(point):
            hub = observed_market_hub[0]
            proxy = observed_market_proxy[0]
            if hub is None or proxy is None:
                return (point, "unavailable")
            rows = []
            for stream in hub._streams.values():
                symbol = stream.key.instrument.native_symbol
                try:
                    state = proxy._state(stream.handle)
                except BaseException as error:
                    rows.append((symbol, "state_unavailable", recovery_error_code(error)))
                    continue
                pending = state.pending
                if pending is None:
                    rows.append((symbol, state.pending_kind, None, None, None))
                    continue
                key = id(pending)
                ordinal = pending_call_ordinals.get(key)
                if ordinal is None or ordinal[0] is not pending:
                    pending_call_sequence[0] += 1
                    ordinal = (pending, pending_call_sequence[0])
                    pending_call_ordinals[key] = ordinal
                    pending_call_history.append((symbol, state.pending_kind, ordinal[1], pending))
                try:
                    with pending._owner._condition:
                        pending_done = pending.done
                        task_state = pending._task.state
                    if task_state not in {"queued", "running", "done"}:
                        task_state = "other"
                except BaseException as error:
                    rows.append((symbol, state.pending_kind, ordinal[1],
                                 "observation_error", recovery_error_code(error)))
                    continue
                rows.append((symbol, state.pending_kind, ordinal[1], pending_done, task_state))
            return (point, tuple(sorted(rows)))

        def owner_proxy_future_history_snapshot(point):
            rows = []
            for symbol, kind, ordinal, pending in pending_call_history:
                try:
                    with pending._owner._condition:
                        pending_done = pending.done
                        task_state = pending._task.state
                    if task_state not in {"queued", "running", "done"}:
                        task_state = "other"
                except BaseException as error:
                    rows.append((symbol, kind, ordinal, "observation_error",
                                 recovery_error_code(error)))
                    continue
                rows.append((symbol, kind, ordinal, pending_done, task_state))
            return (point, tuple(sorted(rows)))

        def gateway_handle_label(handle):
            key = id(handle)
            with recovery_phase_lock:
                label = gateway_handle_labels.get(key)
                if label is None:
                    gateway_handle_sequences[0] += 1
                    label = "gateway_handle_" + str(gateway_handle_sequences[0])
                    gateway_handle_labels[key] = label
                return label

        def recovery_error_code(error):
            try:
                code = error.code
            except Exception:
                code = None
            return code if type(code) is str else type(error).__name__

        def public_recovery_state(provider, handle):
            try:
                return provider.source_recovery_state(handle)
            except Exception as error:
                return ("unavailable", recovery_error_code(error))

        def record_recovery_phase(name, handle=None, outcome=None, before=None, after=None, subject=None):
            if not recovery_phase_enabled[0]:
                return
            symbol = None
            if handle is not None:
                try:
                    symbol = handle.key.instrument.native_symbol
                except Exception:
                    pass
            if symbol is None:
                symbol = subject
            row = (name, symbol, threading.get_ident() == owner_thread_id,
                   outcome, before, after)
            with recovery_phase_lock:
                count = recovery_phase_counts.get(name, 0) + 1
                recovery_phase_counts[name] = count
                if count <= 3:
                    recovery_phase_first_events.append(row)
                if recovery_fault_started[0]:
                    recovery_phase_post_fault_first.setdefault((name, symbol), row)
                    failed = (type(outcome) is tuple and bool(outcome) and
                              (outcome[0] == "error" or
                               ("acknowledge_source_epoch_reset" in name and
                                outcome == ("returned", False))))
                    if failed:
                        recovery_phase_first_failures.setdefault((name, symbol), row)
                recovery_phase_events.append(row)
                if len(recovery_phase_events) > 96:
                    del recovery_phase_events[:len(recovery_phase_events) - 96]

        def recovery_phase_snapshot():
            with recovery_phase_lock:
                return (tuple(sorted(recovery_phase_counts.items())),
                        tuple(sorted(recovery_phase_hub_totals.items())),
                        tuple(recovery_phase_first_events),
                        tuple(recovery_phase_post_fault_first.values()),
                        tuple(recovery_phase_events),
                        tuple(recovery_phase_first_failures.values()))

        original_gateway_begin_subscribe = GatewayOwnerMarketProvider.begin_subscribe
        original_gateway_reset_required = GatewayOwnerMarketProvider.source_epoch_reset_required
        original_gateway_poll = GatewayOwnerMarketProvider.poll
        original_gateway_ack = GatewayOwnerMarketProvider.acknowledge_source_epoch_reset
        original_hub_maintain = GatewayMarketHub.maintain
        original_provider_poll = CtpQuoteV2MarketProvider.poll

        def observe_gateway_begin_subscribe(self, key, *, generation):
            observed_market_proxy[0] = self
            handle = original_gateway_begin_subscribe(self, key, generation=generation)
            with recovery_phase_lock:
                gateway_handle_labels[id(handle)] = key.instrument.native_symbol
            return handle

        GatewayOwnerMarketProvider.begin_subscribe = observe_gateway_begin_subscribe

        def observe_hub_maintain(self, active_registration_ids):
            observed_market_hub[0] = self
            if not recovery_phase_enabled[0]:
                return original_hub_maintain(self, active_registration_ids)
            before = dict(self.source_diagnostics)
            try:
                result = original_hub_maintain(self, active_registration_ids)
            except Exception as error:
                record_recovery_phase(
                    "hub.maintain", outcome=("error", recovery_error_code(error)))
                raise
            after = dict(self.source_diagnostics)
            keys = ("provider_epoch_resets", "provider_epoch_reset_failures",
                    "provider_poll_calls", "provider_poll_failures",
                    "provider_events_polled", "provider_events_published")
            delta = tuple((key, after.get(key, 0) - before.get(key, 0)) for key in keys)
            with recovery_phase_lock:
                for key, value in delta:
                    recovery_phase_hub_totals[key] = recovery_phase_hub_totals.get(key, 0) + value
            record_recovery_phase("hub.maintain", outcome=("returned", delta))
            return result

        GatewayMarketHub.maintain = observe_hub_maintain

        if blocked_poll is None:
            def observe_gateway_reset_required(self, handle):
                if not recovery_phase_enabled[0]:
                    return original_gateway_reset_required(self, handle)
                subject = gateway_handle_label(handle)
                try:
                    result = with_owner_service_tag(
                        "reset", subject,
                        lambda: original_gateway_reset_required(self, handle),
                    )
                except Exception as error:
                    record_recovery_phase(
                        "gateway.source_epoch_reset_required",
                        outcome=("error", recovery_error_code(error)), subject=subject)
                    raise
                record_recovery_phase("gateway.source_epoch_reset_required",
                                      outcome=("returned", result), subject=subject)
                return result

            GatewayOwnerMarketProvider.source_epoch_reset_required = observe_gateway_reset_required

        def observe_gateway_poll(self, handle, *, limit):
            if not recovery_phase_enabled[0]:
                return original_gateway_poll(self, handle, limit=limit)
            subject = gateway_handle_label(handle)
            try:
                result = with_owner_service_tag(
                    "poll", subject, lambda: original_gateway_poll(self, handle, limit=limit)
                )
            except Exception as error:
                record_recovery_phase("gateway.poll", outcome=("error", recovery_error_code(error)),
                                      subject=subject)
                raise
            result_shape = len(result) if type(result) is tuple else type(result).__name__
            record_recovery_phase("gateway.poll", outcome=("returned", result_shape), subject=subject)
            return result

        def observe_gateway_ack(self, handle, *, epoch):
            if not recovery_phase_enabled[0]:
                return original_gateway_ack(self, handle, epoch=epoch)
            subject = gateway_handle_label(handle)
            try:
                result = with_owner_service_tag(
                    "ack", subject,
                    lambda: original_gateway_ack(self, handle, epoch=epoch),
                )
            except Exception as error:
                record_recovery_phase("gateway.acknowledge_source_epoch_reset",
                                      outcome=("error", recovery_error_code(error)), subject=subject)
                raise
            record_recovery_phase("gateway.acknowledge_source_epoch_reset",
                                  outcome=("returned", result), subject=subject)
            return result

        GatewayOwnerMarketProvider.poll = observe_gateway_poll
        GatewayOwnerMarketProvider.acknowledge_source_epoch_reset = observe_gateway_ack

        if blocked_poll is not None:
            armed, poll_entered, poll_release, dequeued, maintenance_entered = blocked_poll
            def signal_maintenance(self, handle):
                if poll_entered.is_set() and not poll_release.is_set():
                    maintenance_entered.set()
                subject = gateway_handle_label(handle)
                return with_owner_service_tag(
                    "reset", subject,
                    lambda: original_gateway_reset_required(self, handle),
                )
            GatewayOwnerMarketProvider.source_epoch_reset_required = signal_maintenance
            def hold_dequeued(self, handle, *, limit):
                result = original_provider_poll(self, handle, limit=limit)
                if result and armed.is_set():
                    armed.clear()
                    with dequeued.get_lock():
                        dequeued.value += len(result)
                    poll_entered.set()
                    if not poll_release.wait(8):
                        raise TimeoutError("synthetic MD poll was not released")
                return result
            CtpQuoteV2MarketProvider.poll = hold_dequeued
        provider_poll_before_trace = CtpQuoteV2MarketProvider.poll
        source_epoch_reset_checks = {"IF2612": (0, None, None, None), "IH2612": (0, None, None, None)}
        source_epoch_reset_seen = {"IF2612": False, "IH2612": False}
        source_epoch_ack_results = {"IF2612": (0, None, None, None), "IH2612": (0, None, None, None)}
        original_source_epoch_reset_required = CtpQuoteV2MarketProvider.source_epoch_reset_required
        original_source_epoch_ack = CtpQuoteV2MarketProvider.acknowledge_source_epoch_reset
        original_retained_events_ready = CtpQuoteV2MarketProvider.retained_events_ready
        original_market_provider_maintain = CtpQuoteV2MarketProvider.maintain
        market_provider_maintain_calls = [0]

        def synthetic_symbol(handle):
            return handle.key.instrument.native_symbol

        def observe_source_epoch_reset(provider, handle):
            before = public_recovery_state(provider, handle) if recovery_phase_enabled[0] else None
            try:
                result = original_source_epoch_reset_required(provider, handle)
            except Exception as error:
                record_recovery_phase(
                    "ctp.source_epoch_reset_required", handle,
                    ("error", recovery_error_code(error)), before,
                    public_recovery_state(provider, handle))
                raise
            after = public_recovery_state(provider, handle) if recovery_phase_enabled[0] else None
            record_recovery_phase("ctp.source_epoch_reset_required", handle,
                                  ("returned", result), before, after)
            symbol = synthetic_symbol(handle)
            count = source_epoch_reset_checks.get(symbol, (0, None, None, None))[0] + 1
            if result is True:
                source_epoch_reset_seen[symbol] = True
            source_epoch_reset_checks[symbol] = (
                count, result, provider.diagnostics["active_handles"], provider.source_recovery_state(handle))
            return result

        def observe_source_epoch_ack(provider, handle, *, epoch):
            before = public_recovery_state(provider, handle) if recovery_phase_enabled[0] else None
            symbol = synthetic_symbol(handle)
            active_handles = provider.diagnostics["active_handles"]
            recovery_state = provider.source_recovery_state(handle)
            try:
                result = original_source_epoch_ack(provider, handle, epoch=epoch)
            except Exception as error:
                record_recovery_phase(
                    "ctp.acknowledge_source_epoch_reset", handle,
                    ("error", recovery_error_code(error)), before,
                    public_recovery_state(provider, handle))
                raise
            record_recovery_phase(
                "ctp.acknowledge_source_epoch_reset", handle, ("returned", result),
                before, public_recovery_state(provider, handle) if recovery_phase_enabled[0] else None)
            count = source_epoch_ack_results.get(symbol, (0, None, None, None))[0] + 1
            source_epoch_ack_results[symbol] = (count, result, active_handles, recovery_state)
            return result

        def observe_retained_events_ready(provider, handle):
            if not recovery_phase_enabled[0]:
                return original_retained_events_ready(provider, handle)
            before = public_recovery_state(provider, handle)
            try:
                result = original_retained_events_ready(provider, handle)
            except Exception as error:
                record_recovery_phase(
                    "ctp.retained_events_ready", handle,
                    ("error", recovery_error_code(error)), before,
                    public_recovery_state(provider, handle))
                raise
            record_recovery_phase("ctp.retained_events_ready", handle,
                                  ("returned", result), before,
                                  public_recovery_state(provider, handle))
            return result

        def observe_provider_poll(provider, handle, *, limit):
            if not recovery_phase_enabled[0]:
                return provider_poll_before_trace(provider, handle, limit=limit)
            before = public_recovery_state(provider, handle)
            try:
                result = provider_poll_before_trace(provider, handle, limit=limit)
            except Exception as error:
                record_recovery_phase(
                    "ctp.poll", handle, ("error", recovery_error_code(error)), before,
                    public_recovery_state(provider, handle))
                raise
            event_count = len(result) if type(result) is tuple else None
            record_recovery_phase("ctp.poll", handle, ("returned", event_count), before,
                                  public_recovery_state(provider, handle))
            return result

        def observe_market_provider_maintain(provider):
            market_provider_maintain_calls[0] += 1
            if recovery_phase_enabled[0]:
                record_recovery_phase("ctp.maintain", outcome=("called", None))
            return original_market_provider_maintain(provider)

        CtpQuoteV2MarketProvider.source_epoch_reset_required = observe_source_epoch_reset
        CtpQuoteV2MarketProvider.acknowledge_source_epoch_reset = observe_source_epoch_ack
        CtpQuoteV2MarketProvider.retained_events_ready = observe_retained_events_ready
        CtpQuoteV2MarketProvider.poll = observe_provider_poll
        CtpQuoteV2MarketProvider.maintain = observe_market_provider_maintain
        from bt_api_py.ctp_gateway_trading import (CtpGatewayTradingSettings,
            CtpGatewayAccountReadSettings, CtpGatewayMarketSettings, CtpGatewayReturnSettings,
            build_ctp_gateway_trading_profile)
        from bt_api_py.gateway_direct_service import GatewayDirectGrant
        from bt_api_py.service_host import TrustedServiceAuthority
        from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

        natives, feeds, streams, stops, settled, return_owners = [], [], [], [], [], []
        from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance
        original_return_init = CtpGatewayReturnMaintenance.__init__
        original_return_poll = CtpGatewayReturnMaintenance.poll

        def gated_return_poll(self):
            if blocked_return_poll is not None:
                (armed, entered, callback_injected, owner_verified,
                 td_fault_done, poll_completed) = blocked_return_poll
                if armed.is_set():
                    armed.clear()
                    entered.set()
                    if not callback_injected.wait(8):
                        raise TimeoutError("synthetic TD callback was not injected")

                    def snapshot(point):
                        is_owner = threading.current_thread() is self._owner_thread
                        buffer_status = self._session.return_buffer.status()
                        state = self.status()
                        return ("td_return_poll_snapshot", point, is_owner,
                                buffer_status.record_count, self._provider.session_generation(),
                                self._generation, state["status"], state["reservation_open"])

                    before_disconnect = snapshot("callback_queued_before_td_disconnect")
                    events.put(before_disconnect)
                    owner_verified.set()
                    if not td_fault_done.wait(8):
                        raise TimeoutError("synthetic TD relogin was not completed")
                    before_poll = snapshot("after_td_relogin_before_original_poll")
                    result = None
                    try:
                        result = original_return_poll(self)
                    finally:
                        after_poll = snapshot("after_original_poll")
                        events.put(("td_return_poll_trace", before_disconnect,
                                    before_poll, after_poll))
                        poll_completed.set()
                    return result
            return original_return_poll(self)

        CtpGatewayReturnMaintenance.poll = gated_return_poll

        def trace_return_init(self, *args, **kwargs):
            original_return_init(self, *args, **kwargs)
            return_owners.append(self)
        CtpGatewayReturnMaintenance.__init__ = trace_return_init
        from bt_api_py.gateway_settlement import GatewaySettlementController
        original_tick = GatewaySettlementController.tick
        def traced_tick(self):
            result = original_tick(self)
            if result.observed_ready:
                settled[:] = [True]
            return result
        GatewaySettlementController.tick = traced_tick

        class Native(_NativeApi):
            def ReqOrderInsert(self, field, request_id):
                from types import SimpleNamespace
                result = super().ReqOrderInsert(field, request_id)
                suffix = str(field.OrderRef)
                common = dict(BrokerID="9999", InvestorID="account", TradingDay="20261106",
                    ExchangeID="CFFEX", InstrumentID=field.InstrumentID, OrderRef=field.OrderRef,
                    OrderSysID="sys-" + suffix, Direction="0")
                order = SimpleNamespace(**common, FrontID=7, SessionID=19, CombOffsetFlag="0",
                    OrderStatus="1", OrderSubmitStatus="3", OrderPriceType="2", LimitPrice=10.0,
                    VolumeTotalOriginal=2, VolumeTraded=1, VolumeTotal=1)
                trade = SimpleNamespace(**common, TradeID="trade-" + suffix, OffsetFlag="0",
                    Volume=1, Price=10.0, TradeTime="09:30:00")
                self.client._spi.OnRtnOrder(order)
                self.client._spi.OnRtnTrade(trade)
                return result
            def _reply(self, kind, rows, request_id):
                for row in rows:
                    self.client._handle_query_callback(kind, row, None, request_id, False)
                self.client._handle_query_callback(kind, None, None, request_id, True)
                return 0

            def ReqQryTradingAccount(self, field, request_id):
                return self._reply("account", [dict(AccountID="account", BrokerID="9999",
                    CurrencyID="CNY", Balance=1000, Available=800)], request_id)

            def ReqQryInvestorPosition(self, field, request_id):
                return self._reply("positions", [], request_id)

            def ReqQryOrder(self, field, request_id):
                return self._reply("orders", [], request_id)

            def ReqQryTrade(self, field, request_id):
                self.calls.append("return_query")
                return self._reply("trades", [], request_id)

        def start(trader, **_kwargs):
            native = Native()
            native.client = trader
            trader._api = native
            _login(trader, native, session_id=19)
            natives.append(native)

        def stop(trader, *, timeout):
            stops.append(1)
            return CtpNativeStopReceipt(trader.connection_generation, False, True, True, False, False)

        class FakeMd(_SyntheticMdClient):
            instances = []
            _gateway_md_dispatch_coordinator = None

            def _bind_gateway_md_dispatch_coordinator(self, coordinator, *, account_scope,
                                                      service_generation):
                # Same contract as the real MdClient: bind once, exact owner only.
                if (not coordinator.matches_owner(account_scope, service_generation)
                        or self._gateway_md_dispatch_coordinator is not None):
                    raise ValueError("ctp_gateway_dispatch_owner_mismatch")
                self._gateway_md_dispatch_coordinator = coordinator
                # Keep observers that patch the synthetic parent hook (MD claim
                # binding test) in the chain instead of shadowing them.
                parent = getattr(super(), "_bind_gateway_md_dispatch_coordinator", None)
                if callable(parent):
                    parent(coordinator, account_scope=account_scope,
                           service_generation=service_generation)

            def __init__(self, *args, **kwargs):
                self.trusted_flow_binding = flow_binding(trusted_flow(kwargs["trusted_flow_root"], kwargs["flow_account_scope"]))
                self.last_stop_receipt = None
                super().__init__(*args)

            def start(self, *, block=False, join_observer=True):
                if blocked_start is not None:
                    entered, release = blocked_start
                    entered.set()
                    if not release.wait(8):
                        raise TimeoutError("synthetic MD start was not released")
                return super().start(block=block, join_observer=join_observer)

            def stop_and_wait(self, *, timeout):
                if blocked_stop is not None and blocked_stop[0].is_set():
                    blocked_stop[0].clear()
                    blocked_stop[1].set()
                    if not blocked_stop[2].wait(8):
                        raise TimeoutError("synthetic MD close was not released")
                self.last_stop_receipt = super().stop_and_wait(timeout=timeout)
                return self.last_stop_receipt

            def emit_disconnect(self, reason=0):
                record_recovery_phase("fake_md.disconnect_callback", outcome=("called", None))
                return super().emit_disconnect(reason)

        TraderClient.start, TraderClient.stop_and_wait = start, stop
        live_ctp_feed.ctp_client.MdClient = FakeMd
        params = dict(broker_id="9999", user_id="account", password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001", md_front="tcp://offline-neutral-md.invalid:30011",
            ctp_env_profile="config_front_pair", ctp_environment="simnow",
            trusted_flow_root=str(root / "flow"), flow_account_scope=SCOPE)

        def feed():
            result = CtpRequestDataFuture(None, auto_settlement_confirm=False, **params)
            result._connect_timeout = 0.05
            feeds.append(result)
            return result

        def stream(key, q):
            del key
            result = CtpMarketStream(q, **params)
            streams.append(result)
            return result

        raw = _service_config(rpc).to_public_dict()
        gateway = raw["components"]["gateway"]["settings"]
        gateway["bind_ports"] += ["service.account_read", "service.market_source"]
        gateway["options"].update(dispatch_enabled=True, cancel_enabled=True)
        raw["communication"]["profiles"]["server-transport"]["endpoints"]["market_bind"] = market
        raw["communication"]["profiles"]["server-transport"].setdefault("options", {})["market_wire_version"] = 2
        identities = (CLIENT_A, CLIENT_B, CLIENT_C)
        grants = {}
        for identity in identities:
            wire = WireScope(DOMAIN, account_ref=SCOPE, strategy_ref=identity.actor_ref)
            selected = [("service.direct_write", DIRECT_OPS if identity != CLIENT_C else OPS)]
            if identity != CLIENT_C:
                selected += [("service.account_read", ACCOUNT_OPS), ("service.market_source", MARKET_OPS)]
            resources = tuple(ResourceGrant("gateway-a", port, "scope-a", wire, ops) for port, ops in selected)
            grants[identity.principal_ref] = (PrincipalGrant(identity, resources),)
        authority = TrustedServiceAuthority(
            identity=SERVICE, scope=WireScope(DOMAIN), scope_ref="scope-a", scope_version="scope-v1",
            security_ref="security-a", security_version="security-v1",
            registration_policy_ref="direct-policy-v1", registration_policy_version="direct-policy-v1",
            service_requirements_ref="direct-requirements-v1", service_requirements_version="direct-requirements-v1",
            curve_public_key=server_keys[0], curve_secret_key=server_keys[1],
            principals={pair[0]: identity.principal_ref for pair, identity in zip(client_keys, identities)}, grants=grants)
        settings = CtpGatewayTradingSettings(ACCOUNT, SCOPE, root / "guard", "joint-owner",
            root / "orders.sqlite", journal_id, 0, root / "flow", SOURCE, ENTITLEMENT,
            write_enabled=True, maintenance_interval_seconds=0.05,
            # A test that holds the native MD start must outlast the provider's
            # authentication wait, or the provider abandons and stops that start.
            connect_timeout_seconds=2 if blocked_start is None else 10, close_timeout_seconds=5)
        keys = (_key("IF2612"), _key("IH2612"))
        read = CtpGatewayAccountReadSettings(root / "account.sqlite", "account-state", (RESOURCE,),
            root / "identity.sqlite", issuer_id, tuple(key.instrument for key in keys),
            refresh_interval_seconds=0.2, max_staleness_seconds=3, request_timeout_seconds=2)
        profile = build_ctp_gateway_trading_profile(parse_service_config(raw), authority, root / "registry.sqlite",
            profile_ref="offline-joint", settings=settings,
            grants={identity.principal_ref: GatewayDirectGrant(identity.actor_ref, frozenset({"IF2612", "IH2612"})) for identity in identities},
            request_feed_factory=feed, market_stream_factory=stream, assert_admitted=lambda _: True,
            return_settings=CtpGatewayReturnSettings(tuple(key.instrument for key in keys), query_interval_seconds=1),
            account_read_settings=read, account_read_principals=frozenset(i.principal_ref for i in (CLIENT_A, CLIENT_B)),
            market_settings=CtpGatewayMarketSettings(frozenset(keys), {
                CLIENT_A.principal_ref: frozenset(keys), CLIENT_B.principal_ref: frozenset({keys[1]})}))
        assert not feeds and not natives and not streams
        deployment = profile.deployment_factory()
        deployment.start()
        events.put(("ready", os.getpid()))
        while True:
            try:
                action = controls.get_nowait()
            except queue.Empty:
                action = None
            if action == "stop":
                break
            if action == "stats":
                md = FakeMd.instances[-1] if FakeMd.instances else None
                events.put(("stats", len(natives), len(feeds), len(streams), len(FakeMd.instances),
                    () if md is None else tuple(md.subscribed), 0 if md is None else md.stop_wait_calls,
                    0 if not natives else natives[0].calls.count("insert"),
                    0 if not natives else natives[0].calls.count("cancel"),
                    bool(settled), 0 if not natives else natives[0].calls.count("return_query"),
                    {} if not return_owners else return_owners[0].status(),
                    bool(md is not None and md.last_stop_receipt is not None
                        and md.last_stop_receipt.complete and md.last_stop_receipt.native_released)))
            if action == "td_stale_return_inject":
                from types import SimpleNamespace
                from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

                trader = natives[0].client
                spi, api = trader._spi, trader._api
                stale_order = _order(TradingDay="20261106", ExchangeID="CFFEX",
                    InstrumentID="IF2612", OrderRef="000000000101", OrderSysID="stale-synthetic")
                spi.OnRtnOrder(SimpleNamespace(**stale_order))
                events.put(("td_stale_return_injected", trader._spi is spi, trader._api is api))
                blocked_return_poll[2].set()
            if action == "td_disconnect_relogin":
                native = natives[0]
                trader = native.client
                spi, api = trader._spi, trader._api
                before = trader.connection_generation
                spi.OnFrontDisconnected(0x1001)
                disconnected_ready = trader.get_session_state()["read_only_ready"]
                _relogin_same_spi(native, "20261106", 20)
                events.put(("td_reconnected", before, trader.connection_generation,
                    trader._spi is spi, trader._api is api, disconnected_ready,
                    trader.get_session_state()["read_only_ready"], time.monotonic_ns()))
                blocked_return_poll[4].set()
            if action == "md_disconnect":
                old_md = FakeMd.instances[-1]
                recovery_phase_enabled[0] = True
                recovery_fault_started[0] = True
                callback_owner_thread = []
                callback_errors = []

                def emit_from_native_callback_thread():
                    callback_owner_thread.append(threading.get_ident() == owner_thread_id)
                    try:
                        old_md.emit_disconnect(0x1001)
                    except Exception as error:
                        callback_errors.append(recovery_error_code(error))

                callback_thread = threading.Thread(
                    target=emit_from_native_callback_thread,
                    name="synthetic-ctp-md-callback",
                    daemon=True,
                )
                callback_thread.start()
                callback_thread.join(timeout=5)
                if callback_thread.is_alive():
                    raise TimeoutError("synthetic MD callback did not join")
                if callback_errors:
                    raise RuntimeError("synthetic MD callback failed: " + callback_errors[0])
                callback_same_owner = callback_owner_thread[0]
                record_recovery_phase("md.callback_join", outcome=("complete", callback_same_owner))
                stream = streams[0]
                events.put(("md_disconnected", len(FakeMd.instances), old_md.connection_generation,
                    stream.quote_v2_service_recovery_required, stream.quote_v2_service_fail_closed,
                    stream.get_quote_clock_source_scope() is None, market_provider_maintain_calls[0],
                    time.monotonic_ns(), callback_same_owner))
            if action == "arm_recovery_trace":
                recovery_phase_enabled[0] = True
                owner_phase_capture[0] = True
                for _ in range(3):
                    deployment.serve_once(10)
                owner_proxy_pre_fault[0] = owner_proxy_pending_snapshot("pre_fault")
                events.put(("recovery_trace_armed", recovery_phase_snapshot(),
                            owner_phase_snapshot("after_three_arm_sweeps"),
                            market_hub_stream_snapshot("pre_fault"),
                            owner_proxy_pre_fault[0]))
            if isinstance(action, tuple) and action[0] == "drive_md_recovery":
                previous_count, previous_generation = action[1:]
                deadline = time.monotonic() + 10
                recovered = None
                during_owner_proxy = None
                while time.monotonic() < deadline:
                    deployment.serve_once(10)
                    if during_owner_proxy is None:
                        during_owner_proxy = owner_proxy_pending_snapshot("during_recovery")
                    scope = streams[0].get_quote_clock_source_scope() if streams else None
                    if (len(FakeMd.instances) > previous_count and scope is not None
                            and scope.connection_generation > previous_generation
                            and all(source_epoch_reset_seen[symbol]
                                    for symbol in ("IF2612", "IH2612"))
                            and all(source_epoch_ack_results[symbol][1] is True
                                    for symbol in ("IF2612", "IH2612"))):
                        current_md = FakeMd.instances[-1]
                        recovered = ("md_reconnected", len(FakeMd.instances),
                            tuple(sorted(current_md.subscribed)), scope.connection_generation,
                            tuple((symbol, source_epoch_reset_checks[symbol], source_epoch_reset_seen[symbol])
                                  for symbol in ("IF2612", "IH2612")),
                            tuple((symbol, source_epoch_ack_results[symbol]) for symbol in ("IF2612", "IH2612")),
                            market_provider_maintain_calls[0],
                            time.monotonic_ns(), recovery_phase_snapshot(),
                            owner_phase_snapshot("after_md_recovery"),
                            (owner_proxy_pre_fault[0], during_owner_proxy,
                             owner_proxy_pending_snapshot("recovered")))
                        break
                if recovered is None:
                    scope = streams[0].get_quote_clock_source_scope() if streams else None
                    recovered = ("md_recovery_timeout", len(FakeMd.instances),
                        None if scope is None else scope.connection_generation,
                        tuple((symbol, source_epoch_reset_checks[symbol], source_epoch_reset_seen[symbol])
                              for symbol in ("IF2612", "IH2612")),
                        tuple((symbol, source_epoch_ack_results[symbol]) for symbol in ("IF2612", "IH2612")),
                        market_provider_maintain_calls[0],
                        time.monotonic_ns(), recovery_phase_snapshot(),
                        owner_phase_snapshot("after_md_recovery_timeout"),
                        market_hub_stream_snapshot("deadline"),
                        (owner_proxy_pre_fault[0], during_owner_proxy,
                         owner_proxy_pending_snapshot("deadline")))
                events.put(recovered)
            if action == "recovery_trace":
                events.put(("recovery_trace", recovery_phase_snapshot(),
                            market_hub_stream_snapshot("recovery_trace"),
                            owner_proxy_pending_snapshot("recovery_trace"),
                            owner_phase_snapshot("recovery_trace"),
                            owner_proxy_future_history_snapshot("recovery_trace")))
            if isinstance(action, tuple) and action[0] == "tick":
                symbol = action[1]
                price = 4005.0 if len(action) == 2 else action[2]
                FakeMd.instances[-1].emit_tick(InstrumentID=symbol, ExchangeID="CFFEX", LastPrice=price,
                    Volume=2, TradingDay="20261020", ActionDay="20261020", UpdateTime="09:30:02", UpdateMillisec=0)
                events.put(("tick", symbol) if len(action) == 2 else ("tick", symbol, price))
            deployment.serve_once(10)
    except BaseException:
        events.put(("error", traceback.format_exc()))
    finally:
        try:
            if deployment is not None and deployment.state in {"started", "close_failed"}:
                deployment.close()
            events.put(("closed", len(locals().get("stops", []))))
        except BaseException:
            events.put(("cleanup_error", traceback.format_exc()))
        finally:
            GatewayMarketHub.maintain = original_hub_maintain
            CtpGatewayReturnMaintenance.poll = original_return_poll
            GatewayOwnerMarketProvider.begin_subscribe = original_gateway_begin_subscribe
            GatewayOwnerMarketProvider.source_epoch_reset_required = original_gateway_reset_required
            GatewayOwnerMarketProvider.poll = original_gateway_poll
            GatewayOwnerMarketProvider.acknowledge_source_epoch_reset = original_gateway_ack
            CtpQuoteV2MarketProvider.source_epoch_reset_required = original_source_epoch_reset_required
            CtpQuoteV2MarketProvider.acknowledge_source_epoch_reset = original_source_epoch_ack
            CtpQuoteV2MarketProvider.retained_events_ready = original_retained_events_ready
            CtpQuoteV2MarketProvider.poll = original_provider_poll
            CtpQuoteV2MarketProvider.maintain = original_market_provider_maintain


def _session(identity, keys, server_public, rpc, market, journal_id, symbol, root, run_suffix="", heartbeat_interval_seconds=None):
    raw = _client_config(rpc, identity).to_public_dict()
    if heartbeat_interval_seconds is not None:
        raw["clients"]["gateway"]["settings"]["options"]["heartbeat_interval_seconds"] = heartbeat_interval_seconds
    bindings = raw["clients"]["gateway"]["settings"]["bindings"]
    bindings[0]["account_ref"] = SCOPE
    account = dict(bindings[0], mount="store.account")
    market_binding = dict(bindings[0], mount="store.market")
    del market_binding["account_ref"]
    bindings += [account, market_binding]
    raw["communication"]["profiles"]["client-transport"]["endpoints"]["market_connect"] = market
    raw["communication"]["profiles"]["client-transport"].setdefault("options", {})["market_wire_version"] = 2
    scope = WireScope(DOMAIN, account_ref=SCOPE, strategy_ref=identity.actor_ref, run_id="run-" + identity.actor_ref + run_suffix)
    subscriptions = {"ticks": GatewayMarketSubscriptionSettings(_key(symbol), require_source_descriptor=True)}
    if identity == CLIENT_A:
        subscriptions["second_ticks"] = GatewayMarketSubscriptionSettings(
            _key("IH2612"), require_source_descriptor=True)
        subscriptions["forbidden"] = GatewayMarketSubscriptionSettings(_key("ZZ2612"))
    return GatewayStrategyClientSession(parse_client_config(raw),
        binding=GatewayCommandBinding(journal_id, SCOPE, identity.actor_ref, identity.principal_ref),
        route=GatewayClientRoute("broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", scope, identity),
        peer=SERVICE, security_profiles={"security-a": ClientCurveKeys(server_public, *keys)},
        host=MemoryHost(), cancel_host=CancelHost(root / (identity.actor_ref + "-cancel.sqlite")), requested_operations=DIRECT_OPS,
        account_settings=GatewayAccountClientSettings(ACCOUNT, (RESOURCE,), ACCOUNT_OPS),
        market_settings=GatewayMarketClientSettings(subscriptions, MARKET_OPS))


def _until(fn, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        try:
            result = fn()
            if result:
                return result
        except Exception:
            if time.monotonic() >= deadline:
                raise
        assert time.monotonic() < deadline
        time.sleep(0.05)


def _secure_tmp_path(path):
    """Keep synthetic Gateway storage private on Windows, where chmod is inert."""
    if win32_security.WINDOWS:
        win32_security._set_owner_only_acl(path)
    return path


def test_spawned_real_ctp_session_shares_one_td_and_lazy_md(tmp_path):
    _secure_tmp_path(tmp_path)
    (tmp_path / "guard").mkdir()
    (tmp_path / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(tmp_path / "orders.sqlite", SCOPE, generation="provisioned")
    issuer = DurableOrderCorrelationIssuer(tmp_path / "identity.sqlite")
    issuer_id = issuer.issuer_id
    issuer.close()
    rpc, market = _free_endpoint(), _free_endpoint()
    server_keys = zmq.curve_keypair()
    client_keys = (zmq.curve_keypair(), zmq.curve_keypair(), zmq.curve_keypair())
    ctx = multiprocessing.get_context("spawn")
    events, controls = ctx.Queue(), ctx.Queue()
    child = ctx.Process(target=_gateway, args=(str(tmp_path), rpc, market, server_keys, client_keys,
        journal.journal_id, issuer_id, events, controls))
    child.start()
    sessions = []
    context = zmq.Context()
    raw_sub = context.socket(zmq.SUB)
    raw_sub.linger = 0
    from zmq.utils.monitor import recv_monitor_message
    monitor = raw_sub.get_monitor_socket(events=zmq.EVENT_HANDSHAKE_SUCCEEDED | zmq.EVENT_HANDSHAKE_FAILED_AUTH)
    raw_sub.curve_serverkey, raw_sub.curve_publickey, raw_sub.curve_secretkey = server_keys[0], *client_keys[2]
    raw_sub.setsockopt(zmq.SUBSCRIBE, b"")
    raw_sub.connect(market)
    try:
        ready = events.get(timeout=20)
        assert ready == ("ready", child.pid) and child.pid != os.getpid(), ready
        for identity, keys, symbol in zip((CLIENT_A, CLIENT_B), client_keys, ("IF2612", "IH2612")):
            session = _session(identity, keys, server_keys[0], rpc, market, journal.journal_id, symbol, tmp_path)
            assert session.status().state == "new"
            sessions.append(session)
            session.start(timeout_seconds=10)
        controls.put("stats")
        assert events.get(timeout=5)[:7] == ("stats", 1, 1, 0, 0, (), 0)
        for session in sessions:
            view = _until(lambda: session.account_client.refresh(timeout_seconds=5))
            assert view.observation.account_key == ACCOUNT
        markets = [session.market_client.subscription("ticks") for session in sessions]
        descriptors = {}
        for symbol, subscription in zip(("IF2612", "IH2612"), markets):
            bootstrap = subscription.start(timeout_seconds=10)
            descriptor = bootstrap.source_descriptor
            assert descriptor.key == _key(symbol) and descriptor.provider == "ctp"
            assert descriptor.connection_generation == 1 and descriptor.epoch == bootstrap.snapshot_epoch
            descriptors[symbol] = descriptor
        controls.put("stats")
        stats = events.get(timeout=5)
        assert stats[:5] == ("stats", 1, 1, 1, 1), stats
        assert set(stats[5]) == {"IF2612", "IH2612"}
        assert monitor.poll(5000)
        assert recv_monitor_message(monitor)["event"] == zmq.EVENT_HANDSHAKE_FAILED_AUTH
        raw = _client_config(rpc, CLIENT_C).to_public_dict()
        raw["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = SCOPE
        c_scope = WireScope(DOMAIN, account_ref=SCOPE, strategy_ref=CLIENT_C.actor_ref, run_id="run-c")
        direct_only = GatewayStrategyClientSession(parse_client_config(raw),
            binding=GatewayCommandBinding(journal.journal_id, SCOPE, CLIENT_C.actor_ref, CLIENT_C.principal_ref),
            route=GatewayClientRoute("broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", c_scope, CLIENT_C),
            peer=SERVICE, security_profiles={"security-a": ClientCurveKeys(server_keys[0], *client_keys[2])},
            host=MemoryHost(), requested_operations=OPS)
        try:
            direct_only.start(timeout_seconds=10)
            c_payload = _order(key="direct-only", symbol="IF2612", account=SCOPE)
            assert _until(lambda: (result if (result := direct_only.command_client.reserve(
                OrderRequest.from_dict(c_payload), timeout_seconds=5)).outcome == "OBSERVED" else None))
        finally:
            direct_closed = direct_only.close(timeout_seconds=10)
            assert direct_closed.state == "closed" and not direct_closed.thread_alive
        for symbol, subscription in zip(("IF2612", "IH2612"), markets):
            controls.put(("tick", symbol))
            assert events.get(timeout=5) == ("tick", symbol)
            event = _until(lambda: (event if (event := subscription.poll(timeout_seconds=5)) and
                event.payload["data"]["last_price"] == 4005.0 else None))
            assert event.header.scope == WireScope(DOMAIN)
            assert event.header.stream_epoch == descriptors[symbol].epoch
            assert event.payload["data"]["instrument"] == _key(symbol).instrument.to_dict()
            assert event.payload["data"]["provider_connection_generation"] == descriptors[symbol].connection_generation
            assert not raw_sub.poll(300), "direct-only principal received a market frame"
        def owner_ready():
            controls.put("stats")
            status = events.get(timeout=5)
            return status[9] and status[10] > 0 and status[11]["reservation_open"]
        _until(owner_ready)
        for index, (session, symbol) in enumerate(zip(sessions, ("IF2612", "IH2612"))):
            payload = _order(key="joint-order-" + str(index), symbol=symbol, quantity="2", account=SCOPE)
            payload.update(exchange_id="CFFEX", offset="open", time_in_force="GFD", quantity_unit="contracts")
            def reserve():
                result = session.command_client.reserve(OrderRequest.from_dict(payload), timeout_seconds=10)
                assert result.outcome in {"OBSERVED", "REQUEST_DENIED"}, result
                return result if result.outcome == "OBSERVED" else None
            reserved = _until(reserve)
            assert reserved.outcome == "OBSERVED"
            # Decision B: this Gateway has market settings but no clock verifier,
            # so a dispatch without a qualified market reference is refused
            # before the owner queue or a marker; the reservation remains.
            # Qualified dispatch, fills and ACK through this composition:
            # test_iter63_gateway_qualified_order_process; cancel: the cancel RPC suite.
            refused = session.command_client.dispatch("joint-order-" + str(index), timeout_seconds=10)
            assert (refused.outcome, refused.code) == ("REQUEST_DENIED", "MARKET_GATE_UNAVAILABLE")
            current = session.command_client.status("joint-order-" + str(index))
            assert current.record is not None and current.record.state == "RESERVED"
        controls.put("stats")
        assert events.get(timeout=5)[7:9] == (0, 0)  # no native send, no cancel
        from bt_api_py.service_runtime_thread import OwnerOperationError
        from bt_api_py.service_runtime import ServiceRuntimeError
        with pytest.raises(ServiceRuntimeError):
            sessions[0].runtime.call("store.market", "market.subscribe", {"key": _key("IF2612").to_dict()},
                account_ref="wrong-account", timeout_seconds=5)
        with pytest.raises(OwnerOperationError):
            sessions[0].market_client.subscription("forbidden").start(timeout_seconds=5)
        closed_a = sessions[0].close(timeout_seconds=10)
        assert closed_a.state == "closed" and not closed_a.thread_alive
        def first_release():
            controls.put("stats")
            status = events.get(timeout=5)
            assert status[:5] == ("stats", 1, 1, 1, 1), status
            # The first strategy's symbol stays natively subscribed while the
            # other strategy keeps the MD session alive (bt_api_ctp parks a
            # released symbol so that its consumer can come back); the session
            # itself is neither stopped nor restarted.
            return status if {"IF2612", "IH2612"} <= set(status[5]) and status[6] == 0 else None
        _until(first_release)
        controls.put(("tick", "IH2612"))
        assert events.get(timeout=5) == ("tick", "IH2612")
        assert _until(lambda: markets[1].poll(timeout_seconds=5)).header.scope == WireScope(DOMAIN)
        closed_b = sessions[1].close(timeout_seconds=10)
        assert closed_b.state == "closed" and not closed_b.thread_alive
        def last_release():
            controls.put("stats")
            status = events.get(timeout=5)
            assert status[:5] == ("stats", 1, 1, 1, 1), status
            return status if status[6] >= 1 and status[12] else None
        _until(last_release)
    finally:
        raw_sub.close()
        monitor.close()
        context.term()
        close_results = []
        for session in sessions:
            try:
                closed = session.close(timeout_seconds=10)
                close_results.append((closed.state, closed.thread_alive))
            except Exception as exc:
                close_results.append((type(exc).__name__, True))
        controls.put("stop")
        child.join(15)
        if child.is_alive():
            child.terminate()
            child.join(5)
            pytest.fail("synthetic Gateway process failed bounded shutdown")
        remaining = []
        while True:
            try:
                remaining.append(events.get_nowait())
            except queue.Empty:
                break
        assert child.exitcode == 0 and not any(item[0] in {"error", "cleanup_error"} for item in remaining), remaining
        assert ("closed", 1) in remaining, remaining
        assert all(state == "closed" and alive is False for state, alive in close_results), close_results


def test_spawned_ctp_joint_md_fault_settles_pre_fault_owner_futures(tmp_path):
    _secure_tmp_path(tmp_path)
    (tmp_path / "guard").mkdir()
    (tmp_path / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(tmp_path / "orders.sqlite", SCOPE, generation="provisioned")
    issuer = DurableOrderCorrelationIssuer(tmp_path / "identity.sqlite")
    issuer_id = issuer.issuer_id
    issuer.close()
    rpc, market = _free_endpoint(), _free_endpoint()
    server_keys = zmq.curve_keypair()
    client_keys = (zmq.curve_keypair(), zmq.curve_keypair(), zmq.curve_keypair())
    ctx = multiprocessing.get_context("spawn")
    poll_armed, poll_entered, poll_release = ctx.Event(), ctx.Event(), ctx.Event()
    dequeued = ctx.Value("i", 0)
    maintenance_entered = ctx.Event()
    events, controls = ctx.Queue(), ctx.Queue()
    blocked_poll = (poll_armed, poll_entered, poll_release, dequeued, maintenance_entered)
    child = ctx.Process(target=_gateway, args=(str(tmp_path), rpc, market, server_keys, client_keys,
        journal.journal_id, issuer_id, events, controls, None, blocked_poll))
    child.start()
    sessions = []
    poll_gate_released = [False]

    def release_poll_gate():
        if not poll_gate_released[0]:
            poll_release.set()
            poll_gate_released[0] = True

    try:
        ready = events.get(timeout=20)
        assert ready == ("ready", child.pid) and child.pid != os.getpid(), ready
        for identity, keys, symbol in zip((CLIENT_A, CLIENT_B), client_keys, ("IF2612", "IH2612")):
            session = _session(identity, keys, server_keys[0], rpc, market,
                journal.journal_id, symbol, tmp_path)
            sessions.append(session)
            session.start(timeout_seconds=10)
        controls.put("stats")
        assert events.get(timeout=5)[:7] == ("stats", 1, 1, 0, 0, (), 0)
        for session in sessions:
            view = _until(lambda: session.account_client.refresh(timeout_seconds=5))
            assert view.observation.account_key == ACCOUNT

        first_market = sessions[0].market_client.subscription("ticks")
        second_market = sessions[0].market_client.subscription("second_ticks")
        client_b_market = sessions[1].market_client.subscription("ticks")
        old_descriptors = {}
        for symbol, subscription in (("IF2612", first_market), ("IH2612", second_market)):
            bootstrap = subscription.start(timeout_seconds=10)
            descriptor = bootstrap.source_descriptor
            assert descriptor.key == _key(symbol) and descriptor.provider == "ctp"
            assert descriptor.connection_generation == 1
            old_descriptors[symbol] = descriptor
        controls.put("stats")
        stats = events.get(timeout=5)
        assert stats[:5] == ("stats", 1, 1, 1, 1), stats
        assert set(stats[5]) == {"IF2612", "IH2612"}, stats

        controls.put("arm_recovery_trace")
        armed = events.get(timeout=5)
        assert armed[0] == "recovery_trace_armed", armed
        poll_armed.set()
        controls.put(("tick", "IF2612", 4123.0))
        assert events.get(timeout=5) == ("tick", "IF2612", 4123.0)
        assert poll_entered.wait(5) and maintenance_entered.wait(5)
        assert dequeued.value == 1

        def read_recovery_trace():
            controls.put("recovery_trace")
            trace = events.get(timeout=5)
            assert trace[0] == "recovery_trace", trace[0]
            return trace

        expected_owner_states = {
            "IF2612": ("poll", "running"),
            "IH2612": ("reset", "queued"),
        }

        def has_both_old_futures(trace):
            rows = {row[0]: row for row in trace[3][1]}
            return (set(rows) == set(expected_owner_states)
                    and all(rows[symbol][1] == expected_owner_states[symbol][0]
                            and rows[symbol][2] is not None
                            and rows[symbol][3] is False
                            and rows[symbol][4] == expected_owner_states[symbol][1]
                            for symbol in expected_owner_states))

        pre_fault = _until(lambda: (trace if has_both_old_futures(
            trace := read_recovery_trace()) else None), timeout=3)
        pre_rows = {row[0]: row for row in pre_fault[3][1]}
        old_future_keys = {symbol: (row[1], row[2]) for symbol, row in pre_rows.items()}
        assert old_future_keys["IF2612"][1] != old_future_keys["IH2612"][1]
        pre_owner = pre_fault[4]
        pre_live = {(row[0], row[1], row[3], row[4]) for row in pre_owner[4]}
        assert ("poll", "IF2612", "running", False) in pre_live, pre_owner
        assert ("reset", "IH2612", "queued", None) in pre_live, pre_owner
        owner_status = pre_owner[1][0]
        assert owner_status[1:4] == (True, True, True) and owner_status[6] >= 1, pre_owner

        controls.put("md_disconnect")
        md_fault = events.get(timeout=5)
        assert md_fault[:3] == ("md_disconnected", 1, 1), md_fault
        assert md_fault[3:6] == (True, False, True), md_fault
        during_fault = _until(lambda: (trace if has_both_old_futures(
            trace := read_recovery_trace()) else None), timeout=3)
        during_rows = {row[0]: row for row in during_fault[3][1]}
        for symbol, (kind, ordinal) in old_future_keys.items():
            row = during_rows[symbol]
            assert (row[1], row[2], row[3], row[4]) == (
                kind, ordinal, False, expected_owner_states[symbol][1]), during_fault
        during_live = {(row[0], row[1], row[3], row[4]) for row in during_fault[4][4]}
        assert ("poll", "IF2612", "running", False) in during_live, during_fault[4]
        assert ("reset", "IH2612", "queued", None) in during_live, during_fault[4]

        release_poll_gate()
        controls.put(("drive_md_recovery", 1, old_descriptors["IF2612"].connection_generation))
        recovered = events.get(timeout=12)
        assert recovered[0] == "md_reconnected", recovered
        assert recovered[1] == 2 and set(recovered[2]) == {"IF2612", "IH2612"}, recovered
        assert recovered[3] > old_descriptors["IF2612"].connection_generation, recovered
        assert all(row[1][1] is True and row[2] is True for row in recovered[4]), recovered
        assert all(row[1][1] is True for row in recovered[5]), recovered

        settled_trace = read_recovery_trace()
        settled_futures = {row[:3]: row[3:] for row in settled_trace[5][1]}
        for symbol, (kind, ordinal) in old_future_keys.items():
            assert settled_futures[(symbol, kind, ordinal)] == (True, "done"), settled_trace[5]
        print(("held_md_owner_futures", pre_fault[3], during_fault[3], settled_trace[5]))
        print(("held_md_owner_phase", pre_fault[4], during_fault[4]))

        new_descriptors = {}
        for symbol, subscription in (("IF2612", first_market), ("IH2612", second_market)):
            bootstrap = subscription.recover(timeout_seconds=10)
            assert bootstrap.resync_required is True
            assert bootstrap.snapshot_epoch != old_descriptors[symbol].epoch
            accepted = subscription.accept_resync(acknowledge_data_loss=True, timeout_seconds=10)
            descriptor = accepted.source_descriptor
            assert descriptor is not None
            assert descriptor.connection_generation == recovered[3]
            assert descriptor.epoch == accepted.snapshot_epoch
            new_descriptors[symbol] = descriptor
        joined = client_b_market.start(timeout_seconds=10)
        assert joined.source_descriptor == new_descriptors["IH2612"]

        for symbol, price, subscriptions in (
            ("IF2612", 4124.0, (first_market,)),
            ("IH2612", 4125.0, (second_market, client_b_market)),
        ):
            controls.put(("tick", symbol, price))
            assert events.get(timeout=5) == ("tick", symbol, price)
            for subscription in subscriptions:
                event = _until(lambda: (event if (event := subscription.poll(timeout_seconds=5))
                    and event.payload["data"]["last_price"] == price else None))
                assert event.header.stream_epoch == new_descriptors[symbol].epoch
                assert event.payload["data"]["provider_connection_generation"] == recovered[3]

        closed_a = sessions[0].close(timeout_seconds=10)
        assert closed_a.state == "closed" and not closed_a.thread_alive
        controls.put(("tick", "IH2612", 4126.0))
        assert events.get(timeout=5) == ("tick", "IH2612", 4126.0)
        joined_event = _until(lambda: (event if (event := client_b_market.poll(timeout_seconds=5))
            and event.payload["data"]["last_price"] == 4126.0 else None))
        assert joined_event.header.stream_epoch == new_descriptors["IH2612"].epoch
        closed_b = sessions[1].close(timeout_seconds=10)
        assert closed_b.state == "closed" and not closed_b.thread_alive
        print(("synthetic_owner_pending_md_recovery_complete", 1, 2))
    finally:
        release_poll_gate()
        close_results = []
        for session in sessions:
            try:
                closed = session.close(timeout_seconds=10)
                close_results.append((closed.state, closed.thread_alive))
            except Exception as exc:
                close_results.append((type(exc).__name__, True))
        controls.put("stop")
        child.join(15)
        if child.is_alive():
            child.terminate()
            child.join(5)
            pytest.fail("synthetic Gateway process failed bounded shutdown")
        remaining = []
        while True:
            try:
                remaining.append(events.get_nowait())
            except queue.Empty:
                break
        assert child.exitcode == 0 and not any(item[0] in {"error", "cleanup_error"} for item in remaining), remaining
        assert ("closed", 1) in remaining, remaining
        assert all(state == "closed" and alive is False for state, alive in close_results), close_results


def test_blocked_native_md_start_retains_one_acquisition(tmp_path):
    from bt_api_py.service_runtime_thread import ThreadedRuntimeError

    _secure_tmp_path(tmp_path)
    (tmp_path / "guard").mkdir()
    (tmp_path / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(tmp_path / "orders.sqlite", SCOPE, generation="provisioned")
    issuer = DurableOrderCorrelationIssuer(tmp_path / "identity.sqlite")
    issuer_id = issuer.issuer_id
    issuer.close()
    rpc, market = _free_endpoint(), _free_endpoint()
    server_keys = zmq.curve_keypair()
    client_keys = (zmq.curve_keypair(), zmq.curve_keypair(), zmq.curve_keypair())
    ctx = multiprocessing.get_context("spawn")
    entered, release = ctx.Event(), ctx.Event()
    poll_armed, poll_entered, poll_release, dequeued = ctx.Event(), ctx.Event(), ctx.Event(), ctx.Value("i", 0)
    maintenance_entered = ctx.Event()
    stop_armed, stop_entered, stop_release = ctx.Event(), ctx.Event(), ctx.Event()
    events, controls = ctx.Queue(), ctx.Queue()
    child = ctx.Process(target=_gateway, args=(str(tmp_path), rpc, market, server_keys, client_keys,
        journal.journal_id, issuer_id, events, controls, (entered, release),
        (poll_armed, poll_entered, poll_release, dequeued, maintenance_entered), (stop_armed, stop_entered, stop_release)))
    child.start()
    session = None
    direct_only = None
    try:
        ready = events.get(timeout=20)
        assert ready == ("ready", child.pid), ready
        session = _session(CLIENT_A, client_keys[0], server_keys[0], rpc, market,
            journal.journal_id, "IF2612", tmp_path)
        session.start(timeout_seconds=10)
        raw = _client_config(rpc, CLIENT_C).to_public_dict()
        raw["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = SCOPE
        c_scope = WireScope(DOMAIN, account_ref=SCOPE, strategy_ref=CLIENT_C.actor_ref, run_id="run-c")
        direct_only = GatewayStrategyClientSession(parse_client_config(raw),
            binding=GatewayCommandBinding(journal.journal_id, SCOPE, CLIENT_C.actor_ref, CLIENT_C.principal_ref),
            route=GatewayClientRoute("broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", c_scope, CLIENT_C),
            peer=SERVICE, security_profiles={"security-a": ClientCurveKeys(server_keys[0], *client_keys[2])},
            host=MemoryHost(), requested_operations=OPS)
        direct_only.start(timeout_seconds=10)
        payload = _order(key="liveness", symbol="IF2612", account=SCOPE)
        assert _until(lambda: (result if (result := direct_only.command_client.reserve(
            OrderRequest.from_dict(payload), timeout_seconds=5)).outcome == "OBSERVED" else None))
        subscription = session.market_client.subscription("ticks")
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            subscription.start(timeout_seconds=0.3)
        assert entered.wait(5)
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            subscription.start(timeout_seconds=0.2)
        release.set()
        bootstrap = subscription.start(timeout_seconds=10)
        assert bootstrap.source_descriptor.connection_generation == 1
        controls.put("stats")
        stats = events.get(timeout=5)
        assert stats[:5] == ("stats", 1, 1, 1, 1), stats
        assert stats[5] == ("IF2612",)
        while subscription.poll(timeout_seconds=5) is not None:
            pass
        poll_armed.set()
        controls.put(("tick", "IF2612"))
        assert events.get(timeout=5) == ("tick", "IF2612")
        assert poll_entered.wait(5)
        # Prove a later Host maintenance pass actually entered the source
        # reset check with the same provider poll still in flight.
        assert maintenance_entered.wait(5)
        time.sleep(0.3)
        assert not poll_release.is_set()
        started = time.monotonic()
        live = direct_only.command_client.status("liveness", timeout_seconds=0.5)
        elapsed = time.monotonic() - started
        assert live.outcome == "OBSERVED" and live.record.state == "RESERVED" and elapsed < 1.0, (live, elapsed)
        try:
            held = subscription.poll(timeout_seconds=0.2)
            assert held is None or held.payload["data"]["last_price"] != 4005.0
        except ThreadedRuntimeError as error:
            assert error.code == "OWNER_TIMEOUT_RUNNING"
        poll_release.set()
        delivered = _until(lambda: (event if (event := subscription.poll(timeout_seconds=10)) and
            event.payload["data"]["last_price"] == 4005.0 else None))
        assert dequeued.value == 1 and delivered.header.scope == WireScope(DOMAIN)
        assert subscription.poll(timeout_seconds=5) is None
        stop_armed.set()
        closed = session.close(timeout_seconds=5)
        assert closed.state == "closed" and not closed.thread_alive
        # The Gateway answered for this client's own reference; the native stop
        # that is still running is the Gateway's work, not an open question of
        # the client's close.
        assert closed.owner_cleanup_uncertainties == 0
        assert stop_entered.wait(5)
        stop_release.set()
        def stop_completed():
            controls.put("stats")
            status = events.get(timeout=5)
            # Gateway-mode disconnect asks the same client for a non-blocking
            # stop receipt, then the provider waits for its typed receipt; both
            # observe one native client and one confirmed release (status[12]).
            return status if status[6] >= 1 and status[12] else None
        stats = _until(stop_completed)
        assert stats[:5] == ("stats", 1, 1, 1, 1), stats
    finally:
        release.set()
        poll_release.set()
        stop_release.set()
        close_result = None
        try:
            if session is not None:
                closed = session.close(timeout_seconds=10)
                close_result = (closed.state, closed.thread_alive)
            if direct_only is not None:
                other_closed = direct_only.close(timeout_seconds=10)
                assert other_closed.state == "closed" and not other_closed.thread_alive
        finally:
            controls.put("stop")
            child.join(15)
            if child.is_alive():
                child.terminate()
                child.join(5)
                pytest.fail("synthetic Gateway process failed bounded shutdown")
        remaining = []
        while True:
            try:
                remaining.append(events.get_nowait())
            except queue.Empty:
                break
        assert child.exitcode == 0 and not any(item[0] in {"error", "cleanup_error"} for item in remaining), remaining
        assert ("closed", 1) in remaining, remaining
        assert session is None or close_result == ("closed", False)


def test_spawned_ctp_td_relogin_fences_stale_return_write_without_md(tmp_path):
    """The same TD SPI can relogin, while its old callback stays uncommitted."""
    _secure_tmp_path(tmp_path)
    (tmp_path / "guard").mkdir()
    (tmp_path / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "orders.sqlite", SCOPE, generation="provisioned"
    )
    issuer = DurableOrderCorrelationIssuer(tmp_path / "identity.sqlite")
    issuer_id = issuer.issuer_id
    issuer.close()
    rpc, market = _free_endpoint(), _free_endpoint()
    server_keys = zmq.curve_keypair()
    client_keys = (zmq.curve_keypair(), zmq.curve_keypair(), zmq.curve_keypair())
    ctx = multiprocessing.get_context("spawn")
    events, controls = ctx.Queue(), ctx.Queue()
    blocked_return_poll = tuple(ctx.Event() for _ in range(6))
    child = ctx.Process(
        target=_gateway,
        args=(str(tmp_path), rpc, market, server_keys, client_keys, journal.journal_id,
              issuer_id, events, controls, None, None, None, blocked_return_poll),
    )
    child.start()
    session = None
    try:
        ready = events.get(timeout=20)
        assert ready == ("ready", child.pid) and child.pid != os.getpid(), ready
        session = _session(CLIENT_A, client_keys[0], server_keys[0], rpc, market,
                           journal.journal_id, "IF2612", tmp_path)
        session.start(timeout_seconds=10)
        initial_account = _until(lambda: session.account_client.refresh(timeout_seconds=5))
        initial_observation_id = initial_account.observation.observation_id

        def returns_ready_without_market():
            controls.put("stats")
            stats = events.get(timeout=5)
            assert stats[0] == "stats" and stats[1] == 1 and stats[2] == 1, stats
            assert stats[3:6] == (0, 0, ()), stats
            status = stats[11]
            return stats if status == {"status": "HEALTHY", "reservation_open": True} else None

        _until(returns_ready_without_market, timeout=12)
        before_progress = session.execution_stream_client.progress(timeout_seconds=5)
        assert before_progress.outcome == "OBSERVED" and before_progress.progress is not None
        baseline_progress = before_progress.progress
        assert baseline_progress.offered_sequence == 0, baseline_progress

        (armed, poll_entered, callback_injected, owner_verified,
         td_fault_done, poll_completed) = blocked_return_poll
        armed.set()
        assert poll_entered.wait(5), "return maintenance did not enter the gated poll"
        td_started = time.monotonic()
        controls.put("td_stale_return_inject")
        injected = events.get(timeout=5)
        assert injected == ("td_stale_return_injected", True, True), injected
        before_td = events.get(timeout=5)
        assert before_td[0:2] == (
            "td_return_poll_snapshot", "callback_queued_before_td_disconnect"
        ), before_td
        assert before_td[2] is True, before_td
        assert before_td[3] == 1, before_td
        assert before_td[4] == before_td[5], before_td
        assert before_td[6:8] == ("HEALTHY", True), before_td
        assert owner_verified.wait(5), "return-buffer status was not captured on its owner thread"

        controls.put("td_disconnect_relogin")
        td = events.get(timeout=5)
        assert td[0] == "td_reconnected", td
        assert td[2] > td[1] and td[3:5] == (True, True), td
        assert td[5:7] == (False, True), td
        assert poll_completed.wait(5), "return poll did not finish after TD relogin"
        poll_trace = events.get(timeout=5)
        assert poll_trace[0] == "td_return_poll_trace", poll_trace
        before_disconnect, before_poll, after_poll = poll_trace[1:]
        assert before_disconnect == before_td, poll_trace
        assert before_poll[0:2] == (
            "td_return_poll_snapshot", "after_td_relogin_before_original_poll"
        ), poll_trace
        assert after_poll[0:2] == ("td_return_poll_snapshot", "after_original_poll"), poll_trace
        assert all(
            snapshot[2] is True for snapshot in (before_disconnect, before_poll, after_poll)
        ), poll_trace
        assert all(
            snapshot[3] == 1 for snapshot in (before_disconnect, before_poll, after_poll)
        ), poll_trace
        assert before_poll[4] > before_disconnect[4], poll_trace
        assert after_poll[4] == after_poll[5] == before_poll[4], poll_trace
        assert after_poll[6:8] == ("SOURCE_STALE", False), poll_trace

        controls.put("stats")
        after_poll_stats = events.get(timeout=5)
        assert after_poll_stats[11]["status"] in {"SOURCE_STALE", "SESSION_CHANGED"}, after_poll_stats
        assert after_poll_stats[11]["reservation_open"] is False, after_poll_stats

        def refreshed_account():
            candidate = session.account_client.refresh(timeout_seconds=2)
            return candidate if candidate.observation.observation_id != initial_observation_id else None

        recovered_account = _until(refreshed_account, timeout=12)
        assert recovered_account.observation.observation_id != initial_observation_id

        stale_payload = _order(key="td-stale-return-fence", symbol="IF2612", account=SCOPE)
        stale_payload.update(exchange_id="CFFEX", offset="open", time_in_force="GFD",
                             quantity_unit="contracts")
        denied = session.command_client.reserve(
            OrderRequest.from_dict(stale_payload), timeout_seconds=8
        )
        assert (denied.outcome, denied.code) == ("REQUEST_DENIED", "ADMISSION_DENIED"), denied
        absent = session.command_client.status("td-stale-return-fence", timeout_seconds=5)
        assert (absent.outcome, absent.code) == ("REQUEST_DENIED", "NOT_FOUND"), absent

        after_progress = session.execution_stream_client.progress(timeout_seconds=5)
        assert after_progress.outcome == "OBSERVED" and after_progress.progress is not None
        assert (after_progress.progress.epoch, after_progress.progress.offered_sequence) == (
            baseline_progress.epoch, baseline_progress.offered_sequence
        ), (baseline_progress, after_progress.progress)
        no_events = session.execution_stream_client.read_after(timeout_seconds=5)
        assert no_events.outcome == "OBSERVED" and no_events.page is not None
        assert no_events.page.events == (), no_events.page

        controls.put("stats")
        after_stats = events.get(timeout=5)
        assert after_stats[:6] == ("stats", 1, 1, 0, 0, ()), after_stats
        assert after_stats[7:9] == (0, 0), after_stats
        assert after_stats[11]["status"] in {"SOURCE_STALE", "SESSION_CHANGED"}, after_stats
        assert after_stats[11]["reservation_open"] is False, after_stats
        elapsed = time.monotonic() - td_started
        print(("synthetic_td_relogin_stale_return", td[2] - td[1],
               after_poll_stats[11]["status"], after_poll[3], baseline_progress.offered_sequence,
               after_progress.progress.offered_sequence, after_stats[3:5], round(elapsed, 3)))
    finally:
        for event in blocked_return_poll[2:]:
            event.set()
        close_result = None
        try:
            if session is not None:
                closed = session.close(timeout_seconds=10)
                close_result = (closed.state, closed.thread_alive, closed.owner_cleanup_uncertainties)
        finally:
            controls.put("stop")
            child.join(15)
            if child.is_alive():
                child.terminate()
                child.join(5)
                pytest.fail("synthetic TD-only Gateway process failed bounded shutdown")
        remaining = []
        while True:
            try:
                remaining.append(events.get_nowait())
            except queue.Empty:
                break
        assert child.exitcode == 0 and not any(
            item[0] in {"error", "cleanup_error"} for item in remaining
        ), remaining
        assert ("closed", 1) in remaining, remaining
        assert close_result == ("closed", False, 0), close_result
