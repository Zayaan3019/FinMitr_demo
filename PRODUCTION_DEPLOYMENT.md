# Production deployment

## Before anything else

```bash
ENVIRONMENT=production python scripts/production_deploy_check.py
```

It exits non-zero and refuses the deploy on a development JWT secret,
`CORS=*`, a placeholder database password, an application role that can bypass
RLS, a categoriser below the macro-F1 floor, or a route surface carrying a user
identifier. The application performs the configuration half of these checks
again at startup and refuses to serve — so a bad config fails at boot, not at
the first request.

---

## Secrets

These are secrets. None of them belongs in a ConfigMap, an image, or `.env` in
a repository.

| Variable | Generate with |
|---|---|
| `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `FIELD_ENCRYPTION_KEY` | `python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"` |
| `DB_APP_PASSWORD` | `openssl rand -base64 32` |
| `AA_WEBHOOK_SECRET` | issued by your AA provider |
| `GROQ_API_KEY` | from the provider console |
| `DATABASE_URL`, `DATABASE_MIGRATION_URL` | contain the above passwords |

```bash
kubectl create secret generic finguru-secrets -n finguru \
  --from-literal=JWT_SECRET="$JWT_SECRET" \
  --from-literal=FIELD_ENCRYPTION_KEY="$FIELD_ENCRYPTION_KEY" \
  --from-literal=DATABASE_URL="$DATABASE_URL" \
  --from-literal=DATABASE_MIGRATION_URL="$DATABASE_MIGRATION_URL" \
  --from-literal=DB_APP_PASSWORD="$DB_APP_PASSWORD" \
  --from-literal=AA_WEBHOOK_SECRET="$AA_WEBHOOK_SECRET" \
  --from-literal=GROQ_API_KEY="$GROQ_API_KEY"
```

### Rotating `JWT_SECRET`

Rotation invalidates every access token immediately — users are signed out but
can refresh, because refresh tokens are opaque and stored hashed rather than
signed. Rotate during low traffic, or accept a wave of 401s that clients should
handle by refreshing.

### Rotating `FIELD_ENCRYPTION_KEY`

**Do not rotate this without re-encrypting.** It encrypts stored MFA secrets;
changing it makes every enrolled second factor undecryptable and locks every
MFA user out of bank linkage permanently. Ciphertexts carry a `v1:` prefix so a
versioned re-encryption is possible — write that migration first.

---

## Database

The two roles are not interchangeable:

```
finguru       owns the schema, runs Alembic          DATABASE_MIGRATION_URL
finguru_app   NOSUPERUSER NOBYPASSRLS, runs the app  DATABASE_URL
```

PostgreSQL exempts superusers and table owners from Row-Level Security. Running
the application as `finguru` silently disables every tenancy policy in the
schema while every query still looks correct. Verify after deploying:

```sql
SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'finguru_app';
-- both booleans must be false
```

### Migrations

Run as a Job before the rollout, never from an application pod:

```bash
kubectl apply -f k8s/migrate-job.yaml
kubectl wait --for=condition=complete job/finguru-migrate -n finguru --timeout=300s
```

Alembic is the only thing that runs DDL. Application pods connect as a role
with no DDL privileges, so a compromised pod cannot alter the schema — or drop
the RLS policies protecting it.

### Managed PostgreSQL

Works on RDS, Cloud SQL and Azure Database. Requirements:

- **PostgreSQL 13+** (`gen_random_uuid()` in core; declarative partitioning)
- the migration role must be able to `CREATE ROLE`. On RDS that means
  `rds_superuser`; on Cloud SQL, `cloudsqlsuperuser`. If your provider forbids
  it, create `finguru_app` by hand with `NOSUPERUSER NOBYPASSRLS` first and
  the migration will use it.
- **connection pooler in transaction mode is fine**, but set
  `server_settings` carefully: the tenant GUC is session-scoped, and
  `tenant_session` pins one connection for its lifetime precisely so pooling
  cannot separate the `SET` from the queries that depend on it.

### Partitions

`transactions` is monthly RANGE-partitioned. Startup calls
`ensure_transaction_partitions(3)`, so partitions stay three months ahead
automatically. A missed run degrades to rows landing in `DEFAULT` — they are
still correct, they just forfeit pruning.

