from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.utils.time import convert_utc_timestamp


class GeminiRequestOrderBookData(RequestData):
    """Gemini Order Book Data Container"""

    def __init__(
        self,
        data,
        symbol=None,
        asset_type=None,
        is_rest=True,
        extra_data=None,
        status=False,
        normalize_func=None,
    ):
        # Handle positional arguments from test
        if extra_data is None:
            extra_data = {}

        # Set exchange_name and symbol_name in extra_data for RequestData
        extra_data.setdefault("exchange_name", "GEMINI")
        if symbol:
            extra_data.setdefault("symbol_name", symbol)
        if asset_type:
            extra_data.setdefault("asset_type", asset_type)

        super().__init__(data, extra_data, status, normalize_func)
        self.symbol = symbol
        self.asset_type = asset_type
        self.is_rest = is_rest

        # Default values
        self.bids = []
        self.asks = []
        self.timestamp = None
        self.exchange_timestamp = None
        self.bids_limit = 50
        self.asks_limit = 50

        if data:
            self._parse_data(data)

    def _parse_data(self, data):
        """Parse Gemini API response"""
        if self.is_rest:
            self._parse_rest_data(data)
        else:
            self._parse_wss_data(data)

    def _parse_rest_data(self, data):
        """Parse REST API response"""
        if isinstance(data, dict):
            # Parse bids - handle both list of lists and list of dicts
            if "bids" in data:
                self.bids = []
                for bid in data["bids"]:
                    if isinstance(bid, dict):
                        self.bids.append(
                            {
                                "price": float(bid.get("price", 0)),
                                "amount": float(bid.get("amount", 0)),
                            }
                        )
                    elif len(bid) >= 2:
                        self.bids.append(
                            {
                                "price": float(bid[0]),
                                "amount": float(bid[1]),
                            }
                        )

            # Parse asks - handle both list of lists and list of dicts
            if "asks" in data:
                self.asks = []
                for ask in data["asks"]:
                    if isinstance(ask, dict):
                        self.asks.append(
                            {
                                "price": float(ask.get("price", 0)),
                                "amount": float(ask.get("amount", 0)),
                            }
                        )
                    elif len(ask) >= 2:
                        self.asks.append(
                            {
                                "price": float(ask[0]),
                                "amount": float(ask[1]),
                            }
                        )

            self.timestamp = data.get("timestampms")
            self.exchange_timestamp = convert_utc_timestamp(self.timestamp)

    def _parse_wss_data(self, data):
        """Parse WebSocket response"""
        if isinstance(data, dict):
            if data.get("type") == "update" and "events" in data:
                events = data.get("events", [])
                for event in events:
                    if event.get("type") == "change":
                        side = event.get("side")
                        price = float(event.get("price", 0))
                        remaining = float(event.get("remaining", 0))

                        if side == "bid":
                            # Update or remove bid
                            found = False
                            for i, bid in enumerate(self.bids):
                                if bid["price"] == price:
                                    if remaining > 0:
                                        self.bids[i]["amount"] = remaining
                                        found = True
                                    else:
                                        self.bids.pop(i)
                                    break

                            if not found and remaining > 0:
                                self.bids.append({"price": price, "amount": remaining})

                            # Sort bids (descending)
                            self.bids.sort(key=lambda x: x["price"], reverse=True)

                        elif side == "ask":
                            # Update or remove ask
                            found = False
                            for i, ask in enumerate(self.asks):
                                if ask["price"] == price:
                                    if remaining > 0:
                                        self.asks[i]["amount"] = remaining
                                        found = True
                                    else:
                                        self.asks.pop(i)
                                    break

                            if not found and remaining > 0:
                                self.asks.append({"price": price, "amount": remaining})

                            # Sort asks (ascending)
                            self.asks.sort(key=lambda x: x["price"])

    # Getter methods for compatibility with tests
    def get_symbol_name(self):
        return self.symbol

    def get_top_bid(self):
        """Get the highest bid price"""
        if self.bids:
            return self.bids[0]
        return None

    def get_top_ask(self):
        """Get the lowest ask price"""
        if self.asks:
            return self.asks[0]
        return None

    def get_spread(self):
        """Get bid-ask spread"""
        top_bid = self.get_top_bid()
        top_ask = self.get_top_ask()

        if top_bid and top_ask:
            return top_ask["price"] - top_bid["price"]
        return None

    def get_mid_price(self):
        """Get mid price (average of bid and ask)"""
        top_bid = self.get_top_bid()
        top_ask = self.get_top_ask()

        if top_bid and top_ask:
            return (top_bid["price"] + top_ask["price"]) / 2
        return None

    def limit_depth(self, bids_limit=None, asks_limit=None):
        """Limit the depth of order book"""
        if bids_limit is not None:
            self.bids_limit = bids_limit
        if asks_limit is not None:
            self.asks_limit = asks_limit

        self.bids = self.bids[: self.bids_limit]
        self.asks = self.asks[: self.asks_limit]

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "is_rest": self.is_rest,
            "bids": self.bids,
            "asks": self.asks,
            "timestamp": self.timestamp,
            "exchange_timestamp": self.exchange_timestamp,
            "top_bid": self.get_top_bid(),
            "top_ask": self.get_top_ask(),
            "spread": self.get_spread(),
            "mid_price": self.get_mid_price(),
        }

    def __str__(self):
        """String representation"""
        return (
            f"GeminiOrderBook(symbol={self.symbol}, bids={len(self.bids)}, asks={len(self.asks)})"
        )


class GeminiSpotWssOrderBookData(GeminiRequestOrderBookData):
    """Gemini Spot WebSocket Order Book Data"""

    def __init__(self, data, symbol=None, asset_type=None):
        super().__init__(data, symbol, asset_type, is_rest=False)
