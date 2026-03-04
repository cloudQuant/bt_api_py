
# Payload: Order update


## Event Description​


Orders are updated with the `executionReport` event.


- *Execution types:**

- NEW - The order has been accepted into the engine.
- CANCELED - The order has been canceled by the user.
- REPLACED (currently unused)
- REJECTED - The order has been rejected and was not processed (This message appears only with Cancel Replace Orders wherein the new order placement is rejected but the request to cancel request succeeds.)
- TRADE - Part of the order or all of the order's quantity has filled.
- EXPIRED - The order was canceled according to the order type's rules (e.g. LIMIT FOK orders with no fill, LIMIT IOC or MARKET orders that partially fill) or by the exchange, (e.g. orders canceled during liquidation, orders canceled during maintenance).
- TRADE_PREVENTION - The order has expired due to STP trigger.

Check the [Public API Definitions](/docs/margin_trading/trade-data-stream/Event-Order-Update#public-api-definitions) for more relevant enum definitions.


These are fields that appear in the payload only if certain conditions are met.


| Field | Name | Description | Examples |

| --- | --- | --- | --- |

| d | Trailing Delta | Appears only for trailing stop orders. | "d": 4 |

| D | Trailing Time | "D": 1668680518494 |  |

| j | Strategy Id | Appears only if the strategyId parameter was provided upon order placement. | "j": 1 |

| J | Strategy Type | Appears only if the strategyType parameter was provided upon order placement. | "J": 1000000 |

| v | Prevented Match Id | Appears only for orders that expired due to STP. | "v": 3 |

| A | Prevented Quantity | "A":"3.000000" |  |

| B | Last Prevented Quantity | "B":"3.000000" |  |

| u | Trade Group Id | "u":1 |  |

| U | Counter Order Id | "U":37 |  |

| Cs | Counter Symbol | "Cs": "BTCUSDT" |  |

| pl | Prevented Execution Quantity | "pl":"2.123456" |  |

| pL | Prevented Execution Price | "pL":"0.10000001" |  |

| pY | Prevented Execution Quote Qty | "pY":"0.21234562" |  |

| W | Working Time | Appears when the order is working on the book | "W": 1668683798379 |

| b | Match Type | Appears for orders that have allocations | "b":"ONE_PARTY_TRADE_REPORT" |

| a | Allocation ID | "a":1234 |  |

| k | Working Floor | Appears for orders that could potentially have allocations | "k":"SOR" |

| uS | UsedSor | Appears for orders that used SOR | "uS":true |


## Event Name​


`executionReport`


## Response Example​


> Payload:


```bash
{  "e": "executionReport",        // Event type  "E": 1499405658658,            // Event time  "s": "ETHBTC",                 // Symbol  "c": "mUvoqJxFIILMdfAW5iGSOW", // Client order ID  "S": "BUY",                    // Side  "o": "LIMIT",                  // Order type  "f": "GTC",                    // Time in force  "q": "1.00000000",             // Order quantity  "p": "0.10264410",             // Order price  "P": "0.00000000",             // Stop price  "F": "0.00000000",             // Iceberg quantity  "g": -1,                       // OrderListId  "C": "",                       // Original client order ID; This is the ID of the order being canceled  "x": "NEW",                    // Current execution type  "X": "NEW",                    // Current order status  "r": "NONE",                   // Order reject reason; will be an error code.  "i": 4293153,                  // Order ID  "l": "0.00000000",             // Last executed quantity  "z": "0.00000000",             // Cumulative filled quantity  "L": "0.00000000",             // Last executed price  "n": "0",                      // Commission amount  "N": null,                     // Commission asset  "T": 1499405658657,            // Transaction time  "t": -1,                       // Trade ID  "I": 8641984,                  // Ignore  "w": true,                     // Is the order on the book?  "m": false,                    // Is this trade the maker side?  "M": false,                    // Ignore  "O": 1499405658657,            // Order creation time  "Z": "0.00000000",             // Cumulative quote asset transacted quantity  "Y": "0.00000000",             // Last quote asset transacted quantity (i.e. lastPrice * lastQty)  "Q": "0.00000000",             // Quote Order Quantity  "W": 1499405658657,            // Working Time; This is only visible if the order has been placed on the book.  "V": "NONE"                    // selfTradePreventionMode}

```bash
If the order is an OCO, an event will be displayed named `ListStatus` in addition to the `executionReport` event.


```bash
{  "e": "listStatus",                //Event Type  "E": 1564035303637,               //Event Time  "s": "ETHBTC",                    //Symbol  "g": 2,                           //OrderListId  "c": "OCO",                       //Contingency Type  "l": "EXEC_STARTED",              //List Status Type  "L": "EXECUTING",                 //List Order Status  "r": "NONE",                      //List Reject Reason  "C": "F4QN4G8DlFATFlIUQ0cjdD",    //List Client Order ID  "T": 1564035303625,               //Transaction Time  "O": [//An array of objects    {      "s": "ETHBTC",                //Symbol      "i": 17,                      // orderId      "c": "AJYsMjErWJesZvqlJCTUgL" //ClientOrderId    },    {      "s": "ETHBTC",      "i": 18,      "c": "bfYPSQdLoqAJeNrOr9adzq"    } ]}

```bash
