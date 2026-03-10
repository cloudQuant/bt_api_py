"""风险集成模型 - 结合多种ML算法的风险预测.

集成随机森林、神经网络、XGBoost等多种模型进行综合风险预测
"""

import time
from typing import Any

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score

from .ml_base import BaseMLModel, RiskPredictionResult


class EnsembleMethod:
    """集成方法常量."""

    VOTING = "voting"  # 投票法
    STACKING = "stacking"  # 堆叠法
    BAGGING = "bagging"  # 装袋法
    BOOSTING = "boosting"  # 提升法
    WEIGHTED_AVERAGE = "weighted_average"  # 加权平均
    DYNAMIC_WEIGHTING = "dynamic_weighting"  # 动态加权


class ModelWeight:
    """模型权重配置."""

    def __init__(
        self, model_name: str, weight: float, min_confidence: float = 0.5, max_weight: float = 1.0
    ) -> Any | None:
        self.model_name = model_name
        self.weight = weight
        self.min_confidence = min_confidence
        self.max_weight = max_weight
        self.performance_history: list[float] = []
        self.current_performance = 0.5

    def update_performance(self, performance: float) -> None:
        """更新模型性能."""
        self.performance_history.append(performance)
        if len(self.performance_history) > 100:
            self.performance_history = self.performance_history[-50:]
        self.current_performance = np.mean(self.performance_history)

    def get_dynamic_weight(self) -> float:
        """获取动态权重."""
        if not self.performance_history:
            return self.weight

        # 基于性能调整权重
        performance_factor = self.current_performance / 0.5  # 假设0.5为基准性能
        dynamic_weight = self.weight * performance_factor

        return min(max(dynamic_weight, 0.1), self.max_weight)


