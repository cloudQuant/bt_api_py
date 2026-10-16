"""Run a fixed pytest argv and preserve public source pins and raw output."""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


spec_path = Path(sys.argv[1]).resolve(strict=True)
spec = json.loads(spec_path.read_text(encoding="utf-8"))
out = Path(spec["output"])
assert not out.exists(), "A consumed output directory cannot be reused"
assert Path(spec["python"]).is_file()
assert Path(spec["cwd"]).is_dir()
files = spec["source_sha256"]
assert files and all(digest(path) == expected for path, expected in files.items())
heads_before = {}
for repo in spec["repositories"]:
    branch = subprocess.check_output(["git", "-C", repo, "branch", "--show-current"]).decode().strip()
    assert branch == "dev", (repo, branch)
    heads_before[repo] = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"]).decode().strip()
assert heads_before == spec["repositories"]
env = os.environ.copy()
for key in spec.get("unset_environment", []):
    env.pop(key, None)
env.update(spec["environment"])
assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
argv = [spec["python"], "-m", "pytest", *spec["pytest_options"],
        "--junitxml=" + str(out / "junit.xml"), "--basetemp=" + str(out / "pytest-tmp"),
        *spec["nodes"]]
out.mkdir(parents=True)


def write(name, value):
    (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


(out / "spec.json").write_bytes(spec_path.read_bytes())
(out / "runner.py").write_bytes(Path(__file__).read_bytes())
write("command.json", {"argv": argv, "cwd": spec["cwd"], "environment": spec["environment"],
                       "unset_environment": spec.get("unset_environment", []), "started_utc": now()})
write("source-before.json", files)
with (out / "stdout.log").open("wb") as stdout, (out / "stderr.log").open("wb") as stderr:
    process = subprocess.Popen(argv, cwd=spec["cwd"], env=env, stdout=stdout, stderr=stderr)
    write("running.json", {"pid": process.pid, "started_utc": now()})
    print(json.dumps({"pid": process.pid, "output": str(out)}), flush=True)
    code = process.wait()
after = {path: digest(path) for path in files}
heads_after = {repo: subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"]).decode().strip()
               for repo in spec["repositories"]}
write("source-after.json", after)
(out / "exit.txt").write_text(str(code) + "\n", encoding="utf-8")
result = {"pytest_exit": code, "finished_utc": now(), "source_count": len(files),
          "sources_unchanged": files == after, "heads_before": heads_before, "heads_after": heads_after,
          "heads_unchanged": heads_before == heads_after,
          "stdout_sha256": digest(out / "stdout.log"), "stderr_sha256": digest(out / "stderr.log"),
          "evidence_level": spec["evidence_level"]}
write("result.json", result)
print(json.dumps(result, ensure_ascii=False), flush=True)
assert files == after and heads_before == heads_after, "Dependency changed during QA"
raise SystemExit(code)
