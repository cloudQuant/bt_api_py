On this page

# Keepalive User Data Stream (USER_STREAM)

## API Description

Keepalive a user data stream to prevent a time out. User data streams will close after 60 minutes. It's recommended to send a ping about every 60 minutes.

## HTTP Request

PUT `/fapi/v1/listenKey`

## Request Weight

- *1**

## Request Parameters

None

## Response Example


    {
        "listenKey": "3HBntNTepshgEdjIwSUIBgB9keLyOCg5qv3n6bYAtktG8ejcaW5HXz9Vx1JgIieg" //the listenkey which got extended
    }


  - [API Description](</docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream#http-request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream#response-example>)
