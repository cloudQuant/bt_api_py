# MIG01：operator-owned Gateway ServiceHost application

本轮在现有 SDK `dev` 增加一个可由安装命令启动的 operator application。CLI 文档只接受 `profile_ref`，并从已安装的代码注册表解析部署 profile；它不接受账户凭据、endpoint、Python factory 或 provider 选择。`GatewayOperatorApplication` 在单一非 daemon owner thread 上构造 profile、启动和维护 `GatewayOperatorDeployment`，并在同一线程执行账户会话恢复、未认领 provider cleanup 重试和关闭。启动/关闭超时保留原 owner 与有限状态；已确认关闭且 cleanup 完成后，owner 线程的短暂退出延迟不会改写为不确定状态或向已完成线程投递命令。CLI 在启动前校验有限且 `0 < timeout <= 300` 的值，监控状态，并在不确定状态下保留控制循环。

本轮精确范围为六个生产/测试文件及本记录：

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `bt_api_py/gateway_operator_application.py` | 33995 | `7FF49E9BFEB42FE39975247D664729965E13BF84FB90C506A3243F0BED2D037C` |
| `bt_api_py/gateway_operator_cli.py` | 5985 | `5A7B9226CF809642AB53CCC52956A5F46BD77E94E1B277981496AE168C7641E0` |
| `tests/test_gateway_operator_application.py` | 12549 | `11AE195715E03A90CEDEF57074E27B931489C768A688799999FC5FD5F2833420` |
| `tests/test_gateway_operator_cli.py` | 4695 | `E1D9B29E91879CF3A22529308898B0643AE92A4577C3B7587D3F6B68F1BEE3DB` |
| `tests/integration/test_gateway_operator_application.py` | 6345 | `EB392F5F4DE27831CD359F07F3586C676973E4F1B2750498154D840E8F4B2791` |
| `pyproject.toml` | 9779 | `2E7E97B66948364E376DA10BA97C624EEA266E001587AFA211B5DC7B2B4836C5` |

`pyproject.toml` 的唯一改动注册 `bt-gateway-operator = bt_api_py.gateway_operator_cli:main`。

作者聚焦命令：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q -o addopts= --noconftest tests/test_gateway_operator_application.py tests/test_gateway_operator_cli.py tests/integration/test_gateway_operator_application.py
```

结果为 **16 passed / 2 warnings / exit 0**。一个 warning 是禁用 pytest 插件自动加载后 pytest 不识别仓库的 `asyncio_default_fixture_loop_scope` 配置；另一个是 Windows Proactor 与 pyzmq 的 `add_reader` 兼容提示，pyzmq 使用 selector thread。五个 Python 文件的 `ruff check` 与 `ruff format --check` 均通过。独立静态复核对上述六个源 pin 返回 **GO_STATIC**；复核没有运行 QA、测试或 Host。

集成节点使用本机合成 OKX ticker queue、真实 SDK `GatewayOperatorDeployment`/`ServiceHost`、本机 Curve ZMQ client 和 `sdk.market.event`，并验证正常关闭。没有连接真实 OKX、账户或外部服务。该轮未构建 wheel/sdist，也未做隔离安装；profile registry 需由受信任的安装代码提供，不能据此声称任一 provider 已开箱配置。正式 MIG01 仍未完成，旧 forwarding/runtime 发行与消费者闭包仍待后续轮。
