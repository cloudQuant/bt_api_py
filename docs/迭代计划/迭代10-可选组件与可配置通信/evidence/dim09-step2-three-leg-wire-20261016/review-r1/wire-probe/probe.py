"""Adversarial over-the-wire probe of S1/S2/S4/S5 gates not covered by the author whitelist."""
import pathlib, sys, tempfile

sys.path.insert(0, r"D:\bt_api_py")
from tests.integration import _dim09_three_leg_fixtures as fx

class Shim:
    def setitem(self, mapping, key, value):
        mapping[key] = value

td = tempfile.mkdtemp(dir=r"D:/temp/it10dim09-step2-review-20261016-r1/wire-probe")
cluster = fx.Dim09ThreeLegCluster(pathlib.Path(td), Shim())
failures = []
def check(desc, ok, detail=""):
    print(("PASS" if ok else "FAIL"), "|", desc, ("| " + str(detail) if not ok else ""))
    if not ok:
        failures.append(desc)

def no_trace(gid):
    return (cluster.origin_run_bindings.get(fx._PORTFOLIO, gid) is None
            and cluster.coordinator_store.get(fx._PORTFOLIO, gid) is None)

def denied(desc, body, code, gid):
    r = cluster.submit(body)
    check(desc, r == {"ok": False, "code": code, "result": {}}, r)
    check(desc + " :: no binding/snapshot", no_trace(gid))

# 1. body with an extra 6th key under version=3
b = cluster.v3_body("g-extra-body"); b["extra"] = 1
denied("extra body key -> INVALID_REQUEST", b, "INVALID_REQUEST", "g-extra-body")

# 2. v3 leg with an extra unknown key
b = cluster.v3_body("g-extra-leg"); b["legs"][2]["extra"] = 1
denied("extra leg key -> GROUP_LEG_INVALID", b, "GROUP_LEG_INVALID", "g-extra-leg")

# 3. baseline v3 submit success
ok_body = cluster.v3_body("g-ok")
r = cluster.submit(ok_body)
check("baseline v3 submit ok", r["ok"] is True and r["result"]["snapshot"]["state"] == "PAIRED", r.get("code"))
# 4. exact replay idempotent
r2 = cluster.submit(cluster.v3_body("g-ok"))
check("exact v3 replay ok (same snapshot)", r2.get("ok") is True and r2["result"]["snapshot"] == r["result"]["snapshot"], r2.get("code"))

# 5. replay with first-leg ratio numerically re-spelled '1.0' (Decimal equality)
b = cluster.v3_body("g-ok"); b["legs"][0]["ratio"] = "1.0"
r3 = cluster.submit(b)
check("replay first-leg ratio '1.0' accepted (Decimal-equal)", r3.get("ok") is True, r3)

# 6. replay with leg2 ratio changed 1 -> 2 (S4 ratio binding)
b = cluster.v3_body("g-ok"); b["legs"][1]["ratio"] = "2"
r4 = cluster.submit(b)
check("replay ratio change -> GROUP_BINDING_CONFLICT", r4 == {"ok": False, "code": "GROUP_BINDING_CONFLICT", "result": {}}, r4)

# 7. replay v3 group as legacy 4-key two-leg body (version 1 vs accepted 3)
b = cluster.legacy_body("g-ok", leg_ids=("leg-put", "leg-future"), sides=(fx.Side.BUY, fx.Side.SELL))
r5 = cluster.submit(b)
check("v3 group replayed as legacy -> GROUP_BINDING_CONFLICT", r5 == {"ok": False, "code": "GROUP_BINDING_CONFLICT", "result": {}}, r5)

# 8. replay v3 group with 5-key body declaring version=2
b = cluster.v3_body("g-ok"); b["version"] = 2
r6 = cluster.submit(b)
check("v3 group replayed with version=2 -> GROUP_VERSION_INVALID", r6 == {"ok": False, "code": "GROUP_VERSION_INVALID", "result": {}}, r6)

# 9. legacy group success then replay with v3 5-key body (accepted 1 vs declared 3)
lr = cluster.submit(cluster.legacy_body("g-legacy"))
check("legacy 2-leg submit ok", lr.get("ok") is True and lr["result"]["snapshot"]["state"] == "PAIRED", lr.get("code"))
b = cluster.v3_body("g-legacy", leg_ids=("leg-e1", "leg-e2", "leg-e3"), sides=(fx.Side.BUY, fx.Side.SELL, fx.Side.BUY))
r7 = cluster.submit(b)
check("legacy group replayed as v3 -> GROUP_BINDING_CONFLICT", r7 == {"ok": False, "code": "GROUP_BINDING_CONFLICT", "result": {}}, r7)

# 10. v3 fresh group, ratio '1e5' -> GROUP_LEG_TERMS_INVALID (and no trace)
b = cluster.v3_body("g-exp-ratio"); b["legs"][1]["ratio"] = "1e5"
denied("ratio '1e5' -> GROUP_LEG_TERMS_INVALID", b, "GROUP_LEG_TERMS_INVALID", "g-exp-ratio")

# 11. v3 fresh group, version key = True (bool) -> GROUP_VERSION_INVALID
b = cluster.v3_body("g-bool-version", version=True)
denied("version=True -> GROUP_VERSION_INVALID", b, "GROUP_VERSION_INVALID", "g-bool-version")

cluster.close()
print("PROBE RESULT:", "ALL-CHECKS-PASSED" if not failures else f"FAILURES={failures}")
sys.exit(0 if not failures else 1)
