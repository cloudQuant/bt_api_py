# IBKR Web API Documentation

> Source: <https://www.interactivebrokers.com/campus/ibkr-api-page/web-api/>
> Last Updated: 2026-02-26

## Table of Contents

This documentation covers the IBKR (Interactive Brokers) Web API for integration with the bt_api_py framework.

### Core Documentation

| Document | Description |

|----------|-------------|

| Overview | API overview, connectivity, authentication, and getting started |

| [Trading](./trading.md) | Trading API, market data, orders, portfolio, and positions |

| [Account Management](./account_management.md) | Account registration, funding, reporting, and SSO |

| [API Quick Reference](./api_reference_quick.md) | 快速参考指南 - 常用端点和示例（中文） |

| [Implementation Guide](./implementation_guide.md) | 实现指南 - Python 代码示例和最佳实践（中文） |

## API Components

The IBKR Web API is split into two key components:

### 1. Account Management

- Client Registration
- Account Maintenance
- User Authentication
- Funding
- Reporting

- *Target Users**: Introducing Brokers, Financial Advisors

### 2. Trading

- Trade Management
- Portfolio Information
- Market Data Access
- Contract Information
- Brokerage Session Authentication

- *Target Users**: All IBKR clients (free of cost)

## Quick Reference

### Base URLs

| Environment | URL |

|-------------|-----|

| Production | `<https://api.interactivebrokers.com`> |

| Testing | `<https://api.test.interactivebrokers.com`> |

### Key Endpoint Groups

| Group | Prefix | Description |

|-------|--------|-------------|

| Account Management | `/gw/api/v1/` | Client accounts, funding, reporting |

| Trading | `/iserver/` | Trading, market data (requires brokerage session) |

| Contract Search | `/trsrv/` | Instrument discovery |

| Portfolio | `/portfolio/` | Account and portfolio data |

| Notifications | `/fyi/` | Alerts and notifications |

### Authentication Methods

| Method | Description | Use Case |

|--------|-------------|----------|

| OAuth 2.0 | `private_key_jwt` client authentication | Institutional/Third-party |

| OAuth 1.0a | Legacy authentication | Third-party (current) |

| SSO | Single Sign-On | Financial Advisors/IBrokers |

| Client Portal Gateway | Local Java gateway | Individual clients |

## Rate Limits

| Context | Limit |

|---------|-------|

| Global (Trading) | 50 req/sec per username |

| CP Gateway | 10 req/sec |

| Account Management | 10 req/sec per endpoint |

## Implementation Status

| Component | Status | Notes |

|-----------|--------|-------|

| Overview | ✅ Documented | |

| Trading API | ✅ Documented | REST + WebSocket |

| Account Management | ✅ Documented | Includes funding, reporting |

| Python Implementation | 🔜 In Progress | See `bt_api_py/feeds/live_ib_feed.py` |

## Support

- **Email**: api@interactivebrokers.com
- **Documentation**: <https://www.interactivebrokers.com/campus/ibkr-api-page/web-api/>
- **API Reference**: <https://www.interactivebrokers.com/api/doc.html>

---

- This documentation is maintained for integration with the bt_api_py trading framework.*
