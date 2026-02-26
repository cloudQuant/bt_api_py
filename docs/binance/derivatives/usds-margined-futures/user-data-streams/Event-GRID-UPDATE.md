On this page

# Event: GRID_UPDATE

## Event Description

`GRID_UPDATE` update when a sub order of a grid is filled or partially filled. **Strategy Status**

  * NEW
  * WORKING
  * CANCELLED
  * EXPIRED

## Event Name

`GRID_UPDATE`

## Response Example
    
    
    {  
    	"e": "GRID_UPDATE", // Event Type  
    	"T": 1669262908216, // Transaction Time  
    	"E": 1669262908218, // Event Time  
    	"gu": {   
    			"si": 176057039, // Strategy ID  
    			"st": "GRID", // Strategy Type  
    			"ss": "WORKING", // Strategy Status  
    			"s": "BTCUSDT", // Symbol  
    			"r": "-0.00300716", // Realized PNL  
    			"up": "16720", // Unmatched Average Price  
    			"uq": "-0.001", // Unmatched Qty  
    			"uf": "-0.00300716", // Unmatched Fee  
    			"mp": "0.0", // Matched PNL  
    			"ut": 1669262908197 // Update Time  
    		   }  
    }  
    

  * [Event Description](</docs/derivatives/usds-margined-futures/user-data-streams/Event-GRID-UPDATE#event-description>)
  * [Event Name](</docs/derivatives/usds-margined-futures/user-data-streams/Event-GRID-UPDATE#event-name>)
  * [Response Example](</docs/derivatives/usds-margined-futures/user-data-streams/Event-GRID-UPDATE#response-example>)

