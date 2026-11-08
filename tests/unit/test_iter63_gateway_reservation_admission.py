from contextlib import contextmanager
from dataclasses import FrozenInstanceError

import pytest

from bt_api_py.gateway_direct_service import (
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
    GatewayReservationAdmissionDenied,
)
from tests.unit.test_iter63_gateway_direct_service import _binding, _context, _order, _reserve


def binding_for(journal, fence):
    return GatewayDirectServiceBinding(
        journal,
        {"principal-a": GatewayDirectGrant("strategy-a", frozenset({"rb"}))},
        reservation_fence=fence,
    )


def test_denial_is_finite_and_retry_bypasses_denied_fence(tmp_path):
    journal, _ = _binding(tmp_path)
    seen = []

    @contextmanager
    def admission(context, request):
        seen.append((context, request))
        with pytest.raises(FrozenInstanceError):
            request.symbol = "other"
        raise GatewayReservationAdmissionDenied()
        yield

    binding = binding_for(journal, admission)
    assert _reserve(binding, _context()) == {
        "status": "REQUEST_REJECTED",
        "code": "ADMISSION_DENIED",
    }
    assert journal.recovery_page(limit=10).revision == 0
    original = _reserve(binding_for(journal, None), _context())
    retry = _reserve(binding, _context())
    assert retry["record"]["reservation_id"] == original["record"]["reservation_id"]
    assert len(seen) == 1 and seen[0][1].to_dict() == _order()
    assert (
        binding.handle(
            "direct.command_status", context=_context(), arguments={"request_key": "key-1"}
        )["status"]
        == "OK"
    )
    record = original["record"]
    assert (
        binding.handle(
            "direct.release",
            context=_context(),
            arguments={
                "request_key": "key-1",
                "reservation_id": record["reservation_id"],
                "digest": record["digest"],
                "operation_id": "release",
                "expected_version": record["version"],
            },
        )["record"]["state"]
        == "RELEASED"
    )


def test_exit_denial_after_commit_propagates_as_unknown(tmp_path):
    journal, _ = _binding(tmp_path)

    @contextmanager
    def admission(context, request):
        yield
        raise GatewayReservationAdmissionDenied("exit")

    binding = binding_for(journal, admission)
    with pytest.raises(GatewayReservationAdmissionDenied, match="exit"):
        _reserve(binding, _context())
    assert journal.get("strategy-a", "key-1", "principal-a").status == "RESERVED"


def test_commit_denial_after_commit_is_not_translated(tmp_path, monkeypatch):
    journal, _ = _binding(tmp_path)

    @contextmanager
    def admission(context, request):
        yield

    original = journal._commit

    def commit(db):
        original(db)
        raise GatewayReservationAdmissionDenied("lost commit reply")

    monkeypatch.setattr(journal, "_commit", commit)
    binding = binding_for(journal, admission)
    with pytest.raises(GatewayReservationAdmissionDenied, match="lost commit reply"):
        _reserve(binding, _context())
    monkeypatch.setattr(journal, "_commit", original)
    assert journal.get("strategy-a", "key-1", "principal-a").status == "RESERVED"


def test_other_errors_and_subclass_denial_propagate(tmp_path):
    journal, _ = _binding(tmp_path)

    class Derived(GatewayReservationAdmissionDenied):
        pass

    def admission(context, request):
        raise Derived("not exact")

    with pytest.raises(Derived):
        _reserve(binding_for(journal, admission), _context())
    assert journal.recovery_page(limit=10).revision == 0


def test_invalid_request_never_enters_admission(tmp_path):
    journal, _ = _binding(tmp_path)

    def admission(context, request):
        raise AssertionError("must not enter")

    binding = binding_for(journal, admission)
    assert _reserve(binding, _context(), payload=_order(symbol="outside"))["code"] == "SCOPE_DENIED"
    assert journal.recovery_page(limit=10).revision == 0
