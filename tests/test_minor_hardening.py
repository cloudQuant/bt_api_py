"""Task 5.7 小项安全与正确性修复测试（E-06/E-07/F-03/A-16/A-12）。"""

from __future__ import annotations

import warnings

import pytest


# ── Step 1: ml_base pickle 路径限制（E-06）──────────────────────────
class _DummyModel:
    pass


def test_load_model_rejects_path_outside_models_dir(tmp_path) -> None:
    from bt_api_py.risk_management.ml_models.ml_base import BaseMLModel

    class Dummy(BaseMLModel):
        def train(self, X, y, validation_data=None):
            return {}

        def predict(self, X):
            return X

        def predict_proba(self, X):
            return X

    outside = tmp_path / "evil.pkl"
    outside.write_bytes(b"dummy")
    model = Dummy("test")
    with pytest.raises(ValueError, match="models"):
        model.load_model(str(outside))


# ── Step 2: mask_sensitive 全掩码（E-07）─────────────────────────────
def test_mask_sensitive_full_masking() -> None:
    from bt_api_py.certification.audit import mask_sensitive

    result = mask_sensitive({"api_key": "sk-abcdef123456", "amount": 1.0})
    # 原值任何子串（含后 4 位）不出现在结果
    assert "3456" not in str(result)
    assert "abcdef" not in str(result)
    # 非敏感字段保留
    assert result["amount"] == 1.0


# ── Step 3: sklearn 惰性导入（F-03）──────────────────────────────────
def test_import_ml_base_without_sklearn(monkeypatch) -> None:
    import builtins
    import importlib

    import bt_api_py.risk_management.ml_models.ml_base as mb

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sklearn" or name.startswith("sklearn."):
            raise ImportError("No module named 'sklearn'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    importlib.reload(mb)  # 不失败


# ── Step 4: mock 加权平均价（A-16）───────────────────────────────────
@pytest.mark.asyncio
async def test_mock_broker_weighted_average_price() -> None:
    from bt_api_py.brokers.mock import MockBrokerAdapter
    from bt_api_py.brokers.types import OrderRequest

    adapter = MockBrokerAdapter()
    await adapter.connect()
    await adapter.place_order(
        OrderRequest(account_id="paper", symbol="RB", side="buy", quantity=1, order_type="limit", price=100.0)
    )
    await adapter.place_order(
        OrderRequest(account_id="paper", symbol="RB", side="buy", quantity=1, order_type="limit", price=200.0)
    )
    positions = await adapter.list_positions("paper")
    assert positions[0].average_price == 150.0  # (1*100 + 1*200)/2


# ── Step 5: get_async_request_api 改名（A-12）─────────────────────────
def test_get_async_request_api_deprecated_alias() -> None:
    from bt_api_py import BtApi

    api = BtApi(None, debug=False)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        api.get_async_request_api("NONEXISTENT")
    assert any(issubclass(x.category, DeprecationWarning) for x in w)
