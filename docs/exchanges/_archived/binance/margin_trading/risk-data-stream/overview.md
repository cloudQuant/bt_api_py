
# User Data Streams Connect

- Margin websocket only support Cross Margin Accounts
- The base API endpoint is: **<https://api.binance.com**>
- A User Data Stream `listenKey` is valid for 60 minutes after creation.
- Doing a `PUT` on a `listenKey` will extend its validity for 60 minutes.
- Doing a `DELETE` on a `listenKey` will close the stream and invalidate the `listenKey` .
- Doing a `POST` on an account with an active `listenKey` will return the currently active `listenKey` and extend its validity for 60 minutes.
- A `listenKey` is a stream.
- Users can listen to multiple streams.
- The base websocket endpoint is: **wss://margin-stream.binance.com**
- User Data Streams are accessed at **/ws/<listenKey>**or**/stream?streams=<listenKey>**
- A single connection to **stream.binance.com** is only valid for 24 hours; expect to be disconnected at the 24 hour mark
