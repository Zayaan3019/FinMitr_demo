"""
Agent modules.

The original demo pipeline lived here -- ``workflow.py`` (LangGraph),
``categorization.py``, ``anomaly_detection.py``, ``advisor.py``, ``budget.py``
and ``forecasting.py``. All six are gone. Each took a ``user_id`` argument
supplied by the caller and trusted it -- the same IDOR the API layer had --
and each has been superseded by a component with actual evaluation behind it:

=============================  ============================================
removed                        replacement
=============================  ============================================
``categorization.py``          :mod:`app.ml.categorizer` -- fine-tuned
(keyword ``if`` chain)         transformer, temporally-split macro-F1
``anomaly_detection.py``       :mod:`app.ml.anomaly_eval` -- Isolation
(3-sigma on a global mean)     Forest, precision@k against a stated base rate
``advisor.py``                 :mod:`app.llm.safe_client` -- redaction,
(unguarded LLM call)           injection fencing, schema and grounding
                               validation, per-user token budgets
``budget.py``, ``forecasting`` ``/api/v1/analysis/*``, computed over the
                               RLS-bound session
``workflow.py``                not replaced; the pipeline is now explicit
=============================  ============================================

:mod:`app.agents.causal` remains and is deliberately *not* mounted by
``main.py``. Its nodes still take ``user_id`` as a plain argument, so exposing
it over HTTP would reintroduce the PHASE 0 vulnerability. Wiring it up means
porting it onto the RLS-bound session first.
"""

__all__: list[str] = []
