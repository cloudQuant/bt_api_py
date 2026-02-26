On this page

# Event: Order update

## Event Description

  * trade related Update under the following conditions: 
    * Order fills
    * Order placed
    * Order cancelled

## Event Name

`ORDER_TRADE_UPDATE`

## Update Speed

**50ms**

## Response Example
    
    
    {  
      "e":"ORDER_TRADE_UPDATE",           //Event Type  
      "E":1657613775883,                  //Event Time   
      "o":[  
        {  
          "T":1657613342918,              //Order Create Time  
          "t":1657613342918,              //Order Update Time  
          "s":"BTC-220930-18000-C",       //Symbol  
          "c":"",                         //clientOrderId  
          "oid":"4611869636869226548",    //order id  
          "p":"1993",                     //order price  
          "q":"1",                        //order quantity (positive for BUY, negative for SELL)  
          "stp":0,                        //not used for now  
          "r":false,                      //reduce only  
          "po":true,                      //post only  
          "S":"PARTIALLY_FILLED",         //status  
          "e":"0.1",                      //completed trade volume(in contracts)         
          "ec":"199.3",                   //completed trade amount(in quote asset)   
          "f":"2",                        //fee   
          "tif": "GTC",                   //time in force   
          "oty":"LIMIT",                  //order type  
          "fi":[  
            {  
              "t":"20",                   //tradeId  
              "p":"1993",                 //trade price  
              "q":"0.1",                  //trade quantity  
              "T":1657613774336,          //trade time  
              "m":"TAKER"                 //taker or maker  
              "f":"0.0002"                //commission(>0) or rebate(<0)  
            }  
          ]  
        }  
      ]  
    }  
    

  * [Event Description](</docs/derivatives/option/user-data-streams/Event-Order-update#event-description>)
  * [Event Name](</docs/derivatives/option/user-data-streams/Event-Order-update#event-name>)
  * [Update Speed](</docs/derivatives/option/user-data-streams/Event-Order-update#update-speed>)
  * [Response Example](</docs/derivatives/option/user-data-streams/Event-Order-update#response-example>)

