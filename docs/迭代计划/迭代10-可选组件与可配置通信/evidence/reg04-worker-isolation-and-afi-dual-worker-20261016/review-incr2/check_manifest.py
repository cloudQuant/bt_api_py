import json
from pathlib import Path

m = json.loads(Path(r"D:/temp/afi_iter10_approved_target_sdk_sources_20261016_r6_fixed/source-manifest.json").read_text(encoding="utf-8"))
tb = json.loads(Path(r"D:/temp/it10reg04-incr2-20261016-r1/tars-before.json").read_text(encoding="utf-8"))
ok = True


def chk(entry):
    global ok
    p = entry["path"]
    match = tb.get(p, {}).get("sha256") == entry["sha256"] and tb.get(p, {}).get("size") == entry["size"]
    ok = ok and match
    print(("MATCH" if match else "MISMATCH"), Path(p).name, entry["sha256"][:24], entry["size"])


for k, v in m["package_archives"].items():
    chk(v)
chk(m["sdk_root_archive"])
print("ALL 6 MANIFEST vs tars-before:", ok)
