from __future__ import annotations

import ast
import unittest
from pathlib import Path


SOURCE = Path(__file__).with_name("test_load01_hundred_strategy_30min.py")
FUNCTION_NAMES = {
    "_normalize_load01_market_watermarks",
    "_validate_load01_terminal_market_message",
    "_validate_load01_strategy_watermark_message",
    "_validate_load01_terminal_market_status",
    "_load01_market_watermark_time_failures",
    "_load01_market_terminal_cursors_match",
    "_load01_market_client_terminal_state",
    "_load01_market_watermark_completion_failures",
}
tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
functions = [
    node
    for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in FUNCTION_NAMES
]
if {node.name for node in functions} != FUNCTION_NAMES:
    raise AssertionError("terminal watermark helper source selection was incomplete")
namespace = {}
exec(
    compile(
        ast.fix_missing_locations(ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__",
                    names=[ast.alias(name="annotations")],
                    level=0,
                ),
                *functions,
            ],
            type_ignores=[],
        )),
        str(SOURCE),
        "exec",
    ),
    namespace,
)
normalize = namespace["_normalize_load01_market_watermarks"]
validate_market_message = namespace["_validate_load01_terminal_market_message"]
validate_strategy_message = namespace["_validate_load01_strategy_watermark_message"]
validate_market_status = namespace["_validate_load01_terminal_market_status"]
terminal_cursors_match = namespace["_load01_market_terminal_cursors_match"]
client_terminal_state = namespace["_load01_market_client_terminal_state"]
completion_failures = namespace["_load01_market_watermark_completion_failures"]


def good_target() -> dict[str, dict[str, object]]:
    return {
        "topic-a": {"epoch": "epoch-a", "sequence": 12},
        "topic-b": {"epoch": "epoch-b", "sequence": 8},
    }


def good_state() -> dict[str, object]:
    return {
        "queue_count": 0,
        "queue_bytes": 0,
        "healthy": True,
        "recovery_required": False,
        "resync_required": False,
        "unsubscription_uncertain": False,
        "pending_poll_replay": False,
        "pending_poll_gap_event": False,
        "pending_resync": False,
    }


def good_market_status() -> dict[str, object]:
    targets = good_target()
    return {
        "provider_starts": {},
        "provider_stops": {},
        "active_provider_keys": (),
        "source_sequences": {key: row["sequence"] for key, row in targets.items()},
        "source_payload_bytes": 0,
        "source_payload_bytes_by_topic": {},
        "first_publish_ns": None,
        "last_publish_ns": None,
        "hub_readiness": {},
        "source_publishing_stopped": True,
        "hub_watermarks": targets,
        "watermark_captured_monotonic_ns": 10_000_000_000,
    }


def good_receipts() -> dict[str, dict[str, object]]:
    return {
        "topic-a": {
            "epoch": "epoch-a",
            "sequence": 12,
            "received_monotonic_ns": 10_200_000_000,
        },
        "topic-b": {
            "epoch": "epoch-b",
            "sequence": 8,
            "received_monotonic_ns": 10_250_000_000,
        },
    }


def good_completion_kwargs() -> dict[str, object]:
    targets = good_target()
    return {
        "targets": targets,
        "expected_stream_ids": ("topic-a", "topic-b"),
        "receipts": good_receipts(),
        "last_sequences": {key: row["sequence"] for key, row in targets.items()},
        "last_epochs": {key: row["epoch"] for key, row in targets.items()},
        "client_states": {key: good_state() for key in targets},
        "runtime_state": {
            "healthy": True,
            "active_bindings": 24,
            "expected_bindings": 24,
            "cleanup_failures": 0,
            "unregister_uncertainties": 0,
        },
        "counter_deltas": {
            "duplicates": 0,
            "gap_count": 0,
            "gap_events": 0,
            "epoch_changes": 0,
        },
        "barrier_captured_monotonic_ns": 10_000_000_000,
        "caught_up_at_monotonic_ns": 10_300_000_000,
    }


class FakeMarketClient:
    queue_count = 0
    queue_bytes = 0
    healthy = True
    recovery_required = False
    resync_required = False
    unsubscription_uncertain = False
    _pending_poll_replay = None
    _pending_poll_gap_event = None
    _pending_resync = None


