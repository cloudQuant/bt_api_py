On this page

# Symbol Price Ticker

## API Description

Latest price for a symbol or symbols.

## HTTP Request

GET `/dapi/v1/ticker/price`

## Request Weight

- *1**for a single symbol,**2** when the symbol parameter is omitted

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| NO|

pair| STRING| NO|

>   *Symbol and pair cannot be sent together
>  *If a pair is sent,tickers for all symbols of the pair will be returned
>  * If either a pair or symbol is sent, tickers for all symbols of all pairs will be returned
>

## Response Example


    [
        {
              "symbol": "BTCUSD_200626",
              "ps": "9647.8",              // pair
              "price": "9647.8",
              "time": 1591257246176
        }
    ]


  - [API Description](</docs/derivatives/coin-margined-futures/market-data/Symbol-Price-Ticker#api-description>)
  - [HTTP Request](</docs/derivatives/coin-margined-futures/market-data/Symbol-Price-Ticker#http-request>)
  - [Request Weight](</docs/derivatives/coin-margined-futures/market-data/Symbol-Price-Ticker#request-weight>)
  - [Request Parameters](</docs/derivatives/coin-margined-futures/market-data/Symbol-Price-Ticker#request-parameters>)
  - [Response Example](</docs/derivatives/coin-margined-futures/market-data/Symbol-Price-Ticker#response-example>)
