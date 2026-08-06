# Setup

## Prerequisites

- Python 3.11+
- Docker (for PostgreSQL and Redis)
- A Groq API key, if you want the advisory chat. Everything else works without
  one — the LLM path degrades to locally computed answers rather than failing.

## 1. Dependencies

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Configuration

```bash
cp .env.example .env
```

Two values must be set before anything runs safely:

```bash
# 32+ characters. Startup refuses the development placeholder in production.
JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")

# 32 bytes, base64. Encrypts stored MFA secrets at rest.
FIELD_ENCRYPTION_KEY=$(python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())")
```

Note the two database URLs, pointing at **different roles**:

```
DATABASE_URL=postgresql+asyncpg://finguru_app:...@localhost:55432/finguru
DATABASE_MIGRATION_URL=postgresql+asyncpg://finguru:...@localhost:55432/finguru
```

This is not redundancy. `finguru` owns the schema and runs migrations;
`finguru_app` is created `NOSUPERUSER NOBYPASSRLS` and is what the application
connects as. PostgreSQL exempts superusers and table owners from Row-Level
Security, so pointing `DATABASE_URL` at the owner would silently disable every
tenancy policy in the schema while all the SQL still looked correct.

## 3. Database and Redis

```bash
docker compose up -d postgres redis
alembic upgrade head
```

The migration creates the schema, the partitions, the RLS policies and the
`finguru_app` role. It is the only thing that runs DDL — there is no
`create_all()` anywhere.

## 4. Run

```bash
uvicorn main:app --reload
```

Then http://localhost:8000/docs.

Startup refuses to proceed if the database is unreachable. That is deliberate:
without PostgreSQL there is no RLS, and serving requests without tenancy
enforcement is worse than serving none.

## 5. Train the categoriser (optional)

```bash
python scripts/train_categorizer.py --rows 14000 --epochs 5
```

Roughly 10–20 minutes on CPU. Without it the application falls back to keyword
rules, tagged `rules-v0` on every row it labels, and logs a warning at startup
so the degradation is never silent.

---

## Trying it out

```bash
# 1. Register
curl -X POST localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"Correct-Horse-Battery-9!"}'
# -> {"access_token": "...", "refresh_token": "..."}

TOKEN=<access_token>

# 2. Import a statement
curl -X POST localhost:8000/api/v1/transactions/import \
  -H "Authorization: Bearer $TOKEN" \
  -F 'file=@statement.csv'

# 3. Read it back
curl localhost:8000/api/v1/analysis/summary -H "Authorization: Bearer $TOKEN"
```

The CSV needs `date`, `amount`, `description` columns. Negative amounts are
outflows.

### Linking a bank account (sandbox)

Bank linkage requires MFA — a password alone must not be able to attach a bank
account.

```bash
# Enrol TOTP
curl -X POST localhost:8000/api/v1/auth/mfa/enrol -H "Authorization: Bearer $TOKEN"
# -> {"secret": "...", "provisioning_uri": "otpauth://..."}

# Confirm with a code from your authenticator
curl -X POST localhost:8000/api/v1/auth/mfa/confirm \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"code":"123456"}'
# -> a new, MFA-satisfied token pair

# Request consent, then approve and fetch
curl -X POST localhost:8000/api/v1/aa/consents \
  -H "Authorization: Bearer $MFA_TOKEN" -H 'Content-Type: application/json' \
  -d '{"purpose_code":"101","fi_types":["DEPOSIT"],"customer_aa_id":"9876543210@onemoney"}'
```

**Sandbox only.** See the README — FinGuru is not an RBI-registered FIU.

---

## Verifying tenant isolation yourself

Worth doing once, because it is the claim everything else rests on.

```bash
# Register two users, import data as the first, query as the second.
# The second sees nothing, and no parameter changes that.
curl "localhost:8000/api/v1/transactions?user_id=<first-user-id>" \
  -H "Authorization: Bearer $SECOND_USER_TOKEN"
# -> []   (the query parameter is ignored; the subject is the token)
```

Or go under the application entirely:

```bash
docker exec -it finguru_postgres psql -U finguru_app -d finguru
```

```sql
SELECT count(*) FROM transactions;              -- 0: no tenant bound
SELECT set_config('app.current_user_id', '<some-uuid>', false);
SELECT count(*) FROM transactions;              -- only that user's rows
```

The automated version is `pytest tests/test_rls_isolation.py -v`.

---

## Tests

```bash
docker compose up -d postgres redis
alembic upgrade head
python -m pytest tests/ -v
```

The suite runs against real PostgreSQL and Redis. Tests needing the database
skip **loudly** if it is unreachable rather than passing silently — a green run
that skipped the isolation tests would be worse than a red one.

---

## Troubleshooting

**`connection refused` on port 55432**
The compose stack uses non-default host ports (55432, 56379) so it cannot
collide with a Postgres or Redis already on the machine. Check
`docker compose ps`.

**`new row violates row-level security policy`**
The session has no tenant bound. Application code should use
`tenant_session(user_id)` or the `get_tenant_session` dependency;
`system_session()` deliberately does not bypass RLS.

**Tests fail with "Future attached to a different loop"**
`pytest.ini` sets `asyncio_default_test_loop_scope = session`. That is required,
not cosmetic: asyncpg connections are bound to the loop that created them, and
the application holds one process-wide pool.

**Every RLS test passes but you suspect it shouldn't**
Check which role you are connected as:

```sql
SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;
```

Both booleans must be false. If either is true, RLS is inert and every
isolation test is passing for the wrong reason.
`tests/test_rls_isolation.py::test_app_role_cannot_bypass_rls` asserts exactly
this, for exactly this reason.

**Categoriser is slow on the first request**
The transformer loads lazily — roughly 2–3 seconds on first use, then cached.

**Rate limited (429) while load testing**
The general limiter is 60 requests/minute per client. That is the limiter
working. Raise `GENERAL_RATE_LIMIT_PER_MINUTE` if you are profiling the
application rather than the limiter.

---

## Deployment

See `PRODUCTION_DEPLOYMENT.md`, and run:

```bash
ENVIRONMENT=production python scripts/production_deploy_check.py
```

It exits non-zero and refuses to deploy on an unsafe configuration — a
development JWT secret, `CORS=*`, a placeholder database password, an
application role that can bypass RLS, or a categoriser below the macro-F1
floor.
