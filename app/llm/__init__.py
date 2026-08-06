"""
LLM safety layer: PII redaction before egress, prompt-injection defence,
schema-validated structured output, grounding, and per-user token budgets.
"""

from app.llm.redaction import (
    PIIEgressError,
    Redactor,
    assert_no_pii,
    find_pii_leaks,
    new_request_redactor,
)
from app.llm.safe_client import (
    SafeLLMClient,
    SafeLLMResult,
    TransactionContext,
    get_safe_llm_client,
    set_safe_llm_client,
)

__all__ = [
    "Redactor",
    "PIIEgressError",
    "assert_no_pii",
    "find_pii_leaks",
    "new_request_redactor",
    "SafeLLMClient",
    "SafeLLMResult",
    "TransactionContext",
    "get_safe_llm_client",
    "set_safe_llm_client",
]
