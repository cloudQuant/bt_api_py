from __future__ import annotations

import importlib.abc
import importlib.metadata
import json
import re
import socket
import sys
from pathlib import Path

BLOCKED_PREFIXES = (
    "zmq",
    "bt_api_transport_zmq",
    "bt_api_gateway",
    "bt_api_execution",
    "bt_api_risk",
    "bt_api_monitor",
)
BLOCKED: list[str] = []
NETWORK: list[str] = []
class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + ".") for p in BLOCKED_PREFIXES):
            BLOCKED.append(fullname)
            raise ModuleNotFoundError("service domain import blocked in offline backtest", name=fullname)
        return None
sys.meta_path.insert(0, Blocker())
def no_network(*args, **kwargs):
    NETWORK.append("socket")
    raise AssertionError("network call attempted in local-only Cerebro run")
socket.create_connection = no_network
socket.socket.connect = no_network
socket.socket.connect_ex = no_network

import backtrader as bt
import pandas as pd

rows = [
    ("2024-01-02", 10.0, 10.5, 9.8, 10.2, 100),
    ("2024-01-03", 10.2, 10.7, 10.0, 10.4, 120),
    ("2024-01-04", 10.4, 10.8, 10.1, 10.6, 130),
]
frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
frame["date"] = pd.to_datetime(frame["date"])
frame = frame.set_index("date")
class OfflineStrategy(bt.Strategy):
    def __init__(self):
        self.seen = 0
    def next(self):
        self.seen += 1

cerebro = bt.Cerebro(stdstats=False)
cerebro.adddata(bt.feeds.PandasData(dataname=frame))
cerebro.addstrategy(OfflineStrategy)
results = cerebro.run()
assert len(results) == 1
assert results[0].seen == len(rows)
assert not BLOCKED, BLOCKED
assert not NETWORK, NETWORK
backtrader_path = Path(bt.__file__).resolve()
prefix = Path(sys.prefix).resolve()
assert backtrader_path.is_relative_to(prefix), (backtrader_path, prefix)
installed = {re.sub(r"[-_.]+", "-", d.metadata.get("Name", "")).lower() for d in importlib.metadata.distributions()}
for name in ("bt-api-gateway", "bt-api-execution", "bt-api-risk", "bt-api-monitor", "bt-api-transport-zmq"):
    assert name not in installed, name
print(json.dumps({
    "backtrader_origin": str(backtrader_path),
    "backtrader_version": importlib.metadata.version("backtrader"),
    "sys_prefix": str(prefix),
    "bars_processed": results[0].seen,
    "strategy_results": len(results),
    "input": "synthetic in-memory local bars",
    "domain_distributions_absent": True,
    "blocked_import_attempts": BLOCKED,
    "network_attempts": NETWORK,
}, sort_keys=True))
