"""Structured logging system for bt_api_py.

Provides correlation IDs, structured JSON logging, and integration with monitoring.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Context variables for correlation tracking
correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)


@dataclass
class LogEvent:
    """Structured log event."""

    timestamp: float = field(default_factory=time.time)
    level: str = "INFO"
    message: str = ""
    correlation_id: str | None = None
    request_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    exchange_name: str | None = None
    component: str = ""
    function: str = ""
    line_number: int | None = None
    duration_ms: float | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def __init__(
        self,
        include_extra_fields: bool = True,
        compact: bool = False,
    ) -> None:
        super().__init__()
        self.include_extra_fields = include_extra_fields
        self.compact = compact

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        # Create base event
        event = LogEvent(
            timestamp=record.created,
            level=record.levelname,
            message=record.getMessage(),
            correlation_id=correlation_id_var.get(),
            request_id=request_id_var.get(),
            user_id=user_id_var.get(),
            session_id=session_id_var.get(),
        )

        # Add context from record
        if hasattr(record, "exchange_name"):
            event.exchange_name = record.exchange_name
        if hasattr(record, "component"):
            event.component = record.component
        if hasattr(record, "function"):
            event.function = record.function
        if hasattr(record, "line_number"):
            event.line_number = record.line_number
        if hasattr(record, "duration_ms"):
            event.duration_ms = record.duration_ms

        # Add error information
        if record.exc_info and record.exc_info[0] is not None:
            event.error = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info) if not self.compact else None,
            }

        # Add metadata
        if self.include_extra_fields:
            for key, value in record.__dict__.items():
                if key not in {
                    "name",
                    "msg",
                    "args",
                    "levelname",
                    "levelno",
                    "pathname",
                    "filename",
                    "module",
                    "lineno",
                    "funcName",
                    "created",
                    "msecs",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "processName",
                    "process",
                    "getMessage",
                    "exc_info",
                    "exc_text",
                    "stack_info",
                    "exchange_name",
                    "component",
                    "function",
                    "line_number",
                    "duration_ms",
                }:
                    event.metadata[key] = value

        # Convert to JSON
        if self.compact:
            return json.dumps(asdict(event), separators=(",", ":"), default=str)
        else:
            return json.dumps(asdict(event), indent=2, default=str)


class StructuredLogger:
    """Structured logger with correlation tracking."""

    def __init__(self, name: str) -> None:
        self.logger = logging.getLogger(name)
        self.name = name

    def _log_with_context(
        self,
        level: int,
        message: str,
        *,
        exchange_name: str | None = None,
        component: str | None = None,
        function: str | None = None,
        line_number: int | None = None,
        duration_ms: float | None = None,
        **kwargs: Any,
    ) -> None:
        """Log message with additional context."""
        extra: dict[str, Any] = {}
        if exchange_name:
            extra["exchange_name"] = exchange_name
        if component:
            extra["component"] = component
        if function:
            extra["function"] = function
        if line_number is not None:
            extra["line_number"] = line_number
        if duration_ms is not None:
            extra["duration_ms"] = duration_ms

        # Add any additional kwargs to extra
        extra.update(kwargs)

        self.logger.log(level, message, extra=extra)

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message."""
        self._log_with_context(logging.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log info message."""
        self._log_with_context(logging.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log warning message."""
        self._log_with_context(logging.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log error message."""
        self._log_with_context(logging.ERROR, message, **kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        """Log critical message."""
        self._log_with_context(logging.CRITICAL, message, **kwargs)

    def exception(self, message: str, **kwargs: Any) -> None:
        """Log exception message."""
        kwargs["exc_info"] = True
        self.error(message, **kwargs)

    def api_request(
        self,
        method: str,
        endpoint: str,
        exchange_name: str,
        status_code: int | None = None,
        duration_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        """Log API request."""
        message = f"API {method} {endpoint}"
        if status_code is not None:
            message += f" -> {status_code}"
        if error:
            message += f" (ERROR: {error})"

        metadata: dict[str, Any] = {
            "method": method,
            "endpoint": endpoint,
            "status_code": status_code,
        }
        if error:
            metadata["error"] = error

        self._log_with_context(
            logging.INFO if not error else logging.ERROR,
            message,
            exchange_name=exchange_name,
            component="api",
            duration_ms=duration_ms,
            **metadata,
        )

    def order_event(
        self,
        event_type: str,
        exchange_name: str,
        symbol: str,
        order_id: str | None = None,
        side: str | None = None,
        quantity: float | None = None,
        price: float | None = None,
        status: str | None = None,
        error: str | None = None,
    ) -> None:
        """Log order event."""
        message = f"Order {event_type}: {symbol}"
        if side and quantity is not None:
            message += f" {side} {quantity}"
        if price is not None:
            message += f" @ {price}"
        if status:
            message += f" ({status})"
        if order_id:
            message += f" [{order_id}]"
        if error:
            message += f" ERROR: {error}"

        metadata_dict: dict[str, Any] = {
            "event_type": event_type,
            "symbol": symbol,
            "order_id": order_id,
            "side": side,
            "quantity": quantity,
            "price": price,
            "status": status,
        }
        if error:
            metadata_dict["error"] = error

        level = logging.ERROR if error else logging.INFO
        self._log_with_context(
            level, message, exchange_name=exchange_name, component="order", **metadata_dict
        )

    def connection_event(
        self,
        event_type: str,
        exchange_name: str,
        connection_type: str,  # "rest" or "websocket"
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log connection event."""
        message = f"Connection {event_type}: {exchange_name} {connection_type}"
        if error:
            message += f" ERROR: {error}"

        metadata_conn: dict[str, Any] = {
            "event_type": event_type,
            "connection_type": connection_type,
        }
        if error:
            metadata_conn["error"] = error
        if details:
            metadata_conn["details"] = details

        level = logging.ERROR if error else logging.INFO
        self._log_with_context(
            level, message, exchange_name=exchange_name, component="connection", **metadata_conn
        )

    def market_data_event(
        self,
        event_type: str,
        exchange_name: str,
        symbol: str | None = None,
        data_type: str | None = None,  # "ticker", "depth", "trade", "kline"
        count: int | None = None,
        lag_ms: float | None = None,
    ) -> None:
        """Log market data event."""
        message = f"Market Data {event_type}: {exchange_name}"
        if symbol:
            message += f" {symbol}"
        if data_type:
            message += f" ({data_type})"
        if count is not None:
            message += f" count={count}"
        if lag_ms is not None:
            message += f" lag={lag_ms:.1f}ms"

        metadata_md: dict[str, Any] = {
            "event_type": event_type,
            "data_type": data_type,
            "symbol": symbol,
            "count": count,
            "lag_ms": lag_ms,
        }

        self._log_with_context(
            logging.DEBUG,
            message,
            exchange_name=exchange_name,
            component="market_data",
            **metadata_md,
        )


class LoggingManager:
    """Manages logging configuration for the entire application."""

    def __init__(self) -> None:
        self.configured = False
        self.loggers: dict[str, StructuredLogger] = {}

    def configure(
        self,
        level: str | int = logging.INFO,
        log_file: str | Path | None = None,
        max_file_size: int = 100 * 1024 * 1024,  # 100MB
        backup_count: int = 5,
        console_output: bool = True,
        json_format: bool = True,
        compact_json: bool = False,
        enable_rotation: bool = True,
    ) -> None:
        """Configure logging for the application."""
        root_logger = logging.getLogger()
        self._close_handlers(root_logger)
        root_logger.setLevel(level)

        # Create formatter
        formatter: logging.Formatter
        if json_format:
            formatter = StructuredFormatter(compact=compact_json)
        else:
            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

        # Add console handler
        if console_output:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            root_logger.addHandler(console_handler)

        # Add file handler
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler: logging.Handler
            if enable_rotation:
                file_handler = logging.handlers.RotatingFileHandler(
                    log_path,
                    maxBytes=max_file_size,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
            else:
                file_handler = logging.FileHandler(
                    log_path,
                    encoding="utf-8",
                )

            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)

        self.configured = True

    @staticmethod
    def _close_handlers(logger: logging.Logger) -> None:
        """Detach and close existing handlers before reconfiguration."""
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def get_logger(self, name: str) -> StructuredLogger:
        """Get structured logger."""
        if name not in self.loggers:
            self.loggers[name] = StructuredLogger(name)
        return self.loggers[name]

    def set_correlation_id(self, correlation_id: str) -> None:
        """Set correlation ID for current context."""
        correlation_id_var.set(correlation_id)

    def set_request_id(self, request_id: str) -> None:
        """Set request ID for current context."""
        request_id_var.set(request_id)

    def set_user_id(self, user_id: str) -> None:
        """Set user ID for current context."""
        user_id_var.set(user_id)

    def set_session_id(self, session_id: str) -> None:
        """Set session ID for current context."""
        session_id_var.set(session_id)

    def get_correlation_id(self) -> str | None:
        """Get correlation ID from current context."""
        return correlation_id_var.get()

    def generate_correlation_id(self) -> str:
        """Generate a new correlation ID."""
        return str(uuid.uuid4())

    def with_correlation_id(self, correlation_id: str) -> _CorrelationIdContext:
        """Context manager for setting correlation ID."""
        return _CorrelationIdContext(correlation_id)


class _CorrelationIdContext:
    """Context manager for correlation ID."""

    def __init__(self, correlation_id: str) -> None:
        self.correlation_id = correlation_id
        self.token: Token[str | None] | None = None

    def __enter__(self) -> _CorrelationIdContext:
        self.token = correlation_id_var.set(self.correlation_id)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.token is not None:
            correlation_id_var.reset(self.token)


# Global logging manager
_logging_manager: LoggingManager | None = None


def get_logging_manager() -> LoggingManager:
    """Get the global logging manager."""
    global _logging_manager
    if _logging_manager is None:
        _logging_manager = LoggingManager()
    return _logging_manager


def get_logger(name: str) -> StructuredLogger:
    """Get structured logger."""
    return get_logging_manager().get_logger(name)


def configure_logging(
    *,
    level: str | int = logging.INFO,
    log_file: str | Path | None = None,
    max_file_size: int = 100 * 1024 * 1024,
    backup_count: int = 5,
    console_output: bool = True,
    json_format: bool = True,
    compact_json: bool = False,
    enable_rotation: bool = True,
) -> None:
    """Configure logging for the application."""
    get_logging_manager().configure(
        level=level,
        log_file=log_file,
        max_file_size=max_file_size,
        backup_count=backup_count,
        console_output=console_output,
        json_format=json_format,
        compact_json=compact_json,
        enable_rotation=enable_rotation,
    )


def setup_logging_for_production(
    log_file: str = "logs/bt_api_py.log",
    level: str = "INFO",
) -> None:
    """Setup logging for production environment."""
    configure_logging(
        level=getattr(logging, level.upper()),
        log_file=log_file,
        console_output=False,
        json_format=True,
        compact_json=True,
        enable_rotation=True,
    )


def setup_logging_for_development(
    level: str = "DEBUG",
    console_output: bool = True,
) -> None:
    """Setup logging for development environment."""
    configure_logging(
        level=getattr(logging, level.upper()),
        console_output=console_output,
        json_format=False,
    )
