"""Bind a received wire-v1 market tick to the receiver's authenticated source.

Iteration 60, step 60.09. On the Gateway path the receiving SDK decodes each
MessagePack message once (``bt_api_base.wire_codec_v1.decode_message``) into a
frozen ``MarketTickPayload``. That payload is data, not an authenticated
event: the receiver binds it to the ``MarketSourceDescriptor`` of the
subscription it authenticated, using the message's stream position as the
cursor. The resulting ``MarketTickEvent`` is the same type the direct path
produces, so Backtrader's adapter consumes both identically.

The descriptor comes from the receiver's own subscription, never from the
message: a message whose stream or epoch does not match is rejected, and the
Base binding rejects any payload whose source, generation or clock identity
differs from the descriptor.
"""

from __future__ import annotations

from bt_api_base.market_protocol import MarketSourceDescriptor
from bt_api_base.market_tick_protocol import (
    MarketEventCursor,
    MarketTickContractError,
    MarketTickEvent,
    bind_market_tick_event,
)
from bt_api_base.wire_codec_v1 import WireMessageV1

__all__ = ["GatewayMarketBindingError", "bind_received_tick"]


class GatewayMarketBindingError(ValueError):
    """Finite, value-free failure binding a received tick."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def bind_received_tick(message: WireMessageV1, source: MarketSourceDescriptor) -> MarketTickEvent:
    """Return the authenticated ``MarketTickEvent`` for one received message."""

    if type(message) is not WireMessageV1 or message.kind != "tick":
        raise GatewayMarketBindingError("GATEWAY_TICK_MESSAGE_INVALID")
    if type(source) is not MarketSourceDescriptor:
        raise GatewayMarketBindingError("GATEWAY_SOURCE_INVALID")
    stream = message.stream
    if stream.stream_id != source.stream_id or stream.epoch != source.epoch:
        raise GatewayMarketBindingError("GATEWAY_STREAM_MISMATCH")
    try:
        return bind_market_tick_event(
            message.payload,
            source,
            MarketEventCursor(
                stream_id=stream.stream_id, epoch=stream.epoch, sequence=stream.sequence
            ),
        )
    except MarketTickContractError as error:
        raise GatewayMarketBindingError(error.args[0] if error.args else "GATEWAY_BINDING") from None
