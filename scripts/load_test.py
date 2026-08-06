#!/usr/bin/env python
"""
Load-test the paths that actually cost something.

The previous version hammered ``/health/live`` and ``/`` and reported
impressive numbers. Those endpoints do no work: they touch neither the database
nor a model, so the result measured uvicorn's accept loop and nothing else.

The expensive paths in this application, in rough order of cost per request:

    POST /auth/login        Argon2id at 64 MiB, deliberately slow. This is the
                            one that will fall over first under credential
                            stuffing, and it is *supposed* to be expensive --
                            the useful question is how many concurrent logins a
                            pod survives, not how to make it faster.
    POST /chat              transformer inference + LLM egress
    GET  /analysis/*        pandas aggregation over an RLS-filtered scan
    GET  /transactions      indexed read, the common case

Reports p50/p95/p99 rather than a mean. A mean hides the tail, and the tail is
what users experience: at 100 rps, a p99 of 2s means one request a second takes
two seconds.

Usage::

    python scripts/load_test.py --url http://localhost:8000
    python scripts/load_test.py --concurrency 50 --requests 500
    python scripts/load_test.py --skip-auth-load     # omit the Argon2 test
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

PASSWORD = "Correct-Horse-Battery-Staple-9!"


@dataclass
class Sample:
    latency_ms: float
    status: int
    error: Optional[str] = None


@dataclass
class Scenario:
    name: str
    note: str
    samples: List[Sample] = field(default_factory=list)

    def summarise(self) -> dict:
        ok = [s for s in self.samples if s.error is None and 200 <= s.status < 400]
        # 429 is counted separately and is NOT an error. The general limiter is
        # 60 requests/minute per client by default, so a load generator will
        # trip it long before the application struggles -- and a run that
        # reports "100% failure" when the rate limiter is working correctly is
        # measuring the limiter, not the service. Raise the limit or lower the
        # volume to profile the application itself.
        throttled = [s for s in self.samples if s.status == 429]
        latencies = sorted(s.latency_ms for s in ok)
        if not latencies:
            return {
                "name": self.name,
                "note": self.note,
                "n": len(self.samples),
                "ok": 0,
                "throttled": len(throttled),
                "error_rate": round(1 - (len(ok) + len(throttled)) / max(1, len(self.samples)), 4),
            }

        def pct(p: float) -> float:
            idx = min(len(latencies) - 1, int(round(p * (len(latencies) - 1))))
            return latencies[idx]

        return {
            "name": self.name,
            "note": self.note,
            "n": len(self.samples),
            "ok": len(ok),
            "throttled": len(throttled),
            "error_rate": round(1 - (len(ok) + len(throttled)) / len(self.samples), 4),
            "p50_ms": round(statistics.median(latencies), 1),
            "p95_ms": round(pct(0.95), 1),
            "p99_ms": round(pct(0.99), 1),
            "max_ms": round(latencies[-1], 1),
        }


async def timed(scenario: Scenario, coro_factory: Callable) -> None:
    started = time.perf_counter()
    try:
        response = await coro_factory()
        scenario.samples.append(
            Sample((time.perf_counter() - started) * 1000, response.status_code)
        )
    except Exception as exc:  # noqa: BLE001
        scenario.samples.append(
            Sample((time.perf_counter() - started) * 1000, 0, f"{type(exc).__name__}: {exc}")
        )


async def drive(scenario: Scenario, factory: Callable, total: int, concurrency: int) -> Scenario:
    """Run `total` requests at a bounded `concurrency`."""
    semaphore = asyncio.Semaphore(concurrency)

    async def one():
        async with semaphore:
            await timed(scenario, factory)

    await asyncio.gather(*(one() for _ in range(total)))
    return scenario


async def provision(client: httpx.AsyncClient, base: str) -> tuple[str, dict]:
    """Register a user and return (email, auth headers)."""
    email = f"loadtest-{uuid.uuid4().hex[:10]}@example.com"
    r = await client.post(f"{base}/auth/register", json={"email": email, "password": PASSWORD})
    r.raise_for_status()
    body = r.json()
    return email, {"Authorization": f"Bearer {body['access_token']}"}


async def seed(client: httpx.AsyncClient, base: str, headers: dict) -> int:
    """Import a CSV so the read paths have something to scan."""
    import io

    rows = ["date,amount,description"]
    for i in range(400):
        day = (i % 28) + 1
        month = (i % 12) + 1
        rows.append(
            f"2026-{month:02d}-{day:02d},-{(i % 900) + 50}.00,UPI/P2M/41729385620{i%10}/SWIGGY"
        )

    csv = "\n".join(rows).encode()
    r = await client.post(
        f"{base}/transactions/import",
        headers=headers,
        files={"file": ("load.csv", io.BytesIO(csv), "text/csv")},
    )
    r.raise_for_status()
    return r.json()["transactions_ingested"]


def report(results: List[dict]) -> int:
    print("\n" + "=" * 90)
    print("LOAD TEST")
    print("=" * 90)
    print(
        f"{'scenario':<26}{'n':>6}{'ok':>6}{'429':>6}{'err':>8}"
        f"{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}"
    )
    print("-" * 90)
    for r in results:
        if "p50_ms" not in r:
            reason = "RATE LIMITED" if r.get("throttled") else "ALL FAILED"
            print(
                f"{r['name']:<26}{r['n']:>6}{r['ok']:>6}{r.get('throttled', 0):>6}"
                f"{r['error_rate']:>8.1%}   {reason}"
            )
            continue
        print(
            f"{r['name']:<26}{r['n']:>6}{r['ok']:>6}{r.get('throttled', 0):>6}"
            f"{r['error_rate']:>8.1%}"
            f"{r['p50_ms']:>9.1f}{r['p95_ms']:>9.1f}{r['p99_ms']:>9.1f}{r['max_ms']:>9.1f}"
        )
    print("-" * 90)
    for r in results:
        print(f"  {r['name']}: {r['note']}")
    print("=" * 90)

    throttled_total = sum(r.get("throttled", 0) for r in results)
    if throttled_total:
        print(
            f"\nNOTE: {throttled_total} request(s) were rate limited (429). That is the\n"
            f"      limiter working, not a failure -- but it means these numbers\n"
            f"      describe the limiter rather than the application. To profile the\n"
            f"      app itself, raise GENERAL_RATE_LIMIT_PER_MINUTE or lower --requests."
        )

    failed = [r for r in results if r["error_rate"] > 0.01]
    if failed:
        print(
            f"\nFAIL: {len(failed)} scenario(s) exceeded a 1% error rate "
            f"(429s excluded): {[r['name'] for r in failed]}"
        )
        return 1
    print("\nAll scenarios within a 1% error budget")
    return 0


async def run(base_url: str, total: int, concurrency: int, skip_auth: bool) -> int:
    base = f"{base_url.rstrip('/')}/api/v1"
    results: List[dict] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            await client.get(f"{base_url}/health/live")
        except Exception as exc:
            print(f"Server unreachable at {base_url}: {exc}")
            return 1

        print(f"Provisioning a user against {base_url} ...")
        _email, headers = await provision(client, base)
        ingested = await seed(client, base, headers)
        print(f"  seeded {ingested} transactions")

        scenarios = [
            (
                Scenario(
                    "GET /transactions",
                    "indexed read on (user_id, txn_date DESC); the common case",
                ),
                lambda: client.get(f"{base}/transactions?months=12&limit=100", headers=headers),
            ),
            (
                Scenario(
                    "GET /analysis/summary",
                    "RLS-filtered scan + pandas aggregation",
                ),
                lambda: client.get(f"{base}/analysis/summary?months=12", headers=headers),
            ),
            (
                Scenario(
                    "GET /analysis/budget",
                    "per-category median over the same scan",
                ),
                lambda: client.get(f"{base}/analysis/budget?months=12", headers=headers),
            ),
            (
                Scenario("GET /stats", "three counts under RLS"),
                lambda: client.get(f"{base}/stats", headers=headers),
            ),
            (
                Scenario("GET /health/live", "baseline: no database, no model"),
                lambda: client.get(f"{base_url}/health/live"),
            ),
        ]

        for scenario, factory in scenarios:
            print(f"  running {scenario.name} ({total} requests, {concurrency} concurrent)")
            await drive(scenario, factory, total, concurrency)
            results.append(scenario.summarise())

        if not skip_auth:
            # Deliberately lower volume. Argon2id at 64 MiB means each login
            # holds 64 MiB while it runs; at concurrency 20 that is 1.3 GiB of
            # transient RSS. That cost is the security property, not a
            # regression -- but it is why login needs its own rate limit,
            # harder than the general one.
            auth_total = max(20, total // 10)
            auth_conc = min(concurrency, 10)
            scenario = Scenario(
                "POST /auth/login",
                f"Argon2id at 64 MiB/hash -- slow BY DESIGN. {auth_conc} concurrent "
                f"logins hold ~{auth_conc * 64} MiB transiently.",
            )
            print(f"  running {scenario.name} ({auth_total} requests, {auth_conc} concurrent)")
            await drive(
                scenario,
                lambda: client.post(
                    f"{base}/auth/login", json={"email": _email, "password": PASSWORD}
                ),
                auth_total,
                auth_conc,
            )
            results.append(scenario.summarise())

        # Clean up after ourselves; the seeded rows are real.
        await client.delete(f"{base}/me", headers=headers)

    return report(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument(
        "--skip-auth-load",
        action="store_true",
        help="omit the login scenario (it is memory-hungry by design)",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.url, args.requests, args.concurrency, args.skip_auth_load))


if __name__ == "__main__":
    raise SystemExit(main())
