"""- ML.

、、XGBoost
"""

from __future__ import annotations

import time
from typing import Any, cast

import numpy as np

from .ml_base import BaseMLModel, RiskPredictionResult


class EnsembleMethod:
    """."""

    VOTING = "voting"  #
    STACKING = "stacking"  #
    BAGGING = "bagging"  #
    BOOSTING = "boosting"  #
    WEIGHTED_AVERAGE = "weighted_average"  #
    DYNAMIC_WEIGHTING = "dynamic_weighting"  #


class ModelWeight:
    """."""

    def __init__(
        self, model_name: str, weight: float, min_confidence: float = 0.5, max_weight: float = 1.0
    ) -> None:
        """__init__ method"""
        self.model_name = model_name
        self.weight = weight
        self.min_confidence = min_confidence
        self.max_weight = max_weight
        self.performance_history: list[float] = []
        self.current_performance: float = 0.5

    def update_performance(self, performance: float) -> None:
        """."""
        self.performance_history.append(performance)
        if len(self.performance_history) > 100:
            self.performance_history = self.performance_history[-50:]
        self.current_performance = float(np.mean(self.performance_history))

    def get_dynamic_weight(self) -> float:
        """."""
        if not self.performance_history:
            return self.weight

        #
        performance_factor = self.current_performance / 0.5  # 0.5
        dynamic_weight = self.weight * performance_factor

        return min(max(dynamic_weight, 0.1), self.max_weight)


