On this page

# Close User Data Stream (USER_STREAM)

## API Description

Close out a user data stream.

## Method

`userDataStream.stop`

## Request


    {
      "id": "819e1b1b-8c06-485b-a13e-131326c69599",
      "method": "userDataStream.stop",
      "params": {
        "listenKey": "xs0mRXdAKlIPDRFrlPcw0qI41Eh3ixNntmymGyhrhgqo7L6FuLaWArTD7RLP",
        "apiKey": "vmPUZE6mv9SD5VNHk9HlWFsOr9aLE2zvsw0MuIgwCIPy8atIco14y7Ju91duEh8A"
      }
    }


## Request Weight

- *5**

## Request Parameters

None

## Response Example


    {
      "id": "819e1b1b-8c06-485b-a13e-131326c69599",
      "status": 200,
      "result": {},
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


  - [API Description](</docs/derivatives/usds-margined-futures/user-data-streams/Close-User-Data-Stream-Wsp#api-description>)
  - [Method](</docs/derivatives/usds-margined-futures/user-data-streams/Close-User-Data-Stream-Wsp#method>)
  - [Request](</docs/derivatives/usds-margined-futures/user-data-streams/Close-User-Data-Stream-Wsp#request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/user-data-streams/Close-User-Data-Stream-Wsp#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/user-data-streams/Close-User-Data-Stream-Wsp#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/user-data-streams/Close-User-Data-Stream-Wsp#response-example>)
