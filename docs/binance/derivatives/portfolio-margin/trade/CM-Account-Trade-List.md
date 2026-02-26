On this page

# CM Account Trade List(USER_DATA)

## API Description

Get trades for a specific account and CM symbol.

## HTTP Request

GET `/papi/v1/cm/userTrades`

## Request Weight

**20** with symbol, **40** with pair

## Request Parameters

Name| Type| Mandatory| Description  
---|---|---|---  
symbol| STRING| NO|   
pair| STRING| NO|   
startTime| LONG| NO|   
endTime| LONG| NO|   
fromId| LONG| NO| Trade id to fetch from. Default gets most recent trades.  
limit| INT| NO| Default 50; max 1000.  
recvWindow| LONG| NO|   
timestamp| LONG| YES|   
  
>   * Either `symbol` or `pair` must be sent
>   * `symbol` and `pair` cannot be sent together
>   * `pair` and `fromId` cannot be sent together
>   * `OrderId` can only be sent together with symbol
>   * If a `pair` is sent, tickers for all symbols of the `pair` will be returned
>   * The parameter `fromId` cannot be sent with `startTime` or `endTime`
> 

## Response Example
    
    
    [  
        {  
            'symbol': 'BTCUSD_200626',  
            'id': 6,  
            'orderId': 28,  
            'pair': 'BTCUSD',  
            'side': 'SELL',  
            'price': '8800',  
            'qty': '1',  
            'realizedPnl': '0',  
            'marginAsset': 'BTC',  
            'baseQty': '0.01136364',  
            'commission': '0.00000454',  
            'commissionAsset': 'BTC',  
            'time': 1590743483586,  
            'positionSide': 'BOTH',  
            'buyer': false,  
            'maker': false  
        }  
    ]  
    

  * [API Description](</docs/derivatives/portfolio-margin/trade/CM-Account-Trade-List#api-description>)
  * [HTTP Request](</docs/derivatives/portfolio-margin/trade/CM-Account-Trade-List#http-request>)
  * [Request Weight](</docs/derivatives/portfolio-margin/trade/CM-Account-Trade-List#request-weight>)
  * [Request Parameters](</docs/derivatives/portfolio-margin/trade/CM-Account-Trade-List#request-parameters>)
  * [Response Example](</docs/derivatives/portfolio-margin/trade/CM-Account-Trade-List#response-example>)

