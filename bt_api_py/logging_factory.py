"""
集中日志工厂 — 统一管理所有模块的 logger 创建
替代各模块分散的 SpdLogManager(...).create_logger() 调用

用法:
    from bt_api_py.logging_factory import get_logger
    logger = get_logger("feed")          # -> logs/feed.log
    logger = get_logger("event_bus")     # -> logs/event_bus.log
    logger = get_logger("api", print_info=True)  # 同时输出到控制台
"""

from bt_api_py.functions.log_message import SpdLogManager

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
_logger_cache: dict = {}


def get_logger(module: str, print_info: bool = False):
    """获取指定模块的 logger

    :param module: 模块标识，如 "api", "feed", "event_bus"，
                   或任意自定义名称（会自动创建 {module}.log）
    :param print_info: 是否同时输出到控制台
    :return: spdlog logger 实例
    """
    cache_key = (module, print_info)
    if cache_key in _logger_cache:
        return _logger_cache[cache_key]

    if module in _MODULE_LOG_MAP:
        file_name, logger_name = _MODULE_LOG_MAP[module]
    else:
        file_name = f"{module}.log"
        logger_name = module

    logger = SpdLogManager(
        file_name=file_name,
        logger_name=logger_name,
        print_info=print_info,
    ).create_logger()

    _logger_cache[cache_key] = logger
    return logger
