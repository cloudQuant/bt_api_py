"""日志管理模块 — 支持 spdlog 高性能日志和标准库 logging 回退"""

import logging
from pathlib import Path
from typing import Any

__all__ = ["SpdLogManager", "_HAS_SPDLOG"]

_LOGGER = logging.getLogger(__name__)

try:
    import spdlog

    _HAS_SPDLOG = hasattr(spdlog, "daily_file_sink_st")
except ImportError:
    spdlog = None  # type: ignore[assignment]
    _HAS_SPDLOG = False
except Exception as e:  # DLL load failure, segfault guard
    spdlog = None  # type: ignore[assignment]
    _HAS_SPDLOG = False
    _LOGGER.debug("spdlog import failed, using stdlib logging fallback: %s", e)


def _get_project_logs_dir() -> str:
    """获取项目根目录下的 logs/ 文件夹绝对路径。
    项目根目录 = bt_api_py 包的上一级目录。
    """
    try:
        import bt_api_py

        pkg_path = Path(bt_api_py.__file__).resolve().parent
        return str(pkg_path.parent / "logs")
    except Exception as e:
        # Fallback when package context unavailable (e.g. during early import)
        logging.getLogger(__name__).debug("Using cwd for logs dir: %s", e)
        return str(Path.cwd() / "logs")


class SpdLogManager:
    _logger_cache: dict[tuple[str, str, int, int, bool], object] = {}
    _project_logs_dir = _get_project_logs_dir()

    def __init__(
        self,
        file_name: str = "log_strategy_info.log",
        logger_name: str = "hello",
        rotation_hour: int = 0,
        rotation_minute: int = 0,
        print_info: bool = False,
    ) -> None:
        # 将所有非绝对路径的日志文件统一重定向到项目根目录 logs/ 下
        self.file_name = self._normalize_file_name(file_name)
        self.logger_name = logger_name
        self.rotation_hour = rotation_hour
        self.rotation_minute = rotation_minute
        self.print_info = print_info

    @classmethod
    def _normalize_file_name(cls, file_name: str) -> str:
        """Normalize relative log file names into the project logs directory."""
        path = Path(file_name)
        if path.is_absolute():
            return file_name

        name = file_name
        if file_name.startswith("./logs/"):
            name = file_name[len("./logs/") :]
        elif file_name.startswith("./"):
            name = file_name[len("./") :]

        return str(Path(cls._project_logs_dir) / name)

    def create_logger(self) -> Any:
        # 创建缓存键
        key = (
            self.file_name,
            self.logger_name,
            self.rotation_hour,
            self.rotation_minute,
            self.print_info,
        )
        # 如果已存在，直接返回缓存的logger
        if key in SpdLogManager._logger_cache:
            return SpdLogManager._logger_cache[key]

        # 确保日志目录存在
        log_dir = Path(self.file_name).parent
        if str(log_dir):
            log_dir.mkdir(parents=True, exist_ok=True)

        if not _HAS_SPDLOG:
            # Fallback to stdlib logging when spdlog is unavailable
            logger = logging.getLogger(self.logger_name)
            if not logger.handlers:
                fh = logging.FileHandler(self.file_name, encoding="utf-8")
                formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
                fh.setFormatter(formatter)
                logger.addHandler(fh)
                if self.print_info:
                    stream_handler = logging.StreamHandler()
                    stream_handler.setFormatter(formatter)
                    logger.addHandler(stream_handler)
                logger.setLevel(logging.DEBUG)
                logger.propagate = False
            SpdLogManager._logger_cache[key] = logger
            return logger

        # 创建sinks
        if self.print_info:
            sinks = [
                spdlog.stdout_sink_st(),  # type: ignore[union-attr]
                spdlog.daily_file_sink_st(self.file_name, self.rotation_hour, self.rotation_minute),  # type: ignore[union-attr]
            ]
        else:
            sinks = [
                spdlog.daily_file_sink_st(self.file_name, self.rotation_hour, self.rotation_minute)  # type: ignore[union-attr]
            ]

        # 创建logger并缓存
        logger = spdlog.SinkLogger(self.logger_name, sinks)  # type: ignore[union-attr]
        SpdLogManager._logger_cache[key] = logger
        return logger
