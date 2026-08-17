# 每仓最低测试基线

**最后更新**：2026-08-17

本文档定义每个 `bt_api_*` 适配器仓库的最低测试基线，杜绝"空壳测试"（如 `test_xxx_sign` 测试体内重实现 hmac 自比较、或仅断言 `exchange_name` 的单断言测试）。

## 三档基线

按交易所仓库的能力分为三档：

| 档位 | 适用仓 | 最低要求 |
|------|--------|----------|
| **L1** | 头部 5 仓（binance / okx / bybit / gateio / hyperliquid） | ①签名黄金值（调用被测 `sign` 方法断言黄金向量）②normalize 真实报文（用真实交易所报文样例断言解析结果）③错误翻译（`_raise_if_error` 对真实错误码抛 UnifiedError），各 ≥3 用例 |
| **L2** | 有私有接口的仓 | ①签名黄金值 ②错误翻译，各 ≥2 用例 |
| **L3** | 纯公开接口仓 | normalize 真实报文 ≥2 用例（禁止 `exchange_name` 单断言空壳） |

## 空壳测试判定

以下形态视为"空壳"，必须修掉：

1. **自指签名测试**：测试体内重新 `import hmac/hashlib` 计算期望值，与实现用同一算法自比较（永远绿，不防回归）。应改为调用被测方法并断言预计算的黄金向量。
2. **单断言空壳**：仅断言 `feed.exchange_name == "XXX"`。应补齐 normalize 真实报文的断言。
3. **`inspect.getsource` 形式检查**：断言源码文本（重构即破坏，不防回归）。应改为行为测试。

## 黄金向量复算约定

签名黄金向量须在测试注释中给出可复算命令，例如：

```bash
python3 -c "import hmac,hashlib,base64; s='SECRET'; pre='TIMESTAMPGET/path'; print(base64.b64encode(hmac.new(s.encode(),pre.encode(),hashlib.sha256).digest()).decode())"
```

## 已落地基线（迭代 4 进度）

- **binance**（L1）：`test_binance_request_base.py` 错误翻译 4 用例（-2014/-1003/-1022 + 成功不抛）；`test_binance_sign_requires_private_key`（私钥缺失抛 ConfigurationError）。
- **okx**（L1）：`test_okx_request_base.py` 签名黄金向量 + 时间戳 ISO8601 + 错误翻译 3 用例；`test_okx_wss_login.py` 登录回执序列测试。
- **bybit**（L1）：`test_bybit_request_base.py` 错误翻译 3 用例（rate limit/auth + 成功不抛）。
- **gateio**（L1）：`test_gateio_request_base.py` 错误翻译 4 用例。
- **hyperliquid**（L1）：`test_hyperliquid_request_data.py` 错误翻译 4 用例 + 能力声明（X-API-Key 模式）。

其余仓按 L2/L3 档位逐仓落地（nightly matrix 全绿为验收）。
