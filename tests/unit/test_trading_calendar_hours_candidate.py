"""Offline candidate derivation; source and declarations are synthetic."""

import copy
import hashlib
import json
import subprocess
import sys
from dataclasses import replace

import pytest
from bt_api_ctp.calendar_shfe_page import parse_shfe_page
from bt_api_ctp.calendar_supplement import _validate

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_hours_candidate as hours
from tests.unit.test_trading_calendar_candidate_inbox import (
    HOURS,
    HOURS_URL,
    ID,
    input_content,
    objects,
    source_checks,
)
from tests.unit.test_trading_calendar_observation_run import env_without_network

DERIVED = "c" * 32
FS = "TRUSTED_LOCAL_POSIX"


def mapping():
    return [
        {
            "venue": "SHFE",
            "kind": "future",
            "page_product_name": "合成甲",
            "product": "cu",
            "contracts": ["cu2701"],
            "valid_from": "2026-01-01",
            "valid_to": "2027-12-31",
        },
        {
            "venue": "INE",
            "kind": "option",
            "page_product_name": "合成甲期权",
            "product": "cu_option",
            "contracts": ["cu2701-C-100"],
            "valid_from": "2026-01-01",
            "valid_to": "2027-12-31",
        },
    ]


def retain(root, *, page=None, mutate=None):
    default_page, _, merged = objects()
    if mutate:
        content = copy.deepcopy(merged.supplement.content)
        mutate(content, default_page)
        merged = replace(merged, supplement=_validate(content))
    return inbox.retain_candidate(
        root=str(root),
        candidate_id=ID,
        merged=merged,
        hours_page=default_page if page is None else page,
        source_checks=source_checks(),
        filesystem=FS,
    )


@pytest.fixture
def root(tmp_path):
    path = tmp_path.resolve() / "calendar"
    path.mkdir()
    assert retain(path).status == "COMMITTED"
    return path


def build(root, selections=None, candidate_id=DERIVED):
    allocated = []
    result = hours.build_once(
        root=str(root),
        source_candidate_id=ID,
        candidate_id=candidate_id,
        selections=mapping() if selections is None else selections,
        filesystem=FS,
        emit=allocated.append,
    )
    assert allocated == [{"event": "HOURS_CANDIDATE_ID_ALLOCATED", "candidate_id": candidate_id}]
    return result


def shown(root, candidate_id=DERIVED):
    return hours.show(root=str(root), candidate_id=candidate_id, filesystem=FS)


def test_derived_candidate_retains_holidays_yaml_day_and_unresolved_night(root):
    source_path = root / "inbox" / f"candidate-{ID}.json"
    original = source_path.read_bytes()
    source = inbox.read_candidate(root=str(root), candidate_id=ID, filesystem=FS)
    assert build(root)["status"] == "COMMITTED"
    result = shown(root)
    assert result["status"] == "VISIBLE_UNAUTHENTICATED"
    data = result["candidate"]
    assert data["schema"] == "bt_api.calendar-hours-review-candidate/1"
    assert data["source_payload_sha256"] == source["payload_sha256"]
    assert data["source_candidate_id"] == ID and data["source_association"] == "NOT_PROVEN"
    assert data["content"]["trading_days"] == source["payload"]["content"]["trading_days"]
    assert data["content"]["exceptions"] == source["payload"]["content"]["exceptions"]
    assert {item["id"] for item in data["content"]["sources"]} >= {"manual"}
    assert {row["kind"] for row in data["content"]["trading_hours"]} == {"future", "option"}
    for row in data["content"]["trading_hours"]:
        assert any(w["start"] == "09:00" and w["end"] == "10:15" for w in row["day_windows"])
        assert row["night_windows"] == row["night_mappings"] == []
        assert row["windows_complete"] is row["exceptions_complete"] is False
    assert len(data["holiday_unresolved_nights"]) == 7
    assert all(item["trading_day"] is None for item in data["holiday_unresolved_nights"])
    assert all(
        item["availability"] == item["trading_day_mapping"] == "UNKNOWN"
        for item in data["hours_unresolved_nights"]
    )
    assert data["declarations"] == data["verification"] == "UNVERIFIED"
    assert data["coverage"] == data["source_authentication"] == "NOT_PROVEN"
    assert data["publication"] == "NOT_AUTHORIZED"
    assert source_path.read_bytes() == original
    assert not (root / "CURRENT.json").exists()
    assert build(root)["status"] == "NOT_COMMITTED"
    assert shown(root)["candidate"] == data


