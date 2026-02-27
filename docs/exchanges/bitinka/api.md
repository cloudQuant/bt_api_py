# Bitinka API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: REST
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://www.bitinka.com/bitinka/api_documentation

## 交易所基本信息
- 官方名称: Bitinka
- 官网: https://www.bitinka.com
- 交易所类型: CEX (中心化交易所)
- 总部: 波兰 (Poznań)，主要服务拉丁美洲、欧洲和亚洲
- 支持的交易对: 64+ (多种法币计价: ARS, BRL, CLP, COP, EUR, PEN, USD, USDT 等)
- 支持的交易类型: 现货(Spot)
- 法币支持: ARS (阿根廷比索), BRL (巴西雷亚尔), CLP (智利比索), COP (哥伦比亚比索), EUR (欧元), PEN (秘鲁索尔), USD, USDT
- 注册公司: Abucoins Sp. z o.o.

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API | `https://www.bitinka.com/api` | 主端点 |
| 交易页面 | `https://www.bitinka.com/trade` | Web 交易 |

> **注意**: Bitinka 的 API 文档有限，部分端点信息来源于社区和第三方整理。建议通过官方文档 https://www.bitinka.com/bitinka/api_documentation 获取最新详情。

## 认证方式

### API密钥获取

1. 注册 Bitinka 账户并完成 KYC
2. 在账户设置中找到 API 管理
3. 生成 API Key 和 Secret

### 认证机制

Bitinka 使用 API Key + Secret 进行认证。具体签名算法请参考官方 API 文档。

## 市场数据API

### 1. 获取行情

Bitinka 支持多种法币市场的交易对行情查询。

```python
import requests

BASE_URL = "https://www.bitinka.com/api"

# 获取行情数据（具体端点以官方文档为准）
# 支持的市场: BTC/ARS, BTC/BRL, BTC/CLP, BTC/COP, BTC/EUR, BTC/PEN, BTC/USD, BTC/USDT 等
```

### 2. 支持的交易对示例

Bitinka 主要面向拉美市场，支持以下法币的交易对：

| 法币 | 国家/地区 | 示例交易对 |
|------|----------|-----------|
| ARS | 阿根廷 | BTC/ARS, ETH/ARS |
| BRL | 巴西 | BTC/BRL, ETH/BRL |
| CLP | 智利 | BTC/CLP, ETH/CLP |
| COP | 哥伦比亚 | BTC/COP, ETH/COP |
| EUR | 欧洲 | BTC/EUR, ETH/EUR |
| PEN | 秘鲁 | BTC/PEN, ETH/PEN |
| USD | 美元 | BTC/USD, ETH/USD |
| USDT | 稳定币 | BTC/USDT, ETH/USDT |

## 交易API

> 交易功能需要通过认证 API 访问。具体端点和参数请参考官方文档。

### 基本功能

- 下单 (Buy/Sell)
- 撤单
- 查询订单
- 查询余额

## 账户管理API

- 余额查询
- 充值/提现
- 交易历史

> 具体端点和参数请参考官方 API 文档。

## 速率限制

具体速率限制请参考官方文档。建议：
- 控制请求频率，避免触发限制
- 使用合理的轮询间隔

## WebSocket支持

官方文档未提供 WebSocket 支持说明。建议使用 REST API 轮询获取实时数据。

## 错误处理

### Python 基础请求示例

```python
import requests

def bitinka_request(endpoint, params=None):
    """Bitinka API 基础请求"""
    try:
        url = f"https://www.bitinka.com/api/{endpoint}"
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None
```

## 注意事项

1. **API 文档有限**: Bitinka 的公开 API 文档较少，建议直接联系官方获取最新 API 文档
2. **拉美市场为主**: 主要服务拉丁美洲用户，支持多种拉美法币
3. **未集成 CCXT**: Bitinka 未被 CCXT 开源库集成，无法通过 CCXT 统一接口访问
4. **KYC 要求**: 交易功能需要完成身份认证

## 变更历史

### 2026-02-27
- 完善文档，添加交易所基本信息和支持的法币市场
- 标注 API 文档有限的现状
- 添加基础 Python 请求示例

---

## 相关资源

- [Bitinka 官网](https://www.bitinka.com)
- [Bitinka API 文档](https://www.bitinka.com/bitinka/api_documentation)
- [Bitinka FAQ](https://www.bitinka.com/uk/bitinka/faq)

---

*本文档由 bt_api_py 项目维护。由于 Bitinka 官方 API 文档有限，部分信息可能不完整，建议参考官方最新文档。*
