"""Offline product-page preview integration; all test data is synthetic."""

import copy
import hashlib
import json
import os

import pytest

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_product_observation as observation
from tests.unit.test_trading_calendar_preview_catalog import (
    SCOPE,
    catalog_flags,
    command,
    make_entry,
    run,
    setup_preview,
    show,
    write_cache,
)

URL = "https://www.shfe.com.cn/products/futures/metal/nonferrousmetal/cu_f/"


def page(rows):
    return (
        "<script>let pageList = " + json.dumps(rows, ensure_ascii=False) + ";</script>"
    ).encode()


def row(symbol="CU", **extra):
    return {
        "ContractSymbol": symbol,
        "ListingExchange": "上海期货交易所",
        "data_standard": "2023-03-29",
        "TradingHours": "合成时段及其他时间",
        **extra,
    }


def flags(path, *, url=URL):
    return [
        "--retain-candidate",
        "--trusted-local-posix",
        "--product-page-url",
        url,
        "--product-page-file",
        str(path),
    ]


def test_real_cli_retains_product_without_mapping_or_path_or_network(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    source = tmp_path / "private-page.html"
    source.write_bytes(page([row(), row(data_standard="2020-01-01")]))
    inject = tmp_path / "inject"
    inject.mkdir()
    (inject / "sitecustomize.py").write_text(
        "import socket\n"
        "class NoNetwork(socket.socket):\n"
        "    def connect(self, *args, **kwargs): raise AssertionError('network forbidden')\n"
        "socket.socket = NoNetwork\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    result = run(command(root, config, supplement, *flags(source)), env=env)
    assert result.returncode == 10, result.stderr
    assert str(source) not in result.stdout + result.stderr
    document = show(root, json.loads(result.stdout)["candidate_id"])
    assert document["schema"].endswith("/3") and document["payload"]["catalog"] is None
    product = document["payload"]["product_page"]
    assert product["status"] == "CANDIDATE" and len(product["records"]) == 2
    assert [r["raw_date"] for r in product["records"]] == ["2023-03-29", "2020-01-01"]
    assert product["source_url"] == URL
    assert product["source_authentication"] == product["retained_data_binding"] == "NOT_PROVEN"
    assert product["current_record"] == product["coverage"] == "UNKNOWN"
    assert all(r["effective_date"] == "UNKNOWN" for r in product["records"])
    assert document["payload"]["identity_mapping"] == "UNKNOWN"
    assert str(source) not in json.dumps(document) and not (root / "CURRENT.json").exists()


def test_product_and_catalog_share_v3_and_hide_all_input_paths(tmp_path):
    root, cache, config, supplement = setup_preview(tmp_path)
    write_cache(cache, [make_entry("rb2701")])
    source = tmp_path / "private-page.html"
    source.write_bytes(page([row()]))
    result = run(
        command(
            root,
            config,
            supplement,
            *catalog_flags(cache),
            "--product-page-url",
            URL,
            "--product-page-file",
            str(source),
        )
    )
    assert result.returncode == 10, result.stderr
    doc = show(root, json.loads(result.stdout)["candidate_id"])
    assert doc["schema"].endswith("/3")
    assert (
        doc["payload"]["catalog"]["status"]
        == doc["payload"]["product_page"]["status"]
        == "CANDIDATE"
    )
    assert (
        str(source) not in json.dumps(doc)
        and str(cache) not in json.dumps(doc)
        and SCOPE not in json.dumps(doc)
    )


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "symlink",
        "directory",
        "oversize",
        "invalid_html",
        "invalid_url",
        "control",
        "private_path",
    ],
)
def test_bad_inputs_retain_yaml_but_product_is_unknown(tmp_path, mode):
    root, _, config, supplement = setup_preview(tmp_path)
    source = tmp_path / "source.html"
    if mode == "symlink":
        target = tmp_path / "target.html"
        target.write_bytes(page([row()]))
        source.symlink_to(target)
    elif mode == "directory":
        source.mkdir()
    elif mode == "oversize":
        source.write_bytes(b"x" * (observation._MAX_PAGE_BYTES + 1))
    elif mode == "invalid_html":
        source.write_bytes(b"invalid html")
    elif mode == "control":
        source.write_bytes(page([row(note="\x1b[31m")]))
    elif mode == "private_path":
        source.write_bytes(page([row(note=str(source).upper())]))
    elif mode != "missing":
        source.write_bytes(page([row()]))
    result = run(
        command(
            root,
            config,
            supplement,
            *flags(source, url="https://evil.example/" if mode == "invalid_url" else URL),
        )
    )
    assert result.returncode == 10, result.stderr
    doc = show(root, json.loads(result.stdout)["candidate_id"])
    product = doc["payload"]["product_page"]
    assert product["status"] == "UNKNOWN" and product["records"] is None
    assert product["coverage"] == "UNKNOWN" and product["source_authentication"] == "NOT_PROVEN"
    assert str(source).casefold() not in json.dumps(doc).casefold()
    assert doc["payload"]["content"]["trading_days"] and not (root / "CURRENT.json").exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--product-page-url", URL],
        ["--product-page-url", URL, "--product-page-file", "/tmp/nope"],
        ["--retain-candidate", "--product-page-url", URL, "--product-page-file", "/tmp/nope"],
    ],
)
def test_explicit_options_are_required(tmp_path, extra):
    root, _, config, supplement = setup_preview(tmp_path)
    result = run(command(root, config, supplement, *extra))
    assert result.returncode == 2 and "/tmp/nope" not in result.stdout + result.stderr
    assert not (root / "inbox").exists()


