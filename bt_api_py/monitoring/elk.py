"""
ELK stack integration for bt_api_py.

Provides Elasticsearch, Logstash, and Kibana integration for log aggregation and analysis.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, cast

from bt_api_base.logging_factory import get_logger

# LogEvent type - using Any for now as the original LogEvent class doesn't exist
LogEvent = Any

logger = get_logger("monitoring")


class ElasticsearchClient:
    """Simple Elasticsearch client for log shipping."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 9200,
        index_prefix: str = "bt_api_py",
        username: str | None = None,
        password: str | None = None,
        use_ssl: bool = False,
        verify_certs: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.index_prefix = index_prefix
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.verify_certs = verify_certs
        self._session: Any = None

    async def connect(self) -> None:
        """Connect to Elasticsearch."""
        try:
            import aiohttp
        except ImportError:
            raise ImportError("aiohttp is required for Elasticsearch integration") from None

        # Build URL
        protocol = "https" if self.use_ssl else "http"
        base_url = f"{protocol}://{self.host}:{self.port}"

        # Create session with authentication
        auth = None
        if self.username and self.password:
            auth = aiohttp.BasicAuth(self.username, self.password)

        connector = aiohttp.TCPConnector(ssl=self.verify_certs if self.use_ssl else False)

        self._session = aiohttp.ClientSession(base_url=base_url, auth=auth, connector=connector)

        # Test connection
        await self._test_connection()

    async def disconnect(self) -> None:
        """Disconnect from Elasticsearch."""
        if self._session:
            await self._session.close()
            self._session = None

    async def _test_connection(self) -> None:
        """Test Elasticsearch connection."""
        if not self._session:
            raise RuntimeError("Not connected to Elasticsearch")

        try:
            async with self._session.get("/") as response:
                if response.status != 200:
                    raise RuntimeError(f"Elasticsearch connection failed: {response.status}")
        except Exception as e:
            raise RuntimeError(f"Cannot connect to Elasticsearch: {e}") from e

    async def index_document(
        self,
        index_name: str,
        document: dict[str, Any],
        doc_id: str | None = None,
    ) -> dict[str, Any]:
        """Index a document in Elasticsearch."""
        if not self._session:
            raise RuntimeError("Not connected to Elasticsearch")

        url = f"/{index_name}/_doc"
        if doc_id:
            url += f"/{doc_id}"

        async with self._session.post(url, json=document) as response:
            if response.status not in (200, 201):
                error_text = await response.text()
                raise RuntimeError(f"Failed to index document: {response.status} - {error_text}")

            result = await response.json()
            return cast("dict[str, Any]", result)

    async def create_index_template(self) -> None:
        """Create index template for bt_api_py logs."""
        if not self._session:
            raise RuntimeError("Not connected to Elasticsearch")

        template = {
            "index_patterns": [f"{self.index_prefix}-*"],
            "template": {
                "settings": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "index.refresh_interval": "5s",
                    "index.max_result_window": 50000,
                },
                "mappings": {
                    "properties": {
                        "timestamp": {"type": "date"},
                        "level": {"type": "keyword"},
                        "message": {"type": "text"},
                        "correlation_id": {"type": "keyword"},
                        "request_id": {"type": "keyword"},
                        "user_id": {"type": "keyword"},
                        "session_id": {"type": "keyword"},
                        "exchange_name": {"type": "keyword"},
                        "component": {"type": "keyword"},
                        "function": {"type": "keyword"},
                        "line_number": {"type": "integer"},
                        "duration_ms": {"type": "float"},
                        "error": {
                            "properties": {
                                "type": {"type": "keyword"},
                                "message": {"type": "text"},
                                "traceback": {"type": "text"},
                            }
                        },
                        "metadata": {"type": "object"},
                    }
                },
            },
        }

        url = f"/_index_template/{self.index_prefix}-template"
        async with self._session.put(url, json=template) as response:
            if response.status not in (200, 201):
                error_text = await response.text()
                raise RuntimeError(
                    f"Failed to create index template: {response.status} - {error_text}"
                )


