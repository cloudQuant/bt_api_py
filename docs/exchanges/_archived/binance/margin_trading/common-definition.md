
# Public API Definitions

## Terminology​

These terms will be used throughout the documentation, so it is recommended especially for new users to read to help their understanding of the API.

- `base asset` refers to the asset that is the `quantity` of a symbol. For the symbol BTCUSDT, BTC would be the `base asset.`
- `quote asset` refers to the asset that is the `price` of a symbol. For the symbol BTCUSDT, USDT would be the `quote asset` .

## ENUM definitions​

- *Symbol status (status):**

- `PRE_TRADING`
- `TRADING`
- `POST_TRADING`
- `END_OF_DAY`
- `HALT`
- `AUCTION_MATCH`
- `BREAK`

[]()

- *Account and Symbol Permissions (permissions):**

- `SPOT`
- `MARGIN`
- `LEVERAGED`
- `TRD_GRP_002`
- `TRD_GRP_003`
- `TRD_GRP_004`
- `TRD_GRP_005`
- `TRD_GRP_006`
- `TRD_GRP_007`
- `TRD_GRP_008`
- `TRD_GRP_009`
- `TRD_GRP_010`
- `TRD_GRP_011`
- `TRD_GRP_012`
- `TRD_GRP_013`
- `TRD_GRP_014`

- *Order status (status):**

| Status | Description |

| --- | --- |

| NEW | The order has been accepted by the engine. |

| PARTIALLY_FILLED | A part of the order has been filled. |

| FILLED | The order has been completed. |

| CANCELED | The order has been canceled by the user. |

| PENDING_CANCEL | Currently unused |

| REJECTED | The order was not accepted by the engine and not processed. |

| EXPIRED | The order was canceled according to the order type's rules (e.g. LIMIT FOK orders with no fill, LIMIT IOC or MARKET orders that partially fill) or by the exchange, (e.g. orders canceled during liquidation, orders canceled during maintenance) |

| EXPIRED_IN_MATCH | The order was canceled by the exchange due to STP trigger. (e.g. an order with EXPIRE_TAKER will match with existing orders on the book with the same account or same tradeGroupId) |

- *OCO Status (listStatusType):**

| Status | Description |

| --- | --- |

| RESPONSE | This is used when the ListStatus is responding to a failed action. (E.g. Orderlist placement or cancellation) |

| EXEC_STARTED | The order list has been placed or there is an update to the order list status. |

| ALL_DONE | The order list has finished executing and thus no longer active. |

- *OCO Order Status (listOrderStatus):**

| Status | Description |

| --- | --- |

| EXECUTING | Either an order list has been placed or there is an update to the status of the list. |

| ALL_DONE | An order list has completed execution and thus no longer active. |

| REJECT | The List Status is responding to a failed action either during order placement or order canceled.) |

- *ContingencyType**

- `OCO`

- *AllocationType**

- `SOR`

- *WorkingFloor**

- `EXCHANGE`
- `SOR`

- *Order types (orderTypes, type):**

- `LIMIT`
- `MARKET`
- `STOP_LOSS`
- `STOP_LOSS_LIMIT`
- `TAKE_PROFIT`
- `TAKE_PROFIT_LIMIT`
- `LIMIT_MAKER`

- *Order Response Type (newOrderRespType):**

- `ACK`
- `RESULT`
- `FULL`

- *Order side (side):**

- BUY
- SELL

- *Time in force (timeInForce):**

This sets how long an order will be active before expiration.

| Status | Description |

| --- | --- |

| GTC | Good Til Canceled  An order will be on the book unless the order is canceled. |

| IOC | Immediate Or Cancel  An order will try to fill the order as much as it can before the order expires. |

| FOK | Fill or Kill  An order will expire if the full order cannot be filled upon execution. |

- *Kline/Candlestick chart intervals:**

s-> seconds; m -> minutes; h -> hours; d -> days; w -> weeks; M -> months

- 1s
- 1m
- 3m
- 5m
- 15m
- 30m
- 1h
- 2h
- 4h
- 6h
- 8h
- 12h
- 1d
- 3d
- 1w
- 1M

