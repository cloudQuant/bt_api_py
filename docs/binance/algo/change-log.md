# Change Log

> 来源: [Binance Algo Trading API](https://developers.binance.com/docs/algo)

## 2025-12-26

### Time-sensitive Notice

- **The following change to REST API will occur at approximately 2026-01-15 07:00 UTC:** When calling endpoints that require signatures, percent-encode payloads before computing signatures. Requests that do not follow this order will be rejected with `-1022 INVALID_SIGNATURE`. Please review and update your signing logic accordingly.

### REST API

- Updated documentation for REST API regarding [Signed Endpoints examples for placing an order](https://developers.binance.com/docs/algo/general-info#signed-endpoint-examples-for-post-apiv3order).

---

## 2023-04-18

- New endpoints for Spot Algo：
  - `POST /sapi/v1/algo/spot/newOrderTwap` to support new order
  - `DELETE /sapi/v1/algo/spot/order` to support cancel Algo order
  - `GET /sapi/v1/algo/spot/openOrders` to support query Algo open orders
  - `GET /sapi/v1/algo/spot/historicalOrders` to support query Algo historical orders
  - `GET /sapi/v1/algo/spot/subOrders` to support query Algo sub orders for a specified algoId

---

## 2022-04-27

- New endpoint for Futures Algo：
  - `POST /sapi/v1/algo/futures/newOrderTwap` to support Twap new order

FAQ: [Time-Weighted Average Price(Twap) Introduction](https://www.binance.com/en/support/faq/093927599fd54fd48857237f6ebec0b0)

---

## 2022-04-13

- New endpoints for Futures Algo：
  - `POST /sapi/v1/algo/futures/newOrderVp` to support VP new order
  - `DELETE /sapi/v1/algo/futures/order` to support cancel Algo order
  - `GET /sapi/v1/algo/futures/openOrders` to support query Algo open orders
  - `GET /sapi/v1/algo/futures/historicalOrders` to support query Algo historical orders
  - `GET /sapi/v1/algo/futures/subOrders` to support query Algo sub orders for a specified algoId

FAQ: [Volume Participation(VP) Introduction](https://www.binance.com/en/support/faq/b0b94dcc8eb64c2585763b8747b60702)
