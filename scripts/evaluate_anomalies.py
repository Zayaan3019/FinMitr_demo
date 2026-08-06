#!/usr/bin/env python
"""
Evaluate the anomaly detector against a labelled window.

**Why this script exists.** The API can return "your 10 most unusual
transactions" without any of this. What it cannot do is tell you whether those
10 are worth a user's attention, and a count of flagged transactions is not a
metric -- it is a consequence of where you set the threshold. Report 50
anomalies instead of 10 and the number doubles while the detector is unchanged.

The metric that means something is **precision@k, read against the base rate.**
If 2% of transactions are genuinely anomalous, a detector whose top 10 contains
2 true positives has precision@10 = 0.20 and lift = 10x: it is ten times better
than picking at random, which is a real claim. Without the base rate printed
next to it, 0.20 is unreadable -- it could be excellent or worse than chance.

Labels come from one of two places:

  --source db       the ``anomalies.reviewed`` / ``anomalies.is_true_positive``
                    columns, i.e. transactions a human has actually adjudicated
  --source synthetic  a generated window with known injected anomalies, for
                    when no review data exists yet

The synthetic mode is honest about what it is: it measures whether the detector
finds the kind of anomaly we injected, which is a weaker claim than finding the
kind real users care about. It is stated in the output rather than glossed.

Usage::

    python scripts/evaluate_anomalies.py --source synthetic --n 4000
    python scripts/evaluate_anomalies.py --source db --user-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.ml.anomaly_eval import (  # noqa: E402
    DETECTOR_VERSION,
    evaluate_detector,
    score_transactions,
)

# ---------------------------------------------------------------------------
# Synthetic labelled window
# ---------------------------------------------------------------------------


def synthetic_window(n: int, base_rate: float, seed: int) -> Tuple[pd.DataFrame, np.ndarray]:
    """
    Build a labelled window with a *stated* anomaly base rate.

    The normal population is log-normal in amount, which is how real spending
    is distributed -- lots of small transactions, a long right tail. Anomalies
    are injected in three shapes, because a detector that only catches one is
    not useful:

      1. amount outliers   -- 20-60x the median, e.g. a drained account
      2. odd-hour activity -- 1am-4am, when the user is asleep
      3. novel merchants   -- a counterparty never seen before

    Injecting only shape 1 would make the evaluation trivially easy: Isolation
    Forest finds amount outliers almost by construction.
    """
    rng = np.random.default_rng(seed)
    n_anomalies = max(1, int(round(n * base_rate)))
    n_normal = n - n_anomalies

    merchants = [
        "SWIGGY",
        "ZOMATO",
        "BIGBASKET",
        "AMAZON",
        "FLIPKART",
        "UBER",
        "OLA",
        "JIO",
        "AIRTEL",
        "DMART",
        "RELIANCE FRESH",
        "PVR",
        "APOLLO PHARMACY",
    ]
    today = date.today()

    rows = []
    for _ in range(n_normal):
        amount = -int(rng.lognormal(mean=6.2, sigma=0.9) * 100)
        rows.append(
            {
                "txn_date": today - timedelta(days=int(rng.integers(0, 180))),
                "amount_minor": amount,
                "narration": f"UPI/P2M/{rng.integers(10**11, 10**12)}/"
                f"{merchants[int(rng.integers(0, len(merchants)))]}",
                "hour": int(rng.integers(7, 23)),
                "is_anomaly": 0,
            }
        )

    median_normal = float(np.median([abs(r["amount_minor"]) for r in rows]))
    for i in range(n_anomalies):
        shape = i % 3
        if shape == 0:  # amount outlier
            amount = -int(median_normal * rng.uniform(20, 60))
            hour = int(rng.integers(7, 23))
            merchant = merchants[int(rng.integers(0, len(merchants)))]
        elif shape == 1:  # odd hour
            amount = -int(rng.lognormal(mean=7.0, sigma=0.7) * 100)
            hour = int(rng.integers(1, 5))
            merchant = merchants[int(rng.integers(0, len(merchants)))]
        else:  # novel merchant
            amount = -int(rng.lognormal(mean=7.5, sigma=0.8) * 100)
            hour = int(rng.integers(7, 23))
            merchant = f"UNKNOWN-MERCHANT-{rng.integers(1000, 9999)}"

        rows.append(
            {
                "txn_date": today - timedelta(days=int(rng.integers(0, 180))),
                "amount_minor": amount,
                "narration": f"UPI/P2M/{rng.integers(10**11, 10**12)}/{merchant}",
                "hour": hour,
                "is_anomaly": 1,
            }
        )

    df = pd.DataFrame(rows).sample(frac=1.0, random_state=seed).reset_index(drop=True)
    labels = df.pop("is_anomaly").to_numpy()
    df["category"] = "shopping"
    df["id"] = [str(uuid.uuid4()) for _ in range(len(df))]
    return df, labels


# ---------------------------------------------------------------------------
# Database-labelled window
# ---------------------------------------------------------------------------


async def db_window(
    user_id: Optional[uuid.UUID],
) -> Tuple[pd.DataFrame, np.ndarray]:
    """
    Load reviewed anomalies from PostgreSQL.

    A transaction counts as a positive only if a human reviewed it *and* marked
    it a true positive. Unreviewed rows are excluded rather than assumed
    negative -- treating "nobody looked" as "not an anomaly" would inflate
    precision by construction.
    """
    from sqlalchemy import text

    from app.db.session import system_session, tenant_session

    query = text("""
        SELECT t.id, t.txn_date, t.amount_minor, t.narration,
               COALESCE(c.slug, 'uncategorised') AS category,
               COALESCE(a.is_true_positive, false) AS is_anomaly
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        LEFT JOIN anomalies a ON a.txn_id = t.id AND a.reviewed = true
        WHERE a.id IS NOT NULL
        ORDER BY t.txn_date DESC
        LIMIT 20000
        """)

    ctx = tenant_session(user_id) if user_id else system_session()
    async with ctx as session:
        rows = (await session.execute(query)).all()

    if not rows:
        raise SystemExit(
            "No reviewed anomalies found.\n"
            "precision@k needs adjudicated labels; there is no way to compute "
            "it from unreviewed flags, and reporting a raw anomaly count in "
            "its place would be exactly the substitution this script exists to "
            "avoid.\n"
            "Use --source synthetic until review data accumulates."
        )

    df = pd.DataFrame(
        [
            {
                "id": str(r[0]),
                "txn_date": r[1],
                "amount_minor": int(r[2]),
                "narration": r[3],
                "category": r[4],
                "is_anomaly": int(bool(r[5])),
            }
            for r in rows
        ]
    )
    labels = df.pop("is_anomaly").to_numpy()
    return df, labels


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["synthetic", "db"], default="synthetic")
    parser.add_argument("--n", type=int, default=4000, help="synthetic window size")
    parser.add_argument(
        "--base-rate",
        type=float,
        default=0.02,
        help="synthetic anomaly base rate (default 2%%, a realistic figure for "
        "retail banking fraud review queues)",
    )
    parser.add_argument("--seed", type=int, default=20260801)
    parser.add_argument("--user-id", type=str, default=None)
    parser.add_argument("--ks", type=int, nargs="+", default=[10, 20, 50, 100])
    parser.add_argument("--out", type=str, default=None, help="write JSON here")
    args = parser.parse_args()

    if args.source == "synthetic":
        df, labels = synthetic_window(args.n, args.base_rate, args.seed)
        provenance = (
            f"SYNTHETIC window: {args.n:,} transactions, injected base rate "
            f"{args.base_rate:.2%}, seed {args.seed}. This measures whether the "
            f"detector finds the anomaly shapes we injected (amount outliers, "
            f"odd-hour activity, novel merchants). That is a weaker claim than "
            f"finding the anomalies real users care about."
        )
    else:
        user_id = uuid.UUID(args.user_id) if args.user_id else None
        df, labels = asyncio.run(db_window(user_id))
        provenance = (
            f"DB window: {len(df):,} human-reviewed transactions. Positives are "
            f"rows an analyst marked as true anomalies; unreviewed rows are "
            f"excluded rather than assumed negative."
        )

    scores = score_transactions(df)
    window = (str(df["txn_date"].min()), str(df["txn_date"].max()))
    evaluation = evaluate_detector(
        scores=scores,
        labels=labels,
        detector_version=DETECTOR_VERSION,
        ks=args.ks,
        window=window,
    )

    print("=" * 72)
    print("ANOMALY DETECTOR EVALUATION")
    print("=" * 72)
    print(provenance)
    print("-" * 72)
    print(evaluation.summary())
    print("-" * 72)
    print(
        "Read precision@k against the base rate above. Lift is the ratio of the\n"
        "two: lift 1.0 means the detector is no better than picking at random,\n"
        "whatever the raw precision looks like."
    )
    print("=" * 72)

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = evaluation.to_dict()
        payload["provenance"] = provenance
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {path}")

    # A detector with no lift over random is a failure, and the exit code
    # should say so if this ever runs in CI.
    top = evaluation.at_k[0] if evaluation.at_k else None
    if top and evaluation.n_positives and top.lift < 1.0:
        print(
            f"\nFAIL: lift@{top.k} = {top.lift:.2f}x -- worse than random selection",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