def test_existing_yaml_hours_and_exceptions_remain_intact(tmp_path):
    root = tmp_path / "calendar"
    root.mkdir()

    def mutate(content, _page):
        content["trading_hours"].append(
            {
                "venue": "DCE",
                "kind": "future",
                "product": "m",
                "contracts": ["m2701"],
                "valid_from": "2026-01-01",
                "valid_to": "2026-12-31",
                "day_windows": [],
                "night_windows": [],
                "night_mappings": [],
                "source_ids": ["manual"],
                "windows_complete": False,
                "exceptions_complete": False,
            }
        )
        content["exceptions"].append(
            {
                "venue": "DCE",
                "kind": "future",
                "product": "m",
                "contract": "m2701",
                "trading_day": "2026-01-02",
                "action": "close_day",
                "window_id": None,
                "source_ids": ["manual"],
            }
        )

    assert retain(root, mutate=mutate).status == "COMMITTED"
    source = inbox.read_candidate(root=str(root), candidate_id=ID, filesystem=FS)["payload"][
        "content"
    ]
    assert build(root)["status"] == "COMMITTED"
    result = shown(root)["candidate"]["content"]
    assert result["trading_hours"][:1] == source["trading_hours"]
    assert result["exceptions"] == source["exceptions"]
    assert result["trading_days"] == source["trading_days"]


@pytest.mark.parametrize(
    "mutation",
    ["missing", "unknown", "duplicate", "bad_venue", "bad_contract", "bad_dates", "extra"],
)
def test_invalid_explicit_declarations_are_rejected(root, mutation):
    selections = mapping()
    if mutation == "missing":
        del selections[0]["valid_to"]
    elif mutation == "unknown":
        selections[0]["page_product_name"] = "不存在"
    elif mutation == "duplicate":
        selections.append(copy.deepcopy(selections[0]))
    elif mutation == "bad_venue":
        selections[0]["venue"] = "DCE"
    elif mutation == "bad_contract":
        selections[0]["contracts"] = ["../bad"]
    elif mutation == "bad_dates":
        selections[0]["valid_from"] = "2028-01-01"
    else:
        selections[0]["authority"] = "VERIFIED"
    assert build(root, selections)["status"] == "NOT_COMMITTED"
    assert shown(root)["status"] == "UNKNOWN"


@pytest.mark.parametrize("case", ["overlap", "source_conflict"])
def test_conflicting_original_content_never_derives(tmp_path, case):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()

    def mutate(content, page):
        if case == "source_conflict":
            content["sources"].append(
                {"id": f"shfe.hours.{page.sha256}", "description": "different"}
            )
        else:
            content["trading_hours"].append(
                {
                    "venue": "SHFE",
                    "kind": "future",
                    "product": "cu",
                    "contracts": ["cu2701"],
                    "valid_from": "2026-01-01",
                    "valid_to": "2026-12-31",
                    "day_windows": [],
                    "night_windows": [],
                    "night_mappings": [],
                    "source_ids": ["manual"],
                    "windows_complete": False,
                    "exceptions_complete": False,
                }
            )

    assert retain(root, mutate=mutate).status == "COMMITTED"
    before = (root / "inbox" / f"candidate-{ID}.json").read_bytes()
    assert build(root)["status"] == "NOT_COMMITTED"
    assert (root / "inbox" / f"candidate-{ID}.json").read_bytes() == before


def test_missing_hours_page_and_yaml_only_are_rejected(tmp_path):
    from bt_api_ctp.calendar_supplement import parse_supplement

    for mode in ("missing_page", "yaml_only"):
        root = tmp_path / mode
        root.mkdir()
        _, _, merged = objects()
        kwargs = {
            "root": str(root),
            "candidate_id": ID,
            "hours_page": None,
            "source_checks": source_checks(),
            "filesystem": FS,
        }
        if mode == "missing_page":
            kwargs["merged"] = merged
        else:
            kwargs.update(
                merged=None, supplement=parse_supplement(json.dumps(input_content()).encode())
            )
            checks = source_checks()
            checks.update(merge_status="MERGE_ERROR", normalization_status="NOT_ATTEMPTED")
            kwargs["source_checks"] = checks
        assert inbox.retain_candidate(**kwargs).status == "COMMITTED"
        assert build(root)["status"] == "NOT_COMMITTED"


