"""
Transaction-narration categoriser (PHASE 4).

Fine-tunes a small BERT (``google/bert_uncased_L-2_H-128_A-2``, 4.4M params) on
transaction narrations. Small on purpose: narrations are 5-15 tokens of
semi-structured text, the label set is 14 classes, and inference sits on the
ingestion path where a 400ms model would be a problem. A 4M-parameter encoder
trains on CPU in minutes and serves in single-digit milliseconds.

The training loop is written directly against PyTorch rather than
``transformers.Trainer`` so that the pieces that matter here -- class-weighted
loss for the long-tailed label distribution, and selection on *validation
macro-F1* rather than accuracy -- are explicit and inspectable.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger
from app.ml.dataset import ID_TO_LABEL, LABEL_TO_ID, LABELS
from app.ml.registry import ModelCard, get_registry, make_version

logger = get_logger(__name__)

MODEL_NAME = "transaction-categoriser"


def _torch():
    import torch

    return torch


@dataclass
class TrainConfig:
    base_model: str = ""
    max_length: int = 48
    batch_size: int = 64
    eval_batch_size: int = 256
    epochs: int = 4
    learning_rate: float = 5e-4
    weight_decay: float = 0.01
    warmup_ratio: float = 0.1
    seed: int = 20260801
    # Long-tailed labels: `fees_charges` is 2% of rows and `dining` is 16%.
    # Without weighting, macro-F1 is dominated by the head classes.
    class_weighting: bool = True
    device: str = "cpu"

    def __post_init__(self):
        if not self.base_model:
            self.base_model = settings.categorizer_base_model


def _encode(tokenizer, texts: Sequence[str], max_length: int):
    return tokenizer(
        list(texts),
        padding="max_length",
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )


def _class_weights(labels: Sequence[int], n_classes: int):
    """Inverse-frequency weights, normalised to mean 1 so the LR still applies."""
    torch = _torch()
    counts = np.bincount(np.asarray(labels), minlength=n_classes).astype(np.float64)
    counts[counts == 0] = 1.0
    weights = counts.sum() / (n_classes * counts)
    weights = weights / weights.mean()
    return torch.tensor(weights, dtype=torch.float32)


def train_categorizer(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    config: Optional[TrainConfig] = None,
) -> Tuple[object, object, Dict]:
    """
    Fine-tune and return ``(model, tokenizer, history)``.

    Model selection is on validation **macro-F1**. Accuracy would pick a model
    that nails `dining` and ignores `fees_charges`, which is the opposite of
    what a categoriser is for.
    """
    torch = _torch()
    from torch.utils.data import DataLoader, TensorDataset
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    config = config or TrainConfig()
    torch.manual_seed(config.seed)
    np.random.seed(config.seed)

    device = torch.device(config.device)
    logger.info(
        f"Fine-tuning {config.base_model} on {len(train_df):,} narrations "
        f"({len(LABELS)} labels, device={device})"
    )

    tokenizer = AutoTokenizer.from_pretrained(config.base_model)
    model = AutoModelForSequenceClassification.from_pretrained(
        config.base_model,
        num_labels=len(LABELS),
        id2label=dict(ID_TO_LABEL),
        label2id=dict(LABEL_TO_ID),
    ).to(device)

    train_enc = _encode(tokenizer, train_df["narration"].tolist(), config.max_length)
    val_enc = _encode(tokenizer, val_df["narration"].tolist(), config.max_length)
    y_train = torch.tensor(train_df["label_id"].to_numpy(), dtype=torch.long)
    y_val = torch.tensor(val_df["label_id"].to_numpy(), dtype=torch.long)

    train_loader = DataLoader(
        TensorDataset(train_enc["input_ids"], train_enc["attention_mask"], y_train),
        batch_size=config.batch_size,
        shuffle=True,
    )
    val_loader = DataLoader(
        TensorDataset(val_enc["input_ids"], val_enc["attention_mask"], y_val),
        batch_size=config.eval_batch_size,
    )

    weights = (
        _class_weights(train_df["label_id"].tolist(), len(LABELS)).to(device)
        if config.class_weighting
        else None
    )
    loss_fn = torch.nn.CrossEntropyLoss(weight=weights)

    optimiser = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    total_steps = max(1, len(train_loader) * config.epochs)
    warmup_steps = int(total_steps * config.warmup_ratio)

    def lr_lambda(step: int) -> float:
        if step < warmup_steps:
            return step / max(1, warmup_steps)
        progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
        return max(0.0, 1.0 - progress)

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimiser, lr_lambda)

    history: Dict = {"epochs": [], "best_epoch": 0, "best_val_macro_f1": -1.0}
    best_state = None

    for epoch in range(1, config.epochs + 1):
        model.train()
        running = 0.0
        for input_ids, attention_mask, labels in train_loader:
            input_ids = input_ids.to(device)
            attention_mask = attention_mask.to(device)
            labels = labels.to(device)

            optimiser.zero_grad()
            logits = model(input_ids=input_ids, attention_mask=attention_mask).logits
            loss = loss_fn(logits, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimiser.step()
            scheduler.step()
            running += float(loss.item())

        preds, truths = _predict_loader(model, val_loader, device)
        macro_f1 = _macro_f1(truths, preds, len(LABELS))
        accuracy = float((preds == truths).mean())

        history["epochs"].append(
            {
                "epoch": epoch,
                "train_loss": round(running / max(1, len(train_loader)), 4),
                "val_macro_f1": round(macro_f1, 4),
                "val_accuracy": round(accuracy, 4),
            }
        )
        logger.info(
            f"epoch {epoch}/{config.epochs} "
            f"loss={running / max(1, len(train_loader)):.4f} "
            f"val_macro_f1={macro_f1:.4f} val_acc={accuracy:.4f}"
        )

        if macro_f1 > history["best_val_macro_f1"]:
            history["best_val_macro_f1"] = round(macro_f1, 4)
            history["best_epoch"] = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
        logger.info(
            f"Restored epoch {history['best_epoch']} "
            f"(val macro-F1 {history['best_val_macro_f1']:.4f})"
        )

    return model, tokenizer, history


def _predict_loader(model, loader, device) -> Tuple[np.ndarray, np.ndarray]:
    torch = _torch()
    model.eval()
    all_preds, all_truth = [], []
    with torch.no_grad():
        for input_ids, attention_mask, labels in loader:
            logits = model(
                input_ids=input_ids.to(device), attention_mask=attention_mask.to(device)
            ).logits
            all_preds.append(logits.argmax(dim=-1).cpu().numpy())
            all_truth.append(labels.numpy())
    return np.concatenate(all_preds), np.concatenate(all_truth)


def _macro_f1(y_true: np.ndarray, y_pred: np.ndarray, n_classes: int) -> float:
    """Unweighted mean of per-class F1 -- every category counts equally."""
    f1s = []
    for c in range(n_classes):
        tp = int(((y_pred == c) & (y_true == c)).sum())
        fp = int(((y_pred == c) & (y_true != c)).sum())
        fn = int(((y_pred != c) & (y_true == c)).sum())
        if tp + fp + fn == 0:
            continue  # class absent from both -- undefined, not zero
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1s.append(2 * precision * recall / (precision + recall) if (precision + recall) else 0.0)
    return float(np.mean(f1s)) if f1s else 0.0


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


@dataclass
class Categorisation:
    """One prediction, carrying the version that produced it."""

    label: str
    confidence: float
    model_version: str
    # Below the floor we return `uncategorised` rather than a bad guess: a
    # wrong category silently corrupts every budget the user sees.
    below_floor: bool = False


class CategorizerService:
    """Loads the active model from the registry and serves predictions."""

    def __init__(self, model_version: Optional[str] = None):
        self.model_version = model_version or settings.categorizer_model_version or None
        self._model = None
        self._tokenizer = None
        self._max_length = 48
        self._loaded_version: Optional[str] = None

    # -- loading ---------------------------------------------------------
    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True

        registry = get_registry()
        version = self.model_version or registry.active_version(MODEL_NAME)
        if not version:
            logger.warning(
                "No active transaction-categoriser in the registry; "
                "falling back to the rule-based categoriser"
            )
            return False

        artifact = registry.artifact_path(MODEL_NAME, version)
        if artifact is None:
            logger.warning(f"Model {MODEL_NAME}:{version} has no artifact on disk")
            return False

        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(str(artifact))
            self._model = AutoModelForSequenceClassification.from_pretrained(str(artifact))
            self._model.eval()
            card = registry.load_card(MODEL_NAME, version)
            if card:
                self._max_length = int(card.training_metadata.get("max_length", self._max_length))
            self._loaded_version = version
            logger.info(f"Loaded categoriser {MODEL_NAME}:{version}")
            return True
        except Exception as exc:
            logger.error(f"Failed to load categoriser {version}: {exc}")
            self._model = None
            return False

    # -- predict ---------------------------------------------------------
    def predict(self, narrations: Sequence[str]) -> List[Categorisation]:
        """Categorise a batch. Falls back to rules when no model is registered."""
        if not narrations:
            return []

        if not self._ensure_loaded():
            return [self._rule_fallback(n) for n in narrations]

        torch = _torch()
        enc = _encode(self._tokenizer, narrations, self._max_length)
        with torch.no_grad():
            logits = self._model(
                input_ids=enc["input_ids"], attention_mask=enc["attention_mask"]
            ).logits
            probs = torch.softmax(logits, dim=-1).numpy()

        out: List[Categorisation] = []
        floor = settings.categorizer_confidence_floor
        for row in probs:
            idx = int(row.argmax())
            confidence = float(row[idx])
            below = confidence < floor
            out.append(
                Categorisation(
                    label="uncategorised" if below else ID_TO_LABEL[idx],
                    confidence=round(confidence, 4),
                    model_version=self._loaded_version or "unknown",
                    below_floor=below,
                )
            )
        return out

    def predict_one(self, narration: str) -> Categorisation:
        return self.predict([narration])[0]

    # -- fallback --------------------------------------------------------
    def _rule_fallback(self, narration: str) -> Categorisation:
        """
        Keyword fallback, used only when no model is registered.

        Tagged ``rules-v0`` so a transaction categorised this way is
        distinguishable in the ledger from one a real model produced.
        """
        from app.ml.dataset import MERCHANTS

        upper = (narration or "").upper()
        for label, merchants in MERCHANTS.items():
            for merchant in merchants:
                if merchant in upper:
                    return Categorisation(label=label, confidence=0.5, model_version="rules-v0")
        return Categorisation(
            label="uncategorised", confidence=0.0, model_version="rules-v0", below_floor=True
        )


_service: Optional[CategorizerService] = None


def get_categorizer() -> CategorizerService:
    global _service
    if _service is None:
        _service = CategorizerService()
    return _service


def reset_categorizer() -> None:
    """Force a reload (after training a new version)."""
    global _service
    _service = None


# ---------------------------------------------------------------------------
# Persist a trained model into the registry
# ---------------------------------------------------------------------------


def register_trained_model(
    model,
    tokenizer,
    metrics: Dict,
    training_metadata: Dict,
    activate: bool = True,
) -> ModelCard:
    """Save weights + tokenizer and record a model card."""
    version = make_version(
        "cat",
        {
            "base_model": training_metadata.get("base_model"),
            "n_train": training_metadata.get("n_train"),
            "epochs": training_metadata.get("epochs"),
            "seed": training_metadata.get("seed"),
            "labels": LABELS,
        },
    )

    with tempfile.TemporaryDirectory() as tmp:
        model.save_pretrained(tmp)
        tokenizer.save_pretrained(tmp)

        card = ModelCard(
            name=MODEL_NAME,
            version=version,
            task="text-classification",
            created_at=datetime.now(timezone.utc).isoformat(),
            base_model=str(training_metadata.get("base_model", "")),
            labels=LABELS,
            metrics=metrics,
            training_metadata=training_metadata,
        )
        get_registry().save(card, artifact_dir=Path(tmp), activate=activate)

    reset_categorizer()
    return card
