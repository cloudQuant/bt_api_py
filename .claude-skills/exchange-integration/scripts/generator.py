"""
Exchange Integration Generator

Auto-generates exchange adapter code for bt_api_py framework.
Usage: python -m bt_api_py.skills.exchange_integration.generator
"""

import os
import sys
import json
import shutil
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import jinja2
except ImportError:
    print("Jinja2 is required. Install with: pip install jinja2")
    sys.exit(1)


class ExchangeConfig:
    """交易所配置类"""

    def __init__(self, exchange_name: str):
        self.exchange_name = exchange_name.lower().replace("-", "_")
        self.exchange_id = self.exchange_name
        self.class_name = "".join(word.title() for word in self.exchange_name.split("_"))
        self.display_name = self.class_name
        self.generation_time = datetime.now().isoformat()

        # 默认配置
        self.asset_types = ["spot"]
        self.auth_type = "hmac_sha256"
        self.venue_type = "cex"
        self.website = f"https://www.{self.exchange_name}.com"
        self.api_doc = ""
        self.rest_urls = {}
        self.wss_urls = {}
        self.api_path = "/api/v1"
        self.timeout = 10
        self.max_retries = 3
        self.rate_limits = []
        self.capabilities = [
            "get_tick", "get_depth", "get_kline",
            "make_order", "cancel_order",
            "get_balance", "get_position"
        ]
        self.field_mappings = self._default_field_mappings()
        self.api_params = {"symbol": True, "limit": True, "interval": True}
        self.order_type_param = "type"
        self.volume_param = "quantity"
        self.symbol_format = "{base}{quote}"
        self.kline_periods = {}
        self.legal_currency = ["USDT"]
        self.has_ticker_container = True
        self.has_order_container = True
        self.has_balance = True
        self.has_position = True
        self.has_market_stream = False
        self.has_account_stream = False
        self.has_balance_handler = True
        self.balance_has_symbol_param = False
        self.position_has_symbol_param = True

    def _default_field_mappings(self):
        """默认字段映射"""
        return {
            "ticker": {
                "rest": {
                    "symbol": "symbol",
                    "time": "time",
                    "bid": "bidPrice",
                    "ask": "askPrice",
                    "bid_qty": "bidQty",
                    "ask_qty": "askQty",
                    "last": "lastPrice",
                    "last_qty": "lastQty"
                },
                "wss": {
                    "symbol": "s",
                    "time": "E",
                    "bid": "b",
                    "ask": "a",
                    "bid_qty": "B",
                    "ask_qty": "A",
                    "last": "c",
                    "last_qty": "v"
                }
            }
        }

    def validate(self) -> tuple[bool, List[str]]:
        """验证配置"""
        errors = []

        if not self.exchange_name:
            errors.append("exchange_name is required")

        if not self.asset_types:
            errors.append("at least one asset_type is required")

        # 自动为缺少 rest_url 的 asset_type 生成默认 URL
        for asset_type in self.asset_types:
            if asset_type not in self.rest_urls:
                if asset_type in ("futures", "swap"):
                    self.rest_urls[asset_type] = f"https://fapi.{self.exchange_name}.com"
                else:
                    self.rest_urls[asset_type] = f"https://api.{self.exchange_name}.com"

        if self.auth_type not in ["hmac_sha256", "hmac_sha256_passphrase", "api_key", "oauth2"]:
            errors.append(f"invalid auth_type: {self.auth_type}")

        return len(errors) == 0, errors


