"""Build TTL candidate r6 + fixed-source spec r5 from the consumed r4/r5 set."""

import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(r"D:\bt_api_py")
R5 = Path(r"D:\temp\it10riskttl-candidate-r5")
R6 = Path(r"D:\temp\it10riskttl-candidate-r6")
SPEC_R4 = REPO / ".codex-tmp" / "iteration10-risk-ttl-root-spec-r4.json"
SPEC_R5 = REPO / ".codex-tmp" / "iteration10-risk-ttl-root-spec-r5.json"
UPDATED = (
    r"bt_api_py\service_risk_order_entry.py",
    r"tests\unit\test_service_risk_order_ttl_restart_integration.py",
)

if R6.exists():
    sys.exit(f"refusing to overwrite existing {R6}")
if SPEC_R5.exists():
    sys.exit(f"refusing to overwrite existing {SPEC_R5}")

shutil.copytree(R5, R6)
for rel in UPDATED:
    shutil.copy2(REPO / rel, R6 / rel)

spec = json.loads(SPEC_R4.read_text(encoding="utf-8"))
old_prefix = str(R5)
new_prefix = str(R6)


def remap(value: str) -> str:
    assert value.startswith(old_prefix), value
    return new_prefix + value[len(old_prefix):]


pins = {}
for key in spec["source_sha256"]:
    new_key = remap(key)
    pins[new_key] = hashlib.sha256(Path(new_key).read_bytes()).hexdigest()
spec["source_sha256"] = pins
spec["cwd"] = remap(spec["cwd"])
spec["output"] = "D:/temp/it10riskttl-root-20261016-r5"
spec["environment"]["PYTHONPATH"] = ";".join(
    remap(entry) for entry in spec["environment"]["PYTHONPATH"].split(";")
)
spec["pytest_options"] = [
    remap(option) if option.startswith(old_prefix) else option
    for option in spec["pytest_options"]
]
SPEC_R5.write_text(
    json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

print("output:", spec["output"])
print("cwd:", spec["cwd"])
print("pins:", len(pins))
for rel in UPDATED:
    key = new_prefix + "\\" + rel
    print("pin", rel, pins[key])
print("spec r5 sha256:", hashlib.sha256(SPEC_R5.read_bytes()).hexdigest())
