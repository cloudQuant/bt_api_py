"""Tests for ml_base module - BaseMLModel, RiskPredictionResult, ModelMetrics, ModelComparator."""

import tempfile
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("sklearn")

from bt_api_py.risk_management.ml_models.ml_base import (
    BaseMLModel,
    ModelComparator,
    ModelMetrics,
    RiskPredictionResult,
)


class DummyMLModel(BaseMLModel):
    """Concrete implementation for testing."""

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        validation_data: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> dict:
        self.is_trained = True
        self.training_time = 1.0
        self.last_training_time = 1.0
        self.metrics["training_samples"] = len(X)
        self.metrics["validation_samples"] = len(validation_data[0]) if validation_data else 0
        return {"status": "success", "samples": len(X)}

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self.is_trained:
            raise RuntimeError("Model not trained")
        return np.ones(len(X), dtype=int)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self.is_trained:
            raise RuntimeError("Model not trained")
        # Return probabilities for 2 classes
        return np.column_stack([np.zeros(len(X)), np.ones(len(X))])


class TestBaseMLModel:
    """Tests for BaseMLModel abstract base class."""

    def test_init_default_config(self):
        """Test initialization with default config."""
        model = DummyMLModel("test_model")

        assert model.model_name == "test_model"
        assert model.config == {}
        assert model.is_trained is False
        assert model.model is None
        assert model.model_version == "1.0.0"
        assert model.training_history == []
        assert model.feature_names == []

    def test_init_custom_config(self):
        """Test initialization with custom config."""
        config = {"param1": "value1", "param2": 42}
        model = DummyMLModel("custom_model", config=config)

        assert model.config == config

    def test_train_and_predict(self):
        """Test training and prediction flow."""
        model = DummyMLModel("test")

        X = np.array([[1, 2], [3, 4], [5, 6]])
        y = np.array([0, 1, 1])

        result = model.train(X, y)

        assert result["status"] == "success"
        assert model.is_trained is True

        predictions = model.predict(X)
        assert len(predictions) == 3

    def test_train_with_validation_data(self):
        """Test training with validation data."""
        model = DummyMLModel("test")

        X = np.array([[1, 2], [3, 4]])
        y = np.array([0, 1])
        X_val = np.array([[5, 6]])
        y_val = np.array([1])

        result = model.train(X, y, validation_data=(X_val, y_val))

        assert result["status"] == "success"
        assert model.metrics["validation_samples"] == 1

    def test_evaluate_not_trained(self):
        """Test evaluate returns empty dict when not trained."""
        model = DummyMLModel("test")

        X = np.array([[1, 2], [3, 4]])
        y = np.array([0, 1])

        result = model.evaluate(X, y)

        assert result == {}

    def test_evaluate_trained(self):
        """Test evaluate returns metrics when trained."""
        model = DummyMLModel("test")

        X = np.array([[1, 2], [3, 4], [5, 6]])
        y = np.array([0, 1, 1])

        model.train(X, y)
        metrics = model.evaluate(X, y)

        assert "accuracy" in metrics
        assert "precision" in metrics
        assert "recall" in metrics
        assert "f1_score" in metrics

    def test_save_and_load_model(self):
        """Test model save and load roundtrip."""
        model = DummyMLModel("test", config={"key": "value"})
        model.feature_names = ["f1", "f2"]
        model.feature_importance = {"f1": 0.8, "f2": 0.2}

        X = np.array([[1, 2], [3, 4]])
        y = np.array([0, 1])
        model.train(X, y)

        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            model_path = f.name

        try:
            # Save
            assert model.save_model(model_path) is True

            # Load into new model
            new_model = DummyMLModel("other")
            assert new_model.load_model(model_path) is True

            assert new_model.model_name == "test"
            assert new_model.config == {"key": "value"}
            assert new_model.feature_names == ["f1", "f2"]
            assert new_model.feature_importance == {"f1": 0.8, "f2": 0.2}
            assert new_model.is_trained is True
        finally:
            Path(model_path).unlink(missing_ok=True)

    def test_save_model_error_handling(self):
        """Test save_model handles errors gracefully."""
        model = DummyMLModel("test")

        # Invalid path should return False
        assert model.save_model("/nonexistent/path/model.pkl") is False

    def test_load_model_error_handling(self):
        """Test load_model handles errors gracefully."""
        model = DummyMLModel("test")

        # Non-existent file should return False
        assert model.load_model("/nonexistent/path/model.pkl") is False

    def test_get_feature_importance(self):
        """Test getting feature importance."""
        model = DummyMLModel("test")
        model.feature_importance = {"f1": 0.5, "f2": 0.3, "f3": 0.2}

        importance = model.get_feature_importance()

        assert importance == {"f1": 0.5, "f2": 0.3, "f3": 0.2}

    def test_update_feature_names(self):
        """Test updating feature names."""
        model = DummyMLModel("test")

        model.update_feature_names(["a", "b", "c"])

        assert model.feature_names == ["a", "b", "c"]
        assert model.metrics["features_count"] == 3

    def test_get_model_info(self):
        """Test getting model info."""
        model = DummyMLModel("test", config={"lr": 0.01})
        model.model_version = "2.0.0"
        model.is_trained = True
        model.training_time = 5.0

        info = model.get_model_info()

        assert info["model_name"] == "test"
        assert info["model_version"] == "2.0.0"
        assert info["is_trained"] is True
        assert info["training_time"] == 5.0
        assert info["config"] == {"lr": 0.01}

    def test_record_training_step(self):
        """Test recording training steps."""
        model = DummyMLModel("test")

        model._record_training_step({"epoch": 1, "loss": 0.5})
        model._record_training_step({"epoch": 2, "loss": 0.3})

        assert len(model.training_history) == 2
        assert "timestamp" in model.training_history[0]
        assert model.training_history[0]["epoch"] == 1

    def test_record_training_step_limits_history(self, monkeypatch):
        """Test training history is limited to 500 entries after exceeding 1000."""
        model = DummyMLModel("test")

        # Add 1001 entries
        for i in range(1001):
            model._record_training_step({"step": i})

        # Should be trimmed to 500
        assert len(model.training_history) == 500
        assert model.training_history[0]["step"] == 501  # First 501 entries removed

    def test_preprocess_features_handles_nan(self):
        """Test _preprocess_features handles NaN values."""
        model = DummyMLModel("test")

        X = np.array([[1.0, np.nan], [np.nan, 4.0]])
        processed = model._preprocess_features(X)

        assert not np.isnan(processed).any()
        assert processed[0, 1] == 0.0
        assert processed[1, 0] == 0.0

    def test_validate_input_empty(self):
        """Test _validate_input rejects empty arrays."""
        model = DummyMLModel("test")

        X = np.array([])
        assert model._validate_input(X) is False

    def test_validate_input_length_mismatch(self):
        """Test _validate_input rejects mismatched lengths."""
        model = DummyMLModel("test")

        X = np.array([[1, 2], [3, 4]])
        y = np.array([0])  # Wrong length

        assert model._validate_input(X, y) is False

    def test_validate_input_feature_count_mismatch(self):
        """Test _validate_input rejects wrong feature count."""
        model = DummyMLModel("test")
        model.feature_names = ["f1", "f2", "f3"]

        X = np.array([[1, 2]])  # Only 2 features, expected 3

        assert model._validate_input(X) is False

    def test_validate_input_success(self):
        """Test _validate_input accepts valid input."""
        model = DummyMLModel("test")
        model.feature_names = ["f1", "f2"]

        X = np.array([[1, 2], [3, 4]])
        y = np.array([0, 1])

        assert model._validate_input(X, y) is True

    def test_str_representation(self):
        """Test __str__ method."""
        model = DummyMLModel("test_model")
        model.is_trained = True

        assert str(model) == "DummyMLModel(name=test_model, trained=True)"

    def test_repr_representation(self):
        """Test __repr__ method."""
        model = DummyMLModel("test_model")
        model.model_version = "2.0.0"
        model.is_trained = True
        model.metrics["accuracy"] = 0.95

        repr_str = repr(model)
        assert "DummyMLModel" in repr_str
        assert "name=test_model" in repr_str
        assert "version=2.0.0" in repr_str
        assert "trained=True" in repr_str
        assert "accuracy=0.950" in repr_str


