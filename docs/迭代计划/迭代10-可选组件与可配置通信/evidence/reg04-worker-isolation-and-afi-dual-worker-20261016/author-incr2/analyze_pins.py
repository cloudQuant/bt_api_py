"""Analyze r9 pin manifest vs the current AFI working tree (read-only)."""
import hashlib
import json
import os
from pathlib import Path

R9 = Path(r"D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/afi07-remainder-owner-group-legs-20261016/qa/r9/authored-spec-r9.json")
spec = json.loads(R9.read_text(encoding="utf-8"))
pins = spec["source_sha256"]
print("pins:", len(pins))


def digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


changed, missing, same = [], [], 0
for key, expected in pins.items():
    p = Path(key)
    if not p.is_file():
        missing.append(key)
        continue
    if digest(p) != expected:
        changed.append(key)
    else:
        same += 1
print("same:", same, "changed:", len(changed), "missing:", len(missing))
for key in changed[:40]:
    print("CHANGED", key)
for key in missing[:40]:
    print("MISSING", key)

# which top-level areas the pins cover
areas = {}
for key in pins:
    parts = Path(key).parts
    area = "/".join(parts[:6])
    areas[area] = areas.get(area, 0) + 1
for area, count in sorted(areas.items(), key=lambda kv: -kv[1])[:12]:
    print("AREA", count, area)

# find .py files under pinned roots not pinned (candidates for additions)
backend = Path(r"D:/source_code/ai-for-investor/src/backend")
pinned = set(Path(k).resolve() for k in pins)
extra = []
for fold in ("app",):
    for p in (backend / fold).rglob("*.py"):
        if p.resolve() not in pinned:
            extra.append(str(p))
print("unpinned app .py:", len(extra))
for e in extra[:20]:
    print("EXTRA", e)
