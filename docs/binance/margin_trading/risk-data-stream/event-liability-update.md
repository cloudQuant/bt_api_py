
# Payload: liability update


## Event Description​


Liability update during the following :

- borrowing
- Repayment
- Interest Calculation

## Event Name​


`USER_LIABILITY_CHANGE`


## Response Example​


> Payload:


```bash
{  "e": "USER_LIABILITY_CHANGE", // Event Type  "E": 1701949801133, // Event Time  "a": "BTC", // Asset  "t": "BORROW", // Liability Update Type  "p": "0.00000100", // Principle Quantity  "i": "0.00000000" // Interest Quantity}

```bash
