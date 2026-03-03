"""
HitBTC Exchange Feed Implementation for bt_api_py

This package implements the HitBTC exchange integration for the bt_api_py framework.
Supports spot trading with REST API and WebSocket connections.

Components:
- request_base.py: Base request handler with authentication
- spot.py: Spot trading implementation
- mixins/: Additional functionality mixins

HitBTC API Reference:
- REST API: https://api.hitbtc.com/api/3
- WebSocket: wss://api.hitbtc.com/api/3/ws/public (public), wss://api.hitbtc.com/api/3/ws/trading (private)
"""