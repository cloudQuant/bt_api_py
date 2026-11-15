"""Offline local-catalog integration; no venue or account facts are established."""

import copy
import hashlib
import json
import multiprocessing
import os
import subprocess
import sys

import pytest
from bt_api_ctp.calendar_local_catalog import TRUSTED_LOCAL_POSIX, read_local_catalog
from bt_api_ctp.calendar_supplement import parse_supplement
from bt_api_ctp.instrument_catalog_cache import (
    InstrumentCatalogEntry,
    _catalog_from_entries,
    _encode_catalog,
)

from bt_api_py import trading_calendar_candidate_inbox as inbox
from tests.unit.test_trading_calendar_candidate_compare_current import (
    absent_checks,
    content,
    current,
)
from tests.unit.test_trading_calendar_candidate_inbox import input_content

SCOPE = "9" * 64
DAY = "20261106"


def make_entry(name, **changes):
    fields = {
        "instrument_id": name,
        "exchange_id": "SHFE",
        "product_id": "rb",
        "product_class": "1",
        "open_date": "20260101",
        "expire_date": "20270115",
        "is_trading": True,
        "life_phase": "1",
    }
    fields.update(changes)
    return InstrumentCatalogEntry(**fields)


def write_cache(cache, entries, *, conflicts=(), skipped=0):
    (cache / SCOPE).mkdir(parents=True, exist_ok=True)
    (cache / SCOPE / f"{DAY}.json").write_bytes(
        _encode_catalog(
            _catalog_from_entries(
                SCOPE,
                DAY,
                entries,
                conflicts=conflicts,
                skipped_rows=skipped,
                built_at_utc="2026-11-06T01:00:00Z",
            )
        )
    )


def setup_preview(tmp_path):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    cache = tmp_path.resolve() / "private-cache"
    config = tmp_path.resolve() / "config.yaml"
    supplement = tmp_path.resolve() / "manual.yaml"
    config.write_text(f"calendars:\n  root: {root}\n", encoding="utf-8")
    supplement.write_text(json.dumps(input_content()), encoding="utf-8")
    return root, cache, config, supplement


def command(root, config, supplement, *extra):
    return [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_preview",
        "--config",
        str(config),
        "--calendar-root",
        str(root),
        "--supplement",
        str(supplement),
        "--budget-seconds",
        "60",
        *extra,
    ]


def catalog_flags(cache, *, day=DAY):
    return [
        "--retain-candidate",
        "--trusted-local-posix",
        "--catalog-cache-dir",
        str(cache),
        "--catalog-scope",
        SCOPE,
        "--catalog-trading-day",
        day,
    ]


def run(cmd, *, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=100, env=env, check=False)


def show(root, candidate_id, *extra):
    result = run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_candidate_inbox",
            "show",
            "--calendar-root",
            str(root),
            "--candidate-id",
            candidate_id,
            "--trusted-local-posix",
            *extra,
        ]
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def row_dict(catalog, instrument):
    return next(
        dict(zip(catalog["columns"], row))  # noqa: B905 - Python 3.9
        for row in catalog["rows"]
        if row[3] == instrument
    )


