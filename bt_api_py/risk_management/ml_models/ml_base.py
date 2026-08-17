"""


"""

from __future__ import annotations

# Model persistence only supports trusted local files.
import pickle  # nosec B403
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np
from bt_api_base.logging_factory import get_logger


# 模型文件仅允许从包内 models/ 目录加载（防 pickle 任意路径反序列化）
_MODELS_DIR = Path(__file__).resolve().parent / "models"


class BaseMLModel(ABC):
    """

    ML
    """

    def __init__(self, model_name: str, config: dict[str, Any] | None = None) -> None:
        """ML

        Args: model_name:
            config: 
        """
        self.model_name = model_name
        self.config = config or {}
        self.logger = get_logger(f"ml_model_{model_name}")

        # 
        self.model: Any = None
        self.is_trained = False
        self.training_time = 0.0
        self.last_training_time = 0.0

        # 
        self.metrics = {
            "accuracy": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "f1_score": 0.0,
            "training_samples": 0,
            "validation_samples": 0,
            "features_count": 0,
        }

        # 
        self.model_version = "1.0.0"
        self.data_version = "1.0.0"

        # 
        self.training_history: list[dict[str, Any]] = []

        # 
        self.feature_names: list[str] = []
        self.feature_importance: dict[str, float] = {}

        self.logger.info(f"ML model {model_name} initialized")

    @abstractmethod
    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        validation_data: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> dict[str, Any]:
        """

        Args: X:
            y: 
            validation_data:  (X_val, y_val)

        Returns: Dict[str, Any]:
        """

    @abstractmethod
    def predict(self, X: np.ndarray) -> np.ndarray:
        """

        Args: X:

        Returns: np.ndarray:
        """

    @abstractmethod
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """

        Args: X:

        Returns: np.ndarray:
        """

    def evaluate(self, X: np.ndarray, y: np.ndarray) -> dict[str, float]:
        """

        Args: X:
            y: 

        Returns: Dict[str, float]:
        """
        if not self.is_trained:
            self.logger.warning("Model not trained yet")
            return {}

        try:
            y_pred = self.predict(X)

            from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

            metrics = {
                "accuracy": accuracy_score(y, y_pred),
                "precision": precision_score(y, y_pred, average="weighted", zero_division=0),
                "recall": recall_score(y, y_pred, average="weighted", zero_division=0),
                "f1_score": f1_score(y, y_pred, average="weighted", zero_division=0),
            }

            # 
            self.metrics.update(metrics)

            return metrics

        except Exception as e:
            self.logger.error(f"Error evaluating model: {e}")
            return {}

    def save_model(self, file_path: str) -> bool:
        """

        Args: file_path:

        Returns: bool:
        """
        try:
            model_data = {
                "model": self.model,
                "model_name": self.model_name,
                "model_version": self.model_version,
                "data_version": self.data_version,
                "config": self.config,
                "metrics": self.metrics,
                "feature_names": self.feature_names,
                "feature_importance": self.feature_importance,
                "is_trained": self.is_trained,
                "training_time": self.training_time,
                "last_training_time": self.last_training_time,
            }

            with Path(file_path).open("wb") as f:
                pickle.dump(model_data, f)

            self.logger.info(f"Model saved to {file_path}")
            return True

        except Exception as e:
            self.logger.error(f"Error saving model: {e}")
            return False

    def load_model(self, file_path: str) -> bool:
        """

        Args: file_path:

        Security: pickle 。，
            、。

        Returns: bool:
        """
        path = Path(file_path).resolve()
        if not path.is_relative_to(_MODELS_DIR):
            raise ValueError(
                f"Refusing to load model from {file_path!r}: "
                f"model files must be under {_MODELS_DIR}"
            )
        try:
            with path.open("rb") as f:
                model_data = pickle.load(f)  # nosec B301

            self.model = model_data.get("model")
            self.model_name = model_data.get("model_name", self.model_name)
            self.model_version = model_data.get("model_version", "1.0.0")
            self.data_version = model_data.get("data_version", "1.0.0")
            self.config = model_data.get("config", {})
            self.metrics = model_data.get("metrics", {})
            self.feature_names = model_data.get("feature_names", [])
            self.feature_importance = model_data.get("feature_importance", {})
            self.is_trained = model_data.get("is_trained", False)
            self.training_time = model_data.get("training_time", 0)
            self.last_training_time = model_data.get("last_training_time", 0)

            self.logger.info(f"Model loaded from {file_path}")
            return True

        except Exception as e:
            self.logger.error(f"Error loading model: {e}")
            return False

    def get_feature_importance(self) -> dict[str, float]:
        """

        Returns: Dict[str, float]:
        """
        return self.feature_importance

    def update_feature_names(self, feature_names: list[str]) -> None:
        """

        Args: feature_names:
        """
        self.feature_names = feature_names
        self.metrics["features_count"] = len(feature_names)

    def get_model_info(self) -> dict[str, Any]:
        """

        Returns: Dict[str, Any]:
        """
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "data_version": self.data_version,
            "is_trained": self.is_trained,
            "training_time": self.training_time,
            "last_training_time": self.last_training_time,
            "metrics": self.metrics,
            "config": self.config,
            "features_count": len(self.feature_names),
        }

    def _record_training_step(self, step_data: dict[str, Any]) -> None:
        """

        Args: step_data:
        """
        step_data["timestamp"] = int(time.time())
        self.training_history.append(step_data)

        # 
        if len(self.training_history) > 1000:
            self.training_history = self.training_history[-500:]

    def _preprocess_features(self, X: np.ndarray) -> np.ndarray:
        """

        Args: X:

        Returns: np.ndarray:
        """
        # ：NaN、
        if np.isnan(X).any():
            X = np.nan_to_num(X, nan=0.0)

        return X

    def _validate_input(self, X: np.ndarray, y: np.ndarray | None = None) -> bool:
        """

        Args: X:
            y:  ()

        Returns: bool:
        """
        if X.size == 0:
            self.logger.error("Empty feature matrix")
            return False

        if y is not None and len(X) != len(y):
            self.logger.error("Feature matrix and target have different lengths")
            return False

        if len(self.feature_names) > 0 and X.shape[1] != len(self.feature_names):
            self.logger.error(
                f"Feature count mismatch: expected {len(self.feature_names)}, got {X.shape[1]}"
            )
            return False

        return True

    def __str__(self) -> str:
        """"""
        return f"{self.__class__.__name__}(name={self.model_name}, trained={self.is_trained})"

    def __repr__(self) -> str:
        """"""
        return (
            f"{self.__class__.__name__}(name={self.model_name}, "
            f"version={self.model_version}, trained={self.is_trained}, "
            f"accuracy={self.metrics['accuracy']:.3f})"
        )