class ExchangeGenerator:
    """交易所代码生成器"""

    def __init__(self, config: ExchangeConfig, project_root: Optional[Path] = None):
        self.config = config
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.templates_dir = Path(__file__).parent.parent / "templates"
        self.env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(self.templates_dir),
            trim_blocks=True,
            lstrip_blocks=True
        )
        self.generated_files = []

    def generate_all(self) -> dict:
        """生成所有文件"""
        results = {
            "success": True,
            "files": [],
            "errors": []
        }

        try:
            # 验证配置
            valid, errors = self.config.validate()
            if not valid:
                results["success"] = False
                results["errors"] = errors
                return results

            # 生成配置文件
            self._generate_config()

            # 生成 ExchangeData 类
            self._generate_exchange_data()

            # 生成 Feed 类 (request_base.py + per-asset subclasses)
            self._generate_request_base()
            self._generate_asset_feeds()

            # 生成 Feed facade 模块
            self._generate_feed_facade()

            # 生成数据容器
            self._generate_ticker_container()
            self._generate_order_container()
            self._generate_orderbook_container()
            self._generate_bar_container()
            self._generate_position_container()
            self._generate_balance_container()

            # 生成注册模块
            self._generate_registration()

            # 生成测试代码
            self._generate_tests()

            # 生成 __init__.py
            self._generate_init_files()

            results["files"] = self.generated_files

        except Exception as e:
            results["success"] = False
            results["errors"].append(str(e))

        return results

    def _generate_config(self):
        """生成 YAML 配置"""
        template = self.env.get_template("config.yaml.j2")
        content = template.render(**self._get_context())

        config_dir = self.project_root / "bt_api_py" / "configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        output_path = config_dir / f"{self.config.exchange_id}.yaml"

        self._write_file(output_path, content, "config")

    def _generate_exchange_data(self):
        """生成 ExchangeData 类"""
        template = self.env.get_template("exchange_data.py.j2")
        content = template.render(**self._get_context())

        output_dir = self.project_root / "bt_api_py" / "containers" / "exchanges"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{self.config.exchange_id}_exchange_data.py"

        self._write_file(output_path, content, "exchange_data")

    def _generate_request_base(self):
        """生成 Feed 基类 (request_base.py)"""
        template = self.env.get_template("request_feed.py.j2")

        feed_dir = self.project_root / "bt_api_py" / "feeds" / f"live_{self.config.exchange_id}"
        feed_dir.mkdir(parents=True, exist_ok=True)

        context = self._get_context()
        context["asset_type"] = self.config.asset_types[0]  # 默认资产类型
        context["is_base_class"] = True

        content = template.render(**context)
        output_path = feed_dir / "request_base.py"
        self._write_file(output_path, content, "request_base")

    def _generate_asset_feeds(self):
        """生成每个 asset_type 的 Feed 子类 (spot.py, swap.py 等)"""
        feed_dir = self.project_root / "bt_api_py" / "feeds" / f"live_{self.config.exchange_id}"
        feed_dir.mkdir(parents=True, exist_ok=True)

        for asset_type in self.config.asset_types:
            context = self._get_context()
            context["asset_type"] = asset_type

            content = f'''"""\n{self.config.display_name} {asset_type.upper()} Feed\n\nAuto-generated by exchange-integration skill\n"""\n\nfrom bt_api_py.containers.exchanges.{self.config.exchange_id}_exchange_data import {self.config.class_name}ExchangeData{asset_type.title()}\nfrom bt_api_py.feeds.live_{self.config.exchange_id}.request_base import {self.config.class_name}RequestData\nfrom bt_api_py.functions.log_message import SpdLogManager\nfrom bt_api_py.functions.utils import update_extra_data\n\n\nclass {self.config.class_name}RequestData{asset_type.title()}({self.config.class_name}RequestData):\n    def __init__(self, data_queue, **kwargs):\n        super().__init__(data_queue, **kwargs)\n        self.asset_type = kwargs.get("asset_type", "{asset_type.upper()}")\n        self.logger_name = kwargs.get("logger_name", "{self.config.exchange_id}_{asset_type}_feed.log")\n        self._params = {self.config.class_name}ExchangeData{asset_type.title()}()\n        self.request_logger = SpdLogManager(\n            "./logs/" + self.logger_name, "request", 0, 0, False\n        ).create_logger()\n        self.async_logger = SpdLogManager(\n            "./logs/" + self.logger_name, "async_request", 0, 0, False\n        ).create_logger()\n\n    # TODO: 添加 {asset_type} 特有的方法\n'''

            output_path = feed_dir / f"{asset_type}.py"
            self._write_file(output_path, content, f"feed_{asset_type}")

    def _generate_feed_facade(self):
        """生成 Feed facade 模块 (live_{exchange}_feed.py)"""
        imports = []
        all_names = []

        for asset_type in self.config.asset_types:
            cls_name = f"{self.config.class_name}RequestData{asset_type.title()}"
            imports.append(
                f"from bt_api_py.feeds.live_{self.config.exchange_id}.{asset_type} import {cls_name}"
            )
            all_names.append(cls_name)

        content = f'''"""\n{self.config.display_name} Feed Facade\n\nAuto-generated by exchange-integration skill\nPublic import surface for all {self.config.display_name} feed classes.\n"""\n\n{chr(10).join(imports)}\n\n__all__ = [\n{chr(10).join(f'    "{name}",' for name in all_names)}\n]\n'''

        output_dir = self.project_root / "bt_api_py" / "feeds"
        output_path = output_dir / f"live_{self.config.exchange_id}_feed.py"
        self._write_file(output_path, content, "feed_facade")

    def _generate_ticker_container(self):
        """生成 Ticker 容器"""
        template = self.env.get_template("ticker_container.py.j2")
        content = template.render(**self._get_context())

        output_dir = self.project_root / "bt_api_py" / "containers" / "tickers"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{self.config.exchange_id}_ticker.py"

        self._write_file(output_path, content, "ticker_container")

    def _generate_order_container(self):
        """生成 Order 容器"""
        template_path = self.templates_dir / "order_container.py.j2"
        if template_path.exists():
            template = self.env.get_template("order_container.py.j2")
            content = template.render(**self._get_context())
        else:
            content = f'''"""
{self.config.display_name} Order Data Container

Auto-generated by exchange-integration skill
"""

from bt_api_py.containers.orders.order import OrderData


class {self.config.class_name}RequestOrderData(OrderData):
    """{self.config.display_name} 订单数据容器"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "{self.config.exchange_id.upper()}"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_info = order_info if has_been_json_encoded else None

    # TODO: 实现具体方法
'''

        output_dir = self.project_root / "bt_api_py" / "containers" / "orders"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{self.config.exchange_id}_order.py"

        self._write_file(output_path, content, "order_container")

    def _generate_orderbook_container(self):
        """生成 OrderBook 容器"""
        template_path = self.templates_dir / "orderbook_container.py.j2"
        if template_path.exists():
            template = self.env.get_template("orderbook_container.py.j2")
            content = template.render(**self._get_context())
        else:
            content = f'''"""
{self.config.display_name} OrderBook Data Container

Auto-generated by exchange-integration skill
"""

from bt_api_py.containers.orderbooks.orderbook import OrderBookData


class {self.config.class_name}RequestOrderBookData(OrderBookData):
    """{self.config.display_name} 订单簿数据容器"""

    def __init__(self, orderbook_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(orderbook_info, has_been_json_encoded)
        self.exchange_name = "{self.config.exchange_id.upper()}"
        self.symbol_name = symbol_name
        self.asset_type = asset_type

    # TODO: 实现具体方法
'''

        output_dir = self.project_root / "bt_api_py" / "containers" / "orderbooks"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{self.config.exchange_id}_orderbook.py"

        self._write_file(output_path, content, "orderbook_container")

    def _generate_bar_container(self):
        """生成 Bar 容器"""
        template_path = self.templates_dir / "bar_container.py.j2"
        if template_path.exists():
            template = self.env.get_template("bar_container.py.j2")
            content = template.render(**self._get_context())
        else:
            content = f'''"""
{self.config.display_name} Bar Data Container

Auto-generated by exchange-integration skill
"""

from bt_api_py.containers.bars.bar import BarData


class {self.config.class_name}RequestBarData(BarData):
    """{self.config.display_name} K线数据容器"""

    def __init__(self, bar_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(bar_info, has_been_json_encoded)
        self.exchange_name = "{self.config.exchange_id.upper()}"
        self.symbol_name = symbol_name
        self.asset_type = asset_type

    # TODO: 实现具体方法
'''

        output_dir = self.project_root / "bt_api_py" / "containers" / "bars"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{self.config.exchange_id}_bar.py"

        self._write_file(output_path, content, "bar_container")

    def _generate_position_container(self):
        """生成 Position 容器"""
        template_path = self.templates_dir / "position_container.py.j2"
        if template_path.exists():
            template = self.env.get_template("position_container.py.j2")
            content = template.render(**self._get_context())
        else:
            content = f'''"""
{self.config.display_name} Position Data Container

Auto-generated by exchange-integration skill
"""

from bt_api_py.containers.positions.position import PositionData


class {self.config.class_name}RequestPositionData(PositionData):
    """{self.config.display_name} 持仓数据容器"""

    def __init__(self, position_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(position_info, has_been_json_encoded)
        self.exchange_name = "{self.config.exchange_id.upper()}"
        self.symbol_name = symbol_name
        self.asset_type = asset_type

    # TODO: 实现具体方法
'''

        output_dir = self.project_root / "bt_api_py" / "containers" / "positions"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{self.config.exchange_id}_position.py"

        self._write_file(output_path, content, "position_container")

    def _generate_balance_container(self):
        """生成 Balance 容器"""
        template_path = self.templates_dir / "balance_container.py.j2"
        if template_path.exists():
            template = self.env.get_template("balance_container.py.j2")
            content = template.render(**self._get_context())
        else:
            content = f'''"""
{self.config.display_name} Balance Data Container

Auto-generated by exchange-integration skill
"""

from bt_api_py.containers.balances.balance import BalanceData


class {self.config.class_name}RequestBalanceData(BalanceData):
    """{self.config.display_name} 余额数据容器"""

    def __init__(self, balance_info, asset_type, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "{self.config.exchange_id.upper()}"
        self.asset_type = asset_type

    # TODO: 实现具体方法
'''

        output_dir = self.project_root / "bt_api_py" / "containers" / "balances"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{self.config.exchange_id}_balance.py"

        self._write_file(output_path, content, "balance_container")

    def _generate_registration(self):
        """生成注册模块"""
        template = self.env.get_template("registration.py.j2")
        content = template.render(**self._get_context())

        output_dir = self.project_root / "bt_api_py" / "feeds"
        output_path = output_dir / f"register_{self.config.exchange_id}.py"

        self._write_file(output_path, content, "registration")

    def _generate_tests(self):
        """生成测试代码"""
        # 测试 __init__.py
        test_dir = self.project_root / "tests" / "feeds" / f"test_{self.config.exchange_id}"
        test_dir.mkdir(parents=True, exist_ok=True)
        test_init_path = test_dir / "__init__.py"
        if not test_init_path.exists():
            self._write_file(test_init_path, "", "test_init")

        # Feed 测试
        test_content = f'''"""
{self.config.display_name} Feed Tests

Auto-generated by exchange-integration skill
"""

import pytest
from bt_api_py.feeds.live_{self.config.exchange_id}.{self.config.asset_types[0]} import {self.config.class_name}RequestData{self.config.asset_types[0].title()}


class Test{self.config.class_name}RequestData{self.config.asset_types[0].title()}:
    """{self.config.display_name} {self.config.asset_types[0].title()} Feed Tests"""

    @pytest.fixture
    def feed(self, data_queue):
        return {self.config.class_name}RequestData{self.config.asset_types[0].title()}(data_queue)

    def test_capabilities(self, feed):
        """Test declared capabilities"""
        caps = feed._capabilities()
        assert "get_tick" in [c.value for c in caps]

    def test_get_tick_request(self, feed):
        """Test get_tick request building"""
        path, params, extra_data = feed._get_tick("BTC-USDT")
        assert path
        assert "symbol_name" in extra_data
        assert extra_data["symbol_name"] == "BTC-USDT"

    # TODO: 添加更多测试
'''

        test_path = test_dir / f"test_{self.config.asset_types[0]}_feed.py"
        self._write_file(test_path, test_content, "test_feed")

    def _generate_init_files(self):
        """生成 __init__.py 文件"""
        # Feed 包 __init__.py
        imports = []
        for asset_type in self.config.asset_types:
            cls_name = f"{self.config.class_name}RequestData{asset_type.title()}"
            imports.append(f"from .{asset_type} import {cls_name}")

        init_content = f'''"""
{self.config.display_name} Feed Package

Auto-generated by exchange-integration skill
"""

from .request_base import {self.config.class_name}RequestData
{chr(10).join(imports)}
'''

        feed_dir = self.project_root / "bt_api_py" / "feeds" / f"live_{self.config.exchange_id}"
        init_path = feed_dir / "__init__.py"
        self._write_file(init_path, init_content, "feed_init")

    def _get_context(self) -> Dict[str, Any]:
        """获取模板渲染上下文"""
        return {
            "exchange_id": self.config.exchange_id,
            "exchange_name": self.config.exchange_name,
            "class_name": self.config.class_name,
            "display_name": self.config.display_name,
            "generation_time": self.config.generation_time,
            "asset_types": self.config.asset_types,
            "auth_type": self.config.auth_type,
            "venue_type": self.config.venue_type,
            "website": self.config.website,
            "api_doc": self.config.api_doc,
            "rest_urls": self.config.rest_urls,
            "wss_urls": self.config.wss_urls,
            "api_path": self.config.api_path,
            "timeout": self.config.timeout,
            "max_retries": self.config.max_retries,
            "rate_limits": self.config.rate_limits,
            "capabilities": self.config.capabilities,
            "field_mappings": self.config.field_mappings,
            "api_params": self.config.api_params,
            "order_type_param": self.config.order_type_param,
            "volume_param": self.config.volume_param,
            "symbol_format": self.config.symbol_format,
            "kline_periods": self.config.kline_periods,
            "legal_currency": self.config.legal_currency,
            "has_ticker_container": self.config.has_ticker_container,
            "has_order_container": self.config.has_order_container,
            "has_balance": self.config.has_balance,
            "has_position": self.config.has_position,
            "has_market_stream": self.config.has_market_stream,
            "has_account_stream": self.config.has_account_stream,
            "has_balance_handler": self.config.has_balance_handler,
            "balance_has_symbol_param": self.config.balance_has_symbol_param,
            "position_has_symbol_param": self.config.position_has_symbol_param,
        }

    def _write_file(self, path: Path, content: str, file_type: str):
        """写入文件"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # 检查文件是否存在
        if path.exists():
            backup_path = path.with_suffix(f".py.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            shutil.copy2(path, backup_path)
            print(f"  [BACKUP] {backup_path}")

        path.write_text(content, encoding="utf-8")
        self.generated_files.append(str(path))
        print(f"  [GENERATE] {path}")


def create_interactive_config(exchange_name: str) -> ExchangeConfig:
    """交互式创建配置"""
    config = ExchangeConfig(exchange_name)

    print(f"\n=== {exchange_name.upper()} Exchange Integration ===\n")

    # 基本信息
    config.display_name = input(f"Display name [{config.display_name}]: ") or config.display_name
    config.website = input(f"Website [{config.website}]: ") or config.website
    config.api_doc = input("API documentation URL: ")

    # 资产类型
    asset_input = input("Asset types (comma-separated, e.g., spot,futures,swap) [spot]: ")
    if asset_input:
        config.asset_types = [t.strip() for t in asset_input.split(",")]

    # 认证方式
    print("\nAuthentication methods:")
    print("  1. hmac_sha256 - API Key + Secret (Binance-style)")
    print("  2. hmac_sha256_passphrase - API Key + Secret + Passphrase (OKX-style)")
    print("  3. api_key - API Key only")
    print("  4. oauth2 - OAuth 2.0")
    auth_choice = input("Authentication method [1]: ") or "1"
    auth_map = {"1": "hmac_sha256", "2": "hmac_sha256_passphrase", "3": "api_key", "4": "oauth2"}
    config.auth_type = auth_map.get(auth_choice, "hmac_sha256")

    # API URLs
    for asset_type in config.asset_types:
        default_url = f"https://api.{config.exchange_name}.com"
        if asset_type == "futures" or asset_type == "swap":
            default_url = f"https://fapi.{config.exchange_name}.com"

        rest_url = input(f"{asset_type.upper()} REST API URL [{default_url}]: ") or default_url
        config.rest_urls[asset_type] = rest_url

        wss_url = input(f"{asset_type.upper()} WebSocket URL (optional): ")
        if wss_url:
            config.wss_urls[asset_type] = wss_url

    # API 路径
    config.api_path = input("API path prefix [/api/v1]: ") or "/api/v1"

    # 限流配置
    has_limits = input("Does this exchange have rate limits? [y/N]: ").lower() == "y"
    if has_limits:
        config.rate_limits = [
            {
                "name": "global",
                "type": "sliding_window",
                "interval": 60,
                "limit": int(input("Global requests per minute [1200]: ") or "1200"),
                "scope": "global"
            }
        ]

    return config


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="Generate exchange integration code")
    parser.add_argument("exchange", help="Exchange name (e.g., bybit, kucoin)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    parser.add_argument("--config", "-c", help="JSON config file")
    parser.add_argument("--project-root", "-p", help="Project root directory")

    args = parser.parse_args()

    # 创建配置
    if args.config:
        with open(args.config) as f:
            config_data = json.load(f)
        config = ExchangeConfig(args.exchange)
        for key, value in config_data.items():
            setattr(config, key, value)
    elif args.interactive:
        config = create_interactive_config(args.exchange)
    else:
        config = ExchangeConfig(args.exchange)

    # 设置项目根目录
    if args.project_root:
        project_root = Path(args.project_root)
    else:
        # 尝试自动检测
        project_root = Path.cwd()
        while project_root != Path("/"):
            if (project_root / "bt_api_py").exists():
                break
            project_root = project_root.parent
        else:
            project_root = Path.cwd()

    # 生成代码
    generator = ExchangeGenerator(config, project_root)
    results = generator.generate_all()

    # 输出结果
    print("\n=== Generation Results ===")
    if results["success"]:
        print(f"\nGenerated {len(results['files'])} files:")
        for file in results["files"]:
            print(f"  - {file}")
        print("\nNext steps:")
        print("  1. Review generated files")
        print("  2. Implement TODO items")
        print("  3. Update field mappings if needed")
        print("  4. Add API credentials for testing")
        print("  5. Run tests")
    else:
        print("\nErrors:")
        for error in results["errors"]:
            print(f"  - {error}")


if __name__ == "__main__":
    main()
