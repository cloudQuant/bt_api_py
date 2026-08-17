"""Module-level docstring."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from bt_api_py.forwarding.schema import CommandAck, ForwardingError, PrivateEvent


def _escape_like_prefix(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SQLiteStateStore:
    """SQLite-backed event and command acknowledgement store."""

    def __init__(self, path: str | Path) -> None:
        """__init__ method"""
        self.path = str(path)
        # SQLite 的内存库指示符是 ":memory:"；"memory:" 会被当作磁盘文件路径。
        # 兼容旧的 "memory:" 写法，统一映射到真正的内存库。
        if self.path == "memory:":
            self.path = ":memory:"
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._closed = False
        self._init_schema()

    def close(self) -> None:
        """close method"""
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True

    def __enter__(self) -> SQLiteStateStore:
        """__enter__ method"""
        return self

    def __exit__(self, *args: object) -> None:
        """__exit__ method"""
        self.close()

    def get_command_ack(self, idempotency_key: str) -> CommandAck | None:
        """get_command_ack method"""
        with self._lock:
            self._ensure_open()
            row = self._conn.execute(
                """
                SELECT command_id, idempotency_key, accepted, status, account_id,
                       strategy_id, order_id, reason, payload, sequence_id, event_time,
                       schema_version
                  FROM command_acks
                 WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return CommandAck(
            command_id=row["command_id"],
            idempotency_key=row["idempotency_key"],
            accepted=bool(row["accepted"]),
            status=row["status"],
            account_id=row["account_id"],
            strategy_id=row["strategy_id"],
            order_id=row["order_id"],
            reason=row["reason"],
            payload=json.loads(row["payload"] or "{}"),
            sequence_id=int(row["sequence_id"] or 0),
            event_time=int(row["event_time"] or 0),
            schema_version=row["schema_version"],
        )

    def save_command_ack(self, ack: CommandAck) -> None:
        """save_command_ack method"""
        with self._lock:
            self._ensure_open()
            self._conn.execute(
                """
                INSERT INTO command_acks (
                    idempotency_key, command_id, accepted, status, account_id,
                    strategy_id, order_id, reason, payload, sequence_id, event_time,
                    schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    command_id = excluded.command_id,
                    accepted = excluded.accepted,
                    status = excluded.status,
                    account_id = excluded.account_id,
                    strategy_id = excluded.strategy_id,
                    order_id = excluded.order_id,
                    reason = excluded.reason,
                    payload = excluded.payload,
                    sequence_id = excluded.sequence_id,
                    event_time = excluded.event_time,
                    schema_version = excluded.schema_version
                """,
                (
                    ack.idempotency_key,
                    ack.command_id,
                    1 if ack.accepted else 0,
                    ack.status,
                    ack.account_id,
                    ack.strategy_id,
                    ack.order_id,
                    ack.reason,
                    json.dumps(ack.payload, separators=(",", ":"), sort_keys=True),
                    ack.sequence_id,
                    ack.event_time,
                    ack.schema_version,
                ),
            )
            self._conn.commit()

    def save_private_event(self, event: PrivateEvent) -> None:
        """save_private_event method"""
        with self._lock:
            self._ensure_open()
            self._conn.execute(
                """
                INSERT INTO private_events (
                    topic, event_type, account_id, strategy_id, payload,
                    sequence_id, event_time, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.topic,
                    event.event_type,
                    event.account_id,
                    event.strategy_id,
                    json.dumps(event.payload, separators=(",", ":"), sort_keys=True),
                    event.sequence_id,
                    event.event_time,
                    event.schema_version,
                ),
            )
            self._conn.commit()

    def list_private_events(self, topic_prefix: str = "", limit: int = 100) -> list[PrivateEvent]:
        """list_private_events method"""
        limit = int(limit)
        if limit < 0:
            raise ValueError("limit must be non-negative")
        with self._lock:
            self._ensure_open()
            if limit == 0:
                return []
            rows = self._conn.execute(
                """
                SELECT topic, event_type, account_id, strategy_id, payload,
                       sequence_id, event_time, schema_version
                  FROM private_events
                 WHERE topic LIKE ? ESCAPE '\\'
                 ORDER BY id DESC
                 LIMIT ?
                """,
                (f"{_escape_like_prefix(topic_prefix)}%", limit),
            ).fetchall()
        return [
            PrivateEvent(
                event_type=row["event_type"],
                account_id=row["account_id"],
                strategy_id=row["strategy_id"],
                payload=json.loads(row["payload"] or "{}"),
                sequence_id=int(row["sequence_id"] or 0),
                event_time=int(row["event_time"] or 0),
                schema_version=row["schema_version"],
                topic=row["topic"],
            )
            for row in reversed(rows)
        ]

    def _init_schema(self) -> None:
        with self._lock:
            self._ensure_open()
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS command_acks (
                    idempotency_key TEXT PRIMARY KEY,
                    command_id TEXT NOT NULL,
                    accepted INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    order_id TEXT,
                    reason TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}',
                    sequence_id INTEGER NOT NULL DEFAULT 0,
                    event_time INTEGER NOT NULL DEFAULT 0,
                    schema_version TEXT NOT NULL DEFAULT '1.0'
                );

                CREATE TABLE IF NOT EXISTS private_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}',
                    sequence_id INTEGER NOT NULL DEFAULT 0,
                    event_time INTEGER NOT NULL DEFAULT 0,
                    schema_version TEXT NOT NULL DEFAULT '1.0'
                );

                CREATE INDEX IF NOT EXISTS idx_private_events_topic_id
                    ON private_events(topic, id);
                """
            )
            self._conn.commit()

    def _ensure_open(self) -> None:
        if self._closed:
            raise ForwardingError("SQLiteStateStore is closed")