- *Rate limiters (rateLimitType)**

> REQUEST_WEIGHT

```bash
    {      "rateLimitType": "REQUEST_WEIGHT",      "interval": "MINUTE",      "intervalNum": 1,      "limit": 6000    }

```bash
> ORDERS

```bash
    {      "rateLimitType": "ORDERS",      "interval": "SECOND",      "intervalNum": 10,      "limit": 100    },    {      "rateLimitType": "ORDERS",      "interval": "DAY",      "intervalNum": 1,      "limit": 200000    }

```bash
> RAW_REQUESTS

```bash
    {      "rateLimitType": "RAW_REQUESTS",      "interval": "MINUTE",      "intervalNum": 5,      "limit": 5000    }

```bash

- REQUEST_WEIGHT
- ORDERS
- RAW_REQUESTS

- *Rate limit intervals (interval)**

- SECOND
- MINUTE
- DAY

- --

# Filters

Filters define trading rules on a symbol or an exchange.
Filters come in two forms: `symbol filters` and `exchange filters`.

## Symbol Filters​

### PRICE_FILTER​

> ExchangeInfo format:

```bash
  {    "filterType": "PRICE_FILTER",    "minPrice": "0.00000100",    "maxPrice": "100000.00000000",    "tickSize": "0.00000100"  }

```bash
The `PRICE_FILTER` defines the `price` rules for a symbol. There are 3 parts:

- `minPrice` defines the minimum `price` / `stopPrice` allowed; disabled on `minPrice` == 0.
- `maxPrice` defines the maximum `price` / `stopPrice` allowed; disabled on `maxPrice` == 0.
- `tickSize` defines the intervals that a `price` / `stopPrice` can be increased/decreased by; disabled on `tickSize` == 0.

Any of the above variables can be set to 0, which disables that rule in the `price filter`. In order to pass the `price filter`, the following must be true for `price`/`stopPrice` of the enabled rules:

- `price` >= `minPrice`
- `price` <= `maxPrice`
- `price` % `tickSize` == 0

### PERCENT_PRICE​

> ExchangeInfo format:

```bash
  {    "filterType": "PERCENT_PRICE",    "multiplierUp": "1.3000",    "multiplierDown": "0.7000",    "avgPriceMins": 5  }

```bash
The `PERCENT_PRICE` filter defines the valid range for the price based on the average of the previous trades.
`avgPriceMins` is the number of minutes the average price is calculated over. 0 means the last price is used.

In order to pass the `percent price`, the following must be true for `price`:

- `price` <= `weightedAveragePrice` *`multiplierUp`
- `price` >= `weightedAveragePrice`*`multiplierDown`

### PERCENT_PRICE_BY_SIDE​

> ExchangeInfo format:

```bash
    {          "filterType": "PERCENT_PRICE_BY_SIDE",          "bidMultiplierUp": "1.2",          "bidMultiplierDown": "0.2",          "askMultiplierUp": "5",          "askMultiplierDown": "0.8",          "avgPriceMins": 1    }

```bash
The `PERCENT_PRICE_BY_SIDE` filter defines the valid range for the price based on the average of the previous trades.

`avgPriceMins` is the number of minutes the average price is calculated over. 0 means the last price is used.

There is a different range depending on whether the order is placed on the `BUY` side or the `SELL` side.

Buy orders will succeed on this filter if:

- `Order price` <= `weightedAveragePrice`*`bidMultiplierUp`
- `Order price` >= `weightedAveragePrice`*`bidMultiplierDown`

Sell orders will succeed on this filter if:

- `Order Price` <= `weightedAveragePrice`*`askMultiplierUp`
- `Order Price` >= `weightedAveragePrice`*`askMultiplierDown`

### LOT_SIZE​

> ExchangeInfo format:

```bash
  {    "filterType": "LOT_SIZE",    "minQty": "0.00100000",    "maxQty": "100000.00000000",    "stepSize": "0.00100000"  }

```bash
The `LOT_SIZE` filter defines the `quantity` (aka "lots" in auction terms) rules for a symbol. There are 3 parts:

