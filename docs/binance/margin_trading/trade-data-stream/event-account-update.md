
# Payload: Account Update


## Event Description​


`outboundAccountPosition` is sent any time an account balance has changed and contains the assets that were possibly changed by the event that generated the balance change.


## Event Name​


`outboundAccountPosition`


## Response Example​


```bash
{  "e": "outboundAccountPosition", //Event type  "E": 1564034571105,             //Event Time  "u": 1564034571073,             //Time of last account update  "B": [//Balances Array    {      "a": "ETH",                 //Asset      "f": "10000.000000",        //Free      "l": "0.000000"             //Locked    } ]}

```bash