class LogstashHandler(logging.Handler):
    """Log handler that sends logs to Logstash."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5000,
        transport: str = "tcp",  # tcp, udp, or http
        **kwargs,
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.transport = transport
        self._writer: Any = None
        self._kwargs = kwargs

    async def connect(self) -> None:
        """Connect to Logstash."""
        if self.transport == "tcp":
            try:
                import aiohttp
            except ImportError:
                raise ImportError("aiohttp is required for HTTP Logstash transport") from None

            self._writer = aiohttp.ClientSession()
        elif self.transport == "udp":
            # UDP transport would need different implementation
            raise NotImplementedError("UDP transport not yet implemented")
        else:
            raise ValueError(f"Unsupported transport: {self.transport}")

    async def disconnect(self) -> None:
        """Disconnect from Logstash."""
        if self._writer:
            await self._writer.close()
            self._writer = None

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record."""
        try:
            # Convert record to JSON
            log_data = self.format_to_logstash(record)

            # Send asynchronously
            asyncio.create_task(self._send_log(log_data))
        except Exception:
            self.handleError(record)

    def format_to_logstash(self, record: logging.LogRecord) -> dict[str, Any]:
        """Format log record for Logstash."""
        # Create base log event
        log_data: dict[str, Any] = {
            "@timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "thread": record.thread,
            "thread_name": record.threadName,
            "process": record.process,
            "process_name": record.processName,
        }

        # Add context variables
        from bt_api_py.logging_system import (
            correlation_id_var,
            request_id_var,
            session_id_var,
            user_id_var,
        )

        correlation_id = correlation_id_var.get()
        if correlation_id:
            log_data["correlation_id"] = correlation_id

        request_id = request_id_var.get()
        if request_id:
            log_data["request_id"] = request_id

        user_id = user_id_var.get()
        if user_id:
            log_data["user_id"] = user_id

        session_id = session_id_var.get()
        if session_id:
            log_data["session_id"] = session_id

        # Add exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": logging.Formatter().formatException(record.exc_info),
            }

        # Add file and line info
        log_data["file"] = record.pathname
        log_data["line"] = record.lineno
        log_data["function"] = record.funcName

        # Add extra fields
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
            }:
                log_data[key] = value

        return log_data

    async def _send_log(self, log_data: dict[str, Any]) -> None:
        """Send log data to Logstash."""
        if not self._writer:
            return

        try:
            if self.transport == "tcp":
                # Send via HTTP
                url = f"http://{self.host}:{self.port}"
                async with self._writer.post(url, json=log_data) as response:
                    if response.status >= 400:
                        # Log error but don't raise to avoid recursion
                        logger.warning(f"Failed to send log to Logstash: {response.status}")

        except Exception as e:
            # Log at debug to avoid recursion (don't ship this to Logstash)
            logger.debug("Failed to send log to Logstash: %s", e, exc_info=True)


