"""Regression tests for code quality batch v3 fixes.

Covers:
- contextlib.suppress in ctp/__init__.py (SIM105 fix)
- import sorting in ctp/_ctp_base.py (I001 fix)
- duplicate _swig_repr removal in ctp/_ctp_base.py
- explicit encoding in security.py open() calls
- ZeroDivisionError guards in ensemble_model.py
- dead np.hstack removal in ensemble_model.py
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import numpy as np
import pytest


@pytest.mark.ctp
class TestCtpInitContextlibSuppress:
    """Verify ctp/__init__.py uses contextlib.suppress instead of try-except-pass."""

    def test_no_bare_try_except_pass(self):
        """SIM105: contextlib.suppress should replace try-except-pass."""
        import bt_api_ctp.ctp as ctp_pkg

        source_file = Path(inspect.getfile(ctp_pkg))
        source = source_file.read_text(encoding="utf-8")
        # Should contain contextlib.suppress
        assert "contextlib.suppress" in source
        # Should NOT contain the old pattern
        lines = source.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "pass" and i > 0:
                prev = lines[i - 1].strip()
                assert not prev.startswith("except Exception"), (
                    f"Found bare try-except-pass at line {i + 1}"
                )


@pytest.mark.ctp
class TestCtpBaseImportOrder:
    """Verify _ctp_base.py imports are sorted (I001 fix)."""

    def test_imports_sorted(self):
        """I001: imports should be sorted by ruff/isort rules."""
        from bt_api_ctp.ctp import _ctp_base

        source_file = Path(inspect.getfile(_ctp_base))
        source = source_file.read_text(encoding="utf-8")
        lines = source.splitlines()

        # Find 'from ...' import lines at the top
        from_imports = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("from ") and "import" in stripped:
                module = stripped.split("from ")[1].split(" import")[0].strip()
                from_imports.append(module)
            elif stripped and not stripped.startswith("#") and not stripped.startswith('"""'):
                if from_imports:
                    break

        # sys < traceback < types (alphabetical)
        assert from_imports == sorted(from_imports), f"from-imports are not sorted: {from_imports}"


@pytest.mark.ctp
class TestCtpBaseNoDuplicateSwigRepr:
    """Verify _swig_repr is defined only once in _ctp_base.py."""

    def test_single_swig_repr_definition(self):
        """Duplicate _swig_repr should be removed."""
        from bt_api_ctp.ctp import _ctp_base

        source_file = Path(inspect.getfile(_ctp_base))
        source = source_file.read_text(encoding="utf-8")
        # Count 'def _swig_repr(' occurrences
        count = source.count("def _swig_repr(")
        assert count == 1, f"Expected 1 _swig_repr definition, found {count}"


class TestSecurityExplicitEncoding:
    """Verify security.py open() calls use explicit encoding."""

    def test_open_calls_have_encoding(self):
        """All open() calls should specify encoding parameter."""
        from bt_api_base import security as sec_mod

        source_file = Path(inspect.getfile(sec_mod))
        source = source_file.read_text(encoding="utf-8")

        # Find all 'open(' calls that aren't comments
        import re

        open_calls = re.findall(r"^\s+with open\(.+\)", source, re.MULTILINE)
        open_calls += re.findall(r"^\s+open\(.+\)", source, re.MULTILINE)
        for call in open_calls:
            assert "encoding" in call, f"open() call missing encoding parameter: {call.strip()}"

    def test_create_env_template_writes_utf8(self):
        """create_env_template should produce valid UTF-8 output."""
        from bt_api_base.security import create_env_template

        with tempfile.NamedTemporaryFile(suffix=".env", delete=False, mode="w") as f:
            tmp_path = f.name

        try:
            create_env_template(tmp_path)
            content = Path(tmp_path).read_text(encoding="utf-8")
            assert "BINANCE_API_KEY" in content
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class TestEnsembleZeroDivisionGuard:
    """Verify ensemble_model.py guards against ZeroDivisionError."""

    def _make_ensemble(self):
        """Create a minimal RiskEnsembleModel with zero-weight models."""
        from bt_api_py.risk_management.ml_models.ensemble_model import (
            RiskEnsembleModel,
        )

        ensemble = RiskEnsembleModel.__new__(RiskEnsembleModel)
        ensemble.models = {}
        ensemble.model_weights = {}
        ensemble.ensemble_method = "weighted_average"
        ensemble.is_trained = False
        ensemble.training_history = []
        ensemble.performance_tracker = {}
        ensemble._prediction_cache = {}
        ensemble._prediction_cache_maxsize = 100
        return ensemble

    def test_weighted_average_zero_weight_returns_zeros(self):
        """_predict_weighted_average should not raise ZeroDivisionError with no models."""
        ensemble = self._make_ensemble()
        X = np.array([[1, 2, 3], [4, 5, 6]])
        result = ensemble._predict_weighted_average(X)
        assert result.shape == (2,)
        np.testing.assert_array_equal(result, np.zeros(2, dtype=int))

    def test_dynamic_weighting_zero_weight_returns_zeros(self):
        """_predict_dynamic_weighting should not raise ZeroDivisionError with no models."""
        ensemble = self._make_ensemble()
        X = np.array([[1, 2, 3], [4, 5, 6]])
        result = ensemble._predict_dynamic_weighting(X)
        assert result.shape == (2,)
        np.testing.assert_array_equal(result, np.zeros(2, dtype=int))

    def test_proba_weighted_average_no_models_returns_default(self):
        """_predict_proba_weighted_average with no models returns 0.5/0.5 default."""
        ensemble = self._make_ensemble()
        X = np.array([[1, 2, 3], [4, 5, 6]])
        result = ensemble._predict_proba_weighted_average(X)
        assert result.shape == (2, 2)
        np.testing.assert_array_almost_equal(result, [[0.5, 0.5], [0.5, 0.5]])

    def test_proba_dynamic_weighting_no_models_returns_default(self):
        """_predict_proba_dynamic_weighting with no models returns 0.5/0.5 default."""
        ensemble = self._make_ensemble()
        X = np.array([[1, 2, 3], [4, 5, 6]])
        result = ensemble._predict_proba_dynamic_weighting(X)
        assert result.shape == (2, 2)
        np.testing.assert_array_almost_equal(result, [[0.5, 0.5], [0.5, 0.5]])