class TestRiskPredictionResult:
    """Tests for RiskPredictionResult data class."""

    def test_init_and_to_dict(self):
        """Test initialization and to_dict conversion."""
        result = RiskPredictionResult(
            prediction=1,
            probability=0.85,
            confidence=0.9,
            model_name="test_model",
            timestamp=1234567890,
            features_used=["f1", "f2", "f3"],
        )

        assert result.prediction == 1
        assert result.probability == 0.85
        assert result.confidence == 0.9
        assert result.model_name == "test_model"
        assert result.timestamp == 1234567890
        assert result.features_used == ["f1", "f2", "f3"]

        d = result.to_dict()
        assert d["prediction"] == 1
        assert d["probability"] == 0.85
        assert d["model_name"] == "test_model"

    def test_optional_ensemble_attributes(self):
        """Test optional ensemble attributes."""
        result = RiskPredictionResult(
            prediction=1,
            probability=0.85,
            confidence=0.9,
            model_name="ensemble",
            timestamp=1234567890,
            features_used=["f1"],
        )

        result.individual_predictions = {"model_a": 1, "model_b": 0}
        result.individual_probabilities = {"model_a": 0.9, "model_b": 0.1}
        result.current_weights = {"model_a": 0.6, "model_b": 0.4}
        result.ensemble_method = "weighted_average"

        assert result.individual_predictions == {"model_a": 1, "model_b": 0}
        assert result.ensemble_method == "weighted_average"


