"""Project one authenticated Gateway market subscription into neutral ticks.

This is a consumer-side projection, not a provider clock or freshness proof.
"""
from __future__ import annotations

import os
import threading
import time

from bt_api_base.market_protocol import (
    MARKET_EVENT_OPERATION, MARKET_EVENT_SCHEMA, MARKET_EVENT_SCHEMA_VERSION,
    MarketSubscriptionKey, MarketSourceDescriptor,
)
from bt_api_base.market_tick_protocol import (
    MarketClockQuality, MarketEventCursor, MarketFreshnessStatus,
    MarketTickPayload, bind_market_tick_event,
)
from bt_api_base.service_protocol import WireEnvelope, WireIdentity, WireScope, validate_event
from bt_api_py.gateway_strategy_market import GatewayMarketReceivedEvent, GatewayMarketSubscriptionClient
from bt_api_py.service_market import MarketReceiveReceipt


class GatewayMarketProjectionError(RuntimeError):
    """Value-free projection error."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class GatewayMarketTickProjector:
    """Bind frames returned by the real Gateway client to its source and cursor."""

    def __init__(self, client, *, key, scope, peer, timeout_seconds=1.0):
        if (type(client) is not GatewayMarketSubscriptionClient
                or type(key) is not MarketSubscriptionKey
                or type(scope) is not WireScope or type(peer) is not WireIdentity
                or type(timeout_seconds) not in (int, float)
                or not 0 < timeout_seconds <= 30):
            raise GatewayMarketProjectionError("INVALID_GATEWAY_MARKET_BINDING")
        self._client, self._key, self._scope, self._peer = client, key, scope, peer
        self._timeout = float(timeout_seconds)
        self._thread, self._pid = threading.current_thread(), os.getpid()
        self._source = None
        self._sequence = 0
        self._snapshot = None
        self._started = False
        self._last_receipt = None

    def _check(self):
        if self._pid != os.getpid() or self._thread is not threading.current_thread():
            raise GatewayMarketProjectionError("GATEWAY_MARKET_OWNER_CHANGED")

    def _adopt(self, bootstrap):
        source = bootstrap.source_descriptor
        if bootstrap.resync_required:
            raise GatewayMarketProjectionError("PROVIDER_RESYNC_REQUIRED")
        if (type(source) is not MarketSourceDescriptor or source.key != self._key
                or source.stream_id != self._key.stream_id
                or bootstrap.stream_id != source.stream_id
                or bootstrap.snapshot_epoch != source.epoch
                or (bootstrap.cursor_epoch or bootstrap.snapshot_epoch) != source.epoch
                or bootstrap.snapshot_kind != "latest_event"
                or bootstrap.snapshot_complete or bootstrap.full_baseline_available):
            raise GatewayMarketProjectionError("GATEWAY_MARKET_BOOTSTRAP_INVALID")
        self._source = source
        self._sequence = bootstrap.cursor_sequence
        self._last_receipt = None
        # A recover bootstrap can advertise a newer latest snapshot while the
        # client's replay cursor is still behind it.  In that case consume
        # the client's ordered replay rather than jumping ahead to the snapshot.
        self._snapshot = (bootstrap.snapshot if bootstrap.snapshot_sequence == self._sequence
                          else None)
        if self._snapshot is not None:
            if self._snapshot.header.sequence != self._sequence:
                raise GatewayMarketProjectionError("GATEWAY_MARKET_SNAPSHOT_INVALID")
        self._started = True

    def start(self):
        self._check()
        self._last_receipt = None
        self._adopt(self._client.start(timeout_seconds=self._timeout))

    def status(self):
        self._check()
        try:
            status = self._client.status(timeout_seconds=self._timeout)
        except BaseException:
            self._last_receipt = None
            raise
        if (not status["healthy"] or status["recovery_required"] or status["resync_required"]
                or status["authorization_interrupted"] is not None or status["local_result_gap"]):
            self._last_receipt = None
        if (status["healthy"] and not status["recovery_required"]
                and not status["resync_required"]
                and status["source_descriptor"] != self._source):
            self._last_receipt = None
            raise GatewayMarketProjectionError("GATEWAY_MARKET_SOURCE_CHANGED")
        return status

    def poll(self):
        self._check()
        self._last_receipt = None
        if not self._started:
            raise GatewayMarketProjectionError("GATEWAY_MARKET_NOT_STARTED")
        frame, snapshot = self._snapshot, self._snapshot is not None
        self._snapshot = None
        receipt = None
        if frame is None:
            received = self._client.poll_with_receipt(timeout_seconds=self._timeout)
            if received is not None:
                if type(received) is not GatewayMarketReceivedEvent:
                    self._started = False
                    raise GatewayMarketProjectionError("GATEWAY_MARKET_RECEIPT_INVALID")
                frame, receipt = received.event, received.receipt
        if frame is None:
            self._last_receipt = None
            return None
        try:
            if type(frame) is not WireEnvelope:
                raise ValueError
            source, header = self._source, frame.header
            sequence = self._sequence if snapshot else self._sequence + 1
            if receipt is not None and (
                type(receipt) is not MarketReceiveReceipt
                or receipt.stream_id != header.stream_id or receipt.stream_epoch != header.stream_epoch
                or receipt.sequence != header.sequence or receipt.message_id != header.message_id
                or type(receipt.pid) is not int or receipt.pid != os.getpid()
                or receipt.source not in ("pub", "replay")
                or type(receipt.received_wall_ns) is not int or receipt.received_wall_ns <= 0
                or type(receipt.received_monotonic_ns) is not int
                or not 0 < receipt.received_monotonic_ns <= time.monotonic_ns()
                or type(receipt.thread_id) is not int or receipt.thread_id <= 0
                or (receipt.source == "pub" and (receipt.request_message_id is not None
                                                  or receipt.reply_message_id is not None))
                or (receipt.source == "replay" and any(
                    type(value) is not str or not value
                    for value in (receipt.request_message_id, receipt.reply_message_id)))
            ):
                raise ValueError
            validate_event(frame, expected_scope=self._scope, expected_stream_id=source.stream_id,
                           expected_stream_epoch=source.epoch, expected_sequence=sequence)
            if (header.identity != self._peer or header.operation != MARKET_EVENT_OPERATION
                    or header.schema_id != MARKET_EVENT_SCHEMA
                    or header.schema_version != MARKET_EVENT_SCHEMA_VERSION
                    or set(frame.payload) != {"key", "data"}
                    or MarketSubscriptionKey.from_dict(frame.payload["key"]) != self._key):
                raise ValueError
            payload = MarketTickPayload.from_dict(frame.payload["data"])
            if (payload.source_clock_quality is not MarketClockQuality.UNKNOWN
                    or payload.receive_clock_quality is not MarketClockQuality.UNKNOWN
                    or payload.freshness_status is not MarketFreshnessStatus.UNAVAILABLE
                    or payload.execution_eligible
                    or not {"GENERIC_EVENT_TIME_NOT_BOUND", "SOURCE_CLOCK_CALIBRATION_NOT_PROVIDED"}
                    <= set(payload.freshness_reason_codes)):
                raise ValueError
            event = bind_market_tick_event(
                payload, source, MarketEventCursor(source.stream_id, source.epoch, sequence)
            )
        except Exception:
            self._started = False
            raise GatewayMarketProjectionError("GATEWAY_MARKET_FRAME_INVALID") from None
        self._sequence = sequence
        self._last_receipt = receipt
        return event

    @property
    def last_receipt(self):
        """Last exact SDK socket-read receipt; None for an unobserved snapshot."""
        self._check()
        return self._last_receipt

    def recover(self):
        self._check()
        self._last_receipt = None
        self._started = False
        self._adopt(self._client.recover(timeout_seconds=self._timeout))

    def accept_resync(self):
        self._check()
        self._last_receipt = None
        self._started = False
        self._adopt(self._client.accept_resync(acknowledge_data_loss=True,
                                              timeout_seconds=self._timeout))
