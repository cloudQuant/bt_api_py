On this page

# New Symbol Info

## Stream Description

New symbol listing stream.

## Stream Name

`option_pair`

## Update Speed

- *50ms**

## Response Example


    {
        "e":"OPTION_PAIR",         //eventType
        "E":1668573571842,         //eventTime
        "u":"BTCUSDT",             //Underlying index of the contract
        "qa":"USDT",               //Quotation asset
        "s":"BTC-221116-21000-C",  //Trading pair name
        "unit":1,                  //Conversion ratio, the quantity of the underlying asset represented by a single contract.
        "mq":"0.01",               //Minimum trade volume of the underlying asset
        "d":"CALL",                //Option type
        "sp":"21000",              //Strike price
        "ed":1668585600000         //expiration time
    }


  - [Stream Description](</docs/derivatives/option/websocket-market-streams/New-Symbol-Info#stream-description>)
  - [Stream Name](</docs/derivatives/option/websocket-market-streams/New-Symbol-Info#stream-name>)
  - [Update Speed](</docs/derivatives/option/websocket-market-streams/New-Symbol-Info#update-speed>)
  - [Response Example](</docs/derivatives/option/websocket-market-streams/New-Symbol-Info#response-example>)
