# Network-Dependent Tests

These tests require **real network access**, exchange API credentials, or external
services (SimNow CTP, Binance, OKX, etc.). They are excluded from the normal
`pytest tests` run.

Several files that were previously stored here but did **not** require real
network access have been moved into the main `tests/` suite.

## Structure

- `feeds/` — Exchange-specific feed tests that still require real network access
- `integration/` — Exchange integration tests (Bitfinex, Bitget, Gemini, etc.)
- `live/` — SimNow CTP live connection tests
- Root network files:
  - `test_hitbtc_network.py`
  - `test_ctp_feed_network.py`

## Moved to `tests/`

The following non-network tests were moved out of this folder and now run as part
of the normal `pytest tests` suite:

- `tests/test_tick_writer.py`
- `tests/functions/test_binance_sign.py`
- `tests/test_hitbtc_integration.py`
- `tests/test_ctp_feed.py`

## Prerequisites

- Exchange API credentials in `configs/account_config.yaml`
- SimNow CTP credentials in `.env` (for CTP network tests)
- Optional: `hypothesis` package (for tests outside this folder that still use it)

## Run manually

```bash
# From project root
pytest examples/network_tests -v

# Run HitBTC real-network tests
pytest examples/network_tests/test_hitbtc_network.py -v

# Run SimNow CTP real-network tests
pytest examples/network_tests/test_ctp_feed_network.py -v -s

# Run specific exchange feed tests
pytest examples/network_tests/feeds/ -v
```
