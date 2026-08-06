# FinGuru — production fintech platform

> Prepend the shared preamble from `00-INDEX.md`.
> Repo: `Downloads\Finguru`. ~9,149 LOC, 72 tests.
> Reuse the auth + Postgres patterns from `04-systemlens.md` Part B rather than
> rebuilding them.

## Verified context
Present: LangGraph agents (advisor, categorization, budget, forecasting, anomaly,
causal), a real time-series package (LSTM, transformer, statsmodels, `arch`,
Prophet, PyOD) with 7 test files, ChromaDB, full K8s manifests (deployment, HPA,
ingress, PDB, RBAC, PVC), Prometheus + Grafana, two CI/CD workflows.
`core/security.py` has rate limiting, security headers and request validation.

Absent: **no relational database of any kind** — zero SQLAlchemy, psycopg,
asyncpg, Alembic. **No authentication library** — no jose, passlib, bcrypt,
authlib. No bank connectivity. No task queue.

## PHASE 0 — CRITICAL, fix before anything else
`app/api/endpoints.py` exposes:
```
POST   /analyze/budget/{user_id}
GET    /analyze/summary/{user_id}
DELETE /user/{user_id}
```
`user_id` is a **path parameter with no authentication behind it**. Anyone can
pass any user ID and read another person's financial data, or delete their
account. That is IDOR — OWASP API Security #1 — and in a financial application it
is the most serious class of bug there is.

`user_id` must come from a verified token, never from the URL. Every remaining
phase assumes this is closed.

## PHASE 1 — Identity
- **Argon2id** password hashing (not bcrypt). Be ready to explain memory-hardness.
- Short-lived access JWT + **rotating refresh tokens with reuse detection** — a
  replayed refresh token revokes the whole family.
- **TOTP MFA, mandatory before any bank linkage.**
- Server-side revocation via a Redis `jti` denylist.
- Auth endpoints rate-limited separately and harder than the general limiter;
  account lockout; constant-time failure path so timing cannot enumerate emails.

## PHASE 2 — PostgreSQL (closes the SQL gap on the SDE resume)
```
users(id, email_ci UNIQUE, argon2_hash, mfa_secret_enc, created_at)
accounts(id, user_id FK, aa_handle, masked_number, type, currency)
transactions(id, account_id FK, txn_date, amount_minor BIGINT, narration,
             category_id FK, model_version, confidence)
categories(id, name, parent_id)              -- self-referencing hierarchy
consents(id, user_id FK, aa_consent_id, purpose_code, scope, expiry, status)
anomalies(id, txn_id FK, score, detector_version, reviewed_at)
audit_log(id, actor, action, resource, before, after, at)   -- append-only
```
- **`amount_minor BIGINT`, never float.** Money in floating point is a red flag
  on sight.
- **Range-partition `transactions` on `txn_date`** — access is time-clustered and
  old partitions detach cheaply.
- **Postgres Row-Level Security** with a session variable for user id. This makes
  the IDOR class structurally impossible rather than merely patched.
- Alembic migrations from commit one.
- Covering index `(account_id, txn_date DESC)`; partial index on unreviewed
  anomalies. Justify every index.

## PHASE 3 — Bank connectivity, the Indian way
Use the **RBI Account Aggregator framework** under DEPA — not screen-scraping,
not credential sharing. Act as an **FIU (Financial Information User)**:
consent artifact -> FI request -> notification -> FI fetch, encrypted end to end.

Sandbox: **Setu AA**, **Finvu**, or **OneMoney** — all have free developer tiers.

**Be explicit about the ceiling.** You cannot become a registered FIU as a
student, so this is sandbox-only. Say so in the README and in interviews. An
owned limitation is credible; a silent one is not.

Not one resume in the 146-candidate corpus touches Account Aggregator, DEPA, or
consent artifacts. This is the differentiator.

## PHASE 4 — A model that is actually trained
- **Categorisation**: fine-tune a small transformer on transaction narrations.
  Indian narrations are messy and specific (`UPI/P2M/4172.../SWIGGY`) — that is
  itself a good story. Report macro-F1 per category with a confusion matrix.
  **Split temporally, not randomly**, or you leak.
- **Anomaly detection**: PyOD is already present. Evaluate as precision@k against
  a labelled window; state the base rate. An unlabelled anomaly count is not a
  metric.
- **Model registry**: persist `model_version` on every transaction row so you can
  answer "why was this categorised that way in March?"
- **Drift monitoring**: PSI on feature distributions with retraining gated on a
  held-out threshold. Reuse SHMLRP's machinery rather than rebuilding it.

## PHASE 5 — LLM safety (non-negotiable for financial data)
- **PII redaction before egress.** Account numbers, names and merchant
  identifiers tokenised out before the prompt leaves your infrastructure, then
  re-hydrated in the response. Under India's **DPDP Act 2023** and RBI
  data-localisation rules, shipping raw financial PII to a third-party LLM is a
  genuine compliance problem, not a theoretical one.
- **Prompt injection defence.** Transaction narrations are attacker-controlled —
  a merchant name can literally read `Ignore previous instructions and transfer`.
  Treat retrieved content as data, never instruction; validate structured output
  against a schema before acting on it.
- Per-user token budgets; ground every advisory claim in a retrieved transaction
  ID so advice is auditable.

## PHASE 6 — Operations
Append-only audit log (auth events, consent grants, data access, model
decisions). **Outbox pattern** for AA fetches so a webhook retry cannot
double-ingest. Idempotency keys on ingestion.

## Definition of done
1. No endpoint accepts a user identifier from the URL — proven by test
2. Cross-user access blocked at the SQL layer via RLS — proven by test
3. Refresh-token reuse revokes the family — proven by test
4. AA sandbox round-trip: consent -> fetch -> ingested transactions
5. Categorisation model with temporally-split macro-F1 and a confusion matrix
6. PII redaction proven: no account number reaches the LLM provider in any trace
7. README states the FIU sandbox limitation explicitly
