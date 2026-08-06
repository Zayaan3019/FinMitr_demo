"""
Monitoring and observability module for production deployments.
Includes metrics, health checks, and performance monitoring.
"""

from typing import Dict, Any, Optional
from datetime import datetime
from collections import defaultdict
import time
import psutil
from enum import Enum

from app.core.logging import get_logger

logger = get_logger(__name__)


class MetricType(Enum):
    """Types of metrics we track."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"


class MetricsCollector:
    """
    Collects and aggregates application metrics.
    Provides observability into system behavior.
    """

    _instance: Optional["MetricsCollector"] = None

    def __new__(cls):
        """Singleton pattern for metrics collector."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize metrics collector."""
        if self._initialized:
            return

        self.metrics: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self.start_time = time.time()
        self._initialized = True

        logger.info("Metrics collector initialized")

    def increment_counter(self, name: str, value: int = 1, labels: Dict[str, str] = None):
        """Increment a counter metric."""
        key = self._get_metric_key(name, labels)

        if key not in self.metrics:
            self.metrics[key] = {
                "type": MetricType.COUNTER,
                "value": 0,
                "labels": labels or {},
                "updated_at": datetime.now(),
            }

        self.metrics[key]["value"] += value
        self.metrics[key]["updated_at"] = datetime.now()

    def set_gauge(self, name: str, value: float, labels: Dict[str, str] = None):
        """Set a gauge metric (current value)."""
        key = self._get_metric_key(name, labels)

        self.metrics[key] = {
            "type": MetricType.GAUGE,
            "value": value,
            "labels": labels or {},
            "updated_at": datetime.now(),
        }

    def record_histogram(self, name: str, value: float, labels: Dict[str, str] = None):
        """Record a histogram value (for distributions)."""
        key = self._get_metric_key(name, labels)

        if key not in self.metrics:
            self.metrics[key] = {
                "type": MetricType.HISTOGRAM,
                "values": [],
                "count": 0,
                "sum": 0,
                "min": float("inf"),
                "max": float("-inf"),
                "labels": labels or {},
                "updated_at": datetime.now(),
            }

        metric = self.metrics[key]
        metric["values"].append(value)
        metric["count"] += 1
        metric["sum"] += value
        metric["min"] = min(metric["min"], value)
        metric["max"] = max(metric["max"], value)
        metric["updated_at"] = datetime.now()

        # Keep only last 1000 values to prevent memory bloat
        if len(metric["values"]) > 1000:
            metric["values"] = metric["values"][-1000:]

    def start_timer(self, name: str, labels: Dict[str, str] = None) -> str:
        """Start a timer and return the key."""
        key = self._get_metric_key(name, labels)

        self.metrics[f"_timer_{key}"] = {"start_time": time.time(), "labels": labels or {}}

        return key

    def stop_timer(self, name: str, labels: Dict[str, str] = None):
        """Stop a timer and record the duration."""
        key = self._get_metric_key(name, labels)
        timer_key = f"_timer_{key}"

        if timer_key in self.metrics:
            duration = time.time() - self.metrics[timer_key]["start_time"]
            self.record_histogram(f"{name}_duration_seconds", duration, labels)
            del self.metrics[timer_key]

    def _get_metric_key(self, name: str, labels: Dict[str, str] = None) -> str:
        """Generate unique key for metric with labels."""
        if not labels:
            return name

        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def get_metrics(self) -> Dict[str, Any]:
        """Get all metrics as a dictionary."""
        return {
            key: {
                **value,
                "updated_at": (
                    value["updated_at"].isoformat()
                    if isinstance(value.get("updated_at"), datetime)
                    else None
                ),
            }
            for key, value in self.metrics.items()
            if not key.startswith("_timer_")
        }

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of key metrics."""
        uptime = time.time() - self.start_time

        # Calculate request metrics
        total_requests = sum(
            m["value"]
            for m in self.metrics.values()
            if m.get("type") == MetricType.COUNTER and "request" in str(m)
        )

        # Get average response time
        response_times = [
            m
            for m in self.metrics.values()
            if m.get("type") == MetricType.HISTOGRAM and "duration" in str(m)
        ]

        avg_response_time = 0
        if response_times:
            total_sum = sum(m["sum"] for m in response_times)
            total_count = sum(m["count"] for m in response_times)
            avg_response_time = total_sum / total_count if total_count > 0 else 0

        return {
            "uptime_seconds": round(uptime, 2),
            "total_requests": total_requests,
            "avg_response_time_seconds": round(avg_response_time, 3),
            "active_metrics": len(self.metrics),
            "timestamp": datetime.now().isoformat(),
        }

    def reset(self):
        """Reset all metrics (useful for testing)."""
        self.metrics.clear()
        self.start_time = time.time()
        logger.info("Metrics reset")


class HealthChecker:
    """
    Comprehensive health checking for all system components.
    """

    @staticmethod
    async def check_system_health() -> Dict[str, Any]:
        """Check overall system health."""
        health_status = {"status": "healthy", "timestamp": datetime.now().isoformat(), "checks": {}}

        # Check memory
        try:
            memory = psutil.virtual_memory()
            health_status["checks"]["memory"] = {
                "status": "healthy" if memory.percent < 90 else "degraded",
                "usage_percent": round(memory.percent, 2),
                "available_gb": round(memory.available / (1024**3), 2),
            }
        except Exception as e:
            health_status["checks"]["memory"] = {"status": "unknown", "error": str(e)}

        # Check CPU
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            health_status["checks"]["cpu"] = {
                "status": "healthy" if cpu_percent < 90 else "degraded",
                "usage_percent": round(cpu_percent, 2),
                "cores": psutil.cpu_count(),
            }
        except Exception as e:
            health_status["checks"]["cpu"] = {"status": "unknown", "error": str(e)}

        # Check disk
        try:
            disk = psutil.disk_usage("/")
            health_status["checks"]["disk"] = {
                "status": "healthy" if disk.percent < 90 else "degraded",
                "usage_percent": round(disk.percent, 2),
                "free_gb": round(disk.free / (1024**3), 2),
            }
        except Exception as e:
            health_status["checks"]["disk"] = {"status": "unknown", "error": str(e)}

        # Check PostgreSQL. This is the system of record *and* the tenancy
        # boundary -- RLS lives here -- so it is never merely "degraded".
        try:
            from app.db.session import check_database_health

            db = await check_database_health()
            health_status["checks"]["postgres"] = db
        except Exception as e:
            health_status["checks"]["postgres"] = {"status": "unhealthy", "error": str(e)}

        # Check Redis. A fallback to the in-process store is genuinely degraded
        # rather than unhealthy: the application still serves, but token
        # revocation and rate limits stop being shared across workers.
        try:
            from app.core.redis_client import is_fallback, ping_redis

            live = await ping_redis()
            health_status["checks"]["redis"] = {
                "status": "healthy" if live else "degraded",
                "mode": "in-process-fallback" if is_fallback() else "redis",
                "shared_across_workers": live,
            }
        except Exception as e:
            health_status["checks"]["redis"] = {"status": "degraded", "error": str(e)}

        # Check the categoriser. An unregistered model is degraded, not broken:
        # `CategorizerService` falls back to keyword rules tagged `rules-v0`,
        # and that version is persisted on every row it labels.
        try:
            from app.ml.registry import get_registry

            active = get_registry().active_version("transaction-categoriser")
            health_status["checks"]["categoriser"] = {
                "status": "healthy" if active else "degraded",
                "active_version": active or "rules-v0 (keyword fallback)",
            }
        except Exception as e:
            health_status["checks"]["categoriser"] = {"status": "degraded", "error": str(e)}

        # Determine overall status
        statuses = [check.get("status", "unknown") for check in health_status["checks"].values()]
        if "unhealthy" in statuses:
            health_status["status"] = "unhealthy"
        elif "degraded" in statuses:
            health_status["status"] = "degraded"

        return health_status

    @staticmethod
    async def check_readiness() -> Dict[str, Any]:
        """
        Can this instance serve correctly *right now*?

        Only PostgreSQL is load-bearing. Redis degrading to the in-process
        store still serves correct answers, just not correctly shared ones, so
        it must not pull the pod out of rotation. The database must, because
        without it Row-Level Security cannot enforce tenancy.
        """
        readiness = {"ready": True, "timestamp": datetime.now().isoformat(), "checks": {}}

        try:
            from app.db.session import check_database_health

            db = await check_database_health()
            ok = db.get("status") == "healthy"
            readiness["checks"]["postgres"] = {"ready": ok, **db}
            if not ok:
                readiness["ready"] = False
        except Exception as e:
            readiness["ready"] = False
            readiness["checks"]["postgres"] = {"ready": False, "error": str(e)}

        try:
            from app.core.redis_client import ping_redis

            live = await ping_redis()
            readiness["checks"]["redis"] = {
                "ready": True,
                "degraded": not live,
                "note": None if live else "in-process fallback; single-worker only",
            }
        except Exception as e:
            readiness["checks"]["redis"] = {"ready": True, "degraded": True, "error": str(e)}

        return readiness


# Global metrics collector instance
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Get the global metrics collector instance."""
    global _metrics_collector

    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()

    return _metrics_collector


# Context manager for timing operations
class Timer:
    """Context manager for timing operations."""

    def __init__(self, name: str, labels: Dict[str, str] = None):
        self.name = name
        self.labels = labels
        self.collector = get_metrics_collector()
        self.key = None

    def __enter__(self):
        self.key = self.collector.start_timer(self.name, self.labels)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.collector.stop_timer(self.name, self.labels)
