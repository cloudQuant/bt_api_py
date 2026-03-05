# Block Trading

Block trading allows institutional traders to execute large orders through RFQ (Request for Quote) workflow.

## Block Trading Workflow

1. Taker creates an RFQ specifying the legs and quantities
2. Makers receive the RFQ and respond with quotes
3. Taker reviews quotes and executes the preferred one
4. Trade is settled

## REST API

### Counterparties & RFQ

| Method | Endpoint | Description |

|--------|----------|-------------|

| GET | `/api/v5/rfq/counterparties` | Get counterparties |

| POST | `/api/v5/rfq/create-rfq` | Create RFQ |

| POST | `/api/v5/rfq/cancel-rfq` | Cancel RFQ |

| POST | `/api/v5/rfq/cancel-multiple-rfqs` | Cancel multiple RFQs |

| POST | `/api/v5/rfq/cancel-all-rfqs` | Cancel all RFQs |

| POST | `/api/v5/rfq/execute-quote` | Execute quote |

### Quote Management

| Method | Endpoint | Description |

|--------|----------|-------------|

| GET | `/api/v5/rfq/quote-products` | Get quote products |

| POST | `/api/v5/rfq/set-quote-products` | Set quote products |

| POST | `/api/v5/rfq/mmp-reset` | Reset MMP status |

| POST | `/api/v5/rfq/mmp-config` | Set MMP |

| GET | `/api/v5/rfq/mmp-config` | Get MMP Config |

| POST | `/api/v5/rfq/create-quote` | Create quote |

| POST | `/api/v5/rfq/cancel-quote` | Cancel quote |

| POST | `/api/v5/rfq/cancel-multiple-quotes` | Cancel multiple quotes |

| POST | `/api/v5/rfq/cancel-all-quotes` | Cancel all quotes |

| POST | `/api/v5/rfq/cancel-all-after` | Cancel all after |

### Query

| Method | Endpoint | Description |

|--------|----------|-------------|

| GET | `/api/v5/rfq/rfqs` | Get RFQs |

| GET | `/api/v5/rfq/quotes` | Get quotes |

| GET | `/api/v5/rfq/trades` | Get trades |

| GET | `/api/v5/rfq/public-trades` | Get public multi-leg transactions |

| GET | `/api/v5/market/block-tickers` | Get block tickers |

| GET | `/api/v5/market/block-ticker` | Get block ticker |

| GET | `/api/v5/rfq/public-block-trades` | Get public single-leg transactions |

---

## WebSocket Private Channel

### RFQs channel

- **URL Path**: `/ws/v5/business` (requires login)
- **Channel**: `rfqs`

### Quotes channel

- **Channel**: `quotes`

### Structure block trades channel

- **Channel**: `struc-block-trades`

## WebSocket Public Channel

### Public structure block trades channel

- **URL Path**: `/ws/v5/public`
- **Channel**: `public-struc-block-trades`

### Public block trades channel

- **Channel**: `public-block-trades`

### Block tickers channel

- **Channel**: `block-tickers`
