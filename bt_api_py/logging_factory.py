"""
集中日志工厂 — 统一管理所有模块的 logger 创建
替代各模块分散的 SpdLogManager(...).create_logger() 调用

用法:
    from bt_api_py.logging_factory import get_logger
    logger = get_logger("feed")          # -> logs/feed.log
    logger = get_logger("event_bus")     # -> logs/event_bus.log
    logger = get_logger("api", print_info=True)  # 同时输出到控制台
"""

import os
import threading
from pathlib import Path
from typing import Any

from bt_api_py.functions.log_message import SpdLogManager

__all__ = ["get_logger"]

# 预定义的模块日志名称映射: module_key -> (file_name, logger_name)
_MODULE_LOG_MAP = {
    "api": ("bt_api.log", "api"),
    "registry": ("bt_api_registry.log", "registry"),
    "feed": ("feed_data.log", "base_feed"),
    "event_bus": ("event_bus.log", "event_bus"),
    "http_client": ("http_client.log", "http_client"),
    "websocket": ("websocket.log", "websocket"),
}

# 缓存已创建的 logger，避免重复创建
_logger_cache: dict[tuple[str, bool], object] = {}
_logger_cache_lock = threading.Lock()


class _LoggerProxy:
    """Compatibility wrapper exposing both `warn` and `warning`."""

    def __init__(self, logger: object):
        self._logger = logger

    def warning(self, *args: Any, **kwargs: Any) -> None:
        warning_method = getattr(self._logger, "warning", None)
        if warning_method is not None:
            warning_method(*args, **kwargs)
            return
        warn_method = getattr(self._logger, "warn", None)
        if warn_method is not None:
            warn_method(*args, **kwargs)

    def warn(self, *args: Any, **kwargs: Any) -> None:
        warn_method = getattr(self._logger, "warn", None)
        if warn_method is not None:
            warn_method(*args, **kwargs)
            return
        warning_method = getattr(self._logger, "warning", None)
        if warning_method is not None:
            warning_method(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._logger, name)


def _resolve_log_file_name(file_name: str) -> str:
    """Resolve a log file name with optional environment-based log directory."""
    log_dir = os.getenv("BT_API_LOG_DIR")
    if not log_dir or os.path.isabs(file_name):
        return file_name

    return str(Path(log_dir).expanduser() / file_name)


def get_logger(module: str, print_info: bool = False):
    """获取指定模块的 logger

    :param module: 模块标识，如 "api", "feed", "event_bus"，
                   或任意自定义名称（会自动创建 {module}.log）
    :param print_info: 是否同时输出到控制台
    :return: spdlog logger 实例
    """
    cache_key = (module, print_info)
    with _logger_cache_lock:
        cached_logger = _logger_cache.get(cache_key)
        if cached_logger is not None:
            return cached_logger
        if module in _MODULE_LOG_MAP:
            file_name, logger_name = _MODULE_LOG_MAP[module]
        else:
            file_name = f"{module}.log"
            logger_name = module

        logger = _LoggerProxy(
            SpdLogManager(
                file_name=_resolve_log_file_name(file_name),
                logger_name=logger_name,
                print_info=print_info,
            ).create_logger()
        )
        _logger_cache[cache_key] = logger
        return logger
