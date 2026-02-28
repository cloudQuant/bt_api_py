
# Query Special key List(Low Latency Trading)(TRADE)


## API Description​


This only applies to Special Key for Low Latency Trading.


## HTTP Request​


GET `/sapi/v1/margin/api-key-list`


## Request Weight​


- *1(UID)**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| symbol | STRING | NO | isolated margin pair |

| recvWindow | LONG | NO | The value cannot be greater than 60000 |

| timestamp | LONG | YES |  |


## Response Example​


```bash
[  {    "apiName": "testName1",    "apiKey":"znpOzOAeLVgr2TuxWfNo43AaPWpBbJEoKezh1o8mSQb6ryE2odE11A4AoVlJbQoG",    "ip": "192.168.0.1,192.168.0.2",    "type": "RSA",     "permissionMode": "TRADE"   },  {    "apiName": "testName2",    "apiKey":"znpOzOAeLVgr2TuxWfNo43AaPWpBbJEoKezh1o8mSQb6ryE2odE11A4AoVlJbQoG",    "ip": "192.168.0.1,192.168.0.2",    "type": "Ed25519",     "permissionMode": "READ"   }]

```bash