def test_cross_record_conflict_is_retained_for_review(tmp_path):
    source = tmp_path / "source.html"
    source.write_bytes(page([row(), row("BC")]))
    data = observation.observe_product_page(
        source_url=URL, path=str(source), filesystem=observation.TRUSTED_LOCAL_POSIX
    )
    assert data["status"] == "NEEDS_REVIEW" and data["review_reasons"] == ["SYMBOL_CONFLICT"]
    assert len(data["records"]) == 2


def test_semantic_tampering_is_rejected_even_after_payload_rehash(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    source = tmp_path / "source.html"
    source.write_bytes(page([row()]))
    result = run(command(root, config, supplement, *flags(source)))
    cid = json.loads(result.stdout)["candidate_id"]
    candidate = root / "inbox" / f"candidate-{cid}.json"
    original = json.loads(candidate.read_bytes())
    for mutation in ("symbol", "authentication", "fields", "reasons"):
        doc = copy.deepcopy(original)
        product = doc["payload"]["product_page"]
        if mutation == "symbol":
            product["records"][0]["symbol"] = "BC"
        elif mutation == "authentication":
            product["source_authentication"] = "PROVEN"
        elif mutation == "fields":
            product["records"][0]["fields"].append(product["records"][0]["fields"][0])
        else:
            product["review_reasons"] = ["SYMBOL_CONFLICT"]
        doc["payload_sha256"] = hashlib.sha256(inbox._canonical(doc["payload"])).hexdigest()
        candidate.write_bytes(inbox._canonical(doc))
        with pytest.raises((ValueError, TypeError)):
            inbox.read_candidate(
                root=root, candidate_id=cid, filesystem=observation.TRUSTED_LOCAL_POSIX
            )


def test_product_not_requested_does_not_read_file_and_preserves_v1(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    inject = tmp_path / "inject"
    inject.mkdir()
    marker = tmp_path / "unexpected"
    (inject / "sitecustomize.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "from bt_api_py import trading_calendar_product_observation as obs\n"
        "def forbidden(path):\n Path(os.environ['ITER67_MARKER']).write_text('called')\n raise AssertionError('forbidden')\n"
        "obs._read_page = forbidden\n"
    )
    env = os.environ.copy()
    env["ITER67_MARKER"] = str(marker)
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    result = run(
        command(root, config, supplement, "--retain-candidate", "--trusted-local-posix"), env=env
    )
    assert result.returncode == 10 and not marker.exists()
    doc = show(root, json.loads(result.stdout)["candidate_id"])
    assert doc["schema"].endswith("/1") and "product_page" not in doc["payload"]


def test_product_read_timeout_does_not_claim_commit(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    source = tmp_path / "source.html"
    source.write_bytes(page([row()]))
    inject = tmp_path / "inject-timeout"
    inject.mkdir()
    (inject / "sitecustomize.py").write_text(
        "import time\nfrom bt_api_py import trading_calendar_product_observation as obs\n"
        "def slow(path): time.sleep(20)\nobs._read_page = slow\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = command(root, config, supplement, *flags(source))
    cmd[cmd.index("60")] = "3"
    result = run(cmd, env=env)
    assert result.returncode == 25
    report = json.loads(result.stdout)
    assert report["candidate_status"] == "COMMIT_UNKNOWN" and len(report["candidate_id"]) == 32
    assert str(source) not in result.stdout + result.stderr and not (root / "CURRENT.json").exists()


def test_symlink_ancestor_is_rejected_without_resolving_it(tmp_path):
    physical = tmp_path.resolve() / "physical"
    physical.mkdir()
    (physical / "source.html").write_bytes(page([row()]))
    link = tmp_path.resolve() / "link"
    link.symlink_to(physical, target_is_directory=True)
    product = observation.observe_product_page(
        source_url=URL, path=str(link / "source.html"), filesystem=observation.TRUSTED_LOCAL_POSIX
    )
    assert (product["status"], product["reason"]) == ("UNKNOWN", "FILE_UNAVAILABLE_OR_CHANGED")
    valid = observation.observe_product_page(
        source_url=URL,
        path=str(physical / "source.html"),
        filesystem=observation.TRUSTED_LOCAL_POSIX,
    )
    assert valid["status"] == "CANDIDATE"


def test_directory_replacement_during_read_is_rejected(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    moving = base / "moving"
    moving.mkdir()
    source = moving / "source.html"
    source.write_bytes(page([row()]))
    original_read = observation.os.read
    changed = False

    def replace_parent(fd, size):
        nonlocal changed
        data = original_read(fd, size)
        if not changed:
            changed = True
            moving.rename(base / "original")
            moving.mkdir()
            (moving / "source.html").write_bytes(page([row("BC")]))
        return data

    monkeypatch.setattr(observation.os, "read", replace_parent)
    product = observation.observe_product_page(
        source_url=URL, path=str(source), filesystem=observation.TRUSTED_LOCAL_POSIX
    )
    assert changed and (product["status"], product["reason"]) == (
        "UNKNOWN",
        "FILE_UNAVAILABLE_OR_CHANGED",
    )
