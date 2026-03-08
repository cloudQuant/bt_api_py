"""Unit tests for EventBus publish/subscribe mechanism."""



from bt_api_py.event_bus import EventBus


class TestEventBus:
    def setup_method(self):
        self.bus = EventBus()
        self.received = []

    def _handler(self, data):
        self.received.append(data)

    def test_on_and_emit(self):
        self.bus.on("tick", self._handler)
        self.bus.emit("tick", {"price": 100})
        assert self.received == [{"price": 100}]

    def test_emit_no_handlers(self):
        # Should not raise
        self.bus.emit("unknown_event", {})

    def test_multiple_handlers(self):
        h2_received = []
        self.bus.on("tick", self._handler)
        self.bus.on("tick", lambda d: h2_received.append(d))
        self.bus.emit("tick", "x")
        assert self.received == ["x"]
        assert h2_received == ["x"]

    def test_no_duplicate_registration(self):
        self.bus.on("tick", self._handler)
        self.bus.on("tick", self._handler)  # same handler again
        self.bus.emit("tick", 1)
        assert self.received == [1]  # only called once

    def test_off_removes_handler(self):
        self.bus.on("tick", self._handler)
        self.bus.off("tick", self._handler)
        self.bus.emit("tick", "should_not_receive")
        assert self.received == []

    def test_off_all_handlers(self):
        self.bus.on("tick", self._handler)
        self.bus.on("tick", lambda d: None)
        self.bus.off("tick")  # remove all
        self.bus.emit("tick", "x")
        assert self.received == []

    def test_handler_error_does_not_stop_others(self):
        """A failing handler should not prevent other handlers from executing."""

        def bad_handler(data):
            raise ValueError("boom")

        self.bus.on("tick", bad_handler)
        self.bus.on("tick", self._handler)
        self.bus.emit("tick", "data")
        # _handler should still have received the event
        assert self.received == ["data"]

    def test_different_event_types_isolated(self):
        other = []
        self.bus.on("tick", self._handler)
        self.bus.on("order", lambda d: other.append(d))
        self.bus.emit("tick", "t")
        self.bus.emit("order", "o")
        assert self.received == ["t"]
        assert other == ["o"]
