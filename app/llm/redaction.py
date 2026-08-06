"""
PII redaction before egress (PHASE 5).

Why this is a compliance problem and not a nicety:

* **DPDP Act 2023** makes FinGuru a Data Fiduciary for its users' personal
  data. Sending account numbers and names to a third-party LLM is a transfer to
  a Data Processor that must be purpose-limited and minimised. "The model
  needed the context" is not a defence for shipping a full account number.
* **RBI data-localisation** (the 2018 storage directive, and the Master
  Direction on outsourcing of IT services) constrains where payment and
  financial data may be processed. A US-hosted inference endpoint is exactly
  the case those rules are about.

So the pipeline is: **tokenise out -> call the model -> re-hydrate**. The model
reasons over ``[ACCT_1]`` and ``[MERCHANT_3]``; the user reads
``XXXX4172`` and ``SWIGGY``. The mapping lives in-process for the duration of
one request and is never persisted.

Indian narration formats are the hard part and are handled explicitly:
``UPI/P2M/417293847562/SWIGGY``, ``IMPS/P2A/9876543210/RAHUL``,
``NEFT-HDFC0000123-SALARY``, ``ACH DR/HDFCBANK/EMI``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Pattern, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RedactionRule:
    """One named pattern plus the placeholder family it maps to."""

    name: str
    token_prefix: str
    pattern: Pattern[str]
    # Which regex group holds the value to replace (0 = whole match).
    group: int = 0


def _c(p: str) -> Pattern[str]:
    return re.compile(p, re.IGNORECASE)


# Order matters: the most specific structures are matched first so a bank
# account number is not first eaten by the generic long-digit rule.
RULES: List[RedactionRule] = [
    # --- Contact (before VPA: an email is a VPA-shaped string with a TLD, so
    # matching VPA first would leave a dangling ".co.in" behind) -----------
    RedactionRule(
        "email",
        "EMAIL",
        _c(r"\b[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b"),
    ),
    # --- UPI ------------------------------------------------------------
    # UPI VPA: name@bank (no TLD, e.g. asha@okaxis)
    RedactionRule("upi_vpa", "VPA", _c(r"\b[a-z0-9._-]{2,64}@(?:ok[a-z]+|[a-z]{2,20})\b")),
    # UPI reference / RRN inside a narration: UPI/P2M/417293847562/SWIGGY
    RedactionRule(
        "upi_ref",
        "UPIREF",
        _c(r"(?<=/)\d{9,18}(?=/|\b)"),
    ),
    # Indian mobile numbers, before the generic account rule so a 10-digit
    # mobile is labelled PHONE rather than ACCT.
    RedactionRule(
        "phone",
        "PHONE",
        _c(r"(?:\+91[\s-]?)?\b[6-9]\d{9}\b"),
    ),
    # --- Card / account -------------------------------------------------
    # Two fixed-shape alternatives rather than `(?:\d[ -]?){13,19}`. That
    # quantified-optional form backtracks exponentially on a long digit run
    # that ultimately fails to match -- a ReDoS reachable from any merchant
    # who can set a narration, which is precisely the input this module exists
    # to handle. Every pattern here must be linear-time on hostile input.
    RedactionRule(
        "card_number",
        "CARD",
        _c(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,7}\b|\b\d{13,19}\b"),
    ),
    # Indian bank account numbers are 9-18 digits.
    RedactionRule("account_number", "ACCT", _c(r"\b\d{9,18}\b")),
    RedactionRule("ifsc", "IFSC", _c(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    # --- Government identifiers ----------------------------------------
    RedactionRule("pan", "PAN", _c(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    RedactionRule("aadhaar", "AADHAAR", _c(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    # --- Already-masked forms still identify an account -----------------
    RedactionRule("masked_account", "ACCT", _c(r"\bX{2,}\d{2,6}\b")),
]

# Merchant / counterparty names are handled separately: they are pulled from
# the *structured* narration segments rather than guessed, because a blanket
# capitalised-word rule would redact "Groceries" and leave the model unable to
# reason at all.
_NARRATION_SPLIT = _c(r"[/|\-]")

# Rail names, posting codes and transaction-type words. These carry no identity
# and are exactly the signal the model needs in order to reason, so they are
# preserved. Everything else in a name position is treated as a counterparty.
_STRUCTURAL_TOKENS = {
    # rails and posting codes
    "UPI",
    "P2M",
    "P2A",
    "IMPS",
    "NEFT",
    "RTGS",
    "ACH",
    "DR",
    "CR",
    "ECS",
    "ATM",
    "POS",
    "NACH",
    "MMT",
    "INF",
    "TPT",
    "CHQ",
    "BIL",
    "SI",
    "REV",
    "REF",
    "TXN",
    "TRF",
    "CMS",
    "IB",
    "MB",
    "OW",
    "IW",
    "RTN",
    # transaction-type words
    "PAYMENT",
    "TRANSFER",
    "DEBIT",
    "CREDIT",
    "COLLECT",
    "PAY",
    "SENT",
    "RECEIVED",
    "PURCHASE",
    "WITHDRAWAL",
    "DEPOSIT",
    "REFUND",
    "REVERSAL",
    "CHARGE",
    "CHARGES",
    "FEE",
    "FEES",
    "CHARGEBACK",
    "AUTOPAY",
    "MANDATE",
    # category-bearing words worth keeping
    "SALARY",
    "EMI",
    "RENT",
    "INTEREST",
    "DIVIDEND",
    "BILL",
    "RECHARGE",
    "INSURANCE",
    "PREMIUM",
    "TAX",
    "GST",
    "TDS",
    "CASHBACK",
    "REWARD",
    # currency / filler
    "INR",
    "RS",
    "BY",
    "TO",
    "FROM",
    "AT",
    "FOR",
    "VIA",
    "AND",
    "THE",
}

_NAME_TOKEN = re.compile(r"^[A-Za-z][A-Za-z.&']{1,30}$")


@dataclass
class RedactionResult:
    """Redacted text plus everything needed to re-hydrate it."""

    text: str
    # placeholder -> original value
    mapping: Dict[str, str] = field(default_factory=dict)
    counts: Dict[str, int] = field(default_factory=dict)

    @property
    def redaction_count(self) -> int:
        return len(self.mapping)


class Redactor:
    """
    Stateful redactor for one request.

    Stateful on purpose: the same account number appearing in twenty
    transactions must map to the same ``[ACCT_1]`` every time, or the model
    cannot tell that they are the same account and its reasoning degrades.
    """

    def __init__(self, redact_merchants: bool = True) -> None:
        self._forward: Dict[str, str] = {}  # original -> placeholder
        self._reverse: Dict[str, str] = {}  # placeholder -> original
        self._counters: Dict[str, int] = {}
        self._counts: Dict[str, int] = {}
        self.redact_merchants = redact_merchants

    # -- internals -------------------------------------------------------
    def _placeholder_for(self, value: str, prefix: str, original: Optional[str] = None) -> str:
        """
        Map a value to a stable placeholder.

        ``value`` is the *lookup key* and ``original`` is what rehydration
        restores. They differ for merchants, which are keyed on an uppercased
        form so "PhonePe" and "PHONEPE" collapse to one placeholder -- but
        rehydrating to the uppercase form would corrupt the answer shown to the
        user, turning "Ramesh Kumar" into "RAMESH KUMAR". Keying and restoring
        are therefore separate concerns.
        """
        if value in self._forward:
            return self._forward[value]
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        placeholder = f"[{prefix}_{self._counters[prefix]}]"
        self._forward[value] = placeholder
        self._reverse[placeholder] = value if original is None else original
        return placeholder

    def _apply_rule(self, text: str, rule: RedactionRule) -> str:
        def _sub(match: re.Match) -> str:
            value = match.group(rule.group)
            if not value or value.startswith("["):
                return match.group(0)
            self._counts[rule.name] = self._counts.get(rule.name, 0) + 1
            placeholder = self._placeholder_for(value, rule.token_prefix)
            whole = match.group(0)
            if rule.group == 0:
                return placeholder
            return whole.replace(value, placeholder)

        return rule.pattern.sub(_sub, text)

    def _redact_merchant_segments(self, narration: str) -> str:
        """
        Replace counterparty names in Indian narrations.

        Works at word level inside each delimiter-separated segment, because
        real narrations mix both forms: ``UPI/P2M/[UPIREF_1]/SWIGGY`` is
        slash-delimited, while ``POS [CARD_1] AMAZON`` and ``ACH DR/HDFCBANK``
        are not. Splitting only on delimiters missed the first kind and
        swallowed whole protocol prefixes ("ACH DR") in the second.

        Any maximal run of name-like words that is not a known rail or
        transaction-type keyword is treated as a counterparty. Over-redaction
        is the safe direction here and costs nothing: categorisation is done by
        FinGuru's own local model on the *unredacted* narration, and the LLM
        receives the resulting category label separately. The LLM never needed
        the merchant string, only a stable identity for it.
        """
        out = narration
        for segment in (s for s in _NARRATION_SPLIT.split(narration) if s.strip()):
            words = segment.split()
            run: List[str] = []

            def flush(run: List[str]) -> None:
                if not run:
                    return
                phrase = " ".join(run)
                upper = phrase.upper()
                self._counts["merchant"] = self._counts.get("merchant", 0) + 1
                placeholder = self._placeholder_for(upper, "MERCHANT", original=phrase)
                nonlocal out
                out = re.sub(rf"(?<![\w\[]){re.escape(phrase)}(?![\w\]])", placeholder, out)

            for word in words:
                token = word.strip(",;:()")
                if (
                    token
                    and not token.startswith("[")
                    and _NAME_TOKEN.match(token)
                    and token.upper() not in _STRUCTURAL_TOKENS
                ):
                    run.append(token)
                else:
                    flush(run)
                    run = []
            flush(run)
        return out

    # -- public API ------------------------------------------------------
    # Bank narrations are short; anything longer is either a bug or an attempt
    # to make regex scanning expensive. Truncating bounds the work regardless
    # of how the patterns evolve.
    MAX_INPUT_CHARS = 4096

    def redact(self, text: str, is_narration: bool = False) -> str:
        """Redact one string, reusing placeholders across calls."""
        if not text:
            return text
        if len(text) > self.MAX_INPUT_CHARS:
            logger.warning(
                f"Truncating {len(text)}-char input to {self.MAX_INPUT_CHARS} before redaction"
            )
            text = text[: self.MAX_INPUT_CHARS]
        out = text
        for rule in RULES:
            out = self._apply_rule(out, rule)
        if is_narration and self.redact_merchants:
            out = self._redact_merchant_segments(out)
        return out

    def redact_name(self, name: str) -> str:
        """Redact a known person/holder name (not pattern-guessable)."""
        if not name or not name.strip():
            return name
        return self._placeholder_for(name.strip().upper(), "NAME")

    def rehydrate(self, text: str) -> str:
        """Restore original values in a model response."""
        if not text:
            return text
        out = text
        # Longest placeholders first so [ACCT_10] is not clobbered by [ACCT_1].
        for placeholder in sorted(self._reverse, key=len, reverse=True):
            out = out.replace(placeholder, self._reverse[placeholder])
        return out

    def result(self, text: str) -> RedactionResult:
        return RedactionResult(text=text, mapping=dict(self._reverse), counts=dict(self._counts))

    @property
    def mapping(self) -> Dict[str, str]:
        return dict(self._reverse)

    @property
    def counts(self) -> Dict[str, int]:
        return dict(self._counts)

    def clear(self) -> None:
        """Drop the mapping. Called as soon as the response is rendered."""
        self._forward.clear()
        self._reverse.clear()
        self._counters.clear()
        self._counts.clear()


# ---------------------------------------------------------------------------
# Verification helpers -- used by tests and by the egress guard
# ---------------------------------------------------------------------------

# Patterns that must NEVER appear in an outbound prompt. This is the assertion
# behind definition-of-done #6.
_LEAK_PATTERNS: List[Tuple[str, Pattern[str]]] = [
    ("account_number", _c(r"\b\d{9,18}\b")),
    ("card_number", _c(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,7}\b|\b\d{13,19}\b")),
    ("aadhaar", _c(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("pan", _c(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("ifsc", _c(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("email", _c(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")),
    ("phone", _c(r"(?:\+91[\s-]?)?\b[6-9]\d{9}\b")),
]


def find_pii_leaks(text: str) -> List[Tuple[str, str]]:
    """
    Return ``(kind, value)`` for every PII-looking token still present.

    Placeholders are skipped, so ``[ACCT_1]`` is not reported as a leak.
    """
    if not text:
        return []
    # Blank out placeholders before scanning.
    scrubbed = re.sub(r"\[[A-Z]+_\d+\]", " ", text)
    leaks: List[Tuple[str, str]] = []
    for kind, pattern in _LEAK_PATTERNS:
        for match in pattern.finditer(scrubbed):
            value = match.group(0).strip()
            if value:
                leaks.append((kind, value))
    return leaks


def assert_no_pii(text: str, context: str = "prompt") -> None:
    """
    Fail closed if PII survived redaction.

    Raises rather than logging, because the alternative -- sending it anyway
    and noting the problem -- is the exact outcome the control exists to
    prevent.
    """
    leaks = find_pii_leaks(text)
    if leaks:
        kinds = sorted({k for k, _ in leaks})
        raise PIIEgressError(
            f"Refusing to send {context} to the LLM provider: "
            f"{len(leaks)} unredacted PII token(s) detected ({', '.join(kinds)})"
        )


class PIIEgressError(RuntimeError):
    """Raised when unredacted PII is detected on an outbound path."""


def new_request_redactor() -> Redactor:
    """Factory -- one redactor per request, never shared or reused."""
    return Redactor()
