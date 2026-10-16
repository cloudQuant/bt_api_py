"""Reconstruct the R8 approved-target cross-process spec from R7 and verify byte equality.

Documents the exact R7->R8 delta: name, nodes (+cross-process node), output dir,
repository pin (a421354b -> b47a68c4 after the round-149 commit), and the test
file pin (a7c44613 -> 282d1307). Writes to a scratch path only; the authored
spec in .codex-tmp is never overwritten.
"""
import hashlib
import json
from pathlib import Path

R7 = Path(r"D:\bt_api_py\.codex-tmp\iteration10-approved-target-http-qa-root-r7.json")
R8 = Path(r"D:\bt_api_py\.codex-tmp\iteration10-approved-target-http-qa-root-r8.json")
OUT = Path(r"D:\temp\it10-afi-r8-reconstruction\spec-reconstructed-r8.json")
TEST_FILE = r"D:\source_code\ai-for-investor\src\backend\tests\test_service_read_authorized_http_integration.py"

spec = json.loads(R7.read_text(encoding="utf-8"))
spec["name"] = "iteration10-afi07-approved-target-http-cross-process-execution-host-r8"
spec["nodes"] = [
    "tests/test_service_read_authorized_http_integration.py::test_approved_target_http_reaches_real_execution_host_and_replays_journal",
    "tests/test_service_read_authorized_http_integration.py::test_approved_target_operation_is_durable_across_concurrent_processes_and_restart",
]
spec["output"] = "D:/temp/afi_iter10_approved_target_http_root_20261016_r8"
spec["repositories"] = {"D:\\source_code\\ai-for-investor": "b47a68c45ecad74eb7fddd9302af268b9623cde2"}
pin = hashlib.sha256(Path(TEST_FILE).read_bytes()).hexdigest()
assert pin == "282d130709b7b49f426927c146608520ecfb37bb620411aefba442443fdbf2f7", pin
spec["source_sha256"][TEST_FILE] = pin

# The authored specs were written via Path.write_text on Windows, which
# translates '\n' to os.linesep; reproduce the CRLF bytes exactly.
raw = (json.dumps(spec, ensure_ascii=False, indent=2) + "\n").replace("\n", "\r\n").encode("utf-8")
authored = R8.read_bytes()
print("reconstructed sha256:", hashlib.sha256(raw).hexdigest())
print("authored      sha256:", hashlib.sha256(authored).hexdigest())
assert raw == authored, "reconstruction is not byte-exact"
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_bytes(raw)
print("MATCH; wrote", OUT)
