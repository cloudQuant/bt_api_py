
# Query Enabled Isolated Margin Account Limit (USER_DATA)


## API Description​


Query enabled isolated margin account limit.


## HTTP Request​


GET `/sapi/v1/margin/isolated/accountLimit`


## Request Weight​


- *1(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| recvWindow | LONG | NO | No more than 60000 |

| timestamp | LONG | YES |  |


## Response Example​


```bash
{  "enabledAccount": 5,  "maxAccount": 20}

```bash
