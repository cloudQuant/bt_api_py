from __future__ import annotations
import hashlib, json, os, pathlib, platform, posixpath, shutil, subprocess, sys, tarfile, time, zipfile
from datetime import datetime, timezone

SDK_ROOT = pathlib.Path(r"D:\bt_api_py")
BASE_ROOT = SDK_ROOT / "bt_api" / "bt_api_base"
OUT = pathlib.Path(r"D:\t\m6")
WHEELHOUSE = pathlib.Path(r"D:\source_code\ai-for-investor\.codex-tmp\afi10-local-release-candidate-r1\wheelhouse")
BUILD_PY = pathlib.Path(r"C:\anaconda3\python.exe")
VENV_BASE_PY = pathlib.Path(r"D:\source_code\ai-for-investor\src\backend\.venv\Scripts\python.exe")
SDK_HEAD = "35e3879a7c3c43e38fce0bfdacd0668c1f69d6a2"
BASE_HEAD = "fb47f3dd733613494aa1d86cfd94b2d6fe70314d"
CONVERTER_SHA256 = "ee0071b531fe20da3cb29d5b66b7fb321a6ecf811d88d1bf9d2ce6ea809c664c"
CONVERTER_SIZE = 21474

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def write_bytes(name: str, data: bytes) -> None:
    (OUT / name).write_bytes(data)

def write_json(name: str, value) -> None:
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def run_logged(name: str, argv: list[str], cwd: pathlib.Path | None = None, env: dict[str, str] | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    record = {
        "argv": [str(x) for x in argv],
        "cwd": str(cwd) if cwd else None,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "timeout_seconds": timeout,
    }
    cp = subprocess.run(argv, cwd=str(cwd) if cwd else None, env=env, capture_output=True, timeout=timeout)
    record["finished_utc"] = datetime.now(timezone.utc).isoformat()
    record["exit_code"] = cp.returncode
    record["stdout_bytes"] = len(cp.stdout)
    record["stdout_sha256"] = sha(cp.stdout)
    record["stderr_bytes"] = len(cp.stderr)
    record["stderr_sha256"] = sha(cp.stderr)
    write_json(name + ".command.json", record)
    write_bytes(name + ".stdout.bin", cp.stdout)
    write_bytes(name + ".stderr.bin", cp.stderr)
    write_json(name + ".exit.json", {"exit_code": cp.returncode})
    if cp.returncode != 0:
        raise RuntimeError(f"{name} failed with exit {cp.returncode}; see exact stdout/stderr artifacts")
    return cp

def git_head(repo: pathlib.Path) -> str:
    cp = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, check=True)
    return cp.stdout.decode("ascii").strip()

def create_archive(repo: pathlib.Path, commit: str, name: str, extract_dir: pathlib.Path) -> None:
    if extract_dir.exists():
        if any(extract_dir.iterdir()):
            raise RuntimeError(f"refusing to reuse nonempty extraction path: {extract_dir}")
        extract_dir.rmdir()
    extract_dir.mkdir(parents=True)
    tar_path = OUT / f"{name}.tar"
    cp = run_logged(f"{name}-git-archive", ["git", "-c", "core.autocrlf=false", "-C", str(repo), "archive", "--format=tar", f"--output={tar_path}", commit], cwd=OUT)
    if cp.stdout or cp.stderr:
        # Git writes the archive to --output; stderr is retained as an artifact.
        pass
    with tarfile.open(tar_path, "r:") as tf:
        members = tf.getmembers()
        for member in members:
            p = pathlib.PurePosixPath(member.name)
            if p.is_absolute() or ".." in p.parts:
                raise RuntimeError(f"unsafe archive path: {member.name}")
            if member.issym():
                target = pathlib.PurePosixPath(posixpath.normpath(str(p.parent / member.linkname)))
                if pathlib.PurePosixPath(member.linkname).is_absolute() or not target.parts or target.parts[0] == "..":
                    raise RuntimeError(f"unsafe archive symlink: {member.name} -> {member.linkname}")
            elif not (member.isdir() or member.isfile()):
                raise RuntimeError(f"unexpected git archive member type: {member.name}")
        tf.extractall(extract_dir, filter="data")
    archive_bytes = tar_path.read_bytes()
    write_json(f"{name}-archive.json", {
        "repo": str(repo),
        "commit": commit,
        "tar_path": str(tar_path),
        "tar_size_bytes": len(archive_bytes),
        "tar_sha256": sha(archive_bytes),
        "extracted_root": str(extract_dir),
        "member_count": len(members),
    })

