# 决策：两个占位模块处置（F-04/F-07）

**日期**：2026-08-17
**状态**：已采纳

## 1. gateway_bridge.py（GatewayBridgeAdapter）

**决策：保留。**

理由：有真实消费方——`brokers/loader.py` 将其注册为内置 adapter（`"gateway_bridge"`），且 `tests/test_broker_contract.py` / `test_broker_loader.py` 已覆盖其契约：`place_order`/`cancel_order` 明确 `raise BrokerError(NOT_SUPPORTED)`，`capabilities()` 不宣称支持写入（`supports_order_submit=False`）。这是**诚实的占位**（写入路径拒绝而非假实现），不构成"假象"，无删除必要。

## 2. backtrader/btapibroker.py（BtApiBroker，16 行 stub）

**决策：从 `bt_api_py/__init__.py` 顶层导出撤下，模块标注 `@deprecated`。**

理由：当前 `BtApiBroker` 只是 `load_adapter(adapter_name)` 的简单包装，**不是** backtrader 官方 `BrokerBase`/`Store` 集成。从顶层导出会让用户误以为"backtrader 集成可用"。真正的 backtrader store 集成（带下单/持仓/行情测试）另立 backlog；在此之前：
- 从 `bt_api_py/__init__.py` 移除 `BtApiBroker` 顶层导出（`bt_api_py.backtrader.BtApiBroker` 路径保留，供显式导入）。
- 模块 docstring 标注 `@deprecated`，指向 `forwarding.ForwardingClient` 作为推荐边界。