class ELKIntegration:
    """ELK stack integration manager."""

    def __init__(
        self,
        elasticsearch_host: str = "localhost",
        elasticsearch_port: int = 9200,
        elasticsearch_username: str | None = None,
        elasticsearch_password: str | None = None,
        elasticsearch_index_prefix: str = "bt_api_py",
        logstash_host: str = "localhost",
        logstash_port: int = 5000,
        logstash_transport: str = "tcp",
    ) -> None:
        self.elasticsearch_client = ElasticsearchClient(
            host=elasticsearch_host,
            port=elasticsearch_port,
            index_prefix=elasticsearch_index_prefix,
            username=elasticsearch_username,
            password=elasticsearch_password,
        )

        self.logstash_handler = LogstashHandler(
            host=logstash_host,
            port=logstash_port,
            transport=logstash_transport,
        )

        self._connected = False

    async def connect(self) -> None:
        """Connect to ELK stack."""
        await self.elasticsearch_client.connect()
        await self.elasticsearch_client.create_index_template()
        await self.logstash_handler.connect()

        # Add Logstash handler to root logger
        logging.getLogger().addHandler(self.logstash_handler)

        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect from ELK stack."""
        if self._connected:
            logging.getLogger().removeHandler(self.logstash_handler)

        await self.elasticsearch_client.disconnect()
        await self.logstash_handler.disconnect()

        self._connected = False

    async def send_log_event(self, event: LogEvent) -> None:
        """Send a log event directly to Elasticsearch."""
        if not self._connected:
            raise RuntimeError("Not connected to ELK stack")

        # Convert event to document
        doc: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(event.timestamp).isoformat(),
            "level": event.level,
            "message": event.message,
        }

        # Add optional fields
        if event.correlation_id:
            doc["correlation_id"] = event.correlation_id
        if event.request_id:
            doc["request_id"] = event.request_id
        if event.user_id:
            doc["user_id"] = event.user_id
        if event.session_id:
            doc["session_id"] = event.session_id
        if event.exchange_name:
            doc["exchange_name"] = event.exchange_name
        if event.component:
            doc["component"] = event.component
        if event.function:
            doc["function"] = event.function
        if event.line_number is not None:
            doc["line_number"] = event.line_number
        if event.duration_ms is not None:
            doc["duration_ms"] = event.duration_ms
        if event.error is not None:
            doc["error"] = event.error
        if event.metadata:
            doc["metadata"] = event.metadata

        # Determine index name (daily index)
        date_str = datetime.now().strftime("%Y.%m.%d")
        index_name = f"{self.elasticsearch_client.index_prefix}-{date_str}"

        await self.elasticsearch_client.index_document(index_name, doc)

    async def search_logs(
        self,
        query: str | None = None,
        level: str | None = None,
        exchange_name: str | None = None,
        component: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        size: int = 100,
    ) -> dict[str, Any]:
        """Search logs in Elasticsearch."""
        if not self._connected:
            raise RuntimeError("Not connected to ELK stack")

        # Build Elasticsearch query
        es_query: dict[str, Any] = {"query": {"bool": {"must": []}}}

        # Add filters
        if level:
            es_query["query"]["bool"]["must"].append({"term": {"level": level}})

        if exchange_name:
            es_query["query"]["bool"]["must"].append({"term": {"exchange_name": exchange_name}})

        if component:
            es_query["query"]["bool"]["must"].append({"term": {"component": component}})

        # Add time range
        if start_time or end_time:
            time_range = {}
            if start_time:
                time_range["gte"] = start_time.isoformat()
            if end_time:
                time_range["lte"] = end_time.isoformat()
            es_query["query"]["bool"]["must"].append({"range": {"timestamp": time_range}})

        # Add text query
        if query:
            es_query["query"]["bool"]["must"].append(
                {
                    "multi_match": {
                        "query": query,
                        "fields": ["message", "error.message", "metadata.*"],
                    }
                }
            )

        # If no filters, match all
        if not es_query["query"]["bool"]["must"]:
            es_query["query"] = {"match_all": {}}

        # Add sort and size
        es_query.update(
            {
                "sort": [{"timestamp": {"order": "desc"}}],
                "size": size,
            }
        )

        # Search across all indices
        index_pattern = f"{self.elasticsearch_client.index_prefix}-*"
        session = self.elasticsearch_client._session
        if session is None:
            raise RuntimeError("Not connected to Elasticsearch")

        async with session.post(f"/{index_pattern}/_search", json=es_query) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"Search failed: {response.status} - {error_text}")

            result = await response.json()
            return cast("dict[str, Any]", result)


# Global ELK integration
_elk_integration: ELKIntegration | None = None


async def setup_elk_integration(**kwargs) -> ELKIntegration:
    """Setup ELK stack integration."""
    global _elk_integration
    if _elk_integration is None:
        _elk_integration = ELKIntegration(**kwargs)
    await _elk_integration.connect()
    return _elk_integration


async def get_elk_integration() -> ELKIntegration | None:
    """Get the global ELK integration."""
    return _elk_integration


async def shutdown_elk_integration() -> None:
    """Shutdown ELK stack integration."""
    global _elk_integration
    if _elk_integration:
        await _elk_integration.disconnect()
        _elk_integration = None
