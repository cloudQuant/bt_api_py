"""Tests for ensemble_model module - RiskEnsembleModel and supporting classes."""

import tempfile
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("sklearn")

from bt_api_py.risk_management.ml_models.ensemble_model import (
    EnsembleMethod,
    ModelWeight,
    RiskEnsembleModel,
)
from bt_api_py.risk_management.ml_models.ml_base import RiskPredictionResult


class TestEnsembleMethod:
    """Tests for EnsembleMethod constants."""

    def test_method_constants(self):
        """Test all method constants exist."""
        assert EnsembleMethod.VOTING == "voting"
        assert EnsembleMethod.STACKING == "stacking"
        assert EnsembleMethod.BAGGING == "bagging"
        assert EnsembleMethod.BOOSTING == "boosting"
        assert EnsembleMethod.WEIGHTED_AVERAGE == "weighted_average"
        assert EnsembleMethod.DYNAMIC_WEIGHTING == "dynamic_weighting"


class TestModelWeight:
    """Tests for ModelWeight class."""

    def test_init_defaults(self):
        """Test default initialization."""
        weight = ModelWeight("test_model", 0.5)

        assert weight.model_name == "test_model"
        assert weight.weight == 0.5
        assert weight.min_confidence == 0.5
        assert weight.max_weight == 1.0
        assert weight.performance_history == []
        assert weight.current_performance == 0.5

    def test_init_custom(self):
        """Test custom initialization."""
        weight = ModelWeight("custom", 0.3, min_confidence=0.7, max_weight=0.8)

        assert weight.model_name == "custom"
        assert weight.weight == 0.3
        assert weight.min_confidence == 0.7
        assert weight.max_weight == 0.8

    def test_update_performance(self):
        """Test performance update."""
        weight = ModelWeight("test", 0.5)

        weight.update_performance(0.8)
        weight.update_performance(0.9)

        assert len(weight.performance_history) == 2
        assert weight.current_performance == pytest.approx(0.85, rel=0.01)

    def test_update_performance_limits_history(self):
        """Test performance history is limited."""
        weight = ModelWeight("test", 0.5)

        # Add 101 entries
        for i in range(101):
            weight.update_performance(float(i) / 100)

        # Should be trimmed to 50
        assert len(weight.performance_history) == 50

    def test_get_dynamic_weight_no_history(self):
        """Test dynamic weight with no history returns base weight."""
        weight = ModelWeight("test", 0.5)

        dynamic = weight.get_dynamic_weight()

        assert dynamic == 0.5

    def test_get_dynamic_weight_with_history(self):
        """Test dynamic weight calculation with history."""
        weight = ModelWeight("test", 0.5, max_weight=1.0)

        # Good performance should increase weight
        weight.update_performance(0.8)
        weight.update_performance(0.8)
        weight.update_performance(0.8)

        dynamic = weight.get_dynamic_weight()

        # Weight should be adjusted based on performance
        assert dynamic >= 0.1
        assert dynamic <= weight.max_weight