class RiskPredictionResult:
    """"""

    def __init__(
        self,
        prediction: Any,
        probability: float,
        confidence: float,
        model_name: str,
        timestamp: int,
        features_used: list[str],
    ) -> None:
        """__init__ method"""
        self.prediction = prediction
        self.probability = probability
        self.confidence = confidence
        self.model_name = model_name
        self.timestamp = timestamp
        self.features_used = features_used
        # Optional attributes for ensemble model details
        self.individual_predictions: dict[str, Any] = {}
        self.individual_probabilities: dict[str, Any] = {}
        self.current_weights: dict[str, float] = {}
        self.ensemble_method: str = ""

    def to_dict(self) -> dict[str, Any]:
        """"""
        return {
            "prediction": self.prediction,
            "probability": self.probability,
            "confidence": self.confidence,
            "model_name": self.model_name,
            "timestamp": self.timestamp,
            "features_used": self.features_used,
        }


class ModelMetrics:
    """"""

    def __init__(self) -> None:
        """__init__ method"""
        self.accuracy = 0.0
        self.precision = 0.0
        self.recall = 0.0
        self.f1_score = 0.0
        self.roc_auc = 0.0
        self.confusion_matrix: Any = None
        self.classification_report: dict[str, Any] = {}
        self.feature_importance: dict[str, Any] = {}
        self.training_time = 0.0
        self.prediction_time = 0.0

    def update_from_sklearn_metrics(
        self, y_true: np.ndarray, y_pred: np.ndarray, y_proba: np.ndarray | None = None
    ) -> None:
        """scikit-learn"""
        from sklearn.metrics import (
            accuracy_score,
            classification_report,
            confusion_matrix,
            f1_score,
            precision_score,
            recall_score,
            roc_auc_score,
        )

        self.accuracy = accuracy_score(y_true, y_pred)
        self.precision = precision_score(y_true, y_pred, average="weighted", zero_division=0)
        self.recall = recall_score(y_true, y_pred, average="weighted", zero_division=0)
        self.f1_score = f1_score(y_true, y_pred, average="weighted", zero_division=0)
        self.confusion_matrix = confusion_matrix(y_true, y_pred).tolist()
        self.classification_report = dict(
            classification_report(y_true, y_pred, output_dict=True, zero_division=0)
        )

        if y_proba is not None and len(np.unique(y_true)) == 2:
            try:
                self.roc_auc = roc_auc_score(y_true, y_proba[:, 1])
            except Exception:
                self.roc_auc = 0.0

    def to_dict(self) -> dict[str, Any]:
        """"""
        return {
            "accuracy": self.accuracy,
            "precision": self.precision,
            "recall": self.recall,
            "f1_score": self.f1_score,
            "roc_auc": self.roc_auc,
            "confusion_matrix": self.confusion_matrix,
            "classification_report": self.classification_report,
            "feature_importance": self.feature_importance,
            "training_time": self.training_time,
            "prediction_time": self.prediction_time,
        }