class TestModelMetrics:
    """Tests for ModelMetrics class."""

    def test_init_defaults(self):
        """Test default initialization."""
        metrics = ModelMetrics()

        assert metrics.accuracy == 0.0
        assert metrics.precision == 0.0
        assert metrics.recall == 0.0
        assert metrics.f1_score == 0.0
        assert metrics.roc_auc == 0.0
        assert metrics.confusion_matrix is None
        assert metrics.training_time == 0.0

    def test_update_from_sklearn_metrics(self):
        """Test updating from sklearn metrics."""
        metrics = ModelMetrics()

        y_true = np.array([0, 1, 1, 0, 1])
        y_pred = np.array([0, 1, 0, 0, 1])
        y_proba = np.array([[0.9, 0.1], [0.2, 0.8], [0.6, 0.4], [0.8, 0.2], [0.3, 0.7]])

        metrics.update_from_sklearn_metrics(y_true, y_pred, y_proba)

        assert metrics.accuracy >= 0.0
        assert metrics.precision >= 0.0
        assert metrics.recall >= 0.0
        assert metrics.f1_score >= 0.0
        assert metrics.confusion_matrix is not None
        assert metrics.classification_report != {}

    def test_update_from_sklearn_metrics_without_proba(self):
        """Test updating without probability array."""
        metrics = ModelMetrics()

        y_true = np.array([0, 1, 1, 0])
        y_pred = np.array([0, 1, 0, 0])

        metrics.update_from_sklearn_metrics(y_true, y_pred, y_proba=None)

        assert metrics.accuracy >= 0.0
        assert metrics.roc_auc == 0.0  # Not computed without proba

    def test_to_dict(self):
        """Test to_dict conversion."""
        metrics = ModelMetrics()
        metrics.accuracy = 0.9
        metrics.precision = 0.85
        metrics.training_time = 5.0

        d = metrics.to_dict()

        assert d["accuracy"] == 0.9
        assert d["precision"] == 0.85
        assert d["training_time"] == 5.0


class TestModelComparator:
    """Tests for ModelComparator class."""

    def test_add_model(self):
        """Test adding models."""
        comparator = ModelComparator()
        model = DummyMLModel("model_a")

        comparator.add_model("model_a", model)

        assert "model_a" in comparator.models
        assert comparator.models["model_a"] is model

    def test_compare_models(self):
        """Test comparing models."""
        comparator = ModelComparator()

        model_a = DummyMLModel("a")
        model_b = DummyMLModel("b")

        X = np.array([[1, 2], [3, 4], [5, 6]])
        y = np.array([0, 1, 1])

        model_a.train(X, y)
        model_b.train(X, y)

        comparator.add_model("a", model_a)
        comparator.add_model("b", model_b)

        results = comparator.compare_models(X, y)

        assert "a" in results
        assert "b" in results
        assert "accuracy" in results["a"]

    def test_compare_models_untrained(self):
        """Test comparing includes untrained models."""
        comparator = ModelComparator()

        model = DummyMLModel("untrained")
        comparator.add_model("untrained", model)

        X = np.array([[1, 2]])
        y = np.array([0])

        results = comparator.compare_models(X, y)

        assert results["untrained"] == {"error": "Model not trained"}

    def test_get_best_model(self):
        """Test getting best model by metric."""
        comparator = ModelComparator()

        model_a = DummyMLModel("a")
        model_b = DummyMLModel("b")

        X = np.array([[1, 2], [3, 4]])
        y = np.array([0, 1])

        model_a.train(X, y)
        model_b.train(X, y)

        comparator.add_model("a", model_a)
        comparator.add_model("b", model_b)

        comparator.compare_models(X, y)

        # Both models predict same, so either could be best
        best_name, best_model = comparator.get_best_model("f1_score")

        assert best_name in ["a", "b"]
        assert best_model is not None

    def test_get_best_model_no_results(self):
        """Test get_best_model with no test results."""
        comparator = ModelComparator()

        name, model = comparator.get_best_model("f1_score")

        assert name is None
        assert model is None

    def test_get_comparison_report(self):
        """Test getting comparison report."""
        comparator = ModelComparator()

        model = DummyMLModel("test")
        X = np.array([[1, 2], [3, 4]])
        y = np.array([0, 1])

        model.train(X, y)
        comparator.add_model("test", model)
        comparator.compare_models(X, y)

        report = comparator.get_comparison_report()

        assert "test_results" in report
        assert "best_models" in report
        assert "model_count" in report
        assert report["model_count"] == 1

    def test_get_comparison_report_no_results(self):
        """Test comparison report with no results."""
        comparator = ModelComparator()

        report = comparator.get_comparison_report()

        assert report == {"error": "No test results available"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