class RiskEnsembleModel(BaseMLModel):
    """.

    ML:
    1.  - 、
    2.  - 、
    3.  - 、
    4.  -
    5.  -
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """.

        Args: config:

        """
        super().__init__("RiskEnsembleModel", config)

        #
        self.ensemble_method = self.config.get("ensemble_method", EnsembleMethod.WEIGHTED_AVERAGE)
        self.use_dynamic_weighting = self.config.get("use_dynamic_weighting", True)
        self.weight_update_frequency = self.config.get("weight_update_frequency", 100)

        #
        from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
        from sklearn.linear_model import LogisticRegression

        self.models = {
            "random_forest": RandomForestClassifier(
                n_estimators=100, max_depth=10, random_state=42, n_jobs=-1
            ),
            "gradient_boosting": GradientBoostingClassifier(
                n_estimators=100, max_depth=6, learning_rate=0.1, random_state=42
            ),
            "logistic_regression": LogisticRegression(
                random_state=42,
                max_iter=1000,
                solver="liblinear",
            ),
        }

        #
        self.model_weights = {
            "random_forest": ModelWeight("random_forest", 0.4, 0.6, 0.5),
            "gradient_boosting": ModelWeight("gradient_boosting", 0.4, 0.6, 0.5),
            "logistic_regression": ModelWeight("logistic_regression", 0.2, 0.5, 0.3),
        }

        #  (stacking)
        self.meta_learner = LogisticRegression(
            random_state=42,
            max_iter=1000,
            solver="liblinear",
        )
        self.use_stacking = self.ensemble_method == EnsembleMethod.STACKING

        #
        self.prediction_history: list[dict[str, Any]] = []
        self.weight_history: list[dict[str, float]] = []

        #
        self.model_performance: dict[str, dict[str, float]] = {}
        self.ensemble_performance: dict[str, float] = {}

        #
        self.prediction_cache: dict[str, RiskPredictionResult] = {}
        self.cache_size_limit = 1000

        self.logger.info("RiskEnsembleModel initialized")

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        validation_data: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> dict[str, Any]:
        """.

        Args: X:
            y:
            validation_data:

        Returns: Dict[str, Any]:

        """
        start_time = time.time()

        if not self._validate_input(X, y):
            return {"error": "Invalid input data"}

        try:
            #
            X_processed = self._preprocess_features(X)

            #
            if validation_data is None:
                from sklearn.model_selection import train_test_split

                X_train, X_val, y_train, y_val = train_test_split(
                    X_processed, y, test_size=0.2, random_state=42, stratify=y
                )
            else:
                X_train, y_train = X_processed, y
                X_val, y_val = validation_data
                X_val = self._preprocess_features(X_val)

            #
            model_results = {}
            for name, model in self.models.items():
                model_start = time.time()
                model.fit(X_train, y_train)
                model_time = time.time() - model_start

                #
                train_score = model.score(X_train, y_train)
                val_score = model.score(X_val, y_val)

                model_results[name] = {
                    "training_time": model_time,
                    "train_score": train_score,
                    "validation_score": val_score,
                }

                #
                self.model_weights[name].update_performance(val_score)

                self.logger.info(f"Model {name} trained - Val Score: {val_score:.4f}")

            #  (stacking)
            if self.use_stacking:
                self._train_meta_learner(X_train, y_train, X_val, y_val)

            #
            self.is_trained = True
            self.training_time = time.time() - start_time
            self.last_training_time = int(time.time())
            self.metrics["training_samples"] = len(X_train)
            self.metrics["validation_samples"] = len(X_val)

            #
            ensemble_metrics = self._evaluate_ensemble(X_val, y_val)
            self.ensemble_performance = ensemble_metrics

            #
            self._update_model_weights(ensemble_metrics)

            #
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
        """.

        Args: X:

        Returns: np.ndarray:

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
        """.

        Args: X:

        Returns: np.ndarray:

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
        """.

        Args: features:
            return_details:

        Returns: RiskPredictionResult:

        """
        try:
            #
            cache_key = self._generate_cache_key(features)

            #
            if cache_key in self.prediction_cache:
                return self.prediction_cache[cache_key]

            #
            if isinstance(features, dict):
                X = self._dict_to_features(features)
                feature_names = list(features.keys())
            else:
                X = features.reshape(1, -1) if features.ndim == 1 else features
                feature_names = self.feature_names

            #
            probabilities = self.predict_proba(X)
            predictions = self.predict(X)

            #
            individual_predictions = {}
            individual_probabilities = {}

            X_processed = self._preprocess_features(X)
            for name, model in self.models.items():
                pred = model.predict(X_processed)[0]
                proba = model.predict_proba(X_processed)[0]
                individual_predictions[name] = pred
                individual_probabilities[name] = proba

            #
            confidence = self._calculate_prediction_confidence(probabilities[0])

            #
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

            #
            if return_details:
                result.individual_predictions = individual_predictions
                result.individual_probabilities = individual_probabilities
                result.current_weights = {
                    name: weight.get_dynamic_weight() for name, weight in self.model_weights.items()
                }
                result.ensemble_method = self.ensemble_method

            #
            self.prediction_cache[cache_key] = result
            if len(self.prediction_cache) > self.cache_size_limit:
                #
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
        """.

        Args: true_labels:
            predictions:

        """
        if not self.is_trained:
            return

        try:
            from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

            #
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

            # ，
            if self.use_dynamic_weighting:
                self._update_weights_based_on_performance(true_labels, predictions)

            #
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
        """.

        Returns: Dict[str, Any]:

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
        """.

        Returns: Dict[str, float]:

        """
        if not self.is_trained:
            return {}

        feature_importance: dict[str, list[float]] = {}

        #
        for model in self.models.values():
            if hasattr(model, "feature_importances_"):
                importance = model.feature_importances_
                for i, imp in enumerate(importance):
                    feature_name = (
                        self.feature_names[i] if i < len(self.feature_names) else f"feature_{i}"
                    )
                    if feature_name not in feature_importance:
                        feature_importance[feature_name] = []
                    feature_importance[feature_name].append(imp)

        #
        avg_importance: dict[str, float] = {}
        for feature, values in feature_importance.items():
            avg_importance[feature] = float(np.mean(values))

        self.feature_importance = avg_importance
        return avg_importance

    #

    def _train_meta_learner(
        self, X_train: np.ndarray, y_train: np.ndarray, X_val: np.ndarray, y_val: np.ndarray
    ) -> None:
        """."""
        #
        meta_features_train = []
        meta_features_val = []

        for model in self.models.values():
            #  ()
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

        #
        X_meta_train = np.hstack(meta_features_train)

        #
        self.meta_learner.fit(X_meta_train, y_train)

    def _predict_stacking(self, X: np.ndarray) -> np.ndarray:
        """stacking."""
        meta_features = []

        for model in self.models.values():
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(X)
                meta_features.append(proba)
            else:
                pred = model.predict(X).reshape(-1, 1)
                meta_features.append(pred)

        X_meta = np.hstack(meta_features)
        return cast("np.ndarray", self.meta_learner.predict(X_meta))

    def _predict_proba_stacking(self, X: np.ndarray) -> np.ndarray:
        """stacking."""
        meta_features = []

        for model in self.models.values():
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(X)
                meta_features.append(proba)
            else:
                pred = model.predict(X).reshape(-1, 1)
                meta_features.append(pred)

        X_meta = np.hstack(meta_features)
        return cast("np.ndarray", self.meta_learner.predict_proba(X_meta))

    def _predict_voting(self, X: np.ndarray) -> np.ndarray:
        """."""
        predictions_list: list[np.ndarray] = []

        for model in self.models.values():
            pred = model.predict(X)
            predictions_list.append(pred)

        #
        predictions_arr = np.array(predictions_list)
        majority_vote = np.apply_along_axis(
            lambda x: np.bincount(x).argmax(), axis=0, arr=predictions_arr
        )

        return majority_vote

    def _predict_proba_voting(self, X: np.ndarray) -> np.ndarray:
        """."""
        probabilities = []

        for model in self.models.values():
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(X)
                probabilities.append(proba)

        #
        avg_proba = np.mean(probabilities, axis=0)
        return cast("np.ndarray", avg_proba)

    def _predict_weighted_average(self, X: np.ndarray) -> np.ndarray:
        """."""
        weighted_predictions: list[np.ndarray] = []
        total_weight: float = 0.0

        for name, model in self.models.items():
            weight = self.model_weights[name].weight
            pred = model.predict(X)
            weighted_predictions.append(pred * weight)
            total_weight += float(weight)

        if total_weight == 0.0:
            return np.zeros(X.shape[0], dtype=int)
        ensemble_pred = np.sum(weighted_predictions, axis=0) / total_weight
        return np.round(ensemble_pred).astype(int)  # type: ignore[no-any-return]

    def _predict_proba_weighted_average(self, X: np.ndarray) -> np.ndarray:
        """."""
        weighted_probabilities: list[np.ndarray] = []
        total_weight: float = 0.0

        for name, model in self.models.items():
            if hasattr(model, "predict_proba"):
                weight = self.model_weights[name].weight
                proba = model.predict_proba(X)
                weighted_probabilities.append(proba * weight)
                total_weight += float(weight)

        if weighted_probabilities:
            ensemble_proba = np.sum(weighted_probabilities, axis=0) / total_weight
            return ensemble_proba  # type: ignore[no-any-return]
        else:
            # ，
            n_samples = X.shape[0]
            return np.array([[0.5, 0.5]] * n_samples)

    def _predict_dynamic_weighting(self, X: np.ndarray) -> np.ndarray:
        """."""
        weighted_predictions_list: list[np.ndarray] = []
        total_weight: float = 0.0

        for name, model in self.models.items():
            weight = self.model_weights[name].get_dynamic_weight()
            pred = model.predict(X)
            weighted_predictions_list.append(pred * weight)
            total_weight += weight

        if total_weight == 0.0:
            return np.zeros(X.shape[0], dtype=int)
        ensemble_pred = np.sum(weighted_predictions_list, axis=0) / total_weight
        return np.round(ensemble_pred).astype(int)  # type: ignore[no-any-return]

    def _predict_proba_dynamic_weighting(self, X: np.ndarray) -> np.ndarray:
        """."""
        weighted_probabilities_dyn: list[np.ndarray] = []
        total_weight_dyn: float = 0.0

        for name, model in self.models.items():
            if hasattr(model, "predict_proba"):
                weight = self.model_weights[name].get_dynamic_weight()
                proba = model.predict_proba(X)
                weighted_probabilities_dyn.append(proba * weight)
                total_weight_dyn += weight

        if weighted_probabilities_dyn:
            ensemble_proba_dyn = np.sum(weighted_probabilities_dyn, axis=0) / total_weight_dyn
            return ensemble_proba_dyn  # type: ignore[no-any-return]
        else:
            n_samples = X.shape[0]
            return np.array([[0.5, 0.5]] * n_samples)

    def _evaluate_ensemble(self, X_val: np.ndarray, y_val: np.ndarray) -> dict[str, float]:
        """."""
        from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

        y_pred = self.predict(X_val)
        y_proba = self.predict_proba(X_val)

        metrics = {
            "accuracy": accuracy_score(y_val, y_pred),
            "precision": precision_score(y_val, y_pred, average="weighted", zero_division=0),
            "recall": recall_score(y_val, y_pred, average="weighted", zero_division=0),
            "f1_score": f1_score(y_val, y_pred, average="weighted", zero_division=0),
        }

        # ，AUC
        if len(np.unique(y_val)) == 2:
            from sklearn.metrics import roc_auc_score

            try:
                metrics["roc_auc"] = roc_auc_score(y_val, y_proba[:, 1])
            except Exception:
                metrics["roc_auc"] = 0.5

        return metrics

    def _update_model_weights(self, performance_metrics: dict[str, float]) -> None:
        """."""
        f1_score = performance_metrics.get("f1_score", 0.5)

        #
        for weight_config in self.model_weights.values():
            #
            if f1_score > 0.8:
                # ，
                weight_config.weight = min(weight_config.weight * 1.05, weight_config.max_weight)
            elif f1_score < 0.6:
                # ，
                weight_config.weight = max(weight_config.weight * 0.95, 0.1)

        #
        total_weight = sum(w.weight for w in self.model_weights.values())
        if total_weight > 0:
            for weight_config in self.model_weights.values():
                weight_config.weight /= total_weight

    def _update_weights_based_on_performance(
        self, true_labels: np.ndarray, predictions: np.ndarray
    ) -> None:
        """."""
        X_for_individual = self._get_last_X_for_individual_predictions()
        from sklearn.metrics import f1_score

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
        """X ()."""
        # ，None
        return None

    def _calculate_prediction_confidence(self, probabilities: np.ndarray) -> float:
        """."""
        if len(probabilities) == 1:
            return 0.5  #

        #
        max_prob = np.max(probabilities)
        return float(max_prob)

    def _dict_to_features(self, data: dict[str, Any]) -> np.ndarray:
        """."""
        if not self.feature_names:
            self.feature_names = list(data.keys())

        features = [float(data.get(name, 0)) for name in self.feature_names]
        return np.array(features).reshape(1, -1)

    def _generate_cache_key(self, features: np.ndarray | dict[str, Any]) -> str:
        """."""
        if isinstance(features, dict):
            #
            feature_str = str(sorted(features.items()))
        else:
            feature_str = str(features.tolist())

        return str(hash(feature_str))
