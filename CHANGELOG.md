# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [3.0.0] — 2026-08-02

A security and correctness rewrite. **This release is not backward compatible:
every endpoint changed, and the datastore changed from ChromaDB to PostgreSQL.**

### Security — fixed

- **Broken Object Level Authorization (OWASP API #1).** `POST
  /analyze/budget/{user_id}`, `GET /analyze/summary/{user_id}`, `POST
  /analyze/forecast/{user_id}`, `DELETE /user/{user_id}`, `POST
  /ingest?user_id=` and `POST /chat {"user_id": …}` all took the subject from
  the client with no authentication behind it. Passing another user's id
  returned their complete financial history; `DELETE /user/{user_id}` erased
  their account.

  Every one of those routes is gone. The subject now comes from a verified
  bearer token and from nowhere else. `tests/test_phase0_idor.py` walks the
  generated OpenAPI document and fails on any parameter, path segment or body
  property that names a user, so a route added later is covered too.

- **Refresh-token family revocation was not durable.** Reuse detection revoked
  the family and then raised; the caller's session context manager rolled back
  on the exception, undoing the revocation. A stolen token family survived its
  own detection. Now committed before raising.

- **Account lockout never engaged.** Same defect, different place: the
  failed-login counter was `flush()`ed and then rolled back by the `AuthError`
  that reported the failure. The counter never advanced past 1, so the
  threshold was never reached and an attacker could guess indefinitely against
  a "protected" account.

- **The tenant session variable could land on the wrong connection.** An
  `AsyncSession` from a sessionmaker releases its connection on every
  `commit()`, so `app.current_user_id` was set on one connection while later
  queries ran on another — and the first went back to the pool still carrying a
  tenant id. `tenant_session` now pins one connection for its lifetime and
  invalidates rather than returns any connection whose GUC cannot be cleared.

- **ReDoS in the PII redactor.** `\b(?:\d[ -]?){13,19}\b` backtracks
  exponentially on a long failing digit run. Narrations are attacker-controlled,
  so one ingested transaction could pin a CPU core. Replaced with fixed-shape
  alternatives plus a 4096-character cap; 50 redactions of a 400-digit hostile
  string now take 0.009s.

- **The user's own chat query was never redacted.** Only narrations were, so
  "is my account 50100234567890 overspending?" sent an account number to the
  model provider. The egress guard caught it — correctly, failing closed — but
  the query is now redacted through the same request-scoped redactor.

- **Webhook signatures were verified after body parsing.** A forged request
  with a malformed body received a 422 without the signature ever being
  checked. The HMAC is now the first gate; every forged webhook gets a 401.

- **`DELETE /me` deadlocked against itself.** The handler held row locks in the
  request transaction while opening a second connection to delete the `users`
  row those locks referenced. The request hung until the client timed out.

- **Outbox events with no handler were retried forever.** `fi.ingested` had no
  handler at all, so post-fetch anomaly scoring never ran, and the relay
  re-selected and re-logged the same rows every two seconds indefinitely.
  Unhandled types are now parked; a test greps for every enqueued `event_type`
  and fails if one lacks a handler.

- **Startup could not fail.** A production deployment with an unset
  `JWT_SECRET` and `CORS=*` started happily. Startup now refuses to serve when
  `validate_production_settings()` returns anything.

- **CORS was `["*"]` with `allow_credentials=True`** — a combination browsers
  reject outright, so it was both insecure in intent and broken in practice.

### Added

- **Identity** (`app/auth/`) — Argon2id password hashing, short-lived access
  JWTs with pinned algorithms, rotating refresh tokens with family reuse
  detection, mandatory TOTP MFA before bank linkage, a three-scope Redis
  revocation denylist, account lockout, auth-specific rate limiting, and a
  constant-work failure path so login timing cannot enumerate accounts.

- **PostgreSQL with Row-Level Security** (`app/db/`, `alembic/`) — full
  relational schema, `amount_minor BIGINT` paise, `transactions`
  RANGE-partitioned monthly by `txn_date`, RLS `ENABLE`d and `FORCE`d on every
  tenant table, a non-owner `NOBYPASSRLS` application role, an append-only
  `audit_log`, and Alembic migrations from the first commit.

- **RBI Account Aggregator** (`app/aa/`) — consent artifact → FI request →
  notification → FI fetch, with real X25519 ECDH → HKDF-SHA256 → AES-256-GCM
  end-to-end encryption, HMAC-authenticated webhooks, and an `AATransport`
  interface with sandbox HTTP and local mock implementations. **Sandbox-only;
  see the README.**

- **Trained categoriser** (`app/ml/`) — fine-tuned transformer over Indian
  transaction narrations. Temporal-split macro-F1 **0.8120** against a
  majority-class baseline of 0.2011, with a confusion matrix, per-class
  support, a quantified random-vs-temporal leakage delta (0.173, 21.3%
  relative), a model registry that stamps `model_version` on every row, and PSI
  drift monitoring gated on held-out degradation.

- **Anomaly evaluation** (`scripts/evaluate_anomalies.py`) — precision@k,
  recall@k and lift against a stated base rate, replacing a bare anomaly count.

- **Guarded LLM path** (`app/llm/`) — nine-stage pipeline: budget check, PII
  redaction, independent fail-closed egress assertion, nonce-fenced prompt
  assembly, injection scanning, schema validation, grounding enforcement, and
  re-hydration.

- **Operations** (`app/ops/`) — transactional outbox with `FOR UPDATE SKIP
  LOCKED`, idempotency keys with request-hash mismatch detection, and an
  append-only audit log.

- **89 tests** against real PostgreSQL and Redis, covering all seven
  definition-of-done criteria. CI runs service containers rather than mocks,
  because the central claim cannot be tested against SQLite.

### Changed

- All endpoints. See the README for the new surface.
- Response schemas no longer carry `user_id` in any form.
- `pytest.ini` used `[tool:pytest]`, which is only recognised in `setup.cfg` —
  so every setting in it, including `--strict-markers`, had silently never
  applied.
- Logging forces UTF-8. On Windows the default ANSI code page raised
  `UnicodeEncodeError` inside the log sink for any non-cp1252 character,
  replacing the log line with a traceback about failing to log it. Reachable
  from bank narrations, which are attacker-influenced text.

### Removed

- The demo agent pipeline (`workflow.py`, `advisor.py`, `categorization.py`,
  `anomaly_detection.py`, `budget.py`, `forecasting.py`) — each took a
  caller-supplied `user_id` and trusted it. Superseded by `app/ml/` and
  `app/llm/`.
- ChromaDB and the vector store. Tenancy by metadata filter is an application
  convention, not an access control: forget it in one query and you return
  everyone's data.
- Nine status/summary documents that described endpoints which no longer exist.
- Demo entry points, example scripts, `.bat` launchers, and the obsolete test
  suite.

### Known limitations

- **FinGuru is not an RBI-registered Financial Information User and cannot
  become one.** AA connectivity is sandbox-only. Stated in the README, returned
  by `GET /api/v1/aa/info`, and asserted in CI.
- `app/agents/causal/` and `app/timeseries/` are present but unmounted; their
  entry points still take `user_id` as an argument.
- The categoriser is trained on generated narrations modelled on real Indian
  payment-rail formats, not on production bank data.

---

## [2.0.0] — 2026-02-02

Added rate limiting, request validation, security headers, metrics, health
checks, LRU caching, circuit breakers, and Docker/Kubernetes manifests to the
proof-of-concept.

**Superseded.** The IDOR described at the top of the 3.0.0 notes was present
throughout this release: none of the above addressed authentication or
authorization, so every endpoint remained anonymously accessible with a
client-supplied user id.

## [1.0.0] — 2026-01-15

Initial release. LangGraph agent workflow, ChromaDB vector store, Groq LLM
integration, CSV ingestion.
