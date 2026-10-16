"""Build the R7 approved-target HTTP spec from R6 by patching the edited test pin."""
import hashlib
import json
import subprocess
from pathlib import Path

R6 = Path(r"D:\bt_api_py\.codex-tmp\iteration10-approved-target-http-qa-root-r6.json")
R7 = Path(r"D:\bt_api_py\.codex-tmp\iteration10-approved-target-http-qa-root-r7.json")
TEST_FILE = r"D:\source_code\ai-for-investor\src\backend\tests\test_service_read_authorized_http_integration.py"

spec = json.loads(R6.read_text(encoding="utf-8"))
print("top-level keys:", sorted(k for k in spec))
small = {k: v for k, v in spec.items() if k not in {"source_sha256", "sdk_source_archives"}}
print(json.dumps(small, ensure_ascii=False, indent=2)[:3000])

new_sha = hashlib.sha256(Path(TEST_FILE).read_bytes()).hexdigest()
old_sha = spec["source_sha256"][TEST_FILE]
print("test file pin:", old_sha, "->", new_sha)
assert old_sha != new_sha, "test file not modified?"

spec["source_sha256"][TEST_FILE] = new_sha
spec["name"] = spec["name"].replace("r6", "r7") if "r6" in spec.get("name", "") else spec["name"] + "-r7"
spec["output"] = spec["output"].replace("_r6", "_r7") if "_r6" in spec["output"] else spec["output"] + "-r7"
assert not Path(spec["output"]).exists(), spec["output"]
assert "r7" in spec["output"]
if "evidence_level" in spec:
    spec["evidence_level"] = spec["evidence_level"].replace("_R6", "_R7") if "_R6" in spec["evidence_level"] else spec["evidence_level"]

mismatch = []
for path, expected in spec["source_sha256"].items():
    if path == TEST_FILE:
        continue
    actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if actual != expected:
        mismatch.append((path, expected, actual))
print("other pin mismatches:", len(mismatch))
for row in mismatch[:10]:
    print("  ", row)

live_heads = {}
for repo, pinned in spec["repositories"].items():
    head = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"]).decode().strip()
    branch = subprocess.check_output(["git", "-C", repo, "branch", "--show-current"]).decode().strip()
    live_heads[repo] = head
    ok = head == pinned and branch == "dev"
    print("repo", repo, "branch", branch, "head", head, "pinned", pinned, "OK" if ok else "MISMATCH")
    assert ok, (repo, pinned, head, branch)

R7.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("wrote", R7)
print("output:", spec["output"])
print("python:", spec["python"])
print("nodes:", spec["nodes"])
