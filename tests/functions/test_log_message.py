import os
import pytest
import spdlog
from datetime import datetime
from bt_api_py.functions.log_message import SpdLogManager  # 替换为实际模块路径


class TestSpdLogManager:
    """测试 SpdLogManager 类"""
    def test_initialization_default_values(self):
        """测试默认初始化值"""
        manager = SpdLogManager()
        assert manager.file_name == "log_strategy_info.log"
        assert manager.logger_name == "hello"
        assert manager.rotation_hour == 0
        assert manager.rotation_minute == 0
        assert manager.print_info is False

    def test_initialization_custom_values(self):
        """测试自定义初始化值"""
        manager = SpdLogManager(
            file_name="custom.log",
            logger_name="custom_logger",
            rotation_hour=12,
            rotation_minute=30,
            print_info=True
        )
        assert manager.file_name == "custom.log"
        assert manager.logger_name == "custom_logger"
        assert manager.rotation_hour == 12
        assert manager.rotation_minute == 30
        assert manager.print_info is True

    def test_multiple_sinks(self):
        """测试多个日志输出目标（文件和控制台）"""
        current_directory = os.getcwd()  # 获取当前工作目录
        log_file_name = os.path.join(current_directory, "test_log2.log")  # 拼接日志文件路径
        manager = SpdLogManager(file_name=log_file_name, logger_name="multi_sink_logger", print_info=True)
        logger = manager.create_logger()
        test_message = "This is a test log message"
        logger.info(test_message)
        logger.flush()  # 确保日志刷新

        # 检查日志文件
        current_directory = os.getcwd()  # 获取当前工作目录
        today_date = datetime.now().strftime("%Y-%m-%d")  # 获取当前日期，格式为 YYYY-MM-DD
        file_name = f"test_log2_{today_date}.log"  # 在文件名后添加日期
        new_file_name = os.path.join(current_directory, file_name)  # 拼接日志文件路径

        # 检查日志文件是否存在
        assert os.path.exists(new_file_name)

        # 检查日志文件内容
        with open(new_file_name, "r") as f:
            log_content = f.read()
            assert test_message in log_content

        logger.close()
        # 清理测试生成的文件
        if os.path.exists(new_file_name):
            os.remove(new_file_name)

    def test_create_logger_with_print_info(self):
        """测试创建日志器（print_info=True）"""
        current_directory = os.getcwd()  # 获取当前工作目录
        log_file_name = os.path.join(current_directory, "test_log3.log")  # 拼接日志文件路径
        logger_manager = SpdLogManager(file_name=log_file_name, logger_name="test_logger", print_info=True)
        logger = logger_manager.create_logger()
        assert isinstance(logger, spdlog.SinkLogger)
        assert logger.name() == "test_logger"
        # 检查日志文件内容
        current_directory = os.getcwd()  # 获取当前工作目录
        today_date = datetime.now().strftime("%Y-%m-%d")  # 获取当前日期，格式为 YYYY-MM-DD
        file_name = f"test_log3_{today_date}.log"  # 在文件名后添加日期
        new_file_name = os.path.join(current_directory, file_name)  # 拼接日志文件路径
        logger.close()
        # 清理测试生成的文件
        if os.path.exists(new_file_name):
            os.remove(new_file_name)

    def test_create_logger_without_print_info(self):
        """测试创建日志器（print_info=False）"""
        current_directory = os.getcwd()  # 获取当前工作目录
        log_file_name = os.path.join(current_directory, "test_log4.log")  # 拼接日志文件路径
        manager = SpdLogManager(file_name=log_file_name, logger_name="test_logger", print_info=False)
        logger = manager.create_logger()
        assert isinstance(logger, spdlog.SinkLogger)
        assert logger.name() == "test_logger"
        # 检查日志文件内容
        current_directory = os.getcwd()  # 获取当前工作目录
        today_date = datetime.now().strftime("%Y-%m-%d")  # 获取当前日期，格式为 YYYY-MM-DD
        file_name = f"test_log4_{today_date}.log"  # 在文件名后添加日期
        new_file_name = os.path.join(current_directory, file_name)  # 拼接日志文件路径
        logger.close()
        # 清理测试生成的文件
        if os.path.exists(new_file_name):
            os.remove(new_file_name)

    def test_log_output_to_file(self):
        """测试日志输出到文件"""
        current_directory = os.getcwd()  # 获取当前工作目录
        log_file_name = os.path.join(current_directory, "test_log5.log")  # 拼接日志文件路径
        logger_manager = SpdLogManager(file_name=log_file_name, logger_name="test_logger", print_info=True)
        logger = logger_manager.create_logger()
        test_message = "This is a test log message"
        logger.info(test_message)
        logger.flush()  # 确保日志写入文件

        # 检查日志文件是否存在
        current_directory = os.getcwd()  # 获取当前工作目录
        today_date = datetime.now().strftime("%Y-%m-%d")  # 获取当前日期，格式为 YYYY-MM-DD
        file_name = f"test_log5_{today_date}.log"  # 在文件名后添加日期
        new_file_name = os.path.join(current_directory, file_name)  # 拼接日志文件路径
        assert os.path.exists(new_file_name), f"new file name = {new_file_name}"

        # 检查日志文件内容
        with open(new_file_name, "r") as f:
            log_content = f.read()
            assert test_message in log_content

        logger.close()
        # 清理测试生成的文件
        if os.path.exists(new_file_name):
            os.remove(new_file_name)

    # def test_log_output_to_console(self, logger_manager, capsys):
    #     """测试日志输出到控制台（print_info=True）"""
    #     logger = logger_manager.create_logger()
    #     test_message = "This is a test log message"
    #     logger.info(test_message)
    #     logger.flush()
    #
    #     # 检查控制台输出
    #     captured = capsys.readouterr()
    #     print(f"Captured output: {captured.out}")  # 打印捕获的输出，用于调试
    #
    #     # 检查日志消息是否在控制台输出中
    #     assert test_message in captured.out, f"Expected '{test_message}' in output, but got '{captured.out}'"

    def test_log_rotation(self):
        """测试日志文件轮换"""
        current_directory = os.getcwd()  # 获取当前工作目录
        log_file_name = os.path.join(current_directory, "test_log6.log")  # 拼接日志文件路径
        manager = SpdLogManager(file_name=log_file_name, logger_name="rotation_logger2", rotation_hour=0, rotation_minute=0)
        logger = manager.create_logger()
        logger.info("Log before rotation")
        logger.flush()

        # 模拟日志轮换
        new_file_name = "rotated_log2.log"
        rotated_manager = SpdLogManager(file_name=new_file_name, logger_name="rotated_logger3")
        rotated_logger = rotated_manager.create_logger()
        rotated_logger.info("Log after rotation")
        rotated_logger.flush()

        # 检查原始日志文件和新日志文件
        current_directory = os.getcwd()  # 获取当前工作目录
        today_date = datetime.now().strftime("%Y-%m-%d")  # 获取当前日期，格式为 YYYY-MM-DD
        file_name = f"test_log6_{today_date}.log"  # 在文件名后添加日期
        new_file_name = os.path.join(current_directory, file_name)  # 拼接日志文件路径
        assert os.path.exists(new_file_name)
        new_file_name2 = os.path.join(current_directory, f"rotated_log2_{today_date}.log")
        assert os.path.exists(new_file_name2)

        # 关闭日志器
        logger.close()
        rotated_logger.close()

        # 清理测试生成的文件
        if os.path.exists(new_file_name):
            os.remove(new_file_name)

        if os.path.exists(new_file_name2):
            os.remove(new_file_name2)

    def test_log_levels(self):
        """测试日志级别"""
        current_directory = os.getcwd()  # 获取当前工作目录
        log_file_name = os.path.join(current_directory, "test_log7.log")  # 拼接日志文件路径
        logger_manager = SpdLogManager(file_name=log_file_name, logger_name="test_logger", print_info=True)
        logger = logger_manager.create_logger()
        logger.set_level(spdlog.LogLevel.INFO)
        assert logger.level() == spdlog.LogLevel.INFO
        # 检查日志文件内容
        current_directory = os.getcwd()  # 获取当前工作目录
        today_date = datetime.now().strftime("%Y-%m-%d")  # 获取当前日期，格式为 YYYY-MM-DD
        file_name = f"test_log7_{today_date}.log"  # 在文件名后添加日期
        new_file_name = os.path.join(current_directory, file_name)  # 拼接日志文件路径
        logger.close()
        # 清理测试生成的文件
        if os.path.exists(new_file_name):
            os.remove(new_file_name)

    def test_logger_name(self):
        """测试日志器名称"""
        current_directory = os.getcwd()  # 获取当前工作目录
        log_file_name = os.path.join(current_directory, "test_log8.log")  # 拼接日志文件路径
        logger_manager = SpdLogManager(file_name=log_file_name, logger_name="test_logger", print_info=True)
        logger = logger_manager.create_logger()
        assert logger.name() == "test_logger"
        # 检查日志文件内容
        current_directory = os.getcwd()  # 获取当前工作目录
        today_date = datetime.now().strftime("%Y-%m-%d")  # 获取当前日期，格式为 YYYY-MM-DD
        file_name = f"test_log8_{today_date}.log"  # 在文件名后添加日期
        new_file_name = os.path.join(current_directory, file_name)  # 拼接日志文件路径
        logger.close()
        # 清理测试生成的文件
        if os.path.exists(new_file_name):
            os.remove(new_file_name)

    def test_logger_flush(self):
        """测试日志刷新"""
        current_directory = os.getcwd()  # 获取当前工作目录
        log_file_name = os.path.join(current_directory, "test_log9.log")  # 拼接日志文件路径
        logger_manager = SpdLogManager(file_name=log_file_name, logger_name="test_logger", print_info=True)
        logger = logger_manager.create_logger()
        test_message = "This is a test log message"
        logger.info(test_message)
        logger.flush()

        # 检查日志文件内容
        current_directory = os.getcwd()  # 获取当前工作目录
        today_date = datetime.now().strftime("%Y-%m-%d")  # 获取当前日期，格式为 YYYY-MM-DD
        file_name = f"test_log9_{today_date}.log"  # 在文件名后添加日期
        new_file_name = os.path.join(current_directory, file_name)  # 拼接日志文件路径

        with open(new_file_name, "r") as f:
            log_content = f.read()
            assert test_message in log_content
        logger.close()
        # 清理测试生成的文件
        if os.path.exists(new_file_name):
            os.remove(new_file_name)

