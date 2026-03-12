import logging
from pathlib import Path

import spdlog


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
        file_name="log_strategy_info.log",
        logger_name="hello",
        rotation_hour=0,
        rotation_minute=0,
        print_info=False,
    ):
        # 将所有非绝对路径的日志文件统一重定向到项目根目录 logs/ 下
        path = Path(file_name)
        if not path.is_absolute():
            # 去除 ./logs/ 或 ./  前缀，提取纯文件名
            name = file_name
            if file_name.startswith("./logs/"):
                name = file_name[len("./logs/") :]
            elif file_name.startswith("./"):
                name = file_name[len("./") :]
            file_name = str(Path(SpdLogManager._project_logs_dir) / name)
        self.file_name = file_name
        self.logger_name = logger_name
        self.rotation_hour = rotation_hour
        self.rotation_minute = rotation_minute
        self.print_info = print_info

    def create_logger(self):
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

        # 创建sinks
        if self.print_info:
            sinks = [
                spdlog.stdout_sink_st(),
                spdlog.daily_file_sink_st(self.file_name, self.rotation_hour, self.rotation_minute),
            ]
        else:
            sinks = [
                spdlog.daily_file_sink_st(self.file_name, self.rotation_hour, self.rotation_minute)
            ]

        # 创建logger并缓存
        logger = spdlog.SinkLogger(self.logger_name, sinks)
        SpdLogManager._logger_cache[key] = logger
        return logger
