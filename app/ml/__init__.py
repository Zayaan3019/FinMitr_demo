"""
Machine-learning layer (PHASE 4).

Contains the trained transaction categoriser, its temporally-honest
evaluation, the anomaly-detection evaluation harness, PSI drift monitoring,
and the model registry that ties a persisted ``model_version`` on every
transaction row back to the artifact that produced it.
"""

from app.ml.registry import ModelRegistry, get_registry

__all__ = ["ModelRegistry", "get_registry"]