def test_real_cli_reads_catalog_once_and_preserves_details_without_sensitive_output(tmp_path):
    root, cache, config, supplement = setup_preview(tmp_path)
    write_cache(
        cache,
        [
            make_entry("rb2701"),
            make_entry(
                "rb2701-C-3000",
                product_class="2",
                underlying_instrument_id="rb2701",
                options_type="1",
                strike_price=3000.0,
                underlying_multiple=1.0,
            ),
            make_entry("unknown", product_id=None, product_class=None, open_date=None),
            make_entry("expired", open_date="20240101", expire_date="20250101"),
            make_entry("duplicate", exchange_id="SHFE"),
            make_entry("duplicate", exchange_id="DCE"),
        ],
        conflicts=("rejected",),
        skipped=2,
    )
    inject = tmp_path / "inject-no-network"
    inject.mkdir()
    sentinel = tmp_path / "forbidden-call"
    (inject / "sitecustomize.py").write_text(
        """\nimport os, socket\nfrom pathlib import Path\nfrom bt_api_ctp.instrument_catalog_cache import InstrumentCatalogCache\ndef forbidden(*args, **kwargs):\n    Path(os.environ["ITER67_FORBIDDEN"]).write_text("called")\n    raise AssertionError("forbidden")\nInstrumentCatalogCache.lookup = forbidden\nInstrumentCatalogCache._supplement = forbidden\nclass NoNetwork(socket.socket):\n    def connect(self, *args, **kwargs):\n        return forbidden(*args, **kwargs)\nsocket.socket = NoNetwork\n"""
    )
    env = os.environ.copy()
    env["ITER67_FORBIDDEN"] = str(sentinel)
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    result = run(command(root, config, supplement, *catalog_flags(cache)), env=env)
    assert result.returncode == 10, result.stderr
    assert not sentinel.exists()
    report = json.loads(result.stdout)
    assert report["candidate_status"] == "COMMITTED"
    assert SCOPE not in result.stdout and str(cache) not in result.stdout
    assert "rb2701" not in result.stdout and not (root / "CURRENT.json").exists()
    document = show(root, report["candidate_id"])
    assert document["schema"].endswith("/2")
    assert SCOPE not in json.dumps(document) and str(cache) not in json.dumps(document)
    catalog = document["payload"]["catalog"]
    assert (catalog["status"], catalog["reason"], catalog["observed_count"]) == (
        "PARTIAL",
        "REFERENCE_GAPS_PRESENT",
        6,
    )
    assert catalog["conflicts"] == ["rejected"] and catalog["skipped_rows"] == 2
    assert catalog["metadata_digest_protection"] == "NOT_COVERED"
    assert catalog["retained_catalog_source_binding"] == "NOT_PROVEN"
    assert (
        catalog["freshness"]
        == catalog["source_authentication"]
        == catalog["absence"]
        == "NOT_PROVEN"
    )
    future = row_dict(catalog, "rb2701")
    option = row_dict(catalog, "rb2701-C-3000")
    assert (future["kind"], future["is_trading"], future["open_date"], future["expire_date"]) == (
        "future",
        True,
        "20260101",
        "20270115",
    )
    assert (option["kind"], option["underlying_instrument_id"], option["underlying_relation"]) == (
        "option",
        "rb2701",
        "NOT_PROVEN",
    )
    assert (option["options_type"], option["strike_price"], option["underlying_multiple"]) == (
        "1",
        3000.0,
        1.0,
    )
    ambiguous = [
        dict(zip(catalog["columns"], row))  # noqa: B905 - Python 3.9
        for row in catalog["rows"]
        if row[3] == "duplicate"
    ]
    assert len(ambiguous) == 2
    assert all(row["identity_status"] == "UNKNOWN" for row in ambiguous)
    assert row_dict(catalog, "unknown")["identity_status"] == "UNKNOWN"
    assert row_dict(catalog, "expired")["lifecycle_status"] == "OUTSIDE_DECLARED_BOUNDS"
    current(root, content())
    before = (root / "CURRENT.json").read_bytes()
    comparison = show(root, report["candidate_id"], "--against-current")
    assert comparison["catalog"]["status"] == "PARTIAL"
    assert comparison["catalog"]["retained_catalog_source_binding"] == "NOT_PROVEN"
    assert comparison["catalog"]["rows"] == "NOT_INCLUDED_USE_SHOW"
    assert (root / "CURRENT.json").read_bytes() == before


