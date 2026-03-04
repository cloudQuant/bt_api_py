
# Query Special key(Low Latency Trading)(TRADE)


## API Description​


Query Special Key Information.


This only applies to Special Key for Low Latency Trading.


## HTTP Request​


GET `/sapi/v1/margin/apiKey`


## Request Weight​


- *1(UID)**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| apiKey | STRING | YES |  |

| symbol | STRING | NO | isolated margin pair |

| recvWindow | LONG | NO | The value cannot be greater than 60000 |

| timestamp | LONG | YES |  |


## Response Example​


```bash
{    "apiKey":"npOzOAeLVgr2TuxWfNo43AaPWpBbJEoKezh1o8mSQb6ryE2odE11A4AoVlJbQoGx",  "ip": "0.0.0.0,192.168.0.1,192.168.0.2", // 0.0.0.0 is just an initial statereference (no extra meaning).  "apiName": "testName",  "type": "RSA",     "permissionMode": "TRADE" }

```bash