- `minQty` defines the minimum `quantity` / `icebergQty` allowed.
- `maxQty` defines the maximum `quantity` / `icebergQty` allowed.
- `stepSize` defines the intervals that a `quantity` / `icebergQty` can be increased/decreased by.

In order to pass the `lot size`, the following must be true for `quantity`/`icebergQty`:

- `quantity` >= `minQty`
- `quantity` <= `maxQty`
- `quantity` % `stepSize` == 0

### MIN_NOTIONAL​

> ExchangeInfo format:

```bash
  {    "filterType": "MIN_NOTIONAL",    "minNotional": "0.00100000",    "applyToMarket": true,    "avgPriceMins": 5  }

```bash
The `MIN_NOTIONAL` filter defines the minimum notional value allowed for an order on a symbol.
An order's notional value is the `price`*`quantity`.
If the order is an Algo order (e.g. `STOP_LOSS_LIMIT`), then the notional value of the `stopPrice`*`quantity` will also be evaluated.
If the order is an Iceberg Order, then the notional value of the `price`*`icebergQty` will also be evaluated.
`applyToMarket` determines whether or not the `MIN_NOTIONAL` filter will also be applied to `MARKET` orders.
Since `MARKET` orders have no price, the average price is used over the last `avgPriceMins` minutes.
`avgPriceMins` is the number of minutes the average price is calculated over. 0 means the last price is used.

### NOTIONAL​

> ExchangeInfo format:

```bash
{   "filterType": "NOTIONAL",   "minNotional": "10.00000000",   "applyMinToMarket": false,   "maxNotional": "10000.00000000",   "applyMaxToMarket": false,   "avgPriceMins": 5}

```bash
The `NOTIONAL` filter defines the acceptable notional range allowed for an order on a symbol.

`applyMinToMarket` determines whether the `minNotional` will be applied to `MARKET` orders.

`applyMaxToMarket` determines whether the `maxNotional` will be applied to `MARKET` orders.

In order to pass this filter, the notional (`price*quantity`) has to pass the following conditions:

- `price*quantity` <= `maxNotional`
- `price* quantity` >= `minNotional`

For `MARKET` orders, the average price used over the last `avgPriceMins` minutes will be used for calculation.

If the `avgPriceMins` is 0, then the last price will be used.

### ICEBERG_PARTS​

> ExchangeInfo format:

```bash
  {    "filterType": "ICEBERG_PARTS",    "limit": 10  }

```bash
The `ICEBERG_PARTS` filter defines the maximum parts an iceberg order can have. The number of `ICEBERG_PARTS` is defined as `CEIL(qty / icebergQty)`.

### MARKET_LOT_SIZE​

> ExchangeInfo format:

```bash
  {    "filterType": "MARKET_LOT_SIZE",    "minQty": "0.00100000",    "maxQty": "100000.00000000",    "stepSize": "0.00100000"  }

```bash
The `MARKET_LOT_SIZE` filter defines the `quantity` (aka "lots" in auction terms) rules for `MARKET` orders on a symbol. There are 3 parts:

- `minQty` defines the minimum `quantity` allowed.
- `maxQty` defines the maximum `quantity` allowed.
- `stepSize` defines the intervals that a `quantity` can be increased/decreased by.

In order to pass the `market lot size`, the following must be true for `quantity`:

- `quantity` >= `minQty`
- `quantity` <= `maxQty`
- `quantity` % `stepSize` == 0

### MAX_NUM_ORDERS​

> ExchangeInfo format:

```bash
  {    "filterType": "MAX_NUM_ORDERS",    "maxNumOrders": 25  }

```bash
The `MAX_NUM_ORDERS` filter defines the maximum number of orders an account is allowed to have open on a symbol.
Note that both "algo" orders and normal orders are counted for this filter.

### MAX_NUM_ALGO_ORDERS​

> ExchangeInfo format:

