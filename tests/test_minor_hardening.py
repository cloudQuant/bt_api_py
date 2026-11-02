"""Task 5.7 小项安全与正确性修复测试（E-06/E-07/F-03/A-16/A-12）。"""

from __future__ import annotations

import pytest


# ── Step 1: ml_base pickle 路径限制（E-06）──────────────────────────
class _DummyModel:
    pass


def test_load_model_rejects_path_outside_models_dir(tmp_path) -> None:
    from bt_api_risk.ml_models.ml_base import BaseMLModel

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

    import bt_api_risk.ml_models.ml_base as mb

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sklearn" or name.startswith("sklearn."):
            raise ImportError("No module named 'sklearn'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    importlib.reload(mb)  # 不失败


# ── Step 5: request API access（A-12）─────────────────────────
def test_get_request_api_unknown_exchange() -> None:
    from bt_api_py import BtApi

    api = BtApi(None, debug=False)
    assert api.get_request_api("NONEXISTENT") is None
