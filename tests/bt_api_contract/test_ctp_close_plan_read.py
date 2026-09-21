"""Contracts for exact and consistent CTP evidence field reads."""

from types import SimpleNamespace

import pytest

from bt_api_py.ctp_close_plan import _MISSING, CtpClosePlanError, _read


@pytest.mark.parametrize(
    "value",
    [
        {"canonical": True, "alias": 1},
        {"alias": 1, "canonical": True},
        SimpleNamespace(canonical=True, alias=1),
    ],
)
def test_read_uses_requested_name_order_for_equal_alias_values(value):
    # bool and int compare equal, so this proves which named value is returned.
    assert _read(value, ("canonical", "alias")) is True


@pytest.mark.parametrize(
    "value",
    [
        {"canonical": "first", "alias": "other"},
        {"alias": "other", "canonical": "first"},
        SimpleNamespace(canonical="first", alias="other"),
    ],
)
def test_read_rejects_inconsistent_aliases(value):
    with pytest.raises(CtpClosePlanError) as raised:
        _read(value, ("canonical", "alias"))

    assert raised.value.code == "O3B_POSITION_EVIDENCE_INCONSISTENT"


@pytest.mark.parametrize("value", [{}, SimpleNamespace()])
def test_read_preserves_missing_sentinel_and_explicit_default(value):
    assert _read(value, ("absent",)) is _MISSING

    default = object()
    assert _read(value, ("absent",), default) is default