```bash
  {    "filterType": "MAX_NUM_ALGO_ORDERS",    "maxNumAlgoOrders": 5  }

```bash
The `MAX_NUM_ALGO_ORDERS` filter defines the maximum number of "algo" orders an account is allowed to have open on a symbol.
"Algo" orders are `STOP_LOSS`, `STOP_LOSS_LIMIT`, `TAKE_PROFIT`, and `TAKE_PROFIT_LIMIT` orders.

### MAX_NUM_ICEBERG_ORDERS​

The `MAX_NUM_ICEBERG_ORDERS` filter defines the maximum number of `ICEBERG` orders an account is allowed to have open on a symbol.
An `ICEBERG` order is any order where the `icebergQty` is > 0.

> ExchangeInfo format:

```bash
  {    "filterType": "MAX_NUM_ICEBERG_ORDERS",    "maxNumIcebergOrders": 5  }

```bash

### MAX_POSITION​

The `MAX_POSITION` filter defines the allowed maximum position an account can have on the base asset of a symbol.
An account's position defined as the sum of the account's:

1. free balance of the base asset
2. locked balance of the base asset
3. sum of the qty of all open BUY orders

`BUY` orders will be rejected if the account's position is greater than the maximum position allowed.

If an order's `quantity` can cause the position to overflow, this will also fail the `MAX_POSITION` filter.

> ExchangeInfo format:

```bash
{  "filterType":"MAX_POSITION",  "maxPosition":"10.00000000"}

```bash

### TRAILING_DELTA​

> ExchangeInfo format:

```bash
    {          "filterType": "TRAILING_DELTA",          "minTrailingAboveDelta": 10,          "maxTrailingAboveDelta": 2000,          "minTrailingBelowDelta": 10,          "maxTrailingBelowDelta": 2000   }

```bash
The `TRAILING_DELTA` filter defines the minimum and maximum value for the parameter `trailingDelta`.

In order for a trailing stop order to pass this filter, the following must be true:

For `STOP_LOSS BUY`, `STOP_LOSS_LIMIT_BUY`,`TAKE_PROFIT SELL` and `TAKE_PROFIT_LIMIT SELL` orders:

- `trailingDelta` >= `minTrailingAboveDelta`
- `trailingDelta` <= `maxTrailingAboveDelta`

For `STOP_LOSS SELL`, `STOP_LOSS_LIMIT SELL`, `TAKE_PROFIT BUY`, and `TAKE_PROFIT_LIMIT BUY` orders:

- `trailingDelta` >= `minTrailingBelowDelta`
- `trailingDelta` <= `maxTrailingBelowDelta`

## Exchange Filters​

### EXCHANGE_MAX_NUM_ORDERS​

> ExchangeInfo format:

```bash
  {    "filterType": "EXCHANGE_MAX_NUM_ORDERS",    "maxNumOrders": 1000  }

```bash
The `EXCHANGE_MAX_NUM_ORDERS` filter defines the maximum number of orders an account is allowed to have open on the exchange.
Note that both "algo" orders and normal orders are counted for this filter.

### EXCHANGE_MAX_NUM_ALGO_ORDERS​

> ExchangeInfo format:

```bash
  {    "filterType": "EXCHANGE_MAX_NUM_ALGO_ORDERS",    "maxNumAlgoOrders": 200  }

```bash
The `EXCHANGE_MAX_NUM_ALGO_ORDERS` filter defines the maximum number of "algo" orders an account is allowed to have open on the exchange.
"Algo" orders are `STOP_LOSS`, `STOP_LOSS_LIMIT`, `TAKE_PROFIT`, and `TAKE_PROFIT_LIMIT` orders.

### EXCHANGE_MAX_NUM_ICEBERG_ORDERS​

The `EXCHANGE_MAX_NUM_ICEBERG_ORDERS` filter defines the maximum number of iceberg orders an account is allowed to have open on the exchange.

> ExchangeInfo format:

```bash
{  "filterType": "EXCHANGE_MAX_NUM_ICEBERG_ORDERS",  "maxNumIcebergOrders": 10000}

```bash
