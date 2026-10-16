"""Reviewer-independent recheck: pins (2148) vs current trees + tar hashes.

Read-only. Writes only to the reviewer outdir r1b.
"""
import hashlib
import json
from pathlib import Path

R9 = Path(r"D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/afi07-remainder-owner-group-legs-20261016/qa/r9/authored-spec-r9.json")
RUN1 = Path(r"D:/temp/it10reg04-incr2-20261016-r1/spec-run1.json")
RUN2 = Path(r"D:/temp/it10reg04-incr2-20261016-r1/spec-run2.json")
NEW_FILE = r"D:\source_code\ai-for-investor\src\backend\tests\test_service_read_reg04_workers.py"
EXPECTED_NEW = "e8c35811387873affe184462f4f7568eaacde58f277bf2bca578b78e8f87277a"
FROZEN = Path(r"D:/temp/afi_iter10_approved_target_sdk_sources_20261016_r6_fixed")
TARS_BEFORE = Path(r"D:/temp/it10reg04-incr2-20261016-r1/tars-before.json")


def digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


r9 = json.loads(R9.read_text(encoding="utf-8"))
s1 = json.loads(RUN1.read_text(encoding="utf-8"))
s2 = json.loads(RUN2.read_text(encoding="utf-8"))
p9, p1, p2 = r9["source_sha256"], s1["source_sha256"], s2["source_sha256"]
print("r9 pins:", len(p9), "run1 pins:", len(p1), "run2 pins:", len(p2))
assert len(p9) == 2147 and len(p1) == len(p2) == 2148
assert set(p1) == set(p9) | {NEW_FILE}, set(p1) ^ (set(p9) | {NEW_FILE})
assert all(p1[k] == v for k, v in p9.items()), "run1 pins diverge from r9 pins"
assert p1[NEW_FILE] == EXPECTED_NEW == digest(Path(NEW_FILE))
assert p1 == p2, "run1/run2 pin maps differ"
print("run1 == r9 pins + new-file pin: OK; new-file pin matches current file: OK")

same = changed = missing = 0
changed_list, missing_list = [], []
for key, expected in p1.items():
    f = Path(key)
    if not f.is_file():
        missing += 1
        missing_list.append(key)
    elif digest(f) != expected:
        changed += 1
        changed_list.append(key)
    else:
        same += 1
print("PIN RECHECK vs current trees -> same:", same, "changed:", changed, "missing:", missing)
for k in changed_list[:10]:
    print("CHANGED", k)
for k in missing_list[:10]:
    print("MISSING", k)

heads = {r"D:\source_code\ai-for-investor": "794eab0c26589ccad7ebb49899ac04e75a46d0cb"}
print("heads in run1 spec:", s1["repositories"])
assert s1["repositories"] == heads and s2["repositories"] == heads
print("nodes run1:", s1["nodes"])
assert s1["nodes"] == s2["nodes"]

tb = json.loads(TARS_BEFORE.read_text(encoding="utf-8"))
tar_ok = True
for name in ("root", "execution", "base", "transport", "monitor", "gateway"):
    p = FROZEN / (name + ".tar")
    cur = digest(p)
    exp = tb[str(p)]["sha256"]
    ok = cur == exp and p.stat().st_size == tb[str(p)]["size"]
    tar_ok = tar_ok and ok
    print("TAR", name, "match tars-before:", ok, cur[:16])
assert tar_ok

manifest = json.loads((FROZEN / "source-manifest.json").read_text(encoding="utf-8"))
print("manifest top-level keys:", sorted(manifest)[:12] if isinstance(manifest, dict) else type(manifest))
# try to locate tar hashes in the manifest
found = 0
def walk(node):
    global found
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and v == tb.get(k, {}).get("sha256"):
                found += 1
                print("MANIFEST-SHARED", k)
            walk(v)
    elif isinstance(node, list):
        for item in node:
            walk(item)
walk(manifest)
print("manifest shared tar sha occurrences:", found)
print("RECHECK COMPLETE")
