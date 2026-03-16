"""Tests for the logger factory."""

from pathlib import Path
from unittest.mock import patch

from bt_api_py import logging_factory


class _FakeLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def warning(self, *args: object, **kwargs: object) -> None:
        self.messages.append(("warning", args, kwargs))

    def warn(self, *args: object, **kwargs: object) -> None:
        self.messages.append(("warn", args, kwargs))


def test_resolve_log_file_name_uses_env_dir(monkeypatch) -> None:
    monkeypatch.setenv("BT_API_LOG_DIR", "~/bt-api-logs")

    resolved = logging_factory._resolve_log_file_name("feed.log")

    assert resolved == str(Path("~/bt-api-logs").expanduser() / "feed.log")


def test_get_logger_sanitizes_custom_file_names(monkeypatch) -> None:
    created_kwargs: dict[str, object] = {}
    cache_key = ("folder/custom logger", False)
    logging_factory._logger_cache.pop(cache_key, None)

    class _FakeManager:
        def __init__(self, *, file_name: str, logger_name: str, print_info: bool) -> None:
            created_kwargs["file_name"] = file_name
            created_kwargs["logger_name"] = logger_name
            created_kwargs["print_info"] = print_info

        def create_logger(self) -> _FakeLogger:
            return _FakeLogger()

    monkeypatch.setenv("BT_API_LOG_DIR", "/tmp/bt-api-tests")

    with patch.object(logging_factory, "SpdLogManager", _FakeManager):
        logger = logging_factory.get_logger("folder/custom logger")

    assert isinstance(logger, logging_factory._LoggerProxy)
    assert created_kwargs["file_name"] == "/tmp/bt-api-tests/folder_custom_logger.log"
    assert created_kwargs["logger_name"] == "folder/custom logger"


def test_logger_proxy_supports_warn_and_warning() -> None:
    fake_logger = _FakeLogger()
    proxy = logging_factory._LoggerProxy(fake_logger)

    proxy.warning("first")
    proxy.warn("second")

    assert fake_logger.messages == [
        ("warning", ("first",), {}),
        ("warn", ("second",), {}),
    ]


def test_get_logger_cache_separates_print_info(monkeypatch) -> None:
    created: list[str] = []
    for cache_key in [("cache-check", False), ("cache-check", True)]:
        logging_factory._logger_cache.pop(cache_key, None)

    class _FakeManager:
        def __init__(self, *, file_name: str, logger_name: str, print_info: bool) -> None:
            created.append(f"{logger_name}:{print_info}")

        def create_logger(self) -> _FakeLogger:
            return _FakeLogger()

    with patch.object(logging_factory, "SpdLogManager", _FakeManager):
        logger_a = logging_factory.get_logger("cache-check", print_info=False)
        logger_b = logging_factory.get_logger("cache-check", print_info=False)
        logger_c = logging_factory.get_logger("cache-check", print_info=True)

    assert logger_a is logger_b
    assert logger_a is not logger_c
    assert created == ["cache-check:False", "cache-check:True"]
