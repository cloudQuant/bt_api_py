On this page

# Live Subscribing/Unsubscribing to streams

  - The following data can be sent through the websocket instance in order to subscribe/unsubscribe from streams. Examples can be seen below.
  - The `id` used in the JSON payloads is an unsigned INT used as an identifier to uniquely identify the messages going back and forth.

## Subscribe to a stream

> **Response**


    {
      "result": null,
      "id": 1
    }


  - **Request**

{
"method": "SUBSCRIBE",
"params":
[
"btcusd_200925@aggTrade",
"btcusd_200925@depth"
],
"id": 1
}

## Unsubscribe to a stream

> **Response**


    {
      "result": null,
      "id": 312
    }


  - **Request**

{
"method": "UNSUBSCRIBE",
"params":
[
"btcusd_200925@depth"
],
"id": 312
}

## Listing Subscriptions

> **Response**


    {
      "result": [
        "btcusd_200925@aggTrade"
      ],
      "id": 3
    }


  - **Request**

{
"method": "LIST_SUBSCRIPTIONS",
"id": 3
}

## Setting Properties

Currently, the only property can be set is to set whether `combined` stream payloads are enabled are not. The combined property is set to `false` when connecting using `/ws/` ("raw streams") and `true` when connecting using `/stream/`.

> **Response**


    {
      "result": null,
      "id": 5
    }


  - **Request**

{
"method": "SET_PROPERTY",
"params":
[
"combined",
true
],
"id": 5
}

## Retrieving Properties

> **Response**


    {
      "result": true, // Indicates that combined is set to true.
      "id": 2
    }


  - **Request**

{
"method": "GET_PROPERTY",
"params":
[
"combined"
],
"id": 2
}

  - [Subscribe to a stream](</docs/derivatives/coin-margined-futures/websocket-market-streams/Live-Subscribing-Unsubscribing-to-streams#subscribe-to-a-stream>)
  - [Unsubscribe to a stream](</docs/derivatives/coin-margined-futures/websocket-market-streams/Live-Subscribing-Unsubscribing-to-streams#unsubscribe-to-a-stream>)
  - [Listing Subscriptions](</docs/derivatives/coin-margined-futures/websocket-market-streams/Live-Subscribing-Unsubscribing-to-streams#listing-subscriptions>)
  - [Setting Properties](</docs/derivatives/coin-margined-futures/websocket-market-streams/Live-Subscribing-Unsubscribing-to-streams#setting-properties>)
  - [Retrieving Properties](</docs/derivatives/coin-margined-futures/websocket-market-streams/Live-Subscribing-Unsubscribing-to-streams#retrieving-properties>)
