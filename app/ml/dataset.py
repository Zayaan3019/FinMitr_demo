"""
Transaction-narration dataset for the categoriser (PHASE 4).

Indian bank narrations are messy in specific, learnable ways, and that
messiness is the reason a trained model beats the keyword table this project
started with:

    UPI/P2M/417293847562/SWIGGY
    UPI/P2A/asha@okaxis/RAHUL S/Payment from Ph
    POS 4532XXXXXXXX0366 BIGBASKET BENGALURU
    ACH DR/HDFCBANK/HDFC LIFE INS PREM
    NEFT-SBIN0001234-ACME TECHNOLOGIES PVT LTD-SALARY MAR
    IMPS/311412345678/RENT/MAR
    ATM WDL 1234 ANDHERI W MUMBAI
    MMT/IMPS/318923456789/Payment from/ASHA/HDFC

A keyword rule fails here because the merchant token sits at a different
position in every rail's format, is frequently abbreviated
(``BIGBSKT``, ``SWGY``), and collides across categories (``PAYTM`` is a wallet
top-up, a bill payment, or a merchant purchase depending on the suffix).

Each row carries a ``txn_date``, which is what makes the temporal split in
``evaluate.py`` possible. A random split would leak: the same recurring EMI
narration appears every month, so a random 80/20 split puts near-duplicates of
the same merchant on both sides and reports an F1 that collapses in production.

Distribution drift is injected deliberately in the later months (new merchants,
a shift toward UPI, rising amounts) so PSI monitoring has something real to
detect.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)


# Canonical label set. Matches the ``categories`` seed in migration 0001.
LABELS: List[str] = [
    "groceries",
    "dining",
    "transport",
    "utilities",
    "rent",
    "shopping",
    "entertainment",
    "healthcare",
    "salary",
    "investment",
    "insurance",
    "loan_emi",
    "transfer",
    "fees_charges",
]

LABEL_TO_ID = {label: i for i, label in enumerate(LABELS)}
ID_TO_LABEL = {i: label for label, i in LABEL_TO_ID.items()}


# Merchants per category, including the abbreviated forms banks actually emit.
MERCHANTS: Dict[str, List[str]] = {
    "groceries": [
        "BIGBASKET",
        "BIGBSKT",
        "DMART",
        "RELIANCE FRESH",
        "MORE RETAIL",
        "ZEPTO",
        "BLINKIT",
        "GROFERS",
        "SPENCERS",
        "NATURES BASKET",
        "STAR BAZAAR",
        "JIOMART",
        "LICIOUS",
    ],
    "dining": [
        "SWIGGY",
        "SWGY",
        "ZOMATO",
        "ZOMATO LTD",
        "DOMINOS",
        "MCDONALDS",
        "KFC INDIA",
        "CAFE COFFEE DAY",
        "BARBEQUE NATION",
        "HALDIRAMS",
        "STARBUCKS",
        "BURGER KING",
        "FAASOS",
        "BOX8",
    ],
    "transport": [
        "OLA",
        "OLACABS",
        "UBER INDIA",
        "RAPIDO",
        "IRCTC",
        "IRCTC WEB",
        "INDIAN OIL",
        "HP PETROL PUMP",
        "BHARAT PETROLEUM",
        "FASTAG RECHARGE",
        "NHAI FASTAG",
        "REDBUS",
        "BMTC",
        "DMRC METRO",
    ],
    "utilities": [
        "TATA POWER",
        "BESCOM",
        "MSEDCL",
        "ADANI ELECTRICITY",
        "AIRTEL",
        "JIO RECHARGE",
        "VODAFONE IDEA",
        "ACT FIBERNET",
        "BSNL",
        "MAHANAGAR GAS",
        "INDANE GAS",
        "BWSSB",
    ],
    "rent": [
        "NOBROKER RENT",
        "HOUSE RENT",
        "RENTPAY",
        "LANDLORD",
        "PG RENT",
        "NESTAWAY",
        "COLIVE",
    ],
    "shopping": [
        "AMAZON",
        "AMAZON IN",
        "FLIPKART",
        "MYNTRA",
        "AJIO",
        "NYKAA",
        "CROMA",
        "RELIANCE DIGITAL",
        "DECATHLON",
        "IKEA",
        "MEESHO",
        "TATA CLIQ",
        "LENSKART",
    ],
    "entertainment": [
        "NETFLIX",
        "HOTSTAR",
        "DISNEY HOTSTAR",
        "SPOTIFY",
        "BOOKMYSHOW",
        "PVR CINEMAS",
        "INOX",
        "SONYLIV",
        "ZEE5",
        "PRIME VIDEO",
        "JIOSAAVN",
    ],
    "healthcare": [
        "APOLLO PHARMACY",
        "PHARMEASY",
        "1MG",
        "NETMEDS",
        "PRACTO",
        "FORTIS HOSPITAL",
        "MANIPAL HOSPITAL",
        "DR LAL PATHLABS",
        "THYROCARE",
        "MEDPLUS",
    ],
    "salary": [
        "ACME TECHNOLOGIES PVT LTD",
        "INFOSYS LTD",
        "TCS LIMITED",
        "WIPRO LTD",
        "SALARY CREDIT",
        "PAYROLL",
        "HCL TECH",
    ],
    "investment": [
        "ZERODHA",
        "GROWW",
        "UPSTOX",
        "ICICI DIRECT",
        "HDFC SEC",
        "SIP MUTUAL FUND",
        "NIPPON INDIA MF",
        "SBI MUTUAL FUND",
        "NPS TRUST",
        "PPF DEPOSIT",
    ],
    "insurance": [
        "HDFC LIFE INS PREM",
        "LIC OF INDIA",
        "ICICI PRU LIFE",
        "STAR HEALTH",
        "BAJAJ ALLIANZ",
        "TATA AIG",
        "MAX LIFE",
        "NIVA BUPA",
    ],
    "loan_emi": [
        "HDFC BANK EMI",
        "BAJAJ FINSERV",
        "HOME LOAN EMI",
        "CAR LOAN EMI",
        "PERSONAL LOAN EMI",
        "IDFC FIRST EMI",
        "TATA CAPITAL",
    ],
    "transfer": [
        "RAHUL SHARMA",
        "PRIYA IYER",
        "SELF TRANSFER",
        "ASHA MENON",
        "VIKRAM SINGH",
        "NEHA GUPTA",
        "OWN ACCOUNT",
    ],
    "fees_charges": [
        "SMS CHARGES",
        "AMC CHARGES",
        "ATM DECLINE CHG",
        "MIN BAL CHARGE",
        "GST ON CHARGES",
        "ANNUAL FEE",
        "LATE PAYMENT FEE",
        "CHQ RETURN CHG",
    ],
}

# Amount ranges in paise: (low, high).
AMOUNT_RANGES: Dict[str, Tuple[int, int]] = {
    "groceries": (15_000, 450_000),
    "dining": (12_000, 250_000),
    "transport": (5_000, 300_000),
    "utilities": (30_000, 500_000),
    "rent": (800_000, 6_000_000),
    "shopping": (30_000, 1_500_000),
    "entertainment": (14_900, 150_000),
    "healthcare": (20_000, 900_000),
    "salary": (4_000_000, 25_000_000),
    "investment": (100_000, 5_000_000),
    "insurance": (200_000, 4_000_000),
    "loan_emi": (500_000, 5_000_000),
    "transfer": (50_000, 3_000_000),
    "fees_charges": (1_180, 59_000),
}

CREDIT_LABELS = {"salary"}

# Merchants that only start appearing in the drift window, so PSI has a real
# distribution change to find rather than sampling noise.
DRIFT_MERCHANTS: Dict[str, List[str]] = {
    "dining": ["EATCLUB", "CUREFOODS", "THIRD WAVE COFFEE"],
    "shopping": ["ZUDIO", "SHEIN IN", "FIRSTCRY"],
    "transport": ["BLUSMART", "SHUTTL", "YULU BIKES"],
    "investment": ["INDMONEY", "SMALLCASE", "COIN BY ZERODHA"],
}


def _rrn(rng: random.Random) -> str:
    """A 12-digit UPI retrieval reference number."""
    return "".join(str(rng.randint(0, 9)) for _ in range(12))


def _acct(rng: random.Random) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(rng.randint(11, 16)))


def _vpa(rng: random.Random) -> str:
    names = ["asha", "rahul", "priya.iyer", "vikram99", "neha.g", "s.menon"]
    banks = ["okaxis", "okhdfcbank", "oksbi", "ybl", "paytm", "upi", "ibl"]
    return f"{rng.choice(names)}@{rng.choice(banks)}"


def _ifsc(rng: random.Random) -> str:
    banks = ["HDFC", "SBIN", "ICIC", "UTIB", "KKBK", "PUNB", "IDFB"]
    return f"{rng.choice(banks)}0{rng.randint(100000, 999999)}"


def _city(rng: random.Random) -> str:
    return rng.choice(
        [
            "MUMBAI",
            "BENGALURU",
            "PUNE",
            "CHENNAI",
            "HYDERABAD",
            "GURGAON",
            "NOIDA",
            "KOLKATA",
            "ANDHERI W",
            "KORAMANGALA",
            "INDIRANAGAR",
        ]
    )


# Narration templates per rail. `{m}` is the merchant token.
def _render(rng: random.Random, merchant: str, label: str) -> str:
    """Emit one narration in a randomly chosen rail format."""
    rail = rng.choices(
        ["upi_p2m", "upi_p2a", "pos", "ach", "neft", "imps", "atm", "mmt", "bil"],
        weights=[34, 10, 16, 8, 8, 8, 4, 6, 6],
    )[0]

    if rail == "upi_p2m":
        suffix = rng.choice(["", f"/{_city(rng)}", "/Payment", f"/{_vpa(rng)}"])
        return f"UPI/P2M/{_rrn(rng)}/{merchant}{suffix}"
    if rail == "upi_p2a":
        return f"UPI/P2A/{_vpa(rng)}/{merchant}/Payment from Ph"
    if rail == "pos":
        masked = f"{rng.randint(4000, 5599)}XXXXXXXX{rng.randint(1000, 9999)}"
        return f"POS {masked} {merchant} {_city(rng)}"
    if rail == "ach":
        bank = rng.choice(["HDFCBANK", "ICICIBANK", "SBIBANK", "AXISBANK"])
        return f"ACH DR/{bank}/{merchant}"
    if rail == "neft":
        month = rng.choice(["JAN", "FEB", "MAR", "APR", "MAY", "JUN"])
        return f"NEFT-{_ifsc(rng)}-{merchant}-{month}"
    if rail == "imps":
        return f"IMPS/{_acct(rng)}/{merchant}/{rng.choice(['MAR', 'APR', 'PAYMENT'])}"
    if rail == "atm":
        return f"ATM WDL {rng.randint(1000, 9999)} {merchant} {_city(rng)}"
    if rail == "mmt":
        return f"MMT/IMPS/{_rrn(rng)}/Payment to/{merchant}/{rng.choice(['HDFC', 'SBI', 'AXIS'])}"
    return f"BIL/ONL/{_rrn(rng)[:9]}/{merchant}/{_city(rng)}"


def _noise(rng: random.Random, text: str) -> str:
    """Apply the corruptions banks actually introduce."""
    roll = rng.random()
    if roll < 0.06:
        return text[: rng.randint(18, max(19, len(text) - 1))]  # column truncation
    if roll < 0.11:
        return text.lower()
    if roll < 0.15:
        return text.replace("/", " ")
    if roll < 0.18:
        return " ".join(text.split())  # collapse padding
    if roll < 0.21:
        return text + "  "
    return text


@dataclass
class DatasetSpec:
    """Generation parameters."""

    n_rows: int = 12_000
    start: date = date(2024, 7, 1)
    end: date = date(2026, 6, 30)
    seed: int = 20260801
    # Fraction of the timeline (from the end) where drift is injected.
    drift_fraction: float = 0.25


def generate(spec: Optional[DatasetSpec] = None) -> pd.DataFrame:
    """
    Build a labelled narration dataset.

    Returns a DataFrame with ``txn_date``, ``narration``, ``label``,
    ``amount_minor`` and ``label_id``, sorted by date.
    """
    spec = spec or DatasetSpec()
    rng = random.Random(spec.seed)

    span_days = (spec.end - spec.start).days
    if span_days <= 0:
        raise ValueError("DatasetSpec.end must be after .start")
    drift_start_day = int(span_days * (1.0 - spec.drift_fraction))

    # Base class mix. Not uniform -- real spending is dominated by a few
    # categories, and a model evaluated on a uniform set would look better
    # than it is.
    base_weights = {
        "groceries": 14,
        "dining": 16,
        "transport": 12,
        "utilities": 8,
        "rent": 3,
        "shopping": 13,
        "entertainment": 6,
        "healthcare": 5,
        "salary": 3,
        "investment": 5,
        "insurance": 3,
        "loan_emi": 4,
        "transfer": 6,
        "fees_charges": 2,
    }

    rows: List[Dict] = []
    for _ in range(spec.n_rows):
        day_offset = rng.randint(0, span_days)
        txn_date = spec.start + timedelta(days=day_offset)
        in_drift = day_offset >= drift_start_day

        weights = dict(base_weights)
        if in_drift:
            # Post-drift behaviour: more food delivery and investing, less cash.
            weights["dining"] += 6
            weights["investment"] += 4
            weights["transport"] += 2
            weights["groceries"] = max(1, weights["groceries"] - 4)

        label = rng.choices(list(weights), weights=list(weights.values()))[0]

        pool = list(MERCHANTS[label])
        if in_drift and label in DRIFT_MERCHANTS:
            pool += DRIFT_MERCHANTS[label] * 3  # new merchants become common
        merchant = rng.choice(pool)

        narration = _noise(rng, _render(rng, merchant, label))

        low, high = AMOUNT_RANGES[label]
        if in_drift:
            high = int(high * 1.25)  # inflation-like amount drift
        amount = rng.randint(low, high)
        if label not in CREDIT_LABELS:
            amount = -amount

        rows.append(
            {
                "txn_date": txn_date,
                "narration": narration,
                "label": label,
                "label_id": LABEL_TO_ID[label],
                "amount_minor": amount,
                "in_drift_window": in_drift,
            }
        )

    df = pd.DataFrame(rows).sort_values("txn_date").reset_index(drop=True)
    logger.info(
        f"Generated {len(df):,} narrations across {df['label'].nunique()} labels "
        f"({df['txn_date'].min()} to {df['txn_date'].max()})"
    )
    return df


def temporal_split(
    df: pd.DataFrame, train_frac: float = 0.7, val_frac: float = 0.15
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Split by time, never at random.

    Cut points are chosen on the *date* axis, so no transaction from the test
    period can influence training. This is the only split that answers the
    question production actually asks -- "how will the model do on next month's
    transactions?" -- and it is strictly harder than a random split because the
    test period contains merchants the model has never seen.
    """
    if not 0 < train_frac < 1 or not 0 <= val_frac < 1:
        raise ValueError("Invalid split fractions")
    if train_frac + val_frac >= 1:
        raise ValueError("train_frac + val_frac must leave room for a test set")

    ordered = df.sort_values("txn_date").reset_index(drop=True)
    dates = ordered["txn_date"]

    train_cut = dates.quantile(train_frac, interpolation="nearest")
    val_cut = dates.quantile(train_frac + val_frac, interpolation="nearest")

    train = ordered[dates <= train_cut]
    val = ordered[(dates > train_cut) & (dates <= val_cut)]
    test = ordered[dates > val_cut]

    logger.info(
        f"Temporal split -- train: {len(train):,} (<= {train_cut}), "
        f"val: {len(val):,} (<= {val_cut}), test: {len(test):,} (> {val_cut})"
    )
    return (
        train.reset_index(drop=True),
        val.reset_index(drop=True),
        test.reset_index(drop=True),
    )


def random_split_for_comparison(
    df: pd.DataFrame, train_frac: float = 0.7, val_frac: float = 0.15, seed: int = 7
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    A random split, provided *only* so the leakage it causes can be measured.

    ``evaluate.py`` reports both. The gap between random-split F1 and
    temporal-split F1 is the size of the leak, and quoting the random number as
    a production estimate is the mistake this function exists to expose.
    """
    shuffled = df.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    n = len(shuffled)
    a, b = int(n * train_frac), int(n * (train_frac + val_frac))
    return shuffled[:a], shuffled[a:b].reset_index(drop=True), shuffled[b:].reset_index(drop=True)


def label_distribution(df: pd.DataFrame) -> Dict[str, float]:
    """Class prior -- always report it next to any accuracy number."""
    counts = df["label"].value_counts(normalize=True)
    return {str(k): round(float(v), 4) for k, v in counts.items()}
