On this page

# Keepalive User Data Stream (USER_STREAM)

## API Description

Keepalive a user data stream to prevent a time out. User data streams will close after 60 minutes. It's recommended to send a ping about every 60 minutes.

## Method

`userDataStream.ping`

## Request
    
    
    {  
      "id": "815d5fce-0880-4287-a567-80badf004c74",  
      "method": "userDataStream.ping",  
      "params": {  
        "listenKey": "xs0mRXdAKlIPDRFrlPcw0qI41Eh3ixNntmymGyhrhgqo7L6FuLaWArTD7RLP",  
        "apiKey": "vmPUZE6mv9SD5VNHk9HlWFsOr9aLE2zvsw0MuIgwCIPy8atIco14y7Ju91duEh8A"  
       }  
    }  
    

## Request Weight

**1**

## Request Parameters

None

## Response Example
    
    
    {  
      "id": "815d5fce-0880-4287-a567-80badf004c74",  
      "status": 200,  
      "result": {  
        "listenKey": "3HBntNTepshgEdjIwSUIBgB9keLyOCg5qv3n6bYAtktG8ejcaW5HXz9Vx1JgIieg"  
      },  
      "rateLimits": [  
        {  
          "rateLimitType": "REQUEST_WEIGHT",  
          "interval": "MINUTE",  
          "intervalNum": 1,  
          "limit": 2400,  
          "count": 2  
        }  
      ]  
    }  
    

  * [API Description](</docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream-Wsp#api-description>)
  * [Method](</docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream-Wsp#method>)
  * [Request](</docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream-Wsp#request>)
  * [Request Weight](</docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream-Wsp#request-weight>)
  * [Request Parameters](</docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream-Wsp#request-parameters>)
  * [Response Example](</docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream-Wsp#response-example>)

