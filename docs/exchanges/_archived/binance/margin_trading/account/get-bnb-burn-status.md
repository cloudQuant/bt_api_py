
# Get BNB Burn Status (USER_DATA)


## API Description​


Get BNB Burn Status


## HTTP Request​


GET `/sapi/v1/bnbBurn`


## Request Weight​


- *1(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| recvWindow | LONG | NO | No more than 60000 |

| timestamp | LONG | YES |  |


## Response Example​


```bash
{   "spotBNBBurn":true,   "interestBNBBurn": false   }

```bash