def test_cross_midnight_night_remains_unresolved(tmp_path):
    root = tmp_path / "calendar"
    root.mkdir()
    page = parse_shfe_page(source_id=HOURS_URL, html=HOURS.replace(b"21:00-23:00", b"21:00-01:00"))
    assert retain(root, page=page).status == "COMMITTED"
    assert build(root)["status"] == "COMMITTED"
    nights = shown(root)["candidate"]["hours_unresolved_nights"]
    assert any(
        stage["day_transition"] == "INFERRED_NEXT_DAY" for n in nights for stage in n["stages"]
    )
    assert all(n["trading_day_mapping"] == "UNKNOWN" for n in nights)


@pytest.mark.parametrize("replacement", [b"10:00-11:30", b"23:30-01:00"])
def test_overlapping_or_cross_day_daytime_is_rejected(tmp_path, replacement):
    root = tmp_path / "calendar"
    root.mkdir()
    target = b"10:30-11:30" if replacement.startswith(b"10:") else b"13:30-15:00"
    page = parse_shfe_page(source_id=HOURS_URL, html=HOURS.replace(target, replacement))
    assert retain(root, page=page).status == "COMMITTED"
    assert build(root)["status"] == "NOT_COMMITTED"
    assert not (root / "CURRENT.json").exists()


def test_missing_day_phase_is_rejected(tmp_path):
    root = tmp_path / "calendar"
    root.mkdir()
    raw = HOURS.replace("集合竞价撮合 08:59-09:00<br>".encode(), b"")
    page = parse_shfe_page(source_id=HOURS_URL, html=raw)
    assert retain(root, page=page).status == "COMMITTED"
    assert build(root)["status"] == "NOT_COMMITTED"


@pytest.mark.parametrize(
    "private",
    [
        "root",
        "/Users/another/private.yaml",
        r"C:\private\file.yaml",
        r"\\server\share\private.yaml",
        "//server/share/private.yaml",
        "file:///Users/another/private.yaml",
        "FILE://LOCALHOST/Users/another/private.yaml",
        "file:/Users/another/private.yaml",
        "file%3A%2F%2F%2FUsers%2Fanother%2Fprivate.yaml",
        "%66%69%6c%65%253A%252F%252F%252FUsers%252Fanother%252Fprivate.yaml",
        r"file://server/share/private.yaml",
        "%5C%5Cserver%5Cshare%5Cprivate.yaml",
    ],
)
def test_source_content_containing_private_path_is_not_emitted(tmp_path, private):
    root = tmp_path / "calendar"
    root.mkdir()

    def mutate(content, _page):
        content["sources"][0]["description"] = (
            str(root) + "/private" if private == "root" else private
        )

    assert retain(root, mutate=mutate).status == "COMMITTED"
    result = build(root)
    assert result["status"] == "NOT_COMMITTED" and str(root) not in json.dumps(result)
    source = inbox.read_candidate(root=str(root), candidate_id=ID, filesystem=FS)
    body = hours._derive(source, mapping(), DERIVED)
    folder = root / "hours-review-candidates"
    folder.mkdir(mode=0o700, exist_ok=True)
    target = folder / f"candidate-{DERIVED}.json"
    target.write_bytes(hours.storage._envelope(body))
    target.chmod(0o600)
    observed = shown(root)
    assert observed["status"] == "UNKNOWN" and "candidate" not in observed
    assert private not in json.dumps(observed)


@pytest.mark.parametrize(
    "description",
    [
        "https://www.shfe.com.cn/services/calenderandholidays/tradinghours/",
        "official page https://example.com/path/file:history?version=1",
        "https://example.com/path%2Fpage",
    ],
)
def test_https_source_references_are_preserved(tmp_path, description):
    root = tmp_path / "calendar"
    root.mkdir()

    def mutate(content, _page):
        content["sources"][0]["description"] = description

    assert retain(root, mutate=mutate).status == "COMMITTED"
    assert build(root)["status"] == "COMMITTED"
    assert shown(root)["candidate"]["content"]["sources"][0]["description"] == description


