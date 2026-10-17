from __future__ import annotations

import importlib.abc
import importlib.metadata
import json
import re
import socket
import sys
from pathlib import Path

ATTEMPTS: list[str] = []
NETWORK_ATTEMPTS: list[str] = []
BLOCKED_PREFIXES = (
    "zmq",
    "bt_api_transport_zmq",
    "bt_api_gateway",
    "bt_api_execution",
    "bt_api_risk",
    "bt_api_monitor",
)

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in BLOCKED_PREFIXES):
            ATTEMPTS.append(fullname)
            raise ModuleNotFoundError("optional service/ZMQ import blocked", name=fullname)
        return None

sys.meta_path.insert(0, Blocker())

def no_network(*args, **kwargs):
    NETWORK_ATTEMPTS.append("socket")
    raise AssertionError("network call attempted during offline DirectBackend smoke")

socket.create_connection = no_network
socket.socket.connect = no_network
socket.socket.connect_ex = no_network

from bt_api_py.bt_api import BtApi
from bt_api_py._direct_backend import DirectBackend

class LocalFeed:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []
        self.closed = 0
    def get_tick(self, symbol, extra_data=None, **kwargs):
        self.calls.append(("get_tick", symbol))
        return {"symbol": symbol, "source": "synthetic-direct-feed", "price": 12.5}
    def close(self):
        self.closed += 1

api = BtApi(debug=False)
assert api.transport_mode.value == "direct"
assert type(api._backend) is DirectBackend
feed = LocalFeed()
api.exchange_feeds["LOCAL___SPOT"] = feed
result = api.get_tick("LOCAL___SPOT", "LOCAL-TEST")
assert result == {"symbol": "LOCAL-TEST", "source": "synthetic-direct-feed", "price": 12.5}
assert feed.calls == [("get_tick", "LOCAL-TEST")]
api.close()
assert not ATTEMPTS, ATTEMPTS
assert not NETWORK_ATTEMPTS, NETWORK_ATTEMPTS
sdk_path = Path(BtApi.__module__ and sys.modules["bt_api_py.bt_api"].__file__).resolve()
base_path = Path(sys.modules["bt_api_base"].__file__).resolve()
prefix = Path(sys.prefix).resolve()
assert sdk_path.is_relative_to(prefix), (sdk_path, prefix)
assert base_path.is_relative_to(prefix), (base_path, prefix)
installed = {re.sub(r"[-_.]+", "-", d.metadata.get("Name", "")).lower() for d in importlib.metadata.distributions()}
for name in ("bt-api-gateway", "bt-api-execution", "bt-api-risk", "bt-api-monitor", "bt-api-transport-zmq"):
    assert name not in installed, name
assert "zmq" not in sys.modules
print(json.dumps({
    "mode": api.transport_mode.value,
    "backend_type": type(api._backend).__name__,
    "call_result": result,
    "feed_call_count": len(feed.calls),
    "feed_closed": feed.closed,
    "sdk_origin": str(sdk_path),
    "base_origin": str(base_path),
    "sys_prefix": str(prefix),
    "forbidden_distributions_absent": True,
    "blocked_import_attempts": ATTEMPTS,
    "network_attempts": NETWORK_ATTEMPTS,
    "zmq_loaded": "zmq" in sys.modules,
}, sort_keys=True))

