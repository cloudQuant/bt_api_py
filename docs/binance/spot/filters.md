# Filters (交易过滤器)

> 来源: <https://github.com/binance/binance-spot-api-docs/blob/master/filters.md>

过滤器定义了交易对或交易所的交易规则，分为三种：`symbol filters`、`exchange filters` 和 `asset filters`。

## Symbol Filters

### PRICE_FILTER

定义交易对的价格规则：

- `minPrice` - 最小价格；设为 0 表示禁用
- `maxPrice` - 最大价格；设为 0 表示禁用
- `tickSize` - 价格步长；设为 0 表示禁用

规则：`price >= minPrice && price <= maxPrice && price % tickSize == 0`

```json
{
  "filterType": "PRICE_FILTER",
  "minPrice": "0.00000100",
  "maxPrice": "100000.00000000",
  "tickSize": "0.00000100"
}

```

### PERCENT_PRICE

基于历史加权平均价的价格范围限制。

- `price <= weightedAveragePrice *multiplierUp`
- `price >= weightedAveragePrice*multiplierDown`

```json
{
  "filterType": "PERCENT_PRICE",
  "multiplierUp": "1.3000",
  "multiplierDown": "0.7000",
  "avgPriceMins": 5
}

```

### PERCENT_PRICE_BY_SIDE

按买卖方向分别定义价格范围。

```json
{
  "filterType": "PERCENT_PRICE_BY_SIDE",
  "bidMultiplierUp": "1.2",
  "bidMultiplierDown": "0.2",
  "askMultiplierUp": "5",
  "askMultiplierDown": "0.8",
  "avgPriceMins": 1
}

```

### LOT_SIZE

定义交易对的数量规则：

- `minQty` - 最小数量
- `maxQty` - 最大数量
- `stepSize` - 数量步长

规则：`quantity >= minQty && quantity <= maxQty && quantity % stepSize == 0`

```json
{
  "filterType": "LOT_SIZE",
  "minQty": "0.00100000",
  "maxQty": "100000.00000000",
  "stepSize": "0.00100000"
}

```

### MIN_NOTIONAL

最小名义价值 (`price* quantity`)。

```json
{
  "filterType": "MIN_NOTIONAL",
  "minNotional": "0.00100000",
  "applyToMarket": true,
  "avgPriceMins": 5
}

```

### NOTIONAL

名义价值范围限制。

```json
{
  "filterType": "NOTIONAL",
  "minNotional": "10.00000000",
  "applyMinToMarket": false,
  "maxNotional": "10000.00000000",
  "applyMaxToMarket": false,
  "avgPriceMins": 5
}

```

### ICEBERG_PARTS

冰山订单最大分拆部分数 = `CEIL(qty / icebergQty)`。

```json
{ "filterType": "ICEBERG_PARTS", "limit": 10 }

```

### MARKET_LOT_SIZE

MARKET 订单的数量规则（同 LOT_SIZE 结构）。

```json
{
  "filterType": "MARKET_LOT_SIZE",
  "minQty": "0.00100000",
  "maxQty": "100000.00000000",
  "stepSize": "0.00100000"
}

```

### MAX_NUM_ORDERS

单交易对最大挂单数（含 algo 订单）。

```json
{ "filterType": "MAX_NUM_ORDERS", "maxNumOrders": 25 }

```

### MAX_NUM_ALGO_ORDERS

单交易对最大 algo 订单数（STOP_LOSS、STOP_LOSS_LIMIT、TAKE_PROFIT、TAKE_PROFIT_LIMIT）。

```json
{ "filterType": "MAX_NUM_ALGO_ORDERS", "maxNumAlgoOrders": 5 }

```

### MAX_NUM_ICEBERG_ORDERS

单交易对最大冰山订单数。

```json
{ "filterType": "MAX_NUM_ICEBERG_ORDERS", "maxNumIcebergOrders": 5 }

```

### MAX_POSITION

账户在某基础资产上的最大持仓（free + locked + 所有 BUY 订单的 qty 之和）。

```json
{ "filterType": "MAX_POSITION", "maxPosition": "10.00000000" }

```

### TRAILING_DELTA

trailingDelta 的最小和最大值。

```json
{
  "filterType": "TRAILING_DELTA",
  "minTrailingAboveDelta": 10,
  "maxTrailingAboveDelta": 2000,
  "minTrailingBelowDelta": 10,
  "maxTrailingBelowDelta": 2000
}

```

### MAX_NUM_ORDER_AMENDS

单个订单的最大修改次数。

```json
{ "filterType": "MAX_NUM_ORDER_AMENDS", "maxNumOrderAmends": 10 }

```

### MAX_NUM_ORDER_LISTS

单交易对最大挂单列表数。

```json
{ "filterType": "MAX_NUM_ORDER_LISTS", "maxNumOrderLists": 20 }

```

## Exchange Filters

### EXCHANGE_MAX_NUM_ORDERS

交易所级别最大挂单数。

```json
{ "filterType": "EXCHANGE_MAX_NUM_ORDERS", "maxNumOrders": 1000 }

```

### EXCHANGE_MAX_NUM_ALGO_ORDERS

交易所级别最大 algo 订单数。

```json
{ "filterType": "EXCHANGE_MAX_NUM_ALGO_ORDERS", "maxNumAlgoOrders": 200 }

```

### EXCHANGE_MAX_NUM_ICEBERG_ORDERS

交易所级别最大冰山订单数。

```json
{ "filterType": "EXCHANGE_MAX_NUM_ICEBERG_ORDERS", "maxNumIcebergOrders": 10000 }

```

### EXCHANGE_MAX_NUM_ORDER_LISTS

交易所级别最大订单列表数。

```json
{ "filterType": "EXCHANGE_MAX_NUM_ORDER_LISTS", "maxNumOrderLists": 20 }

```

## Asset Filters

### MAX_ASSET

单笔订单中某资产的最大交易数量。

```json
{ "filterType": "MAX_ASSET", "asset": "USDC", "limit": "42.00000000" }

```
