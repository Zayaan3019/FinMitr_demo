"""
DEFINITION OF DONE #6 -- no account number reaches the LLM provider, in any trace.

The claim under test is narrow and absolute: whatever the user asks and
whatever the bank narration contains, the bytes handed to the model provider
contain no account number, card number, VPA, PAN, Aadhaar, phone or email.

Why it matters beyond good hygiene: sending a customer's account identifiers to
a third-party inference API is a cross-border disclosure of personal data.
Under the **Digital Personal Data Protection Act 2023** that is processing by a
Data Processor outside the consent the user gave for financial advice, and RBI's
2018 data-localisation directive requires payment-system data to be stored in
India -- a US-hosted inference endpoint is not that. The redaction layer is what
makes an LLM feature compatible with both.

The tests are adversarial on purpose. It is easy to write a redaction test that
passes: feed it the format the regex was written for. These use real Indian
narration shapes across nine payment rails, and the egress assertion is the
*independent* check -- a second scan of the exact payload, so a rule that
matches for redaction but not for detection cannot hide a leak.
"""

from __future__ import annotations

import re

import pytest

# Real-shaped Indian bank narrations. These are the formats an FIU actually
# receives from an Account Aggregator; a redactor tuned to Western statement
# text fails on every one of them.
NARRATIONS = [
    "UPI/P2M/417293856201/SWIGGY/HDFC0000123",
    "UPI/P2A/223344556677/rahul.sharma@okhdfcbank/Rent",
    "POS 4532015112830366 AMAZON RETAIL IN",
    "ACH DR SBI CARD 000123456789012 AUTOPAY",
    "NEFT-HDFC0001234-987654321012345-SALARY CREDIT",
    "IMPS/912345678901/9876543210/JIO RECHARGE",
    "ATM WDL 5241 88 XXXXXXXXXXXX4491 MUMBAI",
    "MMT/IMPS/402912345678/PhonePe/priya@ybl",
    "BIL/BBPS/ADANIELEC/100200300400/AUG",
    "TO TRANSFER INB Ramesh Kumar 50100234567890 PAN ABCDE1234F",
    "REFUND ORDER 8801234567 CARD 4111 1111 1111 1111",
    "SI DEBIT LIC PREMIUM AADHAAR 2345 6789 0123 POLICY 987654321",
    "PAYMENT TO ramesh.k+bills@gmail.com VIA UPI 9123456789",
]

# Independent of the redactor's own rules. If these two ever disagree, the
# disagreement is the finding.
LEAK_PATTERNS = {
    "account_number_12_18": re.compile(r"\b\d{12,18}\b"),
    "card_number_spaced": re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b"),
    "card_number_solid": re.compile(r"\b\d{13,19}\b"),
    "upi_vpa": re.compile(r"\b[\w.+-]{2,}@(?:ok\w+|ybl|paytm|upi|axl|ibl|apl)\b", re.I),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "aadhaar": re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b"),
    "phone_in": re.compile(r"\b(?:\+?91[- ]?)?[6-9]\d{9}\b"),
    "ifsc": re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"),
}


def scan_for_leaks(text: str) -> dict[str, list[str]]:
    """Independent leak scan. Returns {rule: [matches]} for anything found."""
    found = {}
    for name, pattern in LEAK_PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            found[name] = matches
    return found


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("narration", NARRATIONS)
def test_no_identifier_survives_redaction(narration):
    """Every narration format, checked one at a time so failures name the rail."""
    from app.llm.redaction import Redactor

    # is_narration=True enables merchant-segment handling, which is how the
    # application calls it for bank narrations.
    redacted = Redactor().redact(narration, is_narration=True)
    leaks = scan_for_leaks(redacted)

    assert not leaks, (
        f"PII survived redaction.\n"
        f"  input:    {narration}\n"
        f"  redacted: {redacted}\n"
        f"  leaked:   {leaks}"
    )


def test_redaction_is_reversible():
    """
    Tokens must re-hydrate exactly.

    Redaction that loses information would force the answer to reference
    '<ACCOUNT_1>' back to the user. The placeholder is stable per entity within
    a request, so the model can still reason about "the same account" without
    ever seeing which one.
    """
    from app.llm.redaction import Redactor

    for narration in NARRATIONS:
        redactor = Redactor()
        redacted = redactor.redact(narration, is_narration=True)
        assert redactor.rehydrate(redacted) == narration, (
            f"round-trip lost data:\n  in:  {narration}\n  out: " f"{redactor.rehydrate(redacted)}"
        )


def test_the_same_entity_gets_the_same_placeholder():
    """
    Stable mapping. If one account number became ACCOUNT_1 in one sentence and
    ACCOUNT_7 in the next, the model could not tell that two transactions share
    an account -- which is most of what makes the analysis useful.
    """
    from app.llm.redaction import Redactor

    redactor = Redactor()
    a = redactor.redact("NEFT to 50100234567890 for rent")
    b = redactor.redact("NEFT to 50100234567890 for maintenance")

    token_a = re.search(r"\[[A-Z_]+_\d+\]", a)
    token_b = re.search(r"\[[A-Z_]+_\d+\]", b)
    assert token_a and token_b, f"no placeholder emitted: {a!r} / {b!r}"
    assert token_a.group() == token_b.group(), (
        f"the same account produced different placeholders: "
        f"{token_a.group()} vs {token_b.group()}"
    )