class ModelComparator:
    """"""

    def __init__(self) -> None:
        """__init__ method"""
        self.models: dict[str, BaseMLModel] = {}
        self.test_results: dict[str, dict[str, Any]] = {}

    def add_model(self, name: str, model: BaseMLModel) -> None:
        """

        Args: name:
            model: 
        """
        self.models[name] = model

    def compare_models(self, X_test: np.ndarray, y_test: np.ndarray) -> dict[str, dict[str, Any]]:
        """

        Args: X_test:
            y_test: 

        Returns: Dict[str, Dict[str, Any]]:
        """
        results: dict[str, dict[str, Any]] = {}

        for name, model in self.models.items():
            if model.is_trained:
                metrics = model.evaluate(X_test, y_test)
                results[name] = metrics
            else:
                results[name] = {"error": "Model not trained"}

        self.test_results = results
        return results

    def get_best_model(self, metric: str = "f1_score") -> tuple[str | None, BaseMLModel | None]:
        """

        Args: metric:

        Returns: Tuple[str | None, BaseMLModel | None]: (, )
        """
        best_name: str | None = None
        best_score: float = -1.0
        best_model: BaseMLModel | None = None

        for name, model in self.models.items():
            if model.is_trained and name in self.test_results:
                score = self.test_results[name].get(metric, 0)
                if isinstance(score, (int, float)) and score > best_score:
                    best_score = float(score)
                    best_name = name
                    best_model = model

        return best_name, best_model

    def get_comparison_report(self) -> dict[str, Any]:
        """"""
        if not self.test_results:
            return {"error": "No test results available"}

        # 
        best_models: dict[str, str | None] = {}
        metrics = ["accuracy", "precision", "recall", "f1_score"]

        for metric in metrics:
            best_model_name, _ = self.get_best_model(metric)
            best_models[metric] = best_model_name

        return {
            "test_results": self.test_results,
            "best_models": best_models,
            "model_count": len(self.models),
            "comparison_timestamp": int(time.time()),
        }
