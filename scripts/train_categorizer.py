#!/usr/bin/env python
"""
Train, evaluate and register the transaction categoriser (PHASE 4).

    python scripts/train_categorizer.py --rows 12000 --epochs 4

Produces, in ``data/models/transaction-categoriser/<version>/``:

* ``artifact/``            fine-tuned weights + tokenizer
* ``model_card.json``      metrics, labels and training metadata
* ``evaluation.json``      full per-class metrics and confusion matrix
* ``evaluation.txt``       the rendered confusion matrix
* ``drift.json``           PSI of the drift window against the training window

The run reports macro-F1 on a **temporal** split and, alongside it, macro-F1 on
a random split of the same data. The gap between the two is the leakage a
random split would have hidden, and it is printed so nobody quotes the wrong
number.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.ml import dataset as ds  # noqa: E402
from app.ml import drift as drift_mod  # noqa: E402
from app.ml import evaluate as ev  # noqa: E402
from app.ml.categorizer import (  # noqa: E402
    MODEL_NAME,
    TrainConfig,
    _encode,
    register_trained_model,
    train_categorizer,
)
from app.ml.registry import get_registry  # noqa: E402

logger = get_logger(__name__)


def predict_df(model, tokenizer, df, max_length: int, batch_size: int = 256):
    """Batched inference over a DataFrame of narrations."""
    import torch

    model.eval()
    preds = []
    narrations = df["narration"].tolist()
    with torch.no_grad():
        for start in range(0, len(narrations), batch_size):
            chunk = narrations[start : start + batch_size]
            enc = _encode(tokenizer, chunk, max_length)
            logits = model(input_ids=enc["input_ids"], attention_mask=enc["attention_mask"]).logits
            preds.append(logits.argmax(dim=-1).numpy())
    return np.concatenate(preds) if preds else np.array([], dtype=int)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the FinGuru categoriser")
    parser.add_argument("--rows", type=int, default=12_000)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=5e-4)
    parser.add_argument("--seed", type=int, default=20260801)
    parser.add_argument("--max-length", type=int, default=48)
    parser.add_argument("--no-activate", action="store_true", help="Register without activating")
    parser.add_argument(
        "--skip-random-split",
        action="store_true",
        help="Skip the leakage comparison run (halves training time)",
    )
    args = parser.parse_args()

    setup_logging()

    # ---- data ----------------------------------------------------------
    spec = ds.DatasetSpec(n_rows=args.rows, seed=args.seed)
    data = ds.generate(spec)
    train_df, val_df, test_df = ds.temporal_split(data)

    print("\n=== Dataset ===")
    print(f"rows: {len(data):,}  labels: {len(ds.LABELS)}")
    print(f"date range: {data['txn_date'].min()} .. {data['txn_date'].max()}")
    print(f"train {len(train_df):,} | val {len(val_df):,} | test {len(test_df):,}")
    print(f"class prior (full): {json.dumps(ds.label_distribution(data))}")

    config = TrainConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        seed=args.seed,
        max_length=args.max_length,
    )

    # ---- train (temporal) ----------------------------------------------
    print("\n=== Training on the TEMPORAL split ===")
    model, tokenizer, history = train_categorizer(train_df, val_df, config)

    test_pred = predict_df(model, tokenizer, test_df, config.max_length)
    temporal_report = ev.evaluate(
        test_df["label_id"].to_numpy(),
        test_pred,
        split="temporal-test",
        date_range=(str(test_df["txn_date"].min()), str(test_df["txn_date"].max())),
    )

    print("\n" + ev.render_confusion_matrix(temporal_report))

    # ---- leakage comparison --------------------------------------------
    comparison = None
    random_report = None
    if not args.skip_random_split:
        print("\n=== Control: training on a RANDOM split (to measure leakage) ===")
        r_train, r_val, r_test = ds.random_split_for_comparison(data, seed=args.seed)
        r_model, r_tokenizer, _ = train_categorizer(r_train, r_val, config)
        r_pred = predict_df(r_model, r_tokenizer, r_test, config.max_length)
        random_report = ev.evaluate(r_test["label_id"].to_numpy(), r_pred, split="random-test")
        comparison = ev.compare_splits(temporal_report, random_report)
        print(f"\n{random_report.summary()}")
        print(f"\nLeakage comparison: {json.dumps(comparison, indent=2)}")
        print(
            "\nThe TEMPORAL number is the one to quote. The random-split score is "
            "inflated by near-duplicate recurring narrations straddling the split."
        )

    # ---- drift ----------------------------------------------------------
    reference = data[~data["in_drift_window"]]
    current = data[data["in_drift_window"]]
    drift_report = drift_mod.compute_drift(reference, current)

    drift_test = test_df[test_df["in_drift_window"]]
    heldout_f1 = None
    if len(drift_test) > 50:
        drift_pred = predict_df(model, tokenizer, drift_test, config.max_length)
        heldout = ev.evaluate(drift_test["label_id"].to_numpy(), drift_pred, split="drift-window")
        heldout_f1 = heldout.macro_f1
    drift_report = drift_mod.gate_retraining(drift_report, heldout_f1)

    print("\n=== Drift ===")
    print(drift_report.summary())

    # ---- register -------------------------------------------------------
    metrics = {
        "temporal_test_macro_f1": temporal_report.macro_f1,
        "temporal_test_weighted_f1": temporal_report.weighted_f1,
        "temporal_test_accuracy": temporal_report.accuracy,
        "majority_class_baseline": temporal_report.majority_class_baseline,
        "val_macro_f1": history["best_val_macro_f1"],
        "per_class_f1": {c.label: c.f1 for c in temporal_report.per_class},
    }
    if comparison:
        metrics["leakage_comparison"] = comparison

    training_metadata = {
        "base_model": config.base_model,
        "n_train": len(train_df),
        "n_val": len(val_df),
        "n_test": len(test_df),
        "epochs": config.epochs,
        "batch_size": config.batch_size,
        "learning_rate": config.learning_rate,
        "max_length": config.max_length,
        "seed": config.seed,
        "class_weighting": config.class_weighting,
        "split": "temporal",
        "split_boundaries": {
            "train_end": str(train_df["txn_date"].max()),
            "val_end": str(val_df["txn_date"].max()),
            "test_start": str(test_df["txn_date"].min()),
        },
        "class_prior": ds.label_distribution(train_df),
        "history": history["epochs"],
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }

    card = register_trained_model(
        model,
        tokenizer,
        metrics=metrics,
        training_metadata=training_metadata,
        activate=not args.no_activate,
    )

    version_dir = get_registry().version_dir(MODEL_NAME, card.version)
    ev.save_report(
        temporal_report,
        version_dir / "evaluation.json",
        extra={
            "leakage_comparison": comparison,
            "random_split_report": random_report.to_dict() if random_report else None,
        },
    )
    (version_dir / "evaluation.txt").write_text(
        ev.render_confusion_matrix(temporal_report), encoding="utf-8"
    )
    (version_dir / "drift.json").write_text(
        json.dumps(drift_report.to_dict(), indent=2), encoding="utf-8"
    )

    print(f"\n=== Registered {MODEL_NAME}:{card.version} ===")
    print(f"artifacts: {version_dir}")
    print(f"temporal test macro-F1: {temporal_report.macro_f1:.4f}")
    print(f"active: {not args.no_activate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
