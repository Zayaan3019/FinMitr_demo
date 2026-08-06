"""
Population Stability Index drift monitoring (PHASE 4).

PSI compares a reference distribution (what the model was trained on) with a
current one (what it is now seeing):

    PSI = sum over bins of  (actual% - expected%) * ln(actual% / expected%)

Conventional reading, which this module encodes rather than leaves to folklore:

    PSI < 0.10   no material shift
    0.10 - 0.25  moderate shift, investigate
    PSI > 0.25   significant shift, retrain

Retraining is **gated on a held-out threshold**, not fired on the PSI alarm
alone. Drift means the input distribution moved; it does not mean the model got
worse, and retraining on drifted-but-still-fine data burns compute and risks a
regression. So: PSI trips the alarm, and the *held-out macro-F1* of the current
model on recent labelled data decides whether to actually retrain. Both
conditions must hold.

Both categorical (predicted label mix) and numeric (amount) features are
covered, because they fail differently: a new merchant shifts the label mix
while amounts stay put; inflation shifts amounts while the mix stays put.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Floor applied to any bin share before taking the log. Without it a category
# absent from one side sends PSI to infinity, which is an artifact of the
# estimator rather than a real signal.
_EPSILON = 1e-6


@dataclass
class FeatureDrift:
    feature: str
    psi: float
    severity: str
    bins: List[Dict] = field(default_factory=list)


@dataclass
class DriftReport:
    """Drift across all monitored features, plus the retraining decision."""

    features: List[FeatureDrift] = field(default_factory=list)
    max_psi: float = 0.0
    alert: bool = False
    retrain_recommended: bool = False
    heldout_macro_f1: Optional[float] = None
    heldout_threshold: Optional[float] = None
    reason: str = ""
    n_reference: int = 0
    n_current: int = 0

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["features"] = [asdict(f) for f in self.features]
        return d

    def summary(self) -> str:
        lines = [
            f"Drift report -- reference n={self.n_reference:,}, current n={self.n_current:,}",
        ]
        for feature in self.features:
            lines.append(f"  {feature.feature:<24} PSI={feature.psi:.4f}  [{feature.severity}]")
        lines.append(f"  max PSI = {self.max_psi:.4f}  alert={self.alert}")
        if self.heldout_macro_f1 is not None:
            lines.append(
                f"  held-out macro-F1 = {self.heldout_macro_f1:.4f} "
                f"(gate {self.heldout_threshold:.4f})"
            )
        lines.append(f"  retrain_recommended = {self.retrain_recommended} -- {self.reason}")
        return "\n".join(lines)


def severity_for(psi: float) -> str:
    if psi < 0.10:
        return "stable"
    if psi < settings.psi_retrain_threshold:
        return "moderate"
    return "significant"


def categorical_psi(reference: Sequence[str], current: Sequence[str]) -> Tuple[float, List[Dict]]:
    """PSI over a categorical feature (e.g. the predicted-label mix)."""
    ref = pd.Series(list(reference), dtype="object")
    cur = pd.Series(list(current), dtype="object")
    if ref.empty or cur.empty:
        return 0.0, []

    categories = sorted(set(ref.unique()) | set(cur.unique()))
    ref_share = ref.value_counts(normalize=True)
    cur_share = cur.value_counts(normalize=True)

    psi = 0.0
    bins: List[Dict] = []
    for category in categories:
        expected = max(float(ref_share.get(category, 0.0)), _EPSILON)
        actual = max(float(cur_share.get(category, 0.0)), _EPSILON)
        contribution = (actual - expected) * np.log(actual / expected)
        psi += contribution
        bins.append(
            {
                "bin": str(category),
                "expected": round(expected, 6),
                "actual": round(actual, 6),
                "contribution": round(float(contribution), 6),
            }
        )
    return float(psi), sorted(bins, key=lambda b: -abs(b["contribution"]))


def numeric_psi(
    reference: Sequence[float], current: Sequence[float], n_bins: int = 10
) -> Tuple[float, List[Dict]]:
    """
    PSI over a numeric feature using quantile bins from the *reference*.

    Reference-derived edges are essential: re-binning on the current data would
    make the comparison self-referential and PSI would trend to zero no matter
    how far the distribution moved.
    """
    ref = np.asarray([v for v in reference if v is not None], dtype=float)
    cur = np.asarray([v for v in current if v is not None], dtype=float)
    if len(ref) < n_bins or len(cur) == 0:
        return 0.0, []

    quantiles = np.linspace(0, 100, n_bins + 1)
    edges = np.unique(np.percentile(ref, quantiles))
    if len(edges) < 3:
        return 0.0, []
    edges[0], edges[-1] = -np.inf, np.inf

    ref_counts, _ = np.histogram(ref, bins=edges)
    cur_counts, _ = np.histogram(cur, bins=edges)
    ref_share = ref_counts / max(1, ref_counts.sum())
    cur_share = cur_counts / max(1, cur_counts.sum())

    psi = 0.0
    bins: List[Dict] = []
    for i in range(len(ref_share)):
        expected = max(float(ref_share[i]), _EPSILON)
        actual = max(float(cur_share[i]), _EPSILON)
        contribution = (actual - expected) * np.log(actual / expected)
        psi += contribution
        low = edges[i] if np.isfinite(edges[i]) else float("-inf")
        high = edges[i + 1] if np.isfinite(edges[i + 1]) else float("inf")
        bins.append(
            {
                "bin": f"[{low:.4g}, {high:.4g})",
                "expected": round(expected, 6),
                "actual": round(actual, 6),
                "contribution": round(float(contribution), 6),
            }
        )
    return float(psi), bins


def compute_drift(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    categorical_features: Optional[List[str]] = None,
    numeric_features: Optional[List[str]] = None,
) -> DriftReport:
    """Compute PSI for each monitored feature."""
    categorical_features = categorical_features or ["label"]
    numeric_features = numeric_features or ["amount_minor"]

    features: List[FeatureDrift] = []

    for name in categorical_features:
        if name not in reference_df or name not in current_df:
            continue
        psi, bins = categorical_psi(reference_df[name].astype(str), current_df[name].astype(str))
        features.append(
            FeatureDrift(
                feature=name, psi=round(psi, 6), severity=severity_for(psi), bins=bins[:12]
            )
        )

    for name in numeric_features:
        if name not in reference_df or name not in current_df:
            continue
        # Magnitude, not sign: debits are negative and would otherwise dominate.
        psi, bins = numeric_psi(
            reference_df[name].abs().to_numpy(dtype=float),
            current_df[name].abs().to_numpy(dtype=float),
        )
        features.append(
            FeatureDrift(feature=name, psi=round(psi, 6), severity=severity_for(psi), bins=bins)
        )

    max_psi = max((f.psi for f in features), default=0.0)
    return DriftReport(
        features=features,
        max_psi=round(max_psi, 6),
        alert=max_psi >= settings.psi_alert_threshold,
        n_reference=len(reference_df),
        n_current=len(current_df),
        reason="PSI computed; retraining decision not yet evaluated",
    )


def gate_retraining(
    report: DriftReport,
    heldout_macro_f1: Optional[float],
    heldout_threshold: Optional[float] = None,
) -> DriftReport:
    """
    Decide whether to retrain.

    Requires **both** a PSI breach and measured degradation on held-out
    labelled data. Either alone is insufficient: PSI without degradation is a
    distribution that moved harmlessly, and degradation without PSI is a
    labelling or pipeline problem that retraining will not fix.
    """
    threshold = (
        heldout_threshold
        if heldout_threshold is not None
        else float(getattr(settings, "categorizer_min_macro_f1", 0.80))
    )
    report.heldout_macro_f1 = heldout_macro_f1
    report.heldout_threshold = threshold

    psi_breached = report.max_psi >= settings.psi_retrain_threshold

    if heldout_macro_f1 is None:
        report.retrain_recommended = False
        report.reason = (
            "No held-out labelled window available. PSI "
            f"{report.max_psi:.4f} recorded; retraining withheld until "
            "performance can be measured, because drift alone does not imply "
            "degradation."
        )
        return report

    degraded = heldout_macro_f1 < threshold

    if psi_breached and degraded:
        report.retrain_recommended = True
        report.reason = (
            f"PSI {report.max_psi:.4f} >= {settings.psi_retrain_threshold} AND "
            f"held-out macro-F1 {heldout_macro_f1:.4f} < {threshold:.4f}"
        )
    elif psi_breached:
        report.retrain_recommended = False
        report.reason = (
            f"PSI {report.max_psi:.4f} breached but held-out macro-F1 "
            f"{heldout_macro_f1:.4f} still >= {threshold:.4f}: the input "
            "distribution moved without hurting performance. Monitor, do not retrain."
        )
    elif degraded:
        report.retrain_recommended = False
        report.reason = (
            f"Held-out macro-F1 {heldout_macro_f1:.4f} < {threshold:.4f} but PSI "
            f"{report.max_psi:.4f} is stable: investigate labelling or the "
            "ingestion pipeline before retraining."
        )
    else:
        report.retrain_recommended = False
        report.reason = "No drift and no degradation."

    return report
