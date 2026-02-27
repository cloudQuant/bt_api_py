# BingX API 文档

## 交易所信息

- **交易所名称**: BingX
- **官方网站**: https://bingx.com
- **API文档**: https://bingx-api.github.io/docs/
- **24h交易量排名**: #19
- **24h交易量**: $400M+
- **支持的交易对**: 300+ 交易对
- **API版本**: V2（建议）

## API基础信息

### 基础URL

```text
# REST API (新域名)
https://open-api.bingx.com

# 历史旧域名（已逐步弃用）
https://api-swap-rest.bingx.com
```

### 请求头

- 具体签名与鉴权方式请以官方文档为准（BingX Open API V2）

## 认证方式

### 1. 获取API密钥

1. 登录 BingX 账户
2. 进入 API 管理页面
3. 创建 API Key 并设置权限
4. 保存 API Key 与 Secret

### 2. 请求签名算法

- 官方文档提供签名规则与示例（见 `https://bingx-api.github.io/docs/`）

## 市场数据API（示例）

- 现货交易品种: `GET /openApi/spot/v1/common/symbols`
- 其他行情与成交接口详见官方文档

## 交易API（示例）

- 合约下单: `POST /openApi/swap/v2/trade/order`
- 现货/合约更多下单与撤单接口见官方文档

## 账户管理API

- 账户与资产相关接口详见官方文档

## 速率限制

- 官方公告给出各接口限频规则（如 `/openApi/swap/v2/trade/order` 限频调整）

## WebSocket支持

- 提供 WS 行情与账户推送，详见官方文档

## 错误代码

- 官方文档提供错误码说明

## 代码示例

```python
# 查询现货交易对
import requests

url = "https://open-api.bingx.com/openApi/spot/v1/common/symbols"
print(requests.get(url).json())
```