def test_source_missing_or_corrupt_fails_closed(root):
    original = root / "inbox" / f"candidate-{ID}.json"
    data = original.read_bytes()
    original.unlink()
    assert build(root)["status"] == "NOT_COMMITTED"
    original.write_bytes(data[:-1] + b"x")
    original.chmod(0o600)
    assert build(root)["status"] == "NOT_COMMITTED"
    assert shown(root)["status"] == "UNKNOWN"


def test_original_change_or_derived_tampering_is_not_visible(root):
    assert build(root)["status"] == "COMMITTED"
    path = root / "hours-review-candidates" / f"candidate-{DERIVED}.json"
    body, _ = hours.storage._unwrap(path.read_bytes())
    body["content"]["trading_hours"][0]["day_windows"][0]["start"] = "08:00"
    path.write_bytes(hours.storage._envelope(body))
    assert shown(root)["status"] == "UNKNOWN"
    path.unlink()
    assert build(root)["status"] == "COMMITTED"
    original = root / "inbox" / f"candidate-{ID}.json"
    data = json.loads(original.read_bytes())
    data["payload"]["content"]["sources"][0]["description"] = "changed"
    data["payload_sha256"] = hashlib.sha256(inbox._canonical(data["payload"])).hexdigest()
    original.write_bytes(inbox._canonical(data))
    assert shown(root)["status"] == "UNKNOWN"


def test_write_unknown_keeps_id_and_never_retries(root, monkeypatch):
    original = hours.storage._write
    calls = []

    def unknown(*args):
        calls.append(1)
        assert original(*args) == "COMMITTED"
        return "COMMIT_UNKNOWN"

    monkeypatch.setattr(hours.storage, "_write", unknown)
    value = build(root)
    assert value["status"] == "COMMIT_UNKNOWN" and value["candidate_id"] == DERIVED
    assert calls == [1] and shown(root)["status"] == "VISIBLE_UNAUTHENTICATED"


@pytest.mark.parametrize("after_write", [False, True])
@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_interruption_preserves_id_without_replaying(
    root, monkeypatch, after_write, exception_type
):
    original = hours.storage._write
    calls = []

    def interrupted(*args):
        calls.append(1)
        if after_write:
            assert original(*args) == "COMMITTED"
        raise exception_type("synthetic interruption")

    monkeypatch.setattr(hours.storage, "_write", interrupted)
    allocated = []
    with pytest.raises(exception_type) as raised:
        hours.build_once(
            root=str(root),
            source_candidate_id=ID,
            candidate_id=DERIVED,
            selections=mapping(),
            filesystem=FS,
            emit=allocated.append,
        )
    assert allocated == [{"event": "HOURS_CANDIDATE_ID_ALLOCATED", "candidate_id": DERIVED}]
    assert raised.value.commit_status == "COMMIT_UNKNOWN" and raised.value.candidate_id == DERIVED
    assert calls == [1]
    assert shown(root)["status"] == ("VISIBLE_UNAUTHENTICATED" if after_write else "UNKNOWN")


def test_offline_cli_build_and_show_with_no_network(root, tmp_path):
    env = env_without_network(tmp_path)
    base = [sys.executable, "-m", "bt_api_py.trading_calendar_hours_candidate"]
    common = ["--calendar-root", str(root), "--candidate-id", DERIVED, "--trusted-local-posix"]
    result = subprocess.run(
        base + ["build", *common, "--source-candidate-id", ID],
        input=json.dumps(mapping()),
        text=True,
        capture_output=True,
        env=env,
        timeout=20,
    )
    assert result.returncode == 10, (result.stdout, result.stderr)
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines[0]["candidate_id"] == lines[1]["candidate_id"] == DERIVED
    result = subprocess.run(
        base + ["show", *common], text=True, capture_output=True, env=env, timeout=20
    )
    assert (
        result.returncode == 10 and json.loads(result.stdout)["status"] == "VISIBLE_UNAUTHENTICATED"
    )
    assert str(root) not in result.stdout + result.stderr
