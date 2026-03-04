
# Get Interest History (USER_DATA)


## API Description​


Get Interest History


## HTTP Request​


GET `/sapi/v1/margin/interestHistory`


## Request Weight​


- *1(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| asset | STRING | NO |  |

| isolatedSymbol | STRING | NO | isolated symbol |

| startTime | LONG | NO |  |

| endTime | LONG | NO |  |

| current | LONG | NO | Currently querying page. Start from 1. Default:1 |

| size | LONG | NO | Default:10 Max:100 |

| recvWindow | LONG | NO | The value cannot be greater than 60000 |

| timestamp | LONG | YES |  |

- Response in descending order
- If isolatedSymbol is not sent, crossed margin data will be returned
- The max interval between `startTime` and `endTime` is 30 days.  It is a MUST to ensure data correctness.
- If `startTime` and `endTime` not sent, return records of the last 7 days by default.
- If `startTime` is sent and `endTime` is not sent, return records of [max( `startTime` , now-30d), now].
- If `startTime` is not sent and `endTime` is sent, return records of [`endTime` -7, `endTime`]
- `type` in response has 4 enums:
  - `PERIODIC` interest charged per hour
  - `ON_BORROW` first interest charged on borrow
  - `PERIODIC_CONVERTED` interest charged per hour converted into BNB
  - `ON_BORROW_CONVERTED` first interest charged on borrow converted into BNB
  - `PORTFOLIO` interest charged daily on the portfolio margin negative balance

## Response Example​


```bash
{  "rows": [{                  "txId": 1352286576452864727,                 "interestAccuredTime": 1672160400000,                  "asset": "USDT",       "rawAsset": “USDT”,  // will not be returned for isolated margin                 "principal": "45.3313",                  "interest": "0.00024995",                  "interestRate": "0.00013233",                  "type": "ON_BORROW",                 "isolatedSymbol": "BNBUSDT"  // isolated symbol, will not be returned for crossed margin          } ],  "total": 1}

```bash
