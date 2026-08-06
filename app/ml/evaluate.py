"""
Evaluation for the categoriser (PHASE 4).

Reports per-class precision/recall/F1, macro-F1, and a full confusion matrix --
and reports the **random-split** macro-F1 alongside the temporal one, because
the gap between them is the leakage a random split would have hidden.

Macro-F1 rather than accuracy: with a 16%/2% head-to-tail class ratio, a model
that never predicts ``fees_charges`` still scores ~0.97 accuracy. Macro-F1 makes
that failure visible by averaging per-class F1 unweighted.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.core.logging import get_logger
from app.ml.dataset import LABELS

logger = get_logger(__name__)


@dataclass
class ClassMetrics:
    label: str
    support: int
    precision: float
    recall: float
    f1: float


@dataclass
class EvaluationReport:
    """Everything needed to decide whether a model ships."""

    split: str
    n_samples: int
    accuracy: float
    macro_f1: float
    weighted_f1: float
    per_class: List[ClassMetrics] = field(default_factory=list)
    confusion_matrix: List[List[int]] = field(default_factory=list)
    labels: List[str] = field(default_factory=lambda: list(LABELS))
    # Share of the majority class -- the score a constant predictor achieves.
    majority_class_baseline: float = 0.0
    date_range: Optional[Tuple[str, str]] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["per_class"] = [asdict(c) for c in self.per_class]
        return d

    def summary(self) -> str:
        return (
            f"[{self.split}] n={self.n_samples:,} "
            f"macro-F1={self.macro_f1:.4f} accuracy={self.accuracy:.4f} "
            f"(majority baseline {self.majority_class_baseline:.4f})"
        )


def confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray, n_classes: int) -> np.ndarray:
    """Rows are truth, columns are prediction."""
    matrix = np.zeros((n_classes, n_classes), dtype=int)
    # strict=True: unequal lengths here would silently produce a confusion
    # matrix over a truncated set, i.e. a wrong metric reported as a right one.
    for t, p in zip(y_true, y_pred, strict=True):
        matrix[int(t), int(p)] += 1
    return matrix


def evaluate(
    y_true: Sequence[int],
    y_pred: Sequence[int],
    split: str = "test",
    date_range: Optional[Tuple[str, str]] = None,
    labels: Optional[List[str]] = None,
) -> EvaluationReport:
    """Compute the full metric set from raw label ids."""
    labels = labels or LABELS
    n_classes = len(labels)
    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)

    matrix = confusion_matrix(y_true, y_pred, n_classes)
    per_class: List[ClassMetrics] = []
    f1s, weighted_terms, supports = [], [], []

    for c in range(n_classes):
        tp = int(matrix[c, c])
        fp = int(matrix[:, c].sum() - tp)
        fn = int(matrix[c, :].sum() - tp)
        support = int(matrix[c, :].sum())

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

        per_class.append(
            ClassMetrics(
                label=labels[c],
                support=support,
                precision=round(precision, 4),
                recall=round(recall, 4),
                f1=round(f1, 4),
            )
        )
        # A class absent from truth *and* predictions is undefined, not zero --
        # averaging a spurious 0.0 would understate macro-F1.
        if support > 0 or (tp + fp) > 0:
            f1s.append(f1)
            weighted_terms.append(f1 * support)
            supports.append(support)

    total = int(matrix.sum())
    accuracy = float(np.trace(matrix) / total) if total else 0.0
    macro_f1 = float(np.mean(f1s)) if f1s else 0.0
    weighted_f1 = float(sum(weighted_terms) / sum(supports)) if sum(supports) else 0.0
    majority = float(matrix.sum(axis=1).max() / total) if total else 0.0

    notes: List[str] = []
    empty = [labels[c] for c in range(n_classes) if matrix[c, :].sum() == 0]
    if empty:
        notes.append(f"Labels absent from this split: {empty}")
    never_predicted = [
        labels[c] for c in range(n_classes) if matrix[:, c].sum() == 0 and matrix[c, :].sum() > 0
    ]
    if never_predicted:
        notes.append(f"Labels never predicted despite being present: {never_predicted}")

    return EvaluationReport(
        split=split,
        n_samples=total,
        accuracy=round(accuracy, 4),
        macro_f1=round(macro_f1, 4),
        weighted_f1=round(weighted_f1, 4),
        per_class=per_class,
        confusion_matrix=matrix.tolist(),
        labels=list(labels),
        majority_class_baseline=round(majority, 4),
        date_range=date_range,
        notes=notes,
    )


def render_confusion_matrix(report: EvaluationReport, max_label: int = 14) -> str:
    """Fixed-width confusion matrix for logs and the README."""
    labels = report.labels
    matrix = np.asarray(report.confusion_matrix)
    width = max(8, min(max_label, max(len(name) for name in labels)) + 1)

    header = " " * (width + 2) + "".join(f"{name[:6]:>7}" for name in labels)
    lines = [
        f"Confusion matrix -- {report.split} (rows = truth, cols = predicted)",
        header,
    ]
    for i, label in enumerate(labels):
        row = "".join(
            f"{'.':>7}" if matrix[i, j] == 0 else f"{matrix[i, j]:>7}" for j in range(len(labels))
        )
        lines.append(f"{label[:width]:<{width}}| {row}")

    lines.append("")
    lines.append(f"{'label':<{width}}| {'support':>8}{'prec':>8}{'recall':>8}{'f1':>8}")
    for c in report.per_class:
        lines.append(
            f"{c.label[:width]:<{width}}| {c.support:>8}{c.precision:>8.3f}"
            f"{c.recall:>8.3f}{c.f1:>8.3f}"
        )
    lines.append("")
    lines.append(
        f"macro-F1 {report.macro_f1:.4f} | weighted-F1 {report.weighted_f1:.4f} "
        f"| accuracy {report.accuracy:.4f} | majority baseline "
        f"{report.majority_class_baseline:.4f}"
    )
    for note in report.notes:
        lines.append(f"NOTE: {note}")
    return "\n".join(lines)


def compare_splits(temporal: EvaluationReport, random_split: EvaluationReport) -> Dict[str, float]:
    """
    Quantify the leakage a random split would have introduced.

    A materially higher random-split macro-F1 means near-duplicate narrations
    (the same recurring EMI, the same merchant) straddled the split boundary.
    Whatever the sign, the temporal number is the one to quote.
    """
    delta = random_split.macro_f1 - temporal.macro_f1
    return {
        "temporal_macro_f1": temporal.macro_f1,
        "random_macro_f1": random_split.macro_f1,
        "leakage_delta": round(delta, 4),
        "leakage_relative_pct": round(
            100.0 * delta / temporal.macro_f1 if temporal.macro_f1 else 0.0, 2
        ),
    }


def save_report(report: EvaluationReport, path: Path, extra: Optional[Dict] = None) -> Path:
    """Write the report as JSON next to the model artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    if extra:
        payload["extra"] = extra
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(f"Evaluation report written to {path}")
    return path
