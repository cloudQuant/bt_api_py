
# listenToken Subscription Methods


## Create Margin Account listenToken (USER_STREAM)​


### Description​


Create a listenToken that authorizes the user to access the User Data Stream of the current account for a limited amout of time.  The stream's validity is specified by the validity parameter (milliseconds), default 24 hours, maximum 24 hours. The response includes the listenToken and the corresponding expirationTime (in milliseconds).


### HTTP Request​


**POST** `/sapi/v1/userListenToken`


**Request weight (UID)**: 1


### Request Parameters​


| Name | Type | Required | Description |
| --- | --- | --- | --- |
| symbol | STRING | CONDITIONAL | Trading pair symbol; required when isIsolated is true, e.g., BNBUSDT |
| isIsolated | BOOLEAN | NO | Whether it is isolated margin; true means isolated; default is cross margin |
| validity | LONG | NO | Validity in milliseconds; default 24 hours, maximum 24 hours |


### Notes​

- The token validity is determined by the validity parameter; default is 24 hours, maximum 24 hours. expirationTime = current time + validity.
- The response returns the token and expirationTime.

### Response Example​


```
{  "token": "6xXxePXwZRjVSHKhzUCCGnmN3fkvMTXru+pYJS8RwijXk9Vcyr3rkwfVOTcP2OkONqciYA",  "expirationTime": 1758792204196}
```


## Subscribe to User Data Stream using listenToken (USER_STREAM)​


### Description​


Subscribe to the user data stream using listenToken.


This method must be called on the WebSocket API. For more information about how to use the WebSocket API, see : [WebSocket API documentation](https://developers.binance.com/docs/binance-spot-api-docs/websocket-api/general-api-information)


### method​


`userDataStream.subscribe.listenToken`


### Request Example​


```
{  "id": "f3a8f7a29f2e54df796db582f3d",  "method": "userDataStream.subscribe.listenToken",  "params": {    "listenToken": "5DbylArkmImhyHkpG6s9tbiFy5uAMTFwzx9vwsFjDv9dC3GkKxSuoTCj0HvcJC0WYi8fA"  }}
```


### Request weight: 2​


### Request Parameters​


| Name | Type | Required | Description |
| --- | --- | --- | --- |
| listenToken | STRING | YES | The listen token |


### Notes​

- Non-authenticated sessions are allowed to use this feature.
- If the listenToken is invalid, an error **-1209** will be returned.
- The subscription is not automatically renewed by the WebSocket API. To extend the validity of your subscription, you must call `/sapi/v1/userListenToken` before the expiration of your current subscription, obtain a new listenToken with an updated expirationTime, and call `userDataStream.subscribe.listenToken` again passing the new listenToken. This will seamlessly extend your subscription to the new expirationDate.
- If the subscription is not extended, it will expire and you will receive a `eventStreamTerminated` event (see example below).
- You can receive the events in SBE instead of JSON if you require better performance. See the [Simple Binary Encoding (SBE) FAQ](https://developers.binance.com/docs/binance-spot-api-docs/faqs/sbe_faq) for more details.

### Response Example​


```
{  "subscriptionId": 1,  "expirationTime": 1749094553955907}
```


### Subscription Expiration Example​


```
{  "subscriptionId": 0,  "event": {    "e": "eventStreamTerminated",    "E": 1759089357377  }}
```