@pytest.mark.parametrize("mode", ["missing", "corrupt", "wrong_day", "empty"])
def test_unavailable_catalog_still_retains_yaml_as_unknown(tmp_path, mode):
    root, cache, config, supplement = setup_preview(tmp_path)
    if mode != "missing":
        write_cache(cache, [] if mode == "empty" else [make_entry("rb2701")])
    if mode == "corrupt":
        (cache / SCOPE / f"{DAY}.json").write_text("bad")
    result = run(
        command(
            root,
            config,
            supplement,
            *catalog_flags(cache, day="20261107" if mode == "wrong_day" else DAY),
        )
    )
    assert result.returncode == 10, result.stderr
    report = json.loads(result.stdout)
    document = show(root, report["candidate_id"])
    catalog = document["payload"]["catalog"]
    assert catalog["status"] == "UNKNOWN" and catalog["rows"] is None
    assert catalog["absence"] == "NOT_PROVEN"
    assert catalog["retained_catalog_source_binding"] == "NOT_PROVEN"
    assert document["payload"]["content"] == input_content()
    assert SCOPE not in result.stdout and str(cache) not in result.stdout


@pytest.mark.parametrize(
    "extra",
    [
        ["--catalog-cache-dir", "/tmp/nope"],
        [
            "--catalog-cache-dir",
            "/tmp/nope",
            "--catalog-scope",
            SCOPE,
            "--catalog-trading-day",
            DAY,
        ],
        [
            "--retain-candidate",
            "--catalog-cache-dir",
            "/tmp/nope",
            "--catalog-scope",
            SCOPE,
            "--catalog-trading-day",
            DAY,
        ],
    ],
)
def test_partial_or_untrusted_catalog_options_are_rejected(tmp_path, extra):
    root, cache, config, supplement = setup_preview(tmp_path)
    result = run(command(root, config, supplement, *extra))
    assert result.returncode == 2
    assert (
        SCOPE not in result.stdout + result.stderr
        and "/tmp/nope" not in result.stdout + result.stderr
    )
    assert not (root / "inbox").exists()


def test_no_catalog_option_preserves_v1_and_does_not_call_reader(tmp_path):
    root, cache, config, supplement = setup_preview(tmp_path)
    inject = tmp_path / "inject"
    inject.mkdir()
    sentinel = tmp_path / "reader-called"
    (inject / "sitecustomize.py").write_text(
        """\nimport os\nfrom pathlib import Path\nimport bt_api_ctp.calendar_local_catalog as local\ndef forbidden(**kwargs):\n    Path(os.environ["ITER67_SENTINEL"]).write_text("called")\n    raise AssertionError("must not read")\nlocal.read_local_catalog = forbidden\n"""
    )
    env = os.environ.copy()
    env["ITER67_SENTINEL"] = str(sentinel)
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    result = run(
        command(root, config, supplement, "--retain-candidate", "--trusted-local-posix"), env=env
    )
    assert result.returncode == 10 and not sentinel.exists()
    doc = show(root, json.loads(result.stdout)["candidate_id"])
    assert doc["schema"].endswith("/1") and "catalog" not in doc["payload"]


