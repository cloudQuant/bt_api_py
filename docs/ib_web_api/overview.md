# IBKR Web API Overview

> Source: <https://www.interactivebrokers.com/campus/ibkr-api-page/web-api/>
> Last Updated: 2024-12-12

## Introduction

Interactive Brokers (IBKR) RESTful Web API is designed to provide users with seamless, secure, and real-time access to their IBKR account. The Web API runs parallel to the IBKR hosted application, providing users with scalable, and efficient access to essential services.

## API Components

The IBKR Web API is split into two key components:

### Account Management

Provides solution for Introducing Brokers and Financial Advisors to preserve their current user experience and interface design while relying on IBKR's brokerage services.

Advisors and brokers can integrate with the Account Management API to manage:

- Client Registration
- Client Account Maintenance
- User Authentication
- Funding
- Reporting

### Trading

Our trading API is available to all IBKR clients **free of cost**and can be used to:

- Manage trades
- View real-time portfolio information
- Access market data
- View contract information
- Authenticate for brokerage sessions

## Connectivity

IBKR's Web API implementation follows standard HTTP verbs for communication. It employs:

- A range of HTTP status codes
- JSON-formatted messages to convey operation status and error information
- **All API requests must use HTTPS**

Authorization and Authentication for IBKR's Web API is managed using **OAuth 2.0**.

## Authentication

IBKR only supports `private_key_jwt` client authentication as described in [RFC 7521](<https://datatracker.ietf.org/doc/html/rfc7521)> and [RFC 7523](<https://datatracker.ietf.org/doc/html/rfc7523).>

### Authentication Flow

1. Client authenticates against the authorization server by presenting a signed JWT token called a `client_assertion`
2. The authorization server validates the token against the public key(s) provided by the client during registration
3. This scheme is considered safer than the standard client id/client secret authentication scheme used in early OAuth 2.0 integrations given that it prevents the client from having to pass the client secret in back-end requests

## Data Transmission

User requests will be sent to IBKR in **JSON format**using**HTTPS**.

## Documentation Resources

IBKR provides documentation for both developers AND project managers:

- **Documentation**: Long form documentation including best practices, flow charts, and descriptions to help users maximize the API's potential
- **Reference**: API reference includes detailed endpoint references, schema requirements, authentication guides, and sample request and responses

### Documentation Links

- Web API Documentation
- Web API Reference
- Web API v1.0 Documentation
- Web API Changelog

## Getting Started

### For Retail Clients

For retail and individual clients, Authentication to our WebAPI is managed using the **Client Portal Gateway**, a small java program used to route local web requests with appropriate authentication.

### For Institutional or Third Party

For enterprise integrations, IBKR has a designated API Solutions team. To get started, contact **api-solutions@interactivebrokers.com**with the following information:

- Firm Name
- Firm Type (i.e. Introducing Broker, Financial Advisor OR Third Party Service Provider)
- API Services which you are interested in using (i.e. Registration, Funding, Single Sign On, View Portfolio Data, Trading, Reporting)
- Describe intended usage (1-2 sentences)

## Feedback

Have feedback on Web API documentation or reference material?

Email:**API-Feedback@interactivebrokers.com**

> Note: This is an automated feedback inbox and will not have active responses. For specific answers or additional support, contact the API Support team or access general support. Current or prospective institutional clients may contact their sales representative.

## Additional Resources

- [Web API Trading Documentation](<https://www.interactivebrokers.com/campus/ibkr-api-page/web-api/)>
- [Web API Account Management Documentation](<https://www.interactivebrokers.com/campus/ibkr-api-page/web-api/)>
- [IBKR Campus](<https://www.interactivebrokers.com/campus/)>

## Related API Options

IBKR also offers other API solutions:

- **TWS API**- Java/Python/C++ API for desktop trading
- **Excel API**- DDE, RTD, and ActiveX integration
- **FIX Protocol**- Financial Information eXchange protocol for institutional trading
- **Third-Party Integrations** - Available integrations with various trading platforms
