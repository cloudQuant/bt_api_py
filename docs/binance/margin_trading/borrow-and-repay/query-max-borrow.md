
# Query Max Borrow (USER_DATA)


## API Description​


Query Max Borrow


## HTTP Request​


GET `/sapi/v1/margin/maxBorrowable`


## Request Weight​


- *50(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| asset | STRING | YES |  |

| isolatedSymbol | STRING | NO | isolated symbol |

| recvWindow | LONG | NO | The value cannot be greater than 60000 |

| timestamp | LONG | YES |  |

- If isolatedSymbol is not sent, crossed margin data will be sent.
- `borrowLimit` is also available from [<https://www.binance.com/en/margin-fee](<https://www.binance.com/en/margin-fee>)>

## Response Example​


```bash
{  "amount": "1.69248805", // account's currently max borrowable amount with sufficient system availability  "borrowLimit": "60" // max borrowable amount limited by the account level}

```bash
