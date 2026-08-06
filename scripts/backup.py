#!/usr/bin/env python
"""
Back up FinGuru's durable state.

The system of record is PostgreSQL. Everything else is derivable or disposable:

    PostgreSQL      ledger, identities, consents, audit log   MUST back up
    model registry  trained artefacts + model cards           SHOULD back up
    Redis           denylist, rate limits, token budgets      do NOT back up
    logs            operational output                        ship, not back up

**Redis is deliberately excluded.** Restoring a stale denylist would resurrect
tokens revoked after the snapshot -- a backup that re-authorises a stolen
credential is worse than no backup at all. Redis runs `appendonly yes` so it
survives a restart, and total loss of it fails in a bounded way: unknown tokens
are absent from the denylist and therefore valid until they expire (15
minutes), while every rate-limit counter resets. That is acceptable
degradation. A denylist restored from yesterday is not.

The model registry *is* backed up: retraining is expensive, and the
`model_version` values persisted on transaction rows must stay resolvable to a
real artefact.

Usage::

    python scripts/backup.py                    # database + models
    python scripts/backup.py --database-only
    python scripts/backup.py --verify <file>    # check a dump is restorable
    python scripts/backup.py --retention-days 30
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import settings  # noqa: E402


def _dsn_parts(url: str) -> dict:
    """Split a SQLAlchemy URL into libpq connection parameters."""
    parsed = urlparse(url.replace("+asyncpg", "").replace("+psycopg", ""))
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "dbname": (parsed.path or "/").lstrip("/"),
    }


def dump_database(out_dir: Path, stamp: str) -> Path:
    """
    ``pg_dump`` in custom format, gzipped.

    Custom format (``-Fc``) rather than plain SQL: it supports selective and
    parallel restore, and does not require parsing the whole file to extract
    one table.

    Run as the **owner** role. A ``NOBYPASSRLS`` role would dump only the rows
    its own tenant policy allows -- which, with no tenant bound, is none. A
    backup that silently contains zero rows is the worst failure mode
    available here, so the output size is checked below.
    """
    parts = _dsn_parts(settings.database_migration_url)
    target = out_dir / f"finguru-{stamp}.dump.gz"

    env = dict(os.environ)
    if parts["password"]:
        env["PGPASSWORD"] = parts["password"]

    cmd = [
        "pg_dump",
        "-h",
        parts["host"],
        "-p",
        parts["port"],
        "-U",
        parts["user"],
        "-d",
        parts["dbname"],
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--compress=0",
    ]

    print(f"  pg_dump -> {target.name}")
    with gzip.open(target, "wb") as fh:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        assert proc.stdout is not None
        shutil.copyfileobj(proc.stdout, fh)
        _, stderr = proc.communicate()

    if proc.returncode != 0:
        target.unlink(missing_ok=True)
        raise SystemExit(
            f"pg_dump failed ({proc.returncode}): {stderr.decode(errors='replace')[:600]}"
        )

    size_mb = target.stat().st_size / (1024 * 1024)
    print(f"  wrote {size_mb:.2f} MB")
    if size_mb < 0.005:
        raise SystemExit(
            "Dump is suspiciously small. Check that DATABASE_MIGRATION_URL "
            "points at the owner role -- a NOBYPASSRLS role dumps zero rows "
            "from every RLS-protected table without reporting an error."
        )
    return target


def archive_models(out_dir: Path, stamp: str) -> Path | None:
    """Tar the model registry. Skipped, not failed, when it is empty."""
    registry = Path(settings.model_registry_dir)
    if not registry.exists() or not any(registry.iterdir()):
        print("  model registry is empty; nothing to archive")
        return None

    target = out_dir / f"models-{stamp}.tar.gz"
    print(f"  archiving {registry} -> {target.name}")
    with tarfile.open(target, "w:gz") as tar:
        tar.add(registry, arcname="models")
    print(f"  wrote {target.stat().st_size / (1024 * 1024):.2f} MB")
    return target


def verify(dump_path: Path) -> int:
    """
    Confirm a dump is readable and contains the tables that matter.

    **An unverified backup is not a backup.** ``pg_restore --list`` parses the
    archive's table of contents without touching a database, which catches
    truncation, corruption, and the more common failure of a dump that
    completed successfully but is empty.
    """
    print(f"Verifying {dump_path.name}")
    raw = dump_path.with_suffix("")
    with gzip.open(dump_path, "rb") as src, open(raw, "wb") as dst:
        shutil.copyfileobj(src, dst)

    try:
        result = subprocess.run(
            ["pg_restore", "--list", str(raw)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(f"  FAIL: pg_restore could not read the archive: {result.stderr[:400]}")
            return 1

        listing = result.stdout
        required = ["users", "accounts", "transactions", "consents", "audit_log"]
        missing = [t for t in required if f" {t} " not in listing]
        if missing:
            print(f"  FAIL: these tables are absent from the dump: {missing}")
            return 1

        entries = len([ln for ln in listing.splitlines() if ln and not ln.startswith(";")])
        print(f"  OK: archive readable, {entries} entries, all core tables present")
        return 0
    finally:
        raw.unlink(missing_ok=True)


def prune(out_dir: Path, retention_days: int) -> None:
    """Delete backups older than the retention window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    for path in out_dir.glob("*"):
        if not path.is_file():
            continue
        if datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < cutoff:
            path.unlink()
            removed += 1
    if removed:
        print(f"  pruned {removed} backup(s) older than {retention_days} days")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="./backups")
    parser.add_argument("--database-only", action="store_true")
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--verify", metavar="DUMP", help="verify an existing dump and exit")
    args = parser.parse_args()

    if args.verify:
        return verify(Path(args.verify))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"FinGuru backup {stamp}")
    print("-" * 66)

    dump = dump_database(out_dir, stamp)
    if not args.database_only:
        archive_models(out_dir, stamp)

    print("-" * 66)
    if verify(dump) != 0:
        # The bad dump stays on disk for inspection, but the exit code is
        # non-zero so the scheduler alerts instead of recording a success.
        print("BACKUP FAILED VERIFICATION")
        return 1

    prune(out_dir, args.retention_days)
    print(f"Backup complete: {dump}")
    print(
        "\nRestore:\n"
        f"  gunzip -c {dump.name} | pg_restore -d finguru --clean --if-exists\n"
        "\nThen run `alembic upgrade head`. The dump is taken --no-owner\n"
        "--no-privileges, so grants and the NOBYPASSRLS `finguru_app` role are\n"
        "NOT included; the migration recreates them. Restoring without that\n"
        "step leaves the RLS policies in place but no application role to\n"
        "enforce them against, and the app will not be able to connect."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
