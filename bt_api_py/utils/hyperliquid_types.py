"""
Hyperliquid API type definitions

Defines constants for Hyperliquid order types and other API parameters.
"""

# Order types
LIMIT_ORDER = "limit"
MARKET_ORDER = "market"
TRIGGER_ORDER = "trigger"

# Time in force
TIF_GTC = "Gtc"  # Good till canceled
TIF_IOC = "Ioc"  # Immediate or cancel
TIF_POST_ONLY = "PostOnly"  # Post only order

# Order sides
SIDE_BUY = True
SIDE_SELL = False

# Order status
STATUS_NEW = "NEW"
STATUS_PENDING_CANCEL = "PENDING_CANCEL"
STATUS_FILLED = "FILLED"
STATUS_CANCELED = "CANCELED"
STATUS_REJECTED = "REJECTED"
STATUS_EXPIRED = "EXPIRED"
STATUS_OPEN = "OPEN"
STATUS_CLOSED = "CLOSED"

# Trigger types
TRIGGER_STOP = "sl"  # Stop loss
TRIGGER_TAKE_PROFIT = "tp"  # Take profit