
# Query Max Transfer-Out Amount (USER_DATA)


## API Description​


Query Max Transfer-Out Amount


## HTTP Request​


GET `/sapi/v1/margin/maxTransferable`


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

## Response Example​


```bash
 {      "amount": "3.59498107" }

```bash
