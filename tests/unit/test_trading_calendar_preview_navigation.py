"""Offline CLI and race tests for paired product and navigation observations."""

import copy
import hashlib
import json
import os
from dataclasses import replace

import pytest

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_product_navigation_observation as observation
from bt_api_py import trading_calendar_product_observation as local
from tests.unit.test_trading_calendar_candidate_inbox import input_content
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


def page(*, note="", future_label="铜", bad_nav="", duplicate=""):
    row = {
        "ContractSymbol": "CU",
        "ListingExchange": "上海期货交易所",
        "data_standard": "2023-03-29",
        "note": note,
    }
    script = "<script>let pageList = " + json.dumps([row], ensure_ascii=False) + ";</script>"
    nav = (
        '<div class="header-subs"><div class="header-sub"><div class="subContent sub">'
        + bad_nav
        + '<div class="subItem">期货<div class="subItemContent"><ul class="itemContent">'
        + '<li class="isActive"><a href="/products/futures/metal/cu_f/">'
        + future_label
        + "</a></li>"
        + duplicate
        + "</ul></div></div>"
        + '<div class="subItem">期权<div class="subItemContent"><ul class="itemContent">'
        + '<li class="isActive"><a href="/products/option/metal/cu_o/">铜</a></li>'
        + "</ul></div></div></div></div></div>"
    )
    return (script + nav).encode()


def flags(path, *, discover=True):
    return [
        "--retain-candidate",
        "--trusted-local-posix",
        "--product-page-url",
        URL,
        "--product-page-file",
        str(path),
        *(["--discover-product-navigation"] if discover else []),
    ]