def test_private_worker_needs_filesystem_declaration_with_catalog(tmp_path):
    root, cache, config, supplement = setup_preview(tmp_path)
    write_cache(cache, [make_entry("rb2701")])
    args = {
        "config_file": str(config),
        "calendar_root": str(root),
        "supplement_file": str(supplement),
        "budget_seconds": 20,
        "enable_shfe": False,
        "gateway_enabled": False,
        "direct_write_enabled": False,
        "max_retries": 0,
        "retry_interval_seconds": 0,
        "candidate_id": "a" * 32,
        "catalog_cache_dir": str(cache),
        "catalog_scope": SCOPE,
        "catalog_trading_day": DAY,
    }
    for declaration in (None, False, "true"):
        if declaration is None:
            args.pop("trusted_local_posix", None)
        else:
            args["trusted_local_posix"] = declaration
        result = subprocess.run(
            [sys.executable, "-m", "bt_api_py.trading_calendar_preview", "--_worker"],
            input=json.dumps(args),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 2
        assert not (root / "inbox").exists()
        assert (
            SCOPE not in result.stdout + result.stderr
            and str(cache) not in result.stdout + result.stderr
        )


def test_catalog_reader_timeout_returns_original_id_without_false_success(tmp_path):
    root, cache, config, supplement = setup_preview(tmp_path)
    write_cache(cache, [make_entry("rb2701")])
    inject = tmp_path / "inject-timeout"
    inject.mkdir()
    (inject / "sitecustomize.py").write_text(
        """\nimport time\nimport bt_api_ctp.calendar_local_catalog as local\ndef slow(**kwargs):\n    time.sleep(20)\nlocal.read_local_catalog = slow\n"""
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = command(root, config, supplement, *catalog_flags(cache))
    cmd[cmd.index("60")] = "3"
    result = run(cmd, env=env)
    assert result.returncode == 25
    report = json.loads(result.stdout)
    assert report["candidate_status"] == "COMMIT_UNKNOWN" and len(report["candidate_id"]) == 32
    assert (
        SCOPE not in result.stdout + result.stderr
        and str(cache) not in result.stdout + result.stderr
    )
    assert not (root / "CURRENT.json").exists()


def test_missing_reader_module_keeps_yaml_but_whole_ctp_missing_does_not_fake_it(tmp_path):
    root, cache, config, supplement = setup_preview(tmp_path)
    inject = tmp_path / "inject"
    inject.mkdir()
    site = inject / "sitecustomize.py"
    site.write_text(
        """\nimport builtins, os\noriginal = builtins.__import__\ndef guarded(name, *args, **kwargs):\n    if name == "bt_api_ctp.calendar_local_catalog" or (os.environ.get("ITER67_NO_CTP") == "yes" and (name == "bt_api_ctp" or name.startswith("bt_api_ctp."))):\n        raise ImportError("blocked for offline test")\n    return original(name, *args, **kwargs)\nbuiltins.__import__ = guarded\n"""
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    result = run(command(root, config, supplement, *catalog_flags(cache)), env=env)
    assert result.returncode == 10
    catalog = show(root, json.loads(result.stdout)["candidate_id"])["payload"]["catalog"]
    assert (catalog["status"], catalog["reason"], catalog["rows"]) == (
        "UNKNOWN",
        "READER_UNAVAILABLE",
        None,
    )
    env["ITER67_NO_CTP"] = "yes"
    before = {p.name for p in (root / "inbox").iterdir()}
    result = run(command(root, config, supplement, *catalog_flags(cache)), env=env)
    assert result.returncode == 20
    assert {p.name for p in (root / "inbox").iterdir()} == before


def test_resource_limit_keeps_yaml_without_partial_catalog(tmp_path, monkeypatch):
    root, cache, _, _ = setup_preview(tmp_path)
    write_cache(cache, [make_entry("rb2701"), make_entry("rb2702")])
    snapshot = read_local_catalog(
        cache_dir=cache, scope=SCOPE, trading_day=DAY, filesystem=TRUSTED_LOCAL_POSIX
    )
    monkeypatch.setattr(inbox, "_CATALOG_BYTES", 16)
    catalog = inbox.catalog_observation(snapshot, requested_day=DAY, scope=SCOPE, cache_dir=cache)
    assert (catalog["status"], catalog["reason"], catalog["observed_count"], catalog["rows"]) == (
        "UNKNOWN",
        "CACHE_RESOURCE_LIMIT",
        2,
        None,
    )
    assert catalog["absence"] == "NOT_PROVEN"


def test_cli_resource_limit_still_retains_yaml(tmp_path):
    root, cache, config, supplement = setup_preview(tmp_path)
    write_cache(cache, [make_entry("rb2701"), make_entry("rb2702")])
    inject = tmp_path / "inject-limit"
    inject.mkdir()
    (inject / "sitecustomize.py").write_text(
        "from bt_api_py import trading_calendar_candidate_inbox as inbox\n"
        "inbox._CATALOG_BYTES = 16\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    result = run(command(root, config, supplement, *catalog_flags(cache)), env=env)
    assert result.returncode == 10, result.stderr
    candidate = show(root, json.loads(result.stdout)["candidate_id"])
    assert candidate["payload"]["catalog"]["reason"] == "CACHE_RESOURCE_LIMIT"
    assert candidate["payload"]["catalog"]["rows"] is None
    assert candidate["payload"]["catalog"]["observed_count"] == 2
    assert candidate["payload"]["content"]["trading_days"]


def test_sensitive_scope_or_path_in_a_catalog_row_is_not_disclosed(tmp_path):
    root, cache, _, _ = setup_preview(tmp_path)
    write_cache(cache, [make_entry(SCOPE)])
    snapshot = read_local_catalog(
        cache_dir=cache, scope=SCOPE, trading_day=DAY, filesystem=TRUSTED_LOCAL_POSIX
    )
    catalog = inbox.catalog_observation(snapshot, requested_day=DAY, scope=SCOPE, cache_dir=cache)
    assert (catalog["status"], catalog["reason"], catalog["rows"]) == (
        "UNKNOWN",
        "CACHE_SENSITIVE_VALUE",
        None,
    )
    assert SCOPE not in json.dumps(catalog) and str(cache) not in json.dumps(catalog)


def test_cli_does_not_disclose_case_variant_of_scope(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "SCOPE", "ab" * 32)
    root, cache, config, supplement = setup_preview(tmp_path)
    write_cache(cache, [make_entry(SCOPE.upper()), make_entry("rb2701")])
    result = run(command(root, config, supplement, *catalog_flags(cache)))
    assert result.returncode == 10, result.stderr
    report = json.loads(result.stdout)
    assert report["candidate_status"] == "COMMITTED"
    displayed = run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_candidate_inbox",
            "show",
            "--calendar-root",
            str(root),
            "--candidate-id",
            report["candidate_id"],
            "--trusted-local-posix",
        ]
    )
    assert displayed.returncode == 0, displayed.stderr
    payload = json.loads(displayed.stdout)["payload"]
    assert payload["content"]["trading_days"]
    assert (
        payload["catalog"]["status"],
        payload["catalog"]["reason"],
        payload["catalog"]["rows"],
    ) == (
        "UNKNOWN",
        "CACHE_SENSITIVE_VALUE",
        None,
    )
    output = (result.stdout + result.stderr + displayed.stdout + displayed.stderr).casefold()
    assert SCOPE.casefold() not in output and str(cache).casefold() not in output


def test_casefolded_path_detection_and_empty_path_do_not_hide_normal_ids(tmp_path):
    _, cache, _, _ = setup_preview(tmp_path)
    write_cache(cache, [make_entry("rb2701", product_id="/straße")])
    snapshot = read_local_catalog(
        cache_dir=cache, scope=SCOPE, trading_day=DAY, filesystem=TRUSTED_LOCAL_POSIX
    )
    catalog = inbox.catalog_observation(
        snapshot, requested_day=DAY, scope=SCOPE, cache_dir="/STRASSE"
    )
    assert (catalog["status"], catalog["reason"], catalog["rows"]) == (
        "UNKNOWN",
        "CACHE_SENSITIVE_VALUE",
        None,
    )
    ordinary = inbox.catalog_observation(snapshot, requested_day=DAY, scope=SCOPE, cache_dir="")
    assert ordinary["status"] != "UNKNOWN"
    assert row_dict(ordinary, "rb2701")["product_id"] == "/straße"


@pytest.mark.parametrize("supplied", ["/EXAMPLE//PATH", "/EXAMPLE/./PATH"])
def test_lexically_normalized_case_variant_of_cache_path_is_sensitive(tmp_path, supplied):
    _, cache, _, _ = setup_preview(tmp_path)
    write_cache(cache, [make_entry("rb2701", product_id="/example/path")])
    snapshot = read_local_catalog(
        cache_dir=cache, scope=SCOPE, trading_day=DAY, filesystem=TRUSTED_LOCAL_POSIX
    )
    catalog = inbox.catalog_observation(
        snapshot, requested_day=DAY, scope=SCOPE, cache_dir=supplied
    )
    assert (catalog["status"], catalog["reason"], catalog["rows"]) == (
        "UNKNOWN",
        "CACHE_SENSITIVE_VALUE",
        None,
    )


def test_rehashed_catalog_semantic_tampering_is_rejected(tmp_path):
    _, cache, _, _ = setup_preview(tmp_path)
    write_cache(
        cache, [make_entry("rb2701"), make_entry("rb2701-C-3000", product_class="2")], skipped=1
    )
    snapshot = read_local_catalog(
        cache_dir=cache, scope=SCOPE, trading_day=DAY, filesystem=TRUSTED_LOCAL_POSIX
    )
    original = inbox.catalog_observation(snapshot, requested_day=DAY, scope=SCOPE, cache_dir=cache)
    assert original["status"] == "PARTIAL"
    variants = []
    for key, value in (
        ("metadata_digest_protection", "PROVEN"),
        ("source_authentication", "PROVEN"),
        ("freshness", "PROVEN"),
        ("absence", "PROVEN"),
        ("retained_catalog_source_binding", "PROVEN"),
        ("status", "CANDIDATE"),
        ("observed_count", 0),
    ):
        altered = copy.deepcopy(original)
        altered[key] = value
        variants.append(altered)
    altered = copy.deepcopy(original)
    altered["rows"].append(copy.deepcopy(altered["rows"][0]))
    altered["observed_count"] += 1
    variants.append(altered)
    altered = copy.deepcopy(original)
    del altered["retained_catalog_source_binding"]
    variants.append(altered)
    for altered in variants:
        with pytest.raises((ValueError, TypeError)):
            inbox._validate_catalog(altered)
    unknown = inbox.unknown_catalog("CACHE_RESOURCE_LIMIT", DAY, 2)
    unknown["rows"] = []
    with pytest.raises(ValueError):
        inbox._validate_catalog(unknown)


def test_rehashed_row_change_does_not_prove_binding_to_original_cache(tmp_path):
    root, cache, config, supplement = setup_preview(tmp_path)
    write_cache(cache, [make_entry("rb2701")])
    result = run(command(root, config, supplement, *catalog_flags(cache)))
    assert result.returncode == 10, result.stderr
    cid = json.loads(result.stdout)["candidate_id"]
    path = root / "inbox" / f"candidate-{cid}.json"
    document = json.loads(path.read_bytes())
    catalog = document["payload"]["catalog"]
    assert catalog["status"] == "CANDIDATE"
    original_digest = catalog["content_sha256"]
    catalog["rows"][0][catalog["columns"].index("open_date")] = "20250101"
    document["payload_sha256"] = hashlib.sha256(inbox._canonical(document["payload"])).hexdigest()
    path.write_bytes(inbox._canonical(document))
    restored = inbox.read_candidate(root=root, candidate_id=cid, filesystem=TRUSTED_LOCAL_POSIX)
    observed = restored["payload"]["catalog"]
    assert observed["content_sha256"] == original_digest
    assert row_dict(observed, "rb2701")["open_date"] == "20250101"
    assert observed["retained_catalog_source_binding"] == "NOT_PROVEN"
    assert show(root, cid)["payload"]["catalog"]["retained_catalog_source_binding"] == "NOT_PROVEN"


def _competing_v2_writer(root, catalog, gate, results):
    supplement = parse_supplement(json.dumps(input_content()).encode())
    gate.wait(20)
    result = inbox.retain_candidate(
        root=root,
        candidate_id="b" * 32,
        merged=None,
        supplement=supplement,
        hours_page=None,
        source_checks=absent_checks(),
        filesystem=TRUSTED_LOCAL_POSIX,
        catalog=catalog,
    )
    results.put((result.status, result.reason))


def test_two_processes_same_v2_id_do_not_overwrite(tmp_path):
    root, cache, _, _ = setup_preview(tmp_path)
    write_cache(cache, [make_entry("rb2701")])
    snapshot = read_local_catalog(
        cache_dir=cache, scope=SCOPE, trading_day=DAY, filesystem=TRUSTED_LOCAL_POSIX
    )
    catalog = inbox.catalog_observation(snapshot, requested_day=DAY, scope=SCOPE, cache_dir=cache)
    context = multiprocessing.get_context("spawn")
    gate, results = context.Event(), context.Queue()
    processes = [
        context.Process(target=_competing_v2_writer, args=(str(root), catalog, gate, results))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    gate.set()
    try:
        outcomes = [results.get(timeout=40) for _ in processes]
    finally:
        for process in processes:
            process.join(30)
    assert all(process.exitcode == 0 for process in processes)
    assert sum(status == "COMMITTED" for status, _ in outcomes) == 1
    assert all(status in {"COMMITTED", "NOT_COMMITTED", "COMMIT_UNKNOWN"} for status, _ in outcomes)
    saved = inbox.read_candidate(root=root, candidate_id="b" * 32, filesystem=TRUSTED_LOCAL_POSIX)
    assert saved["payload"]["catalog"]["rows"] == catalog["rows"]


def test_v2_tamper_rehash_is_rejected_and_v1_size_limit_remains(tmp_path):
    root, cache, config, supplement = setup_preview(tmp_path)
    write_cache(cache, [make_entry("rb2701")])
    result = run(command(root, config, supplement, *catalog_flags(cache)))
    cid = json.loads(result.stdout)["candidate_id"]
    path = root / "inbox" / f"candidate-{cid}.json"
    document = json.loads(path.read_bytes())
    kind_index = document["payload"]["catalog"]["columns"].index("kind")
    document["payload"]["catalog"]["rows"][0][kind_index] = "option"
    document["payload_sha256"] = hashlib.sha256(inbox._canonical(document["payload"])).hexdigest()
    path.write_bytes(inbox._canonical(document))
    with pytest.raises(ValueError):
        inbox.read_candidate(root=root, candidate_id=cid, filesystem=TRUSTED_LOCAL_POSIX)
    old = run(command(root, config, supplement, "--retain-candidate", "--trusted-local-posix"))
    old_id = json.loads(old.stdout)["candidate_id"]
    old_path = root / "inbox" / f"candidate-{old_id}.json"
    old_path.write_bytes(old_path.read_bytes() + b" " * (2 * 1024 * 1024))
    with pytest.raises(ValueError):
        inbox.read_candidate(root=root, candidate_id=old_id, filesystem=TRUSTED_LOCAL_POSIX)


def test_more_than_50000_catalog_rows_are_retained_without_truncation(tmp_path):
    root, cache, config, supplement = setup_preview(tmp_path)
    entries = [make_entry(f"rb{i:06d}") for i in range(50_001)]
    write_cache(cache, entries)
    result = run(command(root, config, supplement, *catalog_flags(cache)))
    assert result.returncode == 10, result.stderr
    report = json.loads(result.stdout)
    assert report["candidate_status"] == "COMMITTED"
    document = inbox.read_candidate(
        root=root, candidate_id=report["candidate_id"], filesystem=TRUSTED_LOCAL_POSIX
    )
    catalog = document["payload"]["catalog"]
    assert catalog["status"] == "CANDIDATE" and len(catalog["rows"]) == 50_001
    assert catalog["observed_count"] == 50_001 and catalog["absence"] == "NOT_PROVEN"
    assert not (root / "CURRENT.json").exists()