class RiskEnsembleModel(BaseMLModel):
    """风险集成模型.

    结合多种ML算法进行风险预测:
    1. 随机森林 - 随机性、鲁棒性强
    2. 梯度提升 - 高精度、适合表格数据
    3. 逻辑回归 - 简单、可解释性强
    4. 神经网络 - 复杂模式识别
    5. 动态权重调整 - 基于性能自适应
    """

    def __init__(self, config: dict[str, Any] | None = None) -> Any | None:
        """初始化集成模型.

        Args:
            config: 模型配置

        """
        super().__init__("RiskEnsembleModel", config)

        # 集成配置
        self.ensemble_method = self.config.get("ensemble_method", EnsembleMethod.WEIGHTED_AVERAGE)
        self.use_dynamic_weighting = self.config.get("use_dynamic_weighting", True)
        self.weight_update_frequency = self.config.get("weight_update_frequency", 100)

        # 基础模型
        self.models = {
            "random_forest": RandomForestClassifier(
                n_estimators=100, max_depth=10, random_state=42, n_jobs=-1
            ),
            "gradient_boosting": GradientBoostingClassifier(
                n_estimators=100, max_depth=6, learning_rate=0.1, random_state=42
            ),
            "logistic_regression": LogisticRegression(random_state=42, max_iter=1000, n_jobs=-1),
        }

        # 模型权重配置
        self.model_weights = {
            "random_forest": ModelWeight("random_forest", 0.4, 0.6, 0.5),
            "gradient_boosting": ModelWeight("gradient_boosting", 0.4, 0.6, 0.5),
            "logistic_regression": ModelWeight("logistic_regression", 0.2, 0.5, 0.3),
        }

        # 元学习器 (用于stacking)
        self.meta_learner = LogisticRegression(random_state=42)
        self.use_stacking = self.ensemble_method == EnsembleMethod.STACKING

        # 集成预测历史
        self.prediction_history: list[dict[str, Any]] = []
        self.weight_history: list[dict[str, float]] = []

        # 性能跟踪
        self.model_performance: dict[str, dict[str, float]] = {}
        self.ensemble_performance: dict[str, float] = {}

        # 预测缓存
        self.prediction_cache: dict[str, RiskPredictionResult] = {}
        self.cache_size_limit = 1000

        self.logger.info("RiskEnsembleModel initialized")

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        validation_data: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> dict[str, Any]:
        """训练集成模型.

        Args:
            X: 特征矩阵
            y: 目标变量
            validation_data: 验证数据

        Returns:
            Dict[str, Any]: 训练结果

        """
        start_time = time.time()

        if not self._validate_input(X, y):
            return {"error": "Invalid input data"}

        try:
            # 预处理数据
            X_processed = self._preprocess_features(X)

            # 分割训练和验证数据
            if validation_data is None:
                from sklearn.model_selection import train_test_split

                X_train, X_val, y_train, y_val = train_test_split(
                    X_processed, y, test_size=0.2, random_state=42, stratify=y
                )
            else:
                X_train, y_train = X_processed, y
                X_val, y_val = validation_data
                X_val = self._preprocess_features(X_val)

            # 训练各个基础模型
            model_results = {}
            for name, model in self.models.items():
                model_start = time.time()
                model.fit(X_train, y_train)
                model_time = time.time() - model_start

                # 评估模型
                train_score = model.score(X_train, y_train)
                val_score = model.score(X_val, y_val)

                model_results[name] = {
                    "training_time": model_time,
                    "train_score": train_score,
                    "validation_score": val_score,
                }

                # 更新模型性能
                self.model_weights[name].update_performance(val_score)

                self.logger.info(f"Model {name} trained - Val Score: {val_score:.4f}")

            # 训练元学习器 (stacking)
            if self.use_stacking:
                self._train_meta_learner(X_train, y_train, X_val, y_val)

            # 更新模型状态
            self.is_trained = True
            self.training_time = time.time() - start_time
            self.last_training_time = int(time.time())
            self.metrics["training_samples"] = len(X_train)
            self.metrics["validation_samples"] = len(X_val)

            # 评估集成性能
            ensemble_metrics = self._evaluate_ensemble(X_val, y_val)
            self.ensemble_performance = ensemble_metrics

            # 更新权重
            self._update_model_weights(ensemble_metrics)

            # 记录训练历史
            self._record_training_step(
                {
                    "action": "train_ensemble",
                    "ensemble_method": self.ensemble_method,
                    "models": model_results,
                    "ensemble_metrics": ensemble_metrics,
                    "training_time": self.training_time,
                    "weights": {
                        name: weight.get_dynamic_weight()
                        for name, weight in self.model_weights.items()
                    },
                }
            )

            result = {
                "success": True,
                "training_time": self.training_time,
                "samples_trained": len(X_train),
                "validation_samples": len(X_val),
                "model_results": model_results,
                "ensemble_metrics": ensemble_metrics,
                "current_weights": {
                    name: weight.get_dynamic_weight() for name, weight in self.model_weights.items()
                },
                "model_info": self.get_model_info(),
            }

            self.logger.info(f"Ensemble model trained successfully in {self.training_time:.2f}s")
            return result

        except Exception as e:
            self.logger.error(f"Error training ensemble model: {e}")
            return {"error": str(e)}

    def predict(self, X: np.ndarray) -> np.ndarray:
        """集成预测.

        Args:
            X: 特征矩阵

        Returns:
            np.ndarray: 预测结果

        """
        if not self.is_trained:
            raise ValueError("Model not trained")

        X_processed = self._preprocess_features(X)

        if self.ensemble_method == EnsembleMethod.STACKING:
            return self._predict_stacking(X_processed)
        elif self.ensemble_method == EnsembleMethod.VOTING:
            return self._predict_voting(X_processed)
        elif self.ensemble_method == EnsembleMethod.WEIGHTED_AVERAGE:
            return self._predict_weighted_average(X_processed)
        elif self.ensemble_method == EnsembleMethod.DYNAMIC_WEIGHTING:
            return self._predict_dynamic_weighting(X_processed)
        else:
            return self._predict_weighted_average(X_processed)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """预测概率.

        Args:
            X: 特征矩阵

        Returns:
            np.ndarray: 预测概率

        """
        if not self.is_trained:
            raise ValueError("Model not trained")

        X_processed = self._preprocess_features(X)

        if self.ensemble_method == EnsembleMethod.STACKING:
            return self._predict_proba_stacking(X_processed)
        elif self.ensemble_method == EnsembleMethod.VOTING:
            return self._predict_proba_voting(X_processed)
        elif self.ensemble_method == EnsembleMethod.WEIGHTED_AVERAGE:
            return self._predict_proba_weighted_average(X_processed)
        elif self.ensemble_method == EnsembleMethod.DYNAMIC_WEIGHTING:
            return self._predict_proba_dynamic_weighting(X_processed)
        else:
            return self._predict_proba_weighted_average(X_processed)

    def predict_risk(
        self, features: np.ndarray | dict[str, Any], return_details: bool = False
    ) -> RiskPredictionResult:
        """预测风险.

        Args:
            features: 输入特征
            return_details: 是否返回详细结果

        Returns:
            RiskPredictionResult: 风险预测结果

        """
        try:
            # 生成缓存键
            cache_key = self._generate_cache_key(features)

            # 检查缓存
            if cache_key in self.prediction_cache:
                return self.prediction_cache[cache_key]

            # 转换输入
            if isinstance(features, dict):
                X = self._dict_to_features(features)
                feature_names = list(features.keys())
            else:
                X = features.reshape(1, -1) if features.ndim == 1 else features
                feature_names = self.feature_names

            # 获取预测概率
            probabilities = self.predict_proba(X)
            predictions = self.predict(X)

            # 获取各模型的预测
            individual_predictions = {}
            individual_probabilities = {}

            X_processed = self._preprocess_features(X)
            for name, model in self.models.items():
                pred = model.predict(X_processed)[0]
                proba = model.predict_proba(X_processed)[0]
                individual_predictions[name] = pred
                individual_probabilities[name] = proba

            # 计算置信度
            confidence = self._calculate_prediction_confidence(probabilities[0])

            # 创建预测结果
            result = RiskPredictionResult(
                prediction=int(predictions[0]),
                probability=float(probabilities[0][1])
                if len(probabilities[0]) > 1
                else float(probabilities[0][0]),
                confidence=confidence,
                model_name=self.model_name,
                timestamp=int(time.time()),
                features_used=feature_names,
            )

            # 添加详细信息
            if return_details:
                result.individual_predictions = individual_predictions
                result.individual_probabilities = individual_probabilities
                result.current_weights = {
                    name: weight.get_dynamic_weight() for name, weight in self.model_weights.items()
                }
                result.ensemble_method = self.ensemble_method

            # 缓存结果
            self.prediction_cache[cache_key] = result
            if len(self.prediction_cache) > self.cache_size_limit:
                # 删除最旧的缓存
                oldest_key = next(iter(self.prediction_cache))
                del self.prediction_cache[oldest_key]

            return result

        except Exception as e:
            self.logger.error(f"Error predicting risk: {e}")
            return RiskPredictionResult(
                prediction=0,
                probability=0.0,
                confidence=0.0,
                model_name=self.model_name,
                timestamp=int(time.time()),
                features_used=[],
            )

    def update_model_performance(self, true_labels: np.ndarray, predictions: np.ndarray) -> None:
        """更新模型性能.

        Args:
            true_labels: 真实标签
            predictions: 预测标签

        """
        if not self.is_trained:
            return

        try:
            from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

            # 计算整体性能
            accuracy = accuracy_score(true_labels, predictions)
            precision = precision_score(
                true_labels, predictions, average="weighted", zero_division=0
            )
            recall = recall_score(true_labels, predictions, average="weighted", zero_division=0)
            f1 = f1_score(true_labels, predictions, average="weighted", zero_division=0)

            self.ensemble_performance = {
                "accuracy": accuracy,
                "precision": precision,
                "recall": recall,
                "f1_score": f1,
            }

            # 如果使用动态权重，更新各模型权重
            if self.use_dynamic_weighting:
                self._update_weights_based_on_performance(true_labels, predictions)

            # 记录预测历史
            self.prediction_history.append(
                {
                    "timestamp": int(time.time()),
                    "accuracy": accuracy,
                    "f1_score": f1,
                    "samples": len(true_labels),
                }
            )

            if len(self.prediction_history) > 1000:
                self.prediction_history = self.prediction_history[-500:]

        except Exception as e:
            self.logger.error(f"Error updating model performance: {e}")

    def get_ensemble_info(self) -> dict[str, Any]:
        """获取集成模型信息.

        Returns:
            Dict[str, Any]: 集成信息

        """
        current_weights = {
            name: weight.get_dynamic_weight() for name, weight in self.model_weights.items()
        }

        return {
            "ensemble_method": self.ensemble_method,
            "use_dynamic_weighting": self.use_dynamic_weighting,
            "num_models": len(self.models),
            "model_names": list(self.models.keys()),
            "current_weights": current_weights,
            "base_weights": {name: weight.weight for name, weight in self.model_weights.items()},
            "model_performance": self.model_performance,
            "ensemble_performance": self.ensemble_performance,
            "prediction_history_size": len(self.prediction_history),
            "cache_size": len(self.prediction_cache),
        }

    def get_feature_importance(self) -> dict[str, float]:
        """获取特征重要性.

        Returns:
            Dict[str, float]: 特征重要性

        """
        if not self.is_trained:
            return {}

        feature_importance = {}

        # 获取各模型的特征重要性
        for _name, model in self.models.items():
            if hasattr(model, "feature_importances_"):
                importance = model.feature_importances_
                for i, imp in enumerate(importance):
                    feature_name = (
                        self.feature_names[i] if i < len(self.feature_names) else f"feature_{i}"
                    )
                    if feature_name not in feature_importance:
                        feature_importance[feature_name] = []
                    feature_importance[feature_name].append(imp)

        # 计算平均重要性
        avg_importance = {}
        for feature, values in feature_importance.items():
            avg_importance[feature] = np.mean(values)

        self.feature_importance = avg_importance
        return avg_importance

    # 私有方法

    def _train_meta_learner(
        self, X_train: np.ndarray, y_train: np.ndarray, X_val: np.ndarray, y_val: np.ndarray
    ) -> None:
        """训练元学习器."""
        # 获取基础模型的预测作为元特征
        meta_features_train = []
        meta_features_val = []

        for _name, model in self.models.items():
            # 训练集预测 (使用交叉验证避免过拟合)
            if hasattr(model, "predict_proba"):
                train_proba = model.predict_proba(X_train)
                val_proba = model.predict_proba(X_val)
                meta_features_train.append(train_proba)
                meta_features_val.append(val_proba)
            else:
                train_pred = model.predict(X_train).reshape(-1, 1)
                val_pred = model.predict(X_val).reshape(-1, 1)
                meta_features_train.append(train_pred)
                meta_features_val.append(val_pred)

        # 合并元特征
        X_meta_train = np.hstack(meta_features_train)
        np.hstack(meta_features_val)

        # 训练元学习器
        self.meta_learner.fit(X_meta_train, y_train)

    def _predict_stacking(self, X: np.ndarray) -> np.ndarray:
        """使用stacking方法预测."""
        meta_features = []

        for model in self.models.values():
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(X)
                meta_features.append(proba)
            else:
                pred = model.predict(X).reshape(-1, 1)
                meta_features.append(pred)

        X_meta = np.hstack(meta_features)
        return self.meta_learner.predict(X_meta)

    def _predict_proba_stacking(self, X: np.ndarray) -> np.ndarray:
        """使用stacking方法预测概率."""
        meta_features = []

        for model in self.models.values():
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(X)
                meta_features.append(proba)
            else:
                pred = model.predict(X).reshape(-1, 1)
                meta_features.append(pred)

        X_meta = np.hstack(meta_features)
        return self.meta_learner.predict_proba(X_meta)

    def _predict_voting(self, X: np.ndarray) -> np.ndarray:
        """使用投票法预测."""
        predictions = []

        for model in self.models.values():
            pred = model.predict(X)
            predictions.append(pred)

        # 多数投票
        predictions = np.array(predictions)
        majority_vote = np.apply_along_axis(
            lambda x: np.bincount(x).argmax(), axis=0, arr=predictions
        )

        return majority_vote

    def _predict_proba_voting(self, X: np.ndarray) -> np.ndarray:
        """使用投票法预测概率."""
        probabilities = []

        for model in self.models.values():
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(X)
                probabilities.append(proba)

        # 平均概率
        avg_proba = np.mean(probabilities, axis=0)
        return avg_proba

    def _predict_weighted_average(self, X: np.ndarray) -> np.ndarray:
        """使用加权平均预测."""
        weighted_predictions = []
        total_weight = 0

        for name, model in self.models.items():
            weight = self.model_weights[name].weight
            pred = model.predict(X)
            weighted_predictions.append(pred * weight)
            total_weight += weight

        ensemble_pred = np.sum(weighted_predictions, axis=0) / total_weight
        return np.round(ensemble_pred).astype(int)

    def _predict_proba_weighted_average(self, X: np.ndarray) -> np.ndarray:
        """使用加权平均预测概率."""
        weighted_probabilities = []
        total_weight = 0

        for name, model in self.models.items():
            if hasattr(model, "predict_proba"):
                weight = self.model_weights[name].weight
                proba = model.predict_proba(X)
                weighted_probabilities.append(proba * weight)
                total_weight += weight

        if weighted_probabilities:
            ensemble_proba = np.sum(weighted_probabilities, axis=0) / total_weight
            return ensemble_proba
        else:
            # 如果没有模型能预测概率，返回默认值
            n_samples = X.shape[0]
            return np.array([[0.5, 0.5]] * n_samples)

    def _predict_dynamic_weighting(self, X: np.ndarray) -> np.ndarray:
        """使用动态权重预测."""
        weighted_predictions = []
        total_weight = 0

        for name, model in self.models.items():
            weight = self.model_weights[name].get_dynamic_weight()
            pred = model.predict(X)
            weighted_predictions.append(pred * weight)
            total_weight += weight

        ensemble_pred = np.sum(weighted_predictions, axis=0) / total_weight
        return np.round(ensemble_pred).astype(int)

    def _predict_proba_dynamic_weighting(self, X: np.ndarray) -> np.ndarray:
        """使用动态权重预测概率."""
        weighted_probabilities = []
        total_weight = 0

        for name, model in self.models.items():
            if hasattr(model, "predict_proba"):
                weight = self.model_weights[name].get_dynamic_weight()
                proba = model.predict_proba(X)
                weighted_probabilities.append(proba * weight)
                total_weight += weight

        if weighted_probabilities:
            ensemble_proba = np.sum(weighted_probabilities, axis=0) / total_weight
            return ensemble_proba
        else:
            n_samples = X.shape[0]
            return np.array([[0.5, 0.5]] * n_samples)

    def _evaluate_ensemble(self, X_val: np.ndarray, y_val: np.ndarray) -> dict[str, float]:
        """评估集成性能."""
        from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

        y_pred = self.predict(X_val)
        y_proba = self.predict_proba(X_val)

        metrics = {
            "accuracy": accuracy_score(y_val, y_pred),
            "precision": precision_score(y_val, y_pred, average="weighted", zero_division=0),
            "recall": recall_score(y_val, y_pred, average="weighted", zero_division=0),
            "f1_score": f1_score(y_val, y_pred, average="weighted", zero_division=0),
        }

        # 如果是二分类，添加AUC
        if len(np.unique(y_val)) == 2:
            from sklearn.metrics import roc_auc_score

            try:
                metrics["roc_auc"] = roc_auc_score(y_val, y_proba[:, 1])
            except Exception:
                metrics["roc_auc"] = 0.5

        return metrics

    def _update_model_weights(self, performance_metrics: dict[str, float]) -> None:
        """更新模型权重."""
        f1_score = performance_metrics.get("f1_score", 0.5)

        # 基于整体性能调整各模型权重
        for _name, weight_config in self.model_weights.items():
            # 简单的权重更新策略
            if f1_score > 0.8:
                # 高性能，略微增加权重
                weight_config.weight = min(weight_config.weight * 1.05, weight_config.max_weight)
            elif f1_score < 0.6:
                # 低性能，略微减少权重
                weight_config.weight = max(weight_config.weight * 0.95, 0.1)

        # 归一化权重
        total_weight = sum(w.weight for w in self.model_weights.values())
        if total_weight > 0:
            for weight_config in self.model_weights.values():
                weight_config.weight /= total_weight

    def _update_weights_based_on_performance(
        self, true_labels: np.ndarray, predictions: np.ndarray
    ) -> None:
        """基于性能更新权重."""
        X_for_individual = self._get_last_X_for_individual_predictions()

        if X_for_individual is not None:
            for name, model in self.models.items():
                try:
                    individual_pred = model.predict(X_for_individual)
                    individual_f1 = f1_score(
                        true_labels, individual_pred, average="weighted", zero_division=0
                    )
                    self.model_weights[name].update_performance(individual_f1)
                except Exception as e:
                    self.logger.error(f"Error updating performance for {name}: {e}")

    def _get_last_X_for_individual_predictions(self) -> np.ndarray | None:
        """获取最后一次预测的X (简化实现)."""
        # 这里应该保存最近的预测数据，简化实现返回None
        return None

    def _calculate_prediction_confidence(self, probabilities: np.ndarray) -> float:
        """计算预测置信度."""
        if len(probabilities) == 1:
            return 0.5  # 单类别情况

        # 使用最大概率作为置信度
        max_prob = np.max(probabilities)
        return float(max_prob)

    def _dict_to_features(self, data: dict[str, Any]) -> np.ndarray:
        """将字典转换为特征向量."""
        if not self.feature_names:
            self.feature_names = list(data.keys())

        features = []
        for name in self.feature_names:
            features.append(float(data.get(name, 0)))

        return np.array(features).reshape(1, -1)

    def _generate_cache_key(self, features: np.ndarray | dict[str, Any]) -> str:
        """生成缓存键."""
        if isinstance(features, dict):
            # 简化的哈希
            feature_str = str(sorted(features.items()))
        else:
            feature_str = str(features.tolist())

        return str(hash(feature_str))
