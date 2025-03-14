import spdlog
import os


class SpdLogManager(object):
    _logger_cache = {}

    def __init__(self, file_name="log_strategy_info.log", logger_name="hello", rotation_hour=0, rotation_minute=0,
                 print_info=False):
        self.file_name = file_name
        self.logger_name = logger_name
        self.rotation_hour = rotation_hour
        self.rotation_minute = rotation_minute
        self.print_info = print_info

    def create_logger(self):
        # 创建缓存键
        key = (self.file_name, self.logger_name, self.rotation_hour, self.rotation_minute, self.print_info)
        # 如果已存在，直接返回缓存的logger
        if key in SpdLogManager._logger_cache:
            return SpdLogManager._logger_cache[key]

        # 确保日志目录存在
        log_dir = os.path.dirname(self.file_name)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        # 创建sinks
        if self.print_info:
            sinks = [
                spdlog.stdout_sink_st(),
                spdlog.daily_file_sink_st(self.file_name, self.rotation_hour, self.rotation_minute)
            ]
        else:
            sinks = [spdlog.daily_file_sink_st(self.file_name, self.rotation_hour, self.rotation_minute)]

        # 创建logger并缓存
        logger = spdlog.SinkLogger(self.logger_name, sinks)
        SpdLogManager._logger_cache[key] = logger
        return logger
