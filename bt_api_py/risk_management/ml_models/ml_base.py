"""机器学习模型基类

定义机器学习模型的基础接口和通用功能
"""

import pickle
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

from bt_api_py.logging_factory import get_logger


class BaseMLModel(ABC):
    """机器学习模型基类

    定义所有ML模型的通用接口和基础功能
    """

    def __init__(self, model_name: str, config: dict[str, Any] | None = None) -> None:
        """初始化ML模型

        Args:
            model_name: 模型名称
            config: 模型配置
        """
        self.model_name = model_name
        self.config = config or {}
        self.logger = get_logger(f"ml_model_{model_name}")

        # 模型状态
        self.model: Any = None
        self.is_trained = False
        self.training_time = 0.0
        self.last_training_time = 0.0

        # 性能指标
        self.metrics = {
            "accuracy": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "f1_score": 0.0,
            "training_samples": 0,
            "validation_samples": 0,
            "features_count": 0,
        }

        # 模型版本
        self.model_version = "1.0.0"
        self.data_version = "1.0.0"

        # 训练历史
        self.training_history: list[dict[str, Any]] = []

        # 特征信息
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
        """训练模型

        Args:
            X: 特征矩阵
            y: 目标变量
            validation_data: 验证数据 (X_val, y_val)

        Returns:
            Dict[str, Any]: 训练结果
        """

    @abstractmethod
    def predict(self, X: np.ndarray) -> np.ndarray:
        """预测

        Args:
            X: 特征矩阵

        Returns:
            np.ndarray: 预测结果
        """

    @abstractmethod
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """预测概率

        Args:
            X: 特征矩阵

        Returns:
            np.ndarray: 预测概率
        """

    def evaluate(self, X: np.ndarray, y: np.ndarray) -> dict[str, float]:
        """评估模型性能

        Args:
            X: 测试特征
            y: 测试目标

        Returns:
            Dict[str, float]: 评估指标
        """
        if not self.is_trained:
            self.logger.warning("Model not trained yet")
            return {}

        try:
            y_pred = self.predict(X)

            metrics = {
                "accuracy": accuracy_score(y, y_pred),
                "precision": precision_score(y, y_pred, average="weighted", zero_division=0),
                "recall": recall_score(y, y_pred, average="weighted", zero_division=0),
                "f1_score": f1_score(y, y_pred, average="weighted", zero_division=0),
            }

            # 更新模型指标
            self.metrics.update(metrics)

            return metrics

        except Exception as e:
            self.logger.error(f"Error evaluating model: {e}")
            return {}

    def save_model(self, file_path: str) -> bool:
        """保存模型

        Args:
            file_path: 保存路径

        Returns:
            bool: 保存是否成功
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
        """加载模型

        Args:
            file_path: 模型文件路径

        Returns:
            bool: 加载是否成功
        """
        try:
            with Path(file_path).open("rb") as f:
                model_data = pickle.load(f)  # nosec B301 # trusted model files only

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
        """获取特征重要性

        Returns:
            Dict[str, float]: 特征重要性
        """
        return self.feature_importance

    def update_feature_names(self, feature_names: list[str]) -> None:
        """更新特征名称

        Args:
            feature_names: 特征名称列表
        """
        self.feature_names = feature_names
        self.metrics["features_count"] = len(feature_names)

    def get_model_info(self) -> dict[str, Any]:
        """获取模型信息

        Returns:
            Dict[str, Any]: 模型信息
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
        """记录训练步骤

        Args:
            step_data: 训练步骤数据
        """
        step_data["timestamp"] = int(time.time())
        self.training_history.append(step_data)

        # 限制历史记录大小
        if len(self.training_history) > 1000:
            self.training_history = self.training_history[-500:]

    def _preprocess_features(self, X: np.ndarray) -> np.ndarray:
        """预处理特征

        Args:
            X: 原始特征矩阵

        Returns:
            np.ndarray: 预处理后的特征矩阵
        """
        # 基础预处理：处理NaN、标准化等
        if np.isnan(X).any():
            X = np.nan_to_num(X, nan=0.0)

        return X

    def _validate_input(self, X: np.ndarray, y: np.ndarray | None = None) -> bool:
        """验证输入数据

        Args:
            X: 特征矩阵
            y: 目标变量 (可选)

        Returns:
            bool: 验证是否通过
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
        """字符串表示"""
        return f"{self.__class__.__name__}(name={self.model_name}, trained={self.is_trained})"

    def __repr__(self) -> str:
        """详细字符串表示"""
        return (
            f"{self.__class__.__name__}(name={self.model_name}, "
            f"version={self.model_version}, trained={self.is_trained}, "
            f"accuracy={self.metrics['accuracy']:.3f})"
        )


class RiskPredictionResult:
    """风险预测结果"""

    def __init__(
        self,
        prediction: Any,
        probability: float,
        confidence: float,
        model_name: str,
        timestamp: int,
        features_used: list[str],
    ) -> None:
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
        """转换为字典格式"""
        return {
            "prediction": self.prediction,
            "probability": self.probability,
            "confidence": self.confidence,
            "model_name": self.model_name,
            "timestamp": self.timestamp,
            "features_used": self.features_used,
        }


class ModelMetrics:
    """模型性能指标"""

    def __init__(self) -> None:
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
        """从scikit-learn指标更新"""
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
        """转换为字典格式"""
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
    """模型比较器"""

    def __init__(self) -> None:
        self.models: dict[str, BaseMLModel] = {}
        self.test_results: dict[str, dict[str, Any]] = {}

    def add_model(self, name: str, model: BaseMLModel) -> None:
        """添加模型

        Args:
            name: 模型名称
            model: 模型实例
        """
        self.models[name] = model

    def compare_models(self, X_test: np.ndarray, y_test: np.ndarray) -> dict[str, dict[str, Any]]:
        """比较模型性能

        Args:
            X_test: 测试特征
            y_test: 测试目标

        Returns:
            Dict[str, Dict[str, Any]]: 各模型的性能指标
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
        """获取最佳模型

        Args:
            metric: 评估指标

        Returns:
            Tuple[str | None, BaseMLModel | None]: (模型名称, 模型实例)
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
        """获取比较报告"""
        if not self.test_results:
            return {"error": "No test results available"}

        # 计算各指标的最佳模型
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
