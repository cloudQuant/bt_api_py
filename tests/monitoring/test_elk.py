"""Tests for monitoring/elk.py."""

import asyncio
import logging

import pytest

from bt_api_py.monitoring import elk


class TestElk:
    """Tests for ELK integration."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.monitoring import elk

        assert elk is not None


class TestElasticsearchClient:
    """Tests for ElasticsearchClient."""

    def test_init_defaults(self):
        """Test initialization with default values."""
        client = elk.ElasticsearchClient()

        assert client.host == "localhost"
        assert client.port == 9200
        assert client.index_prefix == "bt_api_py"
        assert client.username is None
        assert client.password is None
        assert client.use_ssl is False
        assert client.verify_certs is True
        assert client._session is None

    def test_init_custom(self):
        """Test initialization with custom values."""
        client = elk.ElasticsearchClient(
            host="elasticsearch.example.com",
            port=9201,
            index_prefix="my_app",
            username="admin",
            password="secret",
            use_ssl=True,
            verify_certs=False,
        )

        assert client.host == "elasticsearch.example.com"
        assert client.port == 9201
        assert client.index_prefix == "my_app"
        assert client.username == "admin"
        assert client.password == "secret"
        assert client.use_ssl is True
        assert client.verify_certs is False

    def test_not_connected_error(self):
        """Test that operations fail when not connected."""
        client = elk.ElasticsearchClient()

        async def test_connection():
            with pytest.raises(RuntimeError, match="Not connected"):
                await client._test_connection()

        asyncio.run(test_connection())

        async def test_index():
            with pytest.raises(RuntimeError, match="Not connected"):
                await client.index_document("test", {"key": "value"})

        asyncio.run(test_index())

        async def test_template():
            with pytest.raises(RuntimeError, match="Not connected"):
                await client.create_index_template()

        asyncio.run(test_template())


class TestLogstashHandler:
    """Tests for LogstashHandler."""

    def test_init_defaults(self):
        """Test initialization with default values."""
        handler = elk.LogstashHandler()

        assert handler.host == "localhost"
        assert handler.port == 5000
        assert handler.transport == "tcp"
        assert handler._writer is None

    def test_init_custom(self):
        """Test initialization with custom values."""
        handler = elk.LogstashHandler(
            host="logstash.example.com",
            port=5001,
            transport="tcp",
        )

        assert handler.host == "logstash.example.com"
        assert handler.port == 5001

    def test_format_to_logstash(self):
        """Test formatting log record for Logstash."""
        handler = elk.LogstashHandler()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.funcName = "test_function"

        log_data = handler.format_to_logstash(record)

        assert log_data["level"] == "INFO"
        assert log_data["message"] == "Test message"
        assert log_data["logger"] == "test.logger"
        assert log_data["line"] == 42
        assert log_data["function"] == "test_function"
        assert "@timestamp" in log_data

    def test_format_to_logstash_with_exception(self):
        """Test formatting log record with exception info."""
        handler = elk.LogstashHandler()

        try:
            raise ValueError("Test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        log_data = handler.format_to_logstash(record)

        assert "exception" in log_data
        assert log_data["exception"]["type"] == "ValueError"
        assert log_data["exception"]["message"] == "Test error"

    def test_emit_creates_task(self):
        """Test that emit creates async task."""
        handler = elk.LogstashHandler()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/path/to/file.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        # Should not raise even when not connected
        handler.emit(record)

    def test_udp_transport_not_implemented(self):
        """Test that UDP transport raises NotImplementedError."""
        handler = elk.LogstashHandler(transport="udp")

        async def test_connect():
            with pytest.raises(NotImplementedError, match="UDP transport"):
                await handler.connect()

        asyncio.run(test_connect())

    def test_invalid_transport(self):
        """Test that invalid transport raises ValueError."""
        handler = elk.LogstashHandler(transport="invalid")

        async def test_connect():
            with pytest.raises(ValueError, match="Unsupported transport"):
                await handler.connect()

        asyncio.run(test_connect())


class TestELKIntegration:
    """Tests for ELKIntegration."""

    def test_init_defaults(self):
        """Test initialization with default values."""
        integration = elk.ELKIntegration()

        assert integration.elasticsearch_client is not None
        assert integration.logstash_handler is not None
        assert integration._connected is False

    def test_init_custom(self):
        """Test initialization with custom values."""
        integration = elk.ELKIntegration(
            elasticsearch_host="es.example.com",
            elasticsearch_port=9201,
            elasticsearch_username="admin",
            elasticsearch_password="secret",
            elasticsearch_index_prefix="my_app",
            logstash_host="ls.example.com",
            logstash_port=5001,
            logstash_transport="tcp",
        )

        assert integration.elasticsearch_client.host == "es.example.com"
        assert integration.elasticsearch_client.port == 9201
        assert integration.elasticsearch_client.username == "admin"
        assert integration.elasticsearch_client.password == "secret"
        assert integration.elasticsearch_client.index_prefix == "my_app"
        assert integration.logstash_handler.host == "ls.example.com"
        assert integration.logstash_handler.port == 5001

    def test_not_connected_operations_fail(self):
        """Test that operations fail when not connected."""
        from bt_api_py.logging_system import LogEvent

        integration = elk.ELKIntegration()

        event = LogEvent(
            timestamp=1234567890.0,
            level="INFO",
            message="Test message",
        )

        async def test_send():
            with pytest.raises(RuntimeError, match="Not connected to ELK"):
                await integration.send_log_event(event)

        asyncio.run(test_send())

        async def test_search():
            with pytest.raises(RuntimeError, match="Not connected to ELK"):
                await integration.search_logs()

        asyncio.run(test_search())


class TestGlobalFunctions:
    """Tests for global ELK functions."""

    def test_get_elk_integration_returns_none_initially(self):
        """Test that get_elk_integration returns None before setup."""
        # Reset global state
        elk._elk_integration = None

        async def test_get():
            result = await elk.get_elk_integration()
            assert result is None

        asyncio.run(test_get())

    def test_shutdown_when_none(self):
        """Test that shutdown works when integration is None."""
        elk._elk_integration = None

        async def test_shutdown():
            # Should not raise
            await elk.shutdown_elk_integration()

        asyncio.run(test_shutdown())
