"""Fresh-cluster legacy 2-leg control (matches author whitelist feed setup)."""
import pathlib, sys, tempfile
sys.path.insert(0, r"D:\bt_api_py")
from tests.integration import _dim09_three_leg_fixtures as fx
class Shim:
    def setitem(self, mapping, key, value):
        mapping[key] = value
td = tempfile.mkdtemp(dir=r"D:/temp/it10dim09-step2-review-20261016-r1/wire-probe")
cluster = fx.Dim09ThreeLegCluster(pathlib.Path(td), Shim(), initial_states=("filled", "filled"))
try:
    lr = cluster.submit(cluster.legacy_body("g-legacy-clean"))
    snap = lr["result"]["snapshot"] if lr.get("ok") else None
    writes, counters = cluster.provider.snapshot()
    ok = lr.get("ok") is True and snap["state"] == "PAIRED" and snap["error_code"] is None and len(writes) == 2
    print(("PASS" if ok else "FAIL"), "| fresh legacy 2-leg control ->", snap and snap["state"], counters)
    sys.exit(0 if ok else 1)
finally:
    cluster.close()
