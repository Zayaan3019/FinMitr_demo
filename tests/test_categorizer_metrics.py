"""
DEFINITION OF DONE #5 -- a trained categoriser reported with macro-F1 and a
confusion matrix, split temporally.

These tests read the model card of the **registered, active** model rather than
training one. Training belongs in ``scripts/train_categorizer.py``; the job here
is to assert that whatever is deployed was evaluated honestly and clears the
bar. A test that trains its own model proves nothing about what is serving
traffic.

Three claims are checked, and each corresponds to a way this metric is commonly
faked:

**Temporal split, not random.** Merchants recur. Split a year of transactions
at random and "UPI/P2M/.../SWIGGY" appears in both train and test, so the model
memorises merchant strings and the score measures lookup, not generalisation.
Splitting at a date means the test window contains merchants and spending
patterns the model has genuinely not seen -- which is the only question worth
asking, since tomorrow's transactions are always after today's. The model card
records both numbers so the size of the leak is visible rather than assumed.

**Macro-F1, not accuracy.** The label distribution is heavily imbalanced: the
majority class alone gives 20% accuracy while being useless. Macro-F1 averages
per-class F1 unweighted, so a model that ignores `fees_charges` (29 test
examples) is penalised exactly as much as one that ignores `dining` (420).

**A confusion matrix, not a single number.** 0.81 macro-F1 does not say *what*
the model confuses. The matrix does, and the confusions it shows here are
substantive -- rent/dining, healthcare/shopping -- not noise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import settings

REGISTRY = Path(settings.model_registry_dir) / "transaction-categoriser"


def active_model_card() -> dict:
    """Load the model card of the currently active version, or skip."""
    active_file = REGISTRY / "ACTIVE"
    if not active_file.exists():
        pytest.skip(
            "No categoriser registered. Train one:\n"
            "  python scripts/train_categorizer.py --rows 14000 --epochs 5"
        )
    version = active_file.read_text(encoding="utf-8").strip()
    card_path = REGISTRY / version / "model_card.json"
    if not card_path.exists():
        pytest.fail(
            f"ACTIVE points at '{version}' but {card_path} does not exist -- "
            f"the registry is inconsistent"
        )
    return json.loads(card_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The reported metrics
# ---------------------------------------------------------------------------


def test_a_model_is_registered_and_active():
    card = active_model_card()
    assert card.get("version"), "model card has no version"
    assert card.get("base_model"), "model card does not record its base model"
    assert card.get("labels"), "model card does not record its label space"


def test_macro_f1_clears_the_configured_floor():
    """
    The gate. ``categorizer_min_macro_f1`` is what "good enough to deploy"
    means, stated as a number rather than a feeling.
    """
    card = active_model_card()
    macro_f1 = card["metrics"]["temporal_test_macro_f1"]

    assert macro_f1 >= settings.categorizer_min_macro_f1, (
        f"active model scores macro-F1 {macro_f1:.4f} on the temporal test "
        f"split, below the {settings.categorizer_min_macro_f1} floor. It must "
        f"not be serving."
    )


def test_the_model_meaningfully_beats_the_majority_baseline():
    """
    The floor beneath the floor.

    "Always predict the most common class" is free. A model that does not
    clearly beat it has learned nothing, however respectable its accuracy
    looks.
    """
    card = active_model_card()
    macro_f1 = card["metrics"]["temporal_test_macro_f1"]
    baseline = card["metrics"]["majority_class_baseline"]

    assert macro_f1 > baseline * 2, (
        f"macro-F1 {macro_f1:.4f} versus a majority-class baseline of "
        f"{baseline:.4f} -- the model is barely better than a constant"
    )


def test_every_class_is_actually_learned():
    """
    Macro-F1 is an average and averages hide zeros.

    A class with F1 = 0 means the model never predicts it correctly; the
    aggregate can still look acceptable if the other thirteen carry it. In a
    financial product that is a category of spending the user simply cannot
    see.
    """
    card = active_model_card()
    per_class = card["metrics"]["per_class_f1"]

    dead = {label: f1 for label, f1 in per_class.items() if f1 < 0.30}
    assert not dead, f"these classes are effectively unlearned (F1 < 0.30): {dead}"


# ---------------------------------------------------------------------------
# Honesty of the split
# ---------------------------------------------------------------------------


def test_the_split_is_temporal_and_the_leakage_is_quantified():
    """
    The headline number must come from a temporal split, and the random-split
    number must be recorded next to it.

    Publishing only the random-split score is the single most common way a
    classification result is overstated. Recording both makes the gap
    auditable: if random >> temporal, the model is leaning on merchant
    memorisation.
    """
    card = active_model_card()
    metrics = card["metrics"]

    assert "temporal_test_macro_f1" in metrics, (
        "no temporal-split metric recorded -- the headline number may be "
        "leaking future data into training"
    )

    comparison = metrics.get("leakage_comparison")
    assert comparison, (
        "no random-vs-temporal comparison recorded, so the size of the " "leakage cannot be judged"
    )

    random_f1 = comparison.get("random_macro_f1")
    temporal_f1 = metrics["temporal_test_macro_f1"]
    assert random_f1 is not None, f"leakage_comparison is incomplete: {comparison}"

    # The random split should score *at least* as well -- it is the easier
    # task. If temporal beat it substantially, the split logic is suspect.
    assert temporal_f1 <= random_f1 + 0.10, (
        f"temporal macro-F1 ({temporal_f1:.4f}) exceeds random "
        f"({random_f1:.4f}) by more than noise -- check the split boundary"
    )

    # The gap is the leak, and it is large: reporting the random-split number
    # as the headline would overstate this model by roughly a fifth.
    assert comparison.get("leakage_delta") is not None, (
        "the leakage delta is not recorded, so the overstatement a random "
        "split would have produced is not visible to a reader"
    )


def test_a_confusion_matrix_was_produced():
    """The matrix is a deliverable, not an optional extra."""
    version = (REGISTRY / "ACTIVE").read_text(encoding="utf-8").strip()
    report = REGISTRY / version / "evaluation.txt"
    assert report.exists(), f"no human-readable evaluation report at {report}"

    text = report.read_text(encoding="utf-8")
    assert "Confusion matrix" in text, "the evaluation report has no confusion matrix"
    assert "macro-F1" in text, "the evaluation report does not state macro-F1"
    assert "support" in text, (
        "the per-class table has no support column, so the reader cannot tell "
        "which F1 scores rest on 29 examples and which on 420"
    )


def test_the_label_space_matches_the_api_schema():
    """
    Drift between the model's labels and the OpenAPI enum would let the API
    advertise categories the model can never emit -- or omit ones it does.
    """
    from app.ml.dataset import LABELS
    from app.models.schemas import TransactionCategory

    card_labels = set(active_model_card()["labels"])
    dataset_labels = set(LABELS)
    schema_labels = {c.value for c in TransactionCategory}

    assert card_labels == dataset_labels, (
        f"the deployed model's labels differ from app.ml.dataset.LABELS:\n"
        f"  only in model:   {sorted(card_labels - dataset_labels)}\n"
        f"  only in dataset: {sorted(dataset_labels - card_labels)}"
    )
    # The schema additionally carries 'uncategorised', which is assigned by the
    # confidence floor rather than predicted by the model.
    assert dataset_labels <= schema_labels, (
        f"labels the model can emit but the API schema does not declare: "
        f"{sorted(dataset_labels - schema_labels)}"
    )
    assert schema_labels - dataset_labels == {"uncategorised"}, (
        f"unexpected extra categories in the API schema: "
        f"{sorted(schema_labels - dataset_labels - {'uncategorised'})}"
    )


# ---------------------------------------------------------------------------
# Serving behaviour
# ---------------------------------------------------------------------------


def test_predictions_carry_the_model_version():
    """
    Every categorised row records which artefact labelled it.

    Without it, a bad model deployed for two days leaves rows that cannot be
    distinguished from good ones, and there is nothing to re-label.
    """
    from app.ml.categorizer import get_categorizer

    predictions = get_categorizer().predict(
        ["UPI/P2M/417293856201/SWIGGY", "NEFT-HDFC0001234-987654321012345-SALARY CREDIT"]
    )

    assert len(predictions) == 2
    for prediction in predictions:
        assert prediction.model_version, (
            "a prediction was returned with no model_version -- it could not "
            "be traced back to the artefact that produced it"
        )
        assert (
            0.0 <= prediction.confidence <= 1.0
        ), f"confidence {prediction.confidence} is not a probability"


def test_low_confidence_predictions_become_uncategorised():
    """
    A confident wrong answer is worse than an admitted unknown.

    Below ``categorizer_confidence_floor`` the label is replaced with
    'uncategorised' rather than shown to the user as a categorisation.
    """
    from app.ml.categorizer import get_categorizer

    categorizer = get_categorizer()
    # Deliberately meaningless input: nothing in the label space fits.
    predictions = categorizer.predict(["ZZQX 88123 QQQ ZZZZ"])
    prediction = predictions[0]

    if prediction.confidence < settings.categorizer_confidence_floor:
        assert prediction.label == "uncategorised", (
            f"confidence {prediction.confidence:.3f} is below the "
            f"{settings.categorizer_confidence_floor} floor but the label was "
            f"still reported as '{prediction.label}'"
        )


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


def test_retraining_is_gated_on_more_than_drift():
    """
    PSI alone must not trigger a retrain.

    Population drift means the inputs changed, not that the model got worse --
    a new merchant that the model still classifies correctly moves PSI without
    hurting anything. Retraining on drift alone burns compute and risks
    replacing a good model with a worse one. The gate requires both a PSI
    breach *and* measured degradation on a held-out set.
    """
    from app.ml.drift import DriftReport, gate_retraining

    breached = settings.psi_retrain_threshold + 0.10

    # Drift, but the model still performs: monitor, do not retrain.
    decision = gate_retraining(
        DriftReport(max_psi=breached, alert=True),
        heldout_macro_f1=settings.categorizer_min_macro_f1 + 0.05,
    )
    assert (
        not decision.retrain_recommended
    ), f"retraining was triggered by PSI alone: {decision.reason}"

    # Drift *and* measured degradation: retrain.
    decision = gate_retraining(
        DriftReport(max_psi=breached, alert=True),
        heldout_macro_f1=settings.categorizer_min_macro_f1 - 0.15,
    )
    assert decision.retrain_recommended, (
        f"drift plus measured degradation did not trigger a retrain: " f"{decision.reason}"
    )

    # No labelled window at all: withhold. Retraining without being able to
    # measure whether it helped is a coin flip on production behaviour.
    decision = gate_retraining(DriftReport(max_psi=breached, alert=True), heldout_macro_f1=None)
    assert (
        not decision.retrain_recommended
    ), f"retrained with no held-out measurement: {decision.reason}"
