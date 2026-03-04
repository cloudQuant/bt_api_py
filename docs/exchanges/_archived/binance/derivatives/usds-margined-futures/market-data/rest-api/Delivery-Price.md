On this page

# Quarterly Contract Settlement Price

## API Description

Latest price for a symbol or symbols.

## HTTP Request

GET `/futures/data/delivery-price`

## Request Weight

- *0**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

pair| STRING| YES| e.g BTCUSDT

## Response Example


    [
        {
            "deliveryTime": 1695945600000,
            "deliveryPrice": 27103.00000000
        },
        {
            "deliveryTime": 1688083200000,
            "deliveryPrice": 30733.60000000
        },
        {
            "deliveryTime": 1680220800000,
            "deliveryPrice": 27814.20000000
        },
        {
            "deliveryTime": 1648166400000,
            "deliveryPrice": 44066.30000000
        }
    ]


  - [API Description](</docs/derivatives/usds-margined-futures/market-data/rest-api/Delivery-Price#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/market-data/rest-api/Delivery-Price#http-request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/market-data/rest-api/Delivery-Price#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/rest-api/Delivery-Price#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/market-data/rest-api/Delivery-Price#response-example>)
