# test_my_websocket_app.py
import pytest
from unittest.mock import MagicMock, patch
from bt_api_py.feeds.my_websocket_app import MyWebsocketApp  # 替换为实际模块名
import datetime
import time
import ssl
import threading

# Mock dependencies
@pytest.fixture
def mock_data_queue():
    return MagicMock()

@pytest.fixture
def mock_logger():
    return MagicMock()

@pytest.fixture
def mock_exchange_data():
    exchange_data = MagicMock()
    exchange_data.get_wss_url.return_value = "wss://mock.url"
    return exchange_data

@pytest.fixture
def websocket_app(mock_data_queue, mock_exchange_data, mock_logger):
    with patch('bt_api_py.functions.log_message.SpdLogManager') as mock_log_manager:
        mock_log_manager.return_value.create_logger.return_value = mock_logger
        app = MyWebsocketApp(mock_data_queue, exchange_data=mock_exchange_data)
        return app

def test_initialization(websocket_app, mock_data_queue, mock_exchange_data, mock_logger):
    assert websocket_app.data_queue == mock_data_queue
    assert websocket_app.wss_url == "wss://mock.url"
    assert websocket_app.ping_interval == 10
    assert websocket_app.ping_timeout == 5
    assert websocket_app.sslopt == {'cert_reqs': ssl.CERT_NONE}
    assert websocket_app._running_flag is False
    assert websocket_app._restart_flag is True
    assert isinstance(websocket_app.process, threading.Thread)
    assert websocket_app.process.daemon is True

def test_get_timestamp(websocket_app):
    time_str = "2023-10-01T12:34:56.789Z"
    expected_timestamp = 1696134896789  # 根据时间字符串计算的预期时间戳
    assert websocket_app.get_timestamp(time_str) == expected_timestamp

def test_subscribe(websocket_app):
    websocket_app.ws = MagicMock()
    websocket_app._params.get_wss_path.return_value = "mock_path"
    websocket_app.subscribe()
    websocket_app.ws.send.assert_called_once_with("mock_path")

def test_on_open(websocket_app, mock_logger):
    mock_ws = MagicMock()
    websocket_app.open_rsp = MagicMock()
    websocket_app.on_open(mock_ws)
    websocket_app.open_rsp.assert_called_once()
    assert websocket_app._running_flag is True

def test_stop(websocket_app):
    # Case 1: ws is not None
    websocket_app.ws = MagicMock()
    assert websocket_app.ws is not None, "websocket_app.ws should not be None"
    websocket_app.stop()
    # websocket_app.ws.close.assert_called_once()  # Verify close was called
    assert websocket_app._restart_flag is False  # Verify _restart_flag was set to False
    assert websocket_app.ws is None  # Verify ws was set to None

    # Case 2: ws is None
    websocket_app.ws = None
    websocket_app.stop()  # Should not raise any errors
    assert websocket_app._restart_flag is False  # Verify _restart_flag was set to False
    assert websocket_app.ws is None  # Verify ws remains None

def test_on_message(websocket_app, mock_logger):
    mock_ws = MagicMock()
    message = "test_message"
    websocket_app.message_rsp = MagicMock()
    websocket_app.on_message(mock_ws, message)
    websocket_app.message_rsp.assert_called_once_with(message)

def test_on_error(websocket_app, mock_logger):
    mock_ws = MagicMock()
    error = "test_error"
    websocket_app.error_rsp = MagicMock()
    websocket_app.on_error(mock_ws, error)
    websocket_app.error_rsp.assert_called_once_with(f'error: {error}')

def test_on_close(websocket_app, mock_logger):
    mock_ws = MagicMock()
    close_status_code = 1000
    close_msg = "normal closure"
    websocket_app.close_rsp = MagicMock()
    websocket_app.on_close(mock_ws, close_status_code, close_msg)
    websocket_app.close_rsp.assert_called_once_with(websocket_app._restart_flag)
    assert websocket_app._running_flag is False


def test_restart(websocket_app, mock_logger):
    websocket_app.stop = MagicMock()
    websocket_app.start = MagicMock()
    websocket_app.restart()
    websocket_app.stop.assert_called_once()
    websocket_app.start.assert_called_once()


def test_start(websocket_app, mock_logger):
    # Mock the entire process object
    websocket_app.process = MagicMock()

    # Simulate the _running_flag being set to True after a short delay
    def set_running_flag():
        time.sleep(0.1)  # Simulate a short delay
        websocket_app._running_flag = True

    # Start the flag-setting thread
    flag_thread = threading.Thread(target=set_running_flag)
    flag_thread.start()

    # Call the start method
    websocket_app.start()

    # Verify that the process thread was started
    # websocket_app.process.start.assert_called_once()

    # Wait for the flag-setting thread to finish
    flag_thread.join()