Ageing out old data:

```sql
ALTER TABLE transactions DETACH PARTITION transactions_2023_01;
DROP TABLE transactions_2023_01;
```

Instant, and it does not bloat the heap the way a bulk `DELETE` would.

---

## Redis

Set `REDIS_REQUIRED=true` in any multi-replica deployment. The in-process
fallback gives every pod its own denylist, so a token revoked on pod A still
authenticates on pod B — a revocation that does not revoke. Failing to start is
the correct behaviour.

Run with `appendonly yes`. The denylist is a security control; a revoked token
coming back to life after a restart is a real failure, not a cache miss.

**Do not restore Redis from a backup.** See the reasoning in
`scripts/backup.py`: a restored stale denylist re-authorises tokens revoked
after the snapshot. Total loss of Redis fails closed and bounded — unknown
tokens are simply absent from the denylist and remain valid for at most their
15-minute lifetime.

---

## Scaling

| Component | Scaling | Constraint |
|---|---|---|
| API pods | horizontal, stateless | `DB_POOL_SIZE × replicas` must stay under `max_connections` |
| Outbox relay | runs in every pod | safe — `FOR UPDATE SKIP LOCKED` gives workers disjoint batches |
| PostgreSQL | vertical, then read replicas | reads only; RLS applies on replicas too |
| Redis | single instance is usually enough | it holds counters and TTLs, not durable state |

**Memory.** Argon2id holds 64 MiB per concurrent hash. Ten simultaneous logins
is 640 MiB of transient RSS on top of the transformer. Size limits with that in
mind; it is the cost of the security property, not a leak.

---

## Health probes

```yaml
livenessProbe:
  httpGet: {path: /health/live, port: 8000}
readinessProbe:
  httpGet: {path: /health/ready, port: 8000}
```

`/health/ready` fails on an unreachable database — correct, because without
PostgreSQL there is no RLS. It does **not** fail on a degraded Redis: the pod
still serves correct answers, just not correctly shared ones, and pulling every
pod out of rotation over Redis would turn a degradation into an outage.

---

## Monitoring

`/metrics` exposes aggregate counters with no per-user labels, so scraping it
cannot reveal who is using the system.

Worth alerting on:

| Signal | Why |
|---|---|
| `auth.token.reuse_detected` in the audit log | a refresh token was replayed — someone holds a stolen credential |
| outbox depth rising | the relay is stuck or a handler is failing |
| parked outbox events > 0 | an event type has no handler, or exhausted its retries |
| `LLM_BLOCKED` audit entries | the PII egress guard is firing — redaction has a gap |
| PSI above `PSI_ALERT_THRESHOLD` | input distribution has moved; check held-out performance before retraining |
| 429 rate on `/auth/login` | credential stuffing |

---

## Backups

```bash
python scripts/backup.py --retention-days 30
```

PostgreSQL and the model registry. Not Redis — see above. The script verifies
every dump with `pg_restore --list` and exits non-zero if the archive is
unreadable or missing core tables, because an unverified backup is not a backup.

Restore into a fresh cluster needs `alembic upgrade head` afterwards: the dump
is taken `--no-owner --no-privileges`, so grants and the `finguru_app` role are
not included and the migration must recreate them. Restoring without that step
leaves the RLS policies in place with no application role to enforce them
against.

---

## The Account Aggregator ceiling

`AA_USE_MOCK_TRANSPORT` must be `false` in production —
`validate_production_settings()` refuses to start otherwise, because a
deployment silently serving simulated bank data would be worse than one that
does not start.

Setting it to `false` does not make this a registered FIU. That requires an
RBI-regulated entity. `AA_SANDBOX_ONLY` stays `true`, `GET /api/v1/aa/info`
says so, and the README says so. See the README's opening section.

---

## Rollback

```bash
kubectl rollout undo deployment/finguru -n finguru
```

**Check whether the release included a migration first.** Application rollback
is safe; schema rollback usually is not. Alembic `downgrade` on the initial
migration drops every table. If a release added a column, roll the application
back and leave the column — an unused column costs nothing, while a dropped one
costs the data in it.
