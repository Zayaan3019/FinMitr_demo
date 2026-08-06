"""
Anomaly-detection evaluation (PHASE 4).

The rule this module exists to enforce: **an unlabelled anomaly count is not a
metric.** "We flagged 47 anomalies" says nothing -- 47 out of how many, and how
many were real? A detector that flags 5% of everything will always produce an
impressive-looking count.

The metric that matters for a review queue is **precision@k**: of the k
highest-scoring transactions a human will actually look at, what fraction are
genuinely anomalous? And it is only interpretable next to the **base rate** --
precision@20 of 0.30 is a 15x lift over a 2% base rate and is excellent; the
same 0.30 against a 40% base rate means the detector is worse than random.

Recall@k and lift@k are reported too, because a review queue that is precise
but finds only 5% of the fraud is not a working control.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)

DETECTOR_VERSION = "pyod-iforest-v1"


@dataclass
class PrecisionAtK:
    k: int
    precision: float
    recall: float
    lift: float
    true_positives: int


@dataclass
class AnomalyEvaluation:
    """Evaluation of one detector against a labelled window."""

    detector_version: str
    n_samples: int
    n_positives: int
    base_rate: float
    at_k: List[PrecisionAtK] = field(default_factory=list)
    average_precision: float = 0.0
    roc_auc: float = 0.0
    window: Optional[Tuple[str, str]] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["at_k"] = [asdict(x) for x in self.at_k]
        return d

    def summary(self) -> str:
        lines = [
            f"Detector {self.detector_version} on {self.n_samples:,} transactions",
            f"  base rate: {self.base_rate:.4f} " f"({self.n_positives} labelled anomalies)",
            f"  average precision: {self.average_precision:.4f} | ROC-AUC: {self.roc_auc:.4f}",
        ]
        for entry in self.at_k:
            lines.append(
                f"  precision@{entry.k:<4} = {entry.precision:.4f}  "
                f"recall@{entry.k:<4} = {entry.recall:.4f}  "
                f"lift = {entry.lift:.2f}x  ({entry.true_positives}/{entry.k} true)"
            )
        for note in self.notes:
            lines.append(f"  NOTE: {note}")
        return "\n".join(lines)


def precision_at_k(scores: Sequence[float], labels: Sequence[int], k: int) -> PrecisionAtK:
    """
    Precision, recall and lift over the top-k scored items.

    Ties at the boundary are broken by the sort order; with continuous
    anomaly scores exact ties are rare enough not to change the picture.
    """
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=int)
    n = len(scores)
    if n == 0:
        return PrecisionAtK(k=k, precision=0.0, recall=0.0, lift=0.0, true_positives=0)

    k = min(k, n)
    order = np.argsort(-scores)[:k]
    tp = int(labels[order].sum())
    total_positives = int(labels.sum())

    precision = tp / k if k else 0.0
    recall = tp / total_positives if total_positives else 0.0
    base_rate = total_positives / n if n else 0.0
    lift = precision / base_rate if base_rate > 0 else 0.0

    return PrecisionAtK(
        k=k,
        precision=round(precision, 4),
        recall=round(recall, 4),
        lift=round(lift, 3),
        true_positives=tp,
    )


def average_precision(scores: Sequence[float], labels: Sequence[int]) -> float:
    """Area under the precision-recall curve, computed by summation."""
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=int)
    total_positives = int(labels.sum())
    if total_positives == 0:
        return 0.0

    order = np.argsort(-scores)
    sorted_labels = labels[order]
    cumulative_tp = np.cumsum(sorted_labels)
    ranks = np.arange(1, len(sorted_labels) + 1)
    precisions = cumulative_tp / ranks
    return float((precisions * sorted_labels).sum() / total_positives)


def roc_auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    """ROC-AUC via the rank (Mann-Whitney U) identity."""
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=int)
    pos = int(labels.sum())
    neg = len(labels) - pos
    if pos == 0 or neg == 0:
        return 0.0

    order = np.argsort(scores)
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1)

    # Average ranks within tie groups so ties do not inflate the score.
    sorted_scores = scores[order]
    i = 0
    while i < len(sorted_scores):
        j = i
        while j + 1 < len(sorted_scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        if j > i:
            mean_rank = (i + j + 2) / 2.0
            ranks[order[i : j + 1]] = mean_rank
        i = j + 1

    return float((ranks[labels == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def evaluate_detector(
    scores: Sequence[float],
    labels: Sequence[int],
    detector_version: str = DETECTOR_VERSION,
    ks: Optional[Sequence[int]] = None,
    window: Optional[Tuple[str, str]] = None,
) -> AnomalyEvaluation:
    """Full evaluation against a labelled window."""
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=int)
    n = len(scores)
    positives = int(labels.sum())
    base_rate = positives / n if n else 0.0

    ks = ks or [10, 20, 50, 100]
    notes: List[str] = []
    if positives == 0:
        notes.append(
            "No labelled positives in this window -- precision@k is undefined. "
            "Do not report an anomaly count in its place."
        )
    if positives and base_rate > 0.2:
        notes.append(
            f"Base rate {base_rate:.2%} is unusually high; check the labelling "
            "process before interpreting lift."
        )

    return AnomalyEvaluation(
        detector_version=detector_version,
        n_samples=n,
        n_positives=positives,
        base_rate=round(base_rate, 6),
        at_k=[precision_at_k(scores, labels, k) for k in ks if k <= max(1, n)],
        average_precision=round(average_precision(scores, labels), 4),
        roc_auc=round(roc_auc(scores, labels), 4),
        window=window,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


def build_features(df: pd.DataFrame) -> np.ndarray:
    """
    Feature matrix for anomaly scoring.

    Amount alone finds only "the biggest transaction", which the user already
    knows about. The informative signals are *relative*: how this amount
    compares to the user's own history in that category, and whether the timing
    is unusual for them.
    """
    work = df.copy()
    work["amount_abs"] = work["amount_minor"].abs().astype(float)
    work["log_amount"] = np.log1p(work["amount_abs"])

    dates = pd.to_datetime(work["txn_date"])
    work["day_of_week"] = dates.dt.dayofweek.astype(float)
    work["day_of_month"] = dates.dt.day.astype(float)

    category = work["category"] if "category" in work else pd.Series(["all"] * len(work))
    grouped = work.groupby(category)["log_amount"]
    work["cat_mean"] = grouped.transform("mean")
    work["cat_std"] = grouped.transform("std").fillna(0.0)
    # z-score of this amount within its own category
    work["cat_z"] = (work["log_amount"] - work["cat_mean"]) / work["cat_std"].replace(0, 1.0)

    work["narration_len"] = work["narration"].astype(str).str.len().astype(float)

    features = work[
        ["log_amount", "cat_z", "day_of_week", "day_of_month", "narration_len"]
    ].to_numpy(dtype=float)
    return np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)


def score_transactions(
    df: pd.DataFrame, contamination: float = 0.05, seed: int = 20260801
) -> np.ndarray:
    """
    Score transactions with PyOD's Isolation Forest.

    Higher score = more anomalous. ``contamination`` sets the decision
    threshold, not the ranking, and precision@k depends only on the ranking --
    which is why the queue metric is robust to guessing it wrong.
    """
    features = build_features(df)
    if len(features) < 10:
        logger.warning("Too few transactions to fit a detector; returning zeros")
        return np.zeros(len(features))

    try:
        from pyod.models.iforest import IForest

        detector = IForest(
            contamination=min(0.4, max(0.001, contamination)),
            random_state=seed,
            n_estimators=150,
        )
        detector.fit(features)
        return np.asarray(detector.decision_scores_, dtype=float)
    except Exception as exc:
        logger.error(f"PyOD scoring failed ({exc}); falling back to robust z-score")
        column = features[:, 0]
        median = np.median(column)
        mad = np.median(np.abs(column - median)) or 1.0
        return np.abs(column - median) / mad