def test_merchant_names_are_preserved():
    """
    Redaction must not be so aggressive that the answer becomes useless.

    "You spent a lot at <MERCHANT_3>" is not advice. Merchants are kept;
    identifiers are not.
    """
    from app.llm.redaction import Redactor

    # redact_merchants=False is how the analysis path calls it: identifiers
    # go, merchant names stay, because "you spent a lot at [MERCHANT_3]" is
    # not advice.
    redacted = Redactor(redact_merchants=False).redact(
        "UPI/P2M/417293856201/SWIGGY/HDFC0000123", is_narration=True
    )
    assert "SWIGGY" in redacted, f"merchant name was destroyed: {redacted}"


# ---------------------------------------------------------------------------
# The egress assertion
# ---------------------------------------------------------------------------


def test_the_egress_guard_raises_rather_than_sending():
    """
    Fail closed.

    ``assert_no_pii`` is the last gate before the network call. It must raise
    -- not log, not strip, not truncate -- because a partial send is a full
    disclosure.
    """
    from app.llm.redaction import PIIEgressError, assert_no_pii

    with pytest.raises(PIIEgressError):
        assert_no_pii("Please review account 50100234567890 for the user")

    # A properly redacted payload passes through untouched.
    assert_no_pii("Please review account [ACCT_1] for the user")


@pytest.mark.db
async def test_no_pii_reaches_the_provider_in_a_full_advisory_call(alice):
    """
    **The definition-of-done test: the trace itself.**

    The provider call is intercepted and every byte it would have sent is
    captured, then scanned independently. This is the difference between "we
    call a redactor" and "nothing leaked" -- it inspects the actual egress
    payload rather than trusting the layer above it.
    """
    captured: list[str] = []

    def fake_provider(system: str, user: str) -> str:
        """Stands in for the network call and records exactly what it was given."""
        captured.append(system)
        captured.append(user)
        # Shaped to satisfy the schema guard so the pipeline runs to the end.
        return (
            '{"summary": "Spending is concentrated in dining.", '
            '"findings": [{"claim": "Dining is the largest category.", '
            '"refs": ["T1"]}], '
            '"recommendations": [{"action": "Set a monthly cap.", "refs": ["T1"]}]}'
        )

    from app.llm.safe_client import SafeLLMClient, TransactionContext

    contexts = [
        TransactionContext(
            ref=f"T{i + 1}",
            txn_date="2026-03-14",
            amount_minor=-41200,
            narration=narration,
            category="food_delivery",
        )
        for i, narration in enumerate(NARRATIONS)
    ]

    await SafeLLMClient(llm_invoker=fake_provider).advise(
        user_id=alice.user_id,
        # The query is attacker-controlled too, and carries its own PII.
        query="Is my account 50100234567890 or card 4111 1111 1111 1111 overspending?",
        transactions=contexts,
        insights={"transactions_analysed": len(contexts)},
    )

    assert captured, (
        "the provider was never called -- the pipeline degraded before egress, "
        "so this test proved nothing"
    )

    egress = "\n".join(captured)
    leaks = scan_for_leaks(egress)
    assert not leaks, (
        f"PII REACHED THE PROVIDER. Leaked entities: {leaks}\n" f"--- payload ---\n{egress[:2000]}"
    )


# ---------------------------------------------------------------------------
# ReDoS
# ---------------------------------------------------------------------------


def test_redaction_is_linear_on_hostile_input():
    """
    Regression test for a real bug found in this codebase.

    The original ``card_number`` rule was ``\\b(?:\\d[ -]?){13,19}\\b``. On a
    long run of digits that ultimately fails to match, the optional separator
    creates exponential backtracking -- and a bank narration is
    attacker-controlled (name your UPI handle whatever you like). A single
    ingested transaction could pin a CPU core indefinitely.

    Fixed by two fixed-shape alternatives plus a 4096-character input cap.
    """
    import time

    from app.llm.redaction import Redactor

    hostile = "9" * 400 + "X"
    start = time.perf_counter()
    for _ in range(50):
        Redactor().redact(hostile)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, (
        f"50 redactions of a 400-digit hostile string took {elapsed:.2f}s -- "
        f"this is catastrophic backtracking, reachable from any narration"
    )


def test_oversized_input_is_capped_not_processed():
    """The cap is a control, not an optimisation: it bounds worst-case work."""
    from app.llm.redaction import Redactor

    redactor = Redactor()
    redacted = redactor.redact("A" * 100_000)
    assert (
        len(redacted) <= 4096 + 64
    ), f"input cap not enforced: {len(redacted)} characters passed through"