def inject(tmp_path, body):
    folder = tmp_path / "inject"
    folder.mkdir()
    (folder / "sitecustomize.py").write_text(body)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(folder) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def test_cli_reads_once_from_same_bytes_and_never_networks(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    source = tmp_path / "page.html"
    original = page()
    source.write_bytes(original)
    count = tmp_path / "read-count"
    env = inject(
        tmp_path,
        """
import os, socket
from pathlib import Path
from bt_api_py import trading_calendar_product_observation as p
original = p._read_page
def read_once(path):
    result = original(path)
    marker = Path(os.environ["READ_MARKER"])
    marker.write_text((marker.read_text() if marker.exists() else "") + "1")
    Path(path).write_bytes(b"changed after snapshot")
    return result
p._read_page = read_once
class NoNetwork(socket.socket):
    def connect(self, *args, **kwargs): raise AssertionError("no network")
socket.socket = NoNetwork
""",
    )
    env["READ_MARKER"] = str(count)
    result = run(command(root, config, supplement, *flags(source)), env=env)
    assert result.returncode == 10, result.stderr
    report = json.loads(result.stdout)
    doc = show(root, report["candidate_id"])
    assert count.read_text() == "1" and source.read_bytes() == b"changed after snapshot"
    assert doc["schema"].endswith("/5") and doc["payload"]["catalog"] is None
    product = doc["payload"]["product_page"]
    combined = doc["payload"]["product_navigation"]
    nav = combined["navigation"]
    digest = hashlib.sha256(original).hexdigest()
    assert product["sha256"] == combined["observed_sha256"] == nav["sha256"] == digest
    assert combined["same_buffer_observation"] == "DECLARED_NOT_AUTHENTICATED"
    assert combined["status"] == "CANDIDATE_UNVERIFIED" and len(nav["links"]) == 2
    assert (
        combined["source_authentication"]
        == combined["freshness"]
        == combined["coverage"]
        == "NOT_PROVEN"
    )
    assert (
        combined["retained_data_binding"] == "NOT_PROVEN" and nav["product_identity"] == "UNKNOWN"
    )
    assert all(str(p) not in json.dumps(doc) for p in (root, config, supplement, source))
    assert doc["payload"]["identity_mapping"] == "UNKNOWN" and not (root / "CURRENT.json").exists()


def test_navigation_unknown_or_conflict_preserves_product_without_false_completion(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    source = tmp_path / "page.html"
    for contents, expected_status, nav_status in (
        (page(bad_nav="<section>new section</section>"), "PARTIAL", "UNKNOWN"),
        (
            page(
                duplicate='<li class="isActive"><a href="/products/futures/metal/cu_f/">另一标签</a></li>'
            ),
            "NEEDS_REVIEW",
            "NEEDS_REVIEW",
        ),
    ):
        source.write_bytes(contents)
        result = run(command(root, config, supplement, *flags(source)))
        assert result.returncode == 10, result.stderr
        payload = show(root, json.loads(result.stdout)["candidate_id"])["payload"]
        assert payload["product_page"]["status"] == "CANDIDATE"
        assert payload["product_navigation"]["status"] == expected_status
        assert payload["product_navigation"]["navigation"]["status"] == nav_status
        assert payload["content"]["trading_days"] and not (root / "CURRENT.json").exists()


def test_missing_file_returns_two_unknowns_not_empty_completeness(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    source = tmp_path / "missing"
    result = run(command(root, config, supplement, *flags(source)))
    assert result.returncode == 10, result.stderr
    payload = show(root, json.loads(result.stdout)["candidate_id"])["payload"]
    assert payload["product_page"]["status"] == "UNKNOWN"
    pair = payload["product_navigation"]
    assert pair["status"] == "UNKNOWN" and pair["same_buffer_observation"] == "NOT_OBSERVED"
    assert pair["observed_sha256"] is None and pair["navigation"]["links"] is None


def test_default_local_page_does_not_call_navigation_parser(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    source = tmp_path / "page.html"
    source.write_bytes(page())
    marker = tmp_path / "navigation-called"
    env = inject(
        tmp_path,
        """
import os
from pathlib import Path
import bt_api_ctp.calendar_shfe_product_navigation as n
def forbidden(**kwargs):
    Path(os.environ["NAV_MARKER"]).write_text("called")
    raise AssertionError("not requested")
n.parse_shfe_product_navigation = forbidden
""",
    )
    env["NAV_MARKER"] = str(marker)
    result = run(command(root, config, supplement, *flags(source, discover=False)), env=env)
    assert result.returncode == 10 and not marker.exists()
    doc = show(root, json.loads(result.stdout)["candidate_id"])
    assert doc["schema"].endswith("/3") and "product_navigation" not in doc["payload"]


def test_catalog_can_coexist_and_private_scope_is_not_disclosed(tmp_path):
    root, cache, config, supplement = setup_preview(tmp_path)
    write_cache(cache, [make_entry("rb2701")])
    source = tmp_path / "page.html"
    source.write_bytes(page(future_label=SCOPE))
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
            "--discover-product-navigation",
        )
    )
    assert result.returncode == 10, result.stderr
    doc = show(root, json.loads(result.stdout)["candidate_id"])
    nav = doc["payload"]["product_navigation"]["navigation"]
    assert doc["schema"].endswith("/5") and doc["payload"]["catalog"]["status"] == "CANDIDATE"
    assert nav["status"] == "UNKNOWN" and nav["reason"] == "SENSITIVE_CONTENT"
    assert SCOPE not in json.dumps(doc) and str(cache) not in json.dumps(doc)


def test_private_input_path_in_yaml_prevents_candidate_disclosure(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    content = input_content()
    content["sources"][0]["description"] = str(config)
    supplement.write_text(json.dumps(content), encoding="utf-8")
    source = tmp_path / "page.html"
    source.write_bytes(page())
    result = run(command(root, config, supplement, *flags(source)))
    assert result.returncode == 24, (result.stdout, result.stderr)
    assert str(config) not in result.stdout + result.stderr
    assert not (root / "inbox").exists() and not (root / "CURRENT.json").exists()


def test_navigation_output_limit_is_explicit_and_preserves_product(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    source = tmp_path / "page.html"
    source.write_bytes(page())
    env = inject(
        tmp_path,
        "from bt_api_py import trading_calendar_product_navigation_observation as n\nn._MAX_NAV_BYTES = 32\n",
    )
    result = run(command(root, config, supplement, *flags(source)), env=env)
    assert result.returncode == 10, result.stderr
    payload = show(root, json.loads(result.stdout)["candidate_id"])["payload"]
    assert payload["product_page"]["status"] == "CANDIDATE"
    nav = payload["product_navigation"]["navigation"]
    assert nav["status"] == "UNKNOWN" and nav["reason"] == "RESULT_RESOURCE_LIMIT"


def test_envelope_limit_preserves_unknown_without_truncation(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    source = tmp_path / "page.html"
    source.write_bytes(page(note="x" * 5000))
    env = inject(
        tmp_path,
        "from bt_api_py import trading_calendar_candidate_inbox as i\ni._LIMIT_CATALOG = 4096\n",
    )
    result = run(command(root, config, supplement, *flags(source)), env=env)
    assert result.returncode == 10, result.stderr
    payload = show(root, json.loads(result.stdout)["candidate_id"])["payload"]
    assert payload["product_page"]["status"] == "UNKNOWN"
    pair = payload["product_navigation"]
    assert pair["status"] == "UNKNOWN" and pair["navigation"]["reason"] == "RESULT_RESOURCE_LIMIT"
    assert pair["navigation"]["links"] is None and payload["content"]["trading_days"]


def test_navigation_parser_unavailable_keeps_product_as_partial(tmp_path, monkeypatch):
    import bt_api_ctp.calendar_shfe_product_navigation as nav

    source = tmp_path.resolve() / "page.html"
    source.write_bytes(page())

    def unavailable(**kwargs):
        raise ImportError("unavailable")

    monkeypatch.setattr(nav, "parse_shfe_product_navigation", unavailable)
    product, pair = observation.observe_product_and_navigation(
        source_url=URL, path=str(source), filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert product["status"] == "CANDIDATE" and pair["status"] == "PARTIAL"
    assert pair["navigation"]["reason"] == "PARSER_UNAVAILABLE"


@pytest.mark.parametrize("parser_name", ["product", "navigation"])
def test_parser_result_with_forged_source_or_digest_is_not_retained(
    tmp_path, monkeypatch, parser_name
):
    import bt_api_ctp.calendar_shfe_product_navigation as nav
    import bt_api_ctp.calendar_shfe_product_page as product_module

    source = tmp_path.resolve() / "page.html"
    source.write_bytes(page())
    module, name = (
        (product_module, "parse_shfe_product_page")
        if parser_name == "product"
        else (nav, "parse_shfe_product_navigation")
    )
    original = getattr(module, name)

    def forged(**kwargs):
        return replace(original(**kwargs), sha256="a" * 64)

    monkeypatch.setattr(module, name, forged)
    product, pair = observation.observe_product_and_navigation(
        source_url=URL, path=str(source), filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert pair["status"] == "PARTIAL"
    if parser_name == "product":
        assert product["status"] == "UNKNOWN" and pair["navigation"]["status"] == "CANDIDATE"
    else:
        assert product["status"] == "CANDIDATE" and pair["navigation"]["status"] == "UNKNOWN"


def test_same_buffer_digest_and_navigation_semantics_rechecked_after_rehash(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    source = tmp_path / "page.html"
    source.write_bytes(page())
    result = run(command(root, config, supplement, *flags(source)))
    cid = json.loads(result.stdout)["candidate_id"]
    path = root / "inbox" / f"candidate-{cid}.json"
    original = json.loads(path.read_bytes())
    for field in ("digest", "path", "identity", "qualification"):
        doc = copy.deepcopy(original)
        pair = doc["payload"]["product_navigation"]
        if field == "digest":
            pair["observed_sha256"] = "a" * 64
        elif field == "path":
            pair["navigation"]["links"][0]["path"] = "https://example.invalid/"
        elif field == "identity":
            pair["navigation"]["product_identity"] = "PROVEN"
        else:
            pair["retained_data_binding"] = "PROVEN"
        doc["payload_sha256"] = hashlib.sha256(inbox._canonical(doc["payload"])).hexdigest()
        path.write_bytes(inbox._canonical(doc))
        with pytest.raises((ValueError, TypeError)):
            inbox.read_candidate(root=root, candidate_id=cid, filesystem="TRUSTED_LOCAL_POSIX")


@pytest.mark.parametrize(
    "extra",
    [
        ["--discover-product-navigation"],
        ["--retain-candidate", "--trusted-local-posix", "--discover-product-navigation"],
        [
            "--retain-candidate",
            "--trusted-local-posix",
            "--product-source",
            "CU_FUTURE",
            "--discover-product-navigation",
        ],
    ],
)
def test_navigation_requires_explicit_local_page_and_retention(tmp_path, extra):
    root, _, config, supplement = setup_preview(tmp_path)
    result = run(command(root, config, supplement, *extra))
    assert result.returncode == 2 and not (root / "inbox").exists()


def test_file_change_during_read_yields_unknown_without_retaining_old_or_new(tmp_path, monkeypatch):
    source = tmp_path.resolve() / "page.html"
    source.write_bytes(page())
    original_read = local.os.read
    changed = False

    def mutate(fd, size):
        nonlocal changed
        data = original_read(fd, size)
        if not changed:
            changed = True
            source.write_bytes(b"modified contents")
        return data

    monkeypatch.setattr(local.os, "read", mutate)
    product, pair = observation.observe_product_and_navigation(
        source_url=URL, path=str(source), filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert changed and product["status"] == pair["status"] == "UNKNOWN"
    assert pair["navigation"]["links"] is None and pair["same_buffer_observation"] == "NOT_OBSERVED"


def test_directory_replacement_during_read_yields_unknown(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    parent = base / "moving"
    parent.mkdir()
    source = parent / "page.html"
    source.write_bytes(page())
    original_read = local.os.read
    changed = False

    def swap(fd, size):
        nonlocal changed
        data = original_read(fd, size)
        if not changed:
            changed = True
            parent.rename(base / "old")
            parent.mkdir()
            source.write_bytes(page(note="replacement"))
        return data

    monkeypatch.setattr(local.os, "read", swap)
    product, pair = observation.observe_product_and_navigation(
        source_url=URL, path=str(source), filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert changed and product["status"] == pair["status"] == "UNKNOWN"
    assert pair["same_buffer_observation"] == "NOT_OBSERVED" and pair["navigation"]["links"] is None
