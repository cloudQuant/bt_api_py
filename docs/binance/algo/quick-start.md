# Quick Start

> 来源: [Binance Algo Trading API](https://developers.binance.com/docs/algo/quick-start)

## API Key Setup

- Some endpoints will require an API Key. Please refer to [this page](https://www.binance.com/en/support/articles/360002502072) regarding API key creation.
- Once API key is created, it is recommended to set IP restrictions on the key for security reasons.
- **Never share your API key/secret key to ANYONE.**

> If the API keys were accidentally shared, please delete them immediately and create a new key.

## API Key Restrictions

- After creating the API key, the default restrictions is `Enable Reading`.
- To **enable withdrawals via the API**, the API key restriction needs to be modified through the Binance UI.

## Enabling Accounts

### Spot Account

A `SPOT` account is provided by default upon creation of a Binance Account.

### Margin Account

To enable a `MARGIN` account for Margin Trading, please refer to the [Margin Trading Guide](https://www.binance.vision/tutorials/binance-margin-trading-guide)

## API Library

### Python connector

This is a lightweight library that works as a connector to Binance public API, written in Python.

https://github.com/binance/binance-connector-python

### Javascript connector

This is a lightweight library that works as a connector to Binance public API, written for JavaScript users.

https://github.com/binance/binance-connector-js

### Ruby connector

This is a lightweight library that works as a connector to Binance public API, written for Ruby users.

https://github.com/binance/binance-connector-ruby

### DotNET connector

This is a lightweight library that works as a connector to Binance public API, written for C# users.

https://github.com/binance/binance-connector-dotnet

### Java connector

This is a lightweight library that works as a connector to Binance public API, written for Java users.

https://github.com/binance/binance-connector-java

### Postman Collections

There is now a Postman collection containing the API endpoints for quick and easy use.

This is recommended for new users who want to get a quick-start into using the API.

For more information please refer to this page: [Binance API Postman](https://github.com/binance/binance-api-postman)

### Swagger

A YAML file with OpenAPI specification on the RESTful API is available to be used, as well as a Swagger UI page for the consulting.

https://github.com/binance/binance-api-swagger
