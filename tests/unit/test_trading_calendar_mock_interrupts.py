"""Review interrupt propagation keeps original IDs and never infers commit."""

import signal

import pytest

from bt_api_py import trading_calendar_review_bundle as review
from tests.unit.test_trading_calendar_review_bundle import sample

FS = "TRUSTED_LOCAL_POSIX"


def test_review_interrupt_before_write_compatibility_and_propagation(tmp_path):
    def interrupt(_):
        signal.raise_signal(signal.SIGINT)

    legacy = review.build_once(
        root=str(tmp_path),
        candidate_id="1" * 32,
        input_data=sample(),
        filesystem=FS,
        emit=interrupt,
    )
    assert legacy["status"] == "NOT_COMMITTED"
    with pytest.raises(KeyboardInterrupt) as caught:
        review.build_once(
            root=str(tmp_path),
            candidate_id="2" * 32,
            input_data=sample(),
            filesystem=FS,
            emit=interrupt,
            propagate_interrupt=True,
        )
    assert caught.value.calendar_outcome == {"status": "NOT_COMMITTED", "candidate_id": "2" * 32}
    assert not (tmp_path / "review-bundles").exists()


def test_review_interrupt_after_write_preserves_unknown_id(tmp_path, monkeypatch):
    original = review.storage._write

    def interrupt_after(*args):
        assert original(*args) == "COMMITTED"
        signal.raise_signal(signal.SIGINT)

    monkeypatch.setattr(review.storage, "_write", interrupt_after)
    with pytest.raises(KeyboardInterrupt) as caught:
        review.build_once(
            root=str(tmp_path),
            candidate_id="3" * 32,
            input_data=sample(),
            filesystem=FS,
            emit=lambda _: None,
            propagate_interrupt=True,
        )
    assert caught.value.calendar_outcome == {"status": "COMMIT_UNKNOWN", "candidate_id": "3" * 32}
    visible = review.show(root=str(tmp_path), candidate_id="3" * 32, filesystem=FS)
    assert visible["status"] == "VISIBLE_UNAUTHENTICATED"