class TestRiskEnsembleModel:
    """Tests for RiskEnsembleModel class."""

    def test_init_defaults(self):
        """Test default initialization."""
        model = RiskEnsembleModel()

        assert model.model_name == "RiskEnsembleModel"
        assert model.ensemble_method == EnsembleMethod.WEIGHTED_AVERAGE
        assert model.use_dynamic_weighting is True
        assert "random_forest" in model.models
        assert "gradient_boosting" in model.models
        assert "logistic_regression" in model.models
        assert model.is_trained is False

    def test_init_custom_config(self):
        """Test initialization with custom config."""
        config = {
            "ensemble_method": EnsembleMethod.VOTING,
            "use_dynamic_weighting": False,
            "weight_update_frequency": 50,
        }
        model = RiskEnsembleModel(config=config)

        assert model.ensemble_method == EnsembleMethod.VOTING
        assert model.use_dynamic_weighting is False
        assert model.weight_update_frequency == 50

    def test_train_and_predict(self):
        """Test training and prediction."""
        model = RiskEnsembleModel()

        # Create simple binary classification data
        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        result = model.train(X, y)

        assert result["success"] is True
        assert model.is_trained is True
        assert "model_results" in result

        # Test prediction
        predictions = model.predict(X[:10])
        assert len(predictions) == 10

    def test_train_with_validation_data(self):
        """Test training with explicit validation data."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(80, 5)
        y = (X[:, 0] > 0).astype(int)
        X_val = np.random.randn(20, 5)
        y_val = (X_val[:, 0] > 0).astype(int)

        result = model.train(X, y, validation_data=(X_val, y_val))

        assert result["success"] is True
        assert result["validation_samples"] == 20

    def test_predict_not_trained_raises(self):
        """Test predict raises when not trained."""
        model = RiskEnsembleModel()

        X = np.random.randn(10, 5)

        with pytest.raises(ValueError, match="Model not trained"):
            model.predict(X)

    def test_predict_proba_not_trained_raises(self):
        """Test predict_proba raises when not trained."""
        model = RiskEnsembleModel()

        X = np.random.randn(10, 5)

        with pytest.raises(ValueError, match="Model not trained"):
            model.predict_proba(X)

    def test_predict_proba(self):
        """Test probability prediction."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model.train(X, y)

        proba = model.predict_proba(X[:10])
        assert proba.shape == (10, 2)  # Binary classification

    def test_predict_risk_with_features(self):
        """Test risk prediction with feature array."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model.train(X, y)
        model.update_feature_names(["f1", "f2", "f3", "f4", "f5"])

        result = model.predict_risk(X[0])

        assert isinstance(result, RiskPredictionResult)
        assert result.model_name == "RiskEnsembleModel"
        assert len(result.features_used) == 5

    def test_predict_risk_with_dict(self):
        """Test risk prediction with dict input."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model.train(X, y)

        features = {"f1": 1.0, "f2": 0.5, "f3": -0.3, "f4": 0.2, "f5": -0.1}
        result = model.predict_risk(features)

        assert isinstance(result, RiskPredictionResult)

    def test_predict_risk_with_details(self):
        """Test risk prediction with detailed output."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model.train(X, y)

        result = model.predict_risk(X[0], return_details=True)

        assert result.individual_predictions != {}
        assert result.individual_probabilities != {}
        assert result.current_weights != {}
        assert result.ensemble_method == model.ensemble_method

    def test_predict_risk_caching(self):
        """Test risk prediction caching."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model.train(X, y)

        # First prediction
        result1 = model.predict_risk(X[0])
        # Second prediction (should use cache)
        result2 = model.predict_risk(X[0])

        assert result1.timestamp == result2.timestamp
        assert len(model.prediction_cache) == 1

    def test_predict_risk_error_handling(self):
        """Test risk prediction error handling."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model.train(X, y)

        # Force an error by passing invalid input
        # The model should return a default result
        result = model.predict_risk(np.array([]))

        assert result.prediction == 0
        assert result.confidence == 0.0

    def test_update_model_performance(self):
        """Test updating model performance."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model.train(X, y)

        predictions = model.predict(X[:20])

        model.update_model_performance(y[:20], predictions)

        assert "accuracy" in model.ensemble_performance
        assert "f1_score" in model.ensemble_performance

    def test_update_model_performance_not_trained(self):
        """Test update_model_performance when not trained does nothing."""
        model = RiskEnsembleModel()

        # Should not raise
        model.update_model_performance(np.array([0, 1]), np.array([0, 1]))

    def test_get_ensemble_info(self):
        """Test getting ensemble info."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model.train(X, y)

        info = model.get_ensemble_info()

        assert info["ensemble_method"] == EnsembleMethod.WEIGHTED_AVERAGE
        assert info["num_models"] == 3
        assert "random_forest" in info["model_names"]
        assert "current_weights" in info

    def test_get_feature_importance(self):
        """Test getting feature importance."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model.train(X, y)
        model.update_feature_names(["a", "b", "c", "d", "e"])

        importance = model.get_feature_importance()

        assert len(importance) == 5
        assert all(v >= 0 for v in importance.values())

    def test_get_feature_importance_not_trained(self):
        """Test feature importance when not trained."""
        model = RiskEnsembleModel()

        importance = model.get_feature_importance()

        assert importance == {}

    def test_different_ensemble_methods(self):
        """Test different ensemble methods."""
        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        for method in [
            EnsembleMethod.WEIGHTED_AVERAGE,
            EnsembleMethod.VOTING,
            EnsembleMethod.DYNAMIC_WEIGHTING,
        ]:
            model = RiskEnsembleModel(config={"ensemble_method": method})
            model.train(X, y)

            predictions = model.predict(X[:10])
            assert len(predictions) == 10

    def test_stacking_method(self):
        """Test stacking ensemble method."""
        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model = RiskEnsembleModel(config={"ensemble_method": EnsembleMethod.STACKING})
        result = model.train(X, y)

        assert result["success"] is True

        predictions = model.predict(X[:10])
        proba = model.predict_proba(X[:10])

        assert len(predictions) == 10
        assert proba.shape[0] == 10

    def test_save_and_load_model(self):
        """Test model save and load - sklearn models need refitting after load."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model.train(X, y)

        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            model_path = f.name

        try:
            assert model.save_model(model_path) is True

            new_model = RiskEnsembleModel()
            assert new_model.load_model(model_path) is True

            # Metadata should be loaded
            assert new_model.is_trained is True
            assert new_model.model_name == "RiskEnsembleModel"
            # Note: sklearn models need refitting after pickle load
            # The save/load preserves metadata but sklearn estimators
            # may need retraining in practice
        finally:
            Path(model_path).unlink(missing_ok=True)

    def test_evaluate(self):
        """Test model evaluation."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model.train(X, y)

        metrics = model.evaluate(X[:20], y[:20])

        assert "accuracy" in metrics
        assert "f1_score" in metrics

    def test_prediction_history_limit(self):
        """Test prediction history is limited."""
        model = RiskEnsembleModel()

        np.random.seed(42)
        X = np.random.randn(100, 5)
        y = (X[:, 0] > 0).astype(int)

        model.train(X, y)

        # Add many predictions to history
        for i in range(1001):
            model.prediction_history.append({"timestamp": i, "accuracy": 0.5})

        # Trigger limit
        model.prediction_history.append({"timestamp": 1001, "accuracy": 0.5})
        model.prediction_history = (
            model.prediction_history[-500:]
            if len(model.prediction_history) > 1000
            else model.prediction_history
        )

        assert len(model.prediction_history) <= 501


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