def build_wheel(name: str, source: pathlib.Path, wheel_dir: pathlib.Path) -> pathlib.Path:
    wheel_dir.mkdir(parents=True)
    argv = [
        str(BUILD_PY), "-m", "pip", "wheel",
        "--no-deps", "--no-build-isolation", "--no-cache-dir",
        "--wheel-dir", str(wheel_dir), str(source),
    ]
    run_logged(f"{name}-wheel-build", argv, cwd=source, timeout=600)
    wheels = sorted(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one {name} wheel, found {len(wheels)}")
    return wheels[0]

def wheel_details(path: pathlib.Path, expected_member: str | None = None) -> dict:
    blob = path.read_bytes()
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        metadata_names = [n for n in names if n.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise RuntimeError(f"unexpected METADATA members: {metadata_names}")
        metadata = zf.read(metadata_names[0]).decode("utf-8")
        member = None
        if expected_member:
            if expected_member not in names:
                raise RuntimeError(f"wheel missing expected module: {expected_member}")
            member_bytes = zf.read(expected_member)
            member = {"path": expected_member, "size_bytes": len(member_bytes), "sha256": sha(member_bytes)}
            if len(member_bytes) != CONVERTER_SIZE or sha(member_bytes) != CONVERTER_SHA256:
                raise RuntimeError("installed wheel converter bytes do not match fixed accepted source")
        return {
            "path": str(path),
            "size_bytes": len(blob),
            "sha256": sha(blob),
            "member_count": len(names),
            "metadata_member": metadata_names[0],
            "metadata_sha256": sha(metadata.encode("utf-8")),
            "metadata": metadata,
            "converter_member": member,
        }

def main() -> int:
    if OUT.exists() and any(p.name != "runner.py" for p in OUT.iterdir()):
        raise RuntimeError("output root is not empty; refusing to overwrite")
    if git_head(SDK_ROOT) != SDK_HEAD or git_head(BASE_ROOT) != BASE_HEAD:
        raise RuntimeError("fixed SDK/Base HEAD mismatch")
    if not BUILD_PY.is_file() or not VENV_BASE_PY.is_file() or not WHEELHOUSE.is_dir():
        raise RuntimeError("required local build Python, venv base Python, or wheelhouse unavailable")
    system = {
        "build_python": str(BUILD_PY),
        "build_python_version": subprocess.run([str(BUILD_PY), "--version"], capture_output=True, check=True).stdout.decode().strip()
            or subprocess.run([str(BUILD_PY), "--version"], capture_output=True, check=True).stderr.decode().strip(),
        "venv_base_python": str(VENV_BASE_PY),
        "venv_base_python_version": subprocess.run([str(VENV_BASE_PY), "--version"], capture_output=True, check=True).stdout.decode().strip()
            or subprocess.run([str(VENV_BASE_PY), "--version"], capture_output=True, check=True).stderr.decode().strip(),
        "wheelhouse": str(WHEELHOUSE),
        "wheelhouse_wheel_count": len(list(WHEELHOUSE.glob("*.whl"))),
        "system_site_packages_requested": False,
        "network_allowed": False,
    }
    write_json("environment.json", system)
    sdk_extract = OUT / "source" / "sdk"
    base_extract = OUT / "source" / "base"
    create_archive(SDK_ROOT, SDK_HEAD, "sdk", sdk_extract)
    create_archive(BASE_ROOT, BASE_HEAD, "base", base_extract)
    sdk_src = sdk_extract / "bt_api_py"
    base_src = base_extract / "bt_api" / "bt_api_base"
    # git archive emits paths relative to each repository root.
    if not (sdk_src / "pyproject.toml").is_file():
        sdk_src = sdk_extract
    if not (base_src / "pyproject.toml").is_file():
        base_src = base_extract
    converter = sdk_src / "bt_api_py" / "config_migration.py"
    if converter.stat().st_size != CONVERTER_SIZE or sha(converter.read_bytes()) != CONVERTER_SHA256:
        raise RuntimeError("converter in fixed archive differs from accepted pin")
    sdk_wheel = build_wheel("sdk", sdk_src, OUT / "wheels" / "sdk")
    base_wheel = build_wheel("base", base_src, OUT / "wheels" / "base")
    sdk_details = wheel_details(sdk_wheel, "bt_api_py/config_migration.py")
    base_details = wheel_details(base_wheel)
    if "Name: bt_api_py\n" not in sdk_details["metadata"].replace("\r\n", "\n") or "Version: 0.15.5\n" not in sdk_details["metadata"].replace("\r\n", "\n"):
        raise RuntimeError("unexpected SDK distribution metadata")
    if "Name: bt_api_base\n" not in base_details["metadata"].replace("\r\n", "\n") or "Version: 0.15.8\n" not in base_details["metadata"].replace("\r\n", "\n"):
        raise RuntimeError("unexpected Base distribution metadata")
    write_json("wheel-inspection.json", {"sdk": sdk_details, "base": base_details})
    venv = OUT / "venv"
    run_logged("venv-create", [str(VENV_BASE_PY), "-m", "venv", str(venv)], cwd=OUT, timeout=180)
    venv_python = venv / "Scripts" / "python.exe"
    cfg = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
    include_system = None
    for line in cfg.splitlines():
        if line.strip().lower().startswith("include-system-site-packages"):
            include_system = line.split("=", 1)[1].strip().lower()
    if include_system != "false":
        raise RuntimeError(f"venv is not isolated: include-system-site-packages={include_system!r}")
    (OUT / "pyvenv.cfg.copy").write_text(cfg, encoding="utf-8")
    install_report = OUT / "pip-install-report.json"
    install_argv = [
        str(venv_python), "-m", "pip", "install",
        "--disable-pip-version-check", "--no-index", "--find-links", str(WHEELHOUSE),
        "--report", str(install_report), str(sdk_wheel), str(base_wheel),
    ]
    run_logged("offline-install", install_argv, cwd=OUT, timeout=600)
    if not install_report.is_file():
        raise RuntimeError("pip did not produce its install report")
    report = json.loads(install_report.read_text(encoding="utf-8"))
    installed = []
    for item in report.get("install", []):
        md = item.get("metadata", {})
        archive_info = item.get("download_info", {}).get("archive_info", {})
        installed.append({
            "name": md.get("name"),
            "version": md.get("version"),
            "url": item.get("download_info", {}).get("url"),
            "archive_hashes": archive_info.get("hashes", {}),
        })
    write_json("installed-distributions.json", {
        "venv": str(venv),
        "include_system_site_packages": include_system,
        "pip_report_sha256": sha(install_report.read_bytes()),
        "pip_report_size_bytes": install_report.stat().st_size,
        "installed_count_from_report": len(installed),
        "installed": installed,
    })
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTEST_PLUGINS", "PYTEST_ADDOPTS"):
        env.pop(key, None)
    env["BT_API_PY_LIGHT_IMPORT"] = "1"
    origin_code = (
        "import importlib.metadata as md, json, pathlib, sys; "
        "import bt_api_py.config_migration as m; import bt_api_base; "
        "prefix=pathlib.Path(sys.prefix).resolve(); "
        "sdk=pathlib.Path(m.__file__).resolve(); base=pathlib.Path(bt_api_base.__file__).resolve(); "
        "assert sdk.is_relative_to(prefix), (sdk,prefix); assert base.is_relative_to(prefix), (base,prefix); "
        "assert not any(str(pathlib.Path(p).resolve()).startswith(r'D:\\bt_api_py') for p in sys.path if p); "
        "print(json.dumps({'sys_prefix':str(prefix),'sdk_module':str(sdk),'base_module':str(base),"
        "'sdk_version':md.version('bt_api_py'),'base_version':md.version('bt_api_base'),"
        "'sys_path':sys.path},sort_keys=True))"
    )
    origin_cp = run_logged("installed-origin", [str(venv_python), "-I", "-c", origin_code], cwd=OUT, env=env)
    origin = json.loads(origin_cp.stdout.decode("utf-8"))
    if not origin["sdk_module"].startswith(origin["sys_prefix"]) or not origin["base_module"].startswith(origin["sys_prefix"]):
        raise RuntimeError("core module origin is outside the isolated venv")
    write_json("installed-origin-summary.json", origin)
    positive_cp = run_logged("cli-positive-example", [str(venv_python), "-I", "-m", "bt_api_py.config_migration", "--example"], cwd=OUT, env=env)
    positive = json.loads(positive_cp.stdout.decode("utf-8"))
    result = positive["result"]
    if result.get("status") != "DRAFT_REQUIRES_OPERATOR_REVIEW" or result.get("migration_review", {}).get("ready_to_start") is not False or result.get("migration_review", {}).get("authorization_granted") is not False:
        raise RuntimeError("positive CLI draft did not remain unready/un-authorized")
    if result.get("client_config", {}).get("clients", {}).get("gateway", {}).get("config_schema_version") is None:
        raise RuntimeError("positive CLI output lacks expected target client shape")
    positive_summary = {
        "request_fields": sorted(positive["request"].keys()),
        "status": result["status"],
        "ready_to_start": result["migration_review"]["ready_to_start"],
        "authorization_granted": result["migration_review"]["authorization_granted"],
        "filled_identity_refs": {
            "account_ref": result.get("client_config", {}).get("clients", {}).get("gateway", {}).get("settings", {}).get("bindings", [{}])[0].get("account_ref"),
            "strategy_ref": result.get("client_config", {}).get("registration", {}).get("strategy_ref"),
        },
    }
    write_json("cli-positive-summary.json", positive_summary)
    negative_cp = run_logged("cli-negative-private-example", [str(venv_python), "-I", "-m", "bt_api_py.config_migration", "--example-reject-private"], cwd=OUT, env=env)
    negative_text = negative_cp.stderr.decode("utf-8", errors="strict").strip()
    expected = "UNSUPPORTED_PRIVATE_CHANNEL at $.legacy_forwarding_config.private_endpoint"
    if negative_cp.returncode != 2 or negative_cp.stdout or negative_text != expected:
        raise RuntimeError("negative CLI result did not match the public finite rejection contract")
    summary = {
        "evidence_level": "INSTALLED_WHEEL_OFFLINE_CONFIG_CLI_ONLY_NO_TESTSUITE_NO_HOST_NO_ACCOUNT",
        "fixed_commits": {"sdk": SDK_HEAD, "base": BASE_HEAD},
        "wheelhouse": str(WHEELHOUSE),
        "system_site_packages": False,
        "wheels": {
            "sdk": {k: sdk_details[k] for k in ("path", "size_bytes", "sha256", "member_count", "converter_member")},
            "base": {k: base_details[k] for k in ("path", "size_bytes", "sha256", "member_count")},
        },
        "installed_core_origins": {
            "sdk_version": origin["sdk_version"],
            "sdk_module": origin["sdk_module"],
            "base_version": origin["base_version"],
            "base_module": origin["base_module"],
            "sys_prefix": origin["sys_prefix"],
        },
        "installed_distribution_count": len(installed),
        "positive_cli": {
            "exit": positive_cp.returncode,
            "stdout_bytes": len(positive_cp.stdout),
            "stdout_sha256": sha(positive_cp.stdout),
            "stderr_bytes": len(positive_cp.stderr),
            "stderr_sha256": sha(positive_cp.stderr),
            "summary": positive_summary,
        },
        "negative_cli": {
            "exit": negative_cp.returncode,
            "stdout_bytes": len(negative_cp.stdout),
            "stdout_sha256": sha(negative_cp.stdout),
            "stderr_bytes": len(negative_cp.stderr),
            "stderr_sha256": sha(negative_cp.stderr),
            "message": expected,
        },
        "host_account_network": "not invoked; pip install used --no-index",
    }
    write_json("result.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