class TerminalWatermarkHelperTests(unittest.TestCase):
    def test_final_close_compares_receipts_to_ack_receipt_field(self) -> None:
        receipts = good_receipts()
        ack = {
            "last_sequences": {key: row["sequence"] for key, row in good_target().items()},
            "last_epochs": {key: row["epoch"] for key, row in good_target().items()},
            "receipts": receipts,
        }
        final_stats = {
            "last_sequences": dict(ack["last_sequences"]),
            "last_epochs": dict(ack["last_epochs"]),
            "market_delivery_receipts": {key: dict(value) for key, value in receipts.items()},
        }
        self.assertTrue(terminal_cursors_match(ack, final_stats))
        final_stats["market_delivery_receipts"]["topic-a"]["received_monotonic_ns"] += 1
        self.assertFalse(terminal_cursors_match(ack, final_stats))
        self.assertFalse(terminal_cursors_match(ack, {"last_sequences": {}}))

    def test_valid_terminal_market_status_binds_provider_counter_to_hub_head(self) -> None:
        targets = good_target()
        status = good_market_status()
        self.assertEqual(
            validate_market_status(status, ("topic-a", "topic-b")),
            {"captured_monotonic_ns": 10_000_000_000, "watermarks": targets},
        )

    def test_market_response_requires_exact_tuple_token_and_status_schema(self) -> None:
        status = good_market_status()
        message = ("terminal-watermark", 7, status)
        self.assertEqual(
            validate_market_message(
                message,
                expected_token=7,
                expected_stream_ids=("topic-a", "topic-b"),
            )[1],
            {"captured_monotonic_ns": 10_000_000_000, "watermarks": good_target()},
        )
        malformed = (
            ["terminal-watermark", 7, status],
            ("terminal-watermark", True, status),
            ("terminal-watermark", 7, {**status, "extra": None}),
        )
        for value in malformed:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(AssertionError):
                    validate_market_message(
                        value,
                        expected_token=7,
                        expected_stream_ids=("topic-a", "topic-b"),
                    )

    def test_strategy_response_requires_exact_tuple_token_and_ack_schema(self) -> None:
        targets = good_target()
        result = {
            "complete": True,
            "failures": (),
            "targets": targets,
            "receipts": good_receipts(),
            "last_sequences": {key: row["sequence"] for key, row in targets.items()},
            "last_epochs": {key: row["epoch"] for key, row in targets.items()},
            "client_states": {key: good_state() for key in targets},
            "runtime_state": {
                "healthy": True,
                "active_bindings": 24,
                "expected_bindings": 24,
                "cleanup_failures": 0,
                "unregister_uncertainties": 0,
            },
            "counter_deltas": {
                "duplicates": 0,
                "gap_count": 0,
                "gap_events": 0,
                "epoch_changes": 0,
            },
            "caught_up_at_monotonic_ns": 10_300_000_000,
            "ack_sent_monotonic_ns": 10_310_000_000,
        }
        message = ("market-watermark-result", 2, 1234, 7, result)
        self.assertIs(
            validate_strategy_message(
                message, expected_index=2, expected_pid=1234, expected_token=7
            ),
            result,
        )
        malformed_result = {**result, "extra": None}
        malformed_timestamp = {**result, "ack_sent_monotonic_ns": 10_299_999_999}
        malformed = (
            ("market-watermark-result", 2, 1234, True, result),
            ("market-watermark-result", 2, 1234, 7, malformed_result),
            ("market-watermark-result", 2, 1234, 7, malformed_timestamp),
        )
        for value in malformed:
            with self.subTest(token=value[3]):
                with self.assertRaises(AssertionError):
                    validate_strategy_message(
                        value, expected_index=2, expected_pid=1234, expected_token=7
                    )

    def test_market_status_rejects_unstopped_missing_or_mismatched_head(self) -> None:
        targets = good_target()
        base = good_market_status()
        cases = (
            {**base, "source_publishing_stopped": False},
            {**base, "hub_watermarks": {"topic-a": targets["topic-a"]}},
            {
                **base,
                "source_sequences": {"topic-a": 13, "topic-b": 8},
            },
            {
                **base,
                "hub_watermarks": {
                    **targets,
                    "topic-b": {"epoch": "epoch-b", "sequence": True},
                },
            },
        )
        for status in cases:
            with self.subTest(status=status):
                with self.assertRaises(AssertionError):
                    validate_market_status(status, ("topic-a", "topic-b"))

    def test_complete_requires_direct_poll_receipt_and_matching_cursor(self) -> None:
        self.assertEqual(completion_failures(**good_completion_kwargs()), ())

        kwargs = good_completion_kwargs()
        kwargs["receipts"] = {"topic-a": good_receipts()["topic-a"]}
        failures = completion_failures(**kwargs)
        self.assertIn("receipt_coverage_invalid", failures)
        self.assertIn("receipt_missing:topic-b", failures)

        kwargs = good_completion_kwargs()
        kwargs["last_sequences"] = {"topic-a": 12, "topic-b": 0}
        failures = completion_failures(**kwargs)
        self.assertIn("cursor_behind_target:topic-b", failures)

    def test_epoch_sequence_and_counter_discontinuities_fail_closed(self) -> None:
        kwargs = good_completion_kwargs()
        kwargs["receipts"]["topic-a"]["epoch"] = "stale-epoch"
        kwargs["last_epochs"]["topic-b"] = "stale-epoch"
        kwargs["counter_deltas"]["duplicates"] = 1
        kwargs["counter_deltas"]["gap_events"] = 2
        failures = completion_failures(**kwargs)
        self.assertIn("receipt_epoch_mismatch:topic-a", failures)
        self.assertIn("cursor_epoch_mismatch:topic-b", failures)
        self.assertIn("counter_delta_nonzero:duplicates", failures)
        self.assertIn("counter_delta_nonzero:gap_events", failures)

    def test_queue_pending_recovery_lease_and_binding_state_are_required(self) -> None:
        kwargs = good_completion_kwargs()
        kwargs["client_states"]["topic-a"].update(
            {
                "queue_count": 1,
                "queue_bytes": 64,
                "pending_poll_replay": True,
                "pending_poll_gap_event": True,
                "unsubscription_uncertain": True,
            }
        )
        kwargs["client_states"]["topic-b"]["pending_resync"] = True
        kwargs["runtime_state"]["active_bindings"] = 23
        kwargs["runtime_state"]["healthy"] = False
        failures = completion_failures(**kwargs)
        self.assertIn("queue_count_nonzero:topic-a", failures)
        self.assertIn("queue_bytes_nonzero:topic-a", failures)
        self.assertIn("pending_poll_replay_true_or_missing:topic-a", failures)
        self.assertIn("pending_poll_gap_event_true_or_missing:topic-a", failures)
        self.assertIn("unsubscription_uncertain_true_or_missing:topic-a", failures)
        self.assertIn("pending_resync_true_or_missing:topic-b", failures)
        self.assertIn("runtime_unhealthy", failures)
        self.assertIn("runtime_binding_count_changed", failures)

    def test_ack_must_arrive_within_ten_seconds_and_receipt_precedes_ack(self) -> None:
        kwargs = good_completion_kwargs()
        kwargs["caught_up_at_monotonic_ns"] = 20_000_000_001
        failures = completion_failures(**kwargs)
        self.assertIn("catchup_outside_ten_second_window", failures)

        kwargs = good_completion_kwargs()
        kwargs["receipts"]["topic-a"]["received_monotonic_ns"] = 10_300_000_001
        failures = completion_failures(**kwargs)
        self.assertIn("receipt_after_ack:topic-a", failures)

    def test_client_state_includes_private_pending_flags(self) -> None:
        client = FakeMarketClient()
        client._pending_poll_replay = ("epoch-a", 12)
        client._pending_poll_gap_event = object()
        client._pending_resync = object()
        state = client_terminal_state(client)
        self.assertTrue(state["pending_poll_replay"])
        self.assertTrue(state["pending_poll_gap_event"])
        self.assertTrue(state["pending_resync"])
        self.assertTrue(state["healthy"])
        self.assertFalse(state["unsubscription_uncertain"])


if __name__ == "__main__":
    unittest.main()
