"""
Security middleware for production-grade protection.
Includes rate limiting, request validation, and security headers.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Dict, Optional
from collections import defaultdict
import hashlib
import time

from app.core.logging import get_logger
from app.core.config import settings

logger = get_logger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware using token bucket algorithm.
    Prevents API abuse and ensures fair usage.
    """

    def __init__(self, app, requests_per_minute: int = 60, requests_per_hour: int = 1000):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour

        # Store: {identifier: {"minute": [(timestamp, count)], "hour": [(timestamp, count)]}}
        self.request_history: Dict[str, Dict[str, list]] = defaultdict(
            lambda: {"minute": [], "hour": []}
        )

        # Cleanup old entries every 5 minutes
        self.last_cleanup = time.time()
        self.cleanup_interval = 300  # 5 minutes

    def _get_client_identifier(self, request: Request) -> str:
        """Generate unique identifier for client (IP + User-Agent)."""
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")

        # Hash for privacy
        identifier = f"{client_ip}:{user_agent}"
        return hashlib.sha256(identifier.encode()).hexdigest()[:16]

    def _cleanup_old_entries(self):
        """Remove expired entries to prevent memory bloat."""
        current_time = time.time()

        if current_time - self.last_cleanup < self.cleanup_interval:
            return

        cutoff_minute = current_time - 60
        cutoff_hour = current_time - 3600

        for identifier in list(self.request_history.keys()):
            history = self.request_history[identifier]

            # Remove old entries
            history["minute"] = [
                (ts, count) for ts, count in history["minute"] if ts > cutoff_minute
            ]
            history["hour"] = [(ts, count) for ts, count in history["hour"] if ts > cutoff_hour]

            # Remove identifier if no recent requests
            if not history["minute"] and not history["hour"]:
                del self.request_history[identifier]

        self.last_cleanup = current_time
        logger.debug(f"Cleaned up rate limit history. Active clients: {len(self.request_history)}")

    def _check_rate_limit(self, identifier: str) -> Optional[str]:
        """
        Check if request exceeds rate limits.

        Returns:
            Error message if rate limit exceeded, None otherwise
        """
        current_time = time.time()
        history = self.request_history[identifier]

        # Check minute limit
        recent_minute = [count for ts, count in history["minute"] if ts > current_time - 60]
        minute_count = sum(recent_minute)

        if minute_count >= self.requests_per_minute:
            return f"Rate limit exceeded: {self.requests_per_minute} requests per minute"

        # Check hour limit
        recent_hour = [count for ts, count in history["hour"] if ts > current_time - 3600]
        hour_count = sum(recent_hour)

        if hour_count >= self.requests_per_hour:
            return f"Rate limit exceeded: {self.requests_per_hour} requests per hour"

        # Record this request
        history["minute"].append((current_time, 1))
        history["hour"].append((current_time, 1))

        return None

    async def dispatch(self, request: Request, call_next):
        """Process request with rate limiting."""
        # Skip rate limiting for health checks
        if request.url.path.endswith("/health"):
            return await call_next(request)

        # Cleanup old entries periodically
        self._cleanup_old_entries()

        # Get client identifier
        identifier = self._get_client_identifier(request)

        # Check rate limit
        error = self._check_rate_limit(identifier)

        if error:
            logger.warning(
                f"Rate limit exceeded for client {identifier[:8]}... on {request.url.path}"
            )
            return JSONResponse(
                status_code=429,
                content={"error": "Too Many Requests", "detail": error, "retry_after": 60},
                headers={"Retry-After": "60"},
            )

        # Continue with request
        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit-Minute"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Limit-Hour"] = str(self.requests_per_hour)

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to all responses.
    Protects against common web vulnerabilities.
    """

    async def dispatch(self, request: Request, call_next):
        """Add security headers to response."""
        response = await call_next(request)

        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        return response


class RequestValidationMiddleware(BaseHTTPMiddleware):
    """
    Validate incoming requests for suspicious patterns.
    Prevents injection attacks and malicious payloads.
    """

    # Suspicious patterns to detect
    SUSPICIOUS_PATTERNS = [
        "<script",
        "javascript:",
        "onerror=",
        "onclick=",
        "onload=",
        "../",
        "..\\",
        "DROP TABLE",
        "DELETE FROM",
        "INSERT INTO",
        "UPDATE SET",
        "'; --",
        "1' OR '1'='1",
    ]

    async def dispatch(self, request: Request, call_next):
        """Validate request for suspicious patterns."""

        # Check URL path
        path = str(request.url.path).lower()
        for pattern in self.SUSPICIOUS_PATTERNS:
            if pattern.lower() in path:
                logger.warning(f"Suspicious pattern detected in URL: {pattern}")
                return JSONResponse(
                    status_code=400,
                    content={"error": "Bad Request", "detail": "Invalid request format"},
                )

        # Check query parameters
        query = str(request.url.query).lower()
        for pattern in self.SUSPICIOUS_PATTERNS:
            if pattern.lower() in query:
                logger.warning(f"Suspicious pattern detected in query: {pattern}")
                return JSONResponse(
                    status_code=400,
                    content={"error": "Bad Request", "detail": "Invalid query parameters"},
                )

        # Check User-Agent (basic bot detection)
        user_agent = request.headers.get("user-agent", "").lower()
        suspicious_agents = ["bot", "crawler", "spider", "scraper"]

        # Allow legitimate bots (health checks, monitoring)
        if request.url.path.endswith("/health"):
            return await call_next(request)

        # Block suspicious bots on sensitive endpoints
        if any(agent in user_agent for agent in suspicious_agents):
            if request.url.path in ["/api/v1/ingest", "/api/v1/chat"]:
                logger.warning(f"Suspicious user agent blocked: {user_agent[:50]}")
                return JSONResponse(
                    status_code=403, content={"error": "Forbidden", "detail": "Access denied"}
                )

        return await call_next(request)


def setup_security_middleware(app):
    """
    Install request validation, rate limiting and security headers.

    The limits come from configuration. They were previously hardcoded to
    60/minute and 1000/hour, which made ``GENERAL_RATE_LIMIT_PER_MINUTE`` dead
    config: it appeared in ``Settings``, in ``.env`` and in the Kubernetes
    ConfigMap, and changing it did nothing. An operator raising the limit
    during an incident would have watched it have no effect.

    Note this is the *general* limiter. Authentication endpoints are limited
    separately and harder (``app/auth/rate_limit.py``), keyed on both IP and
    email, because credential stuffing has a different shape from ordinary
    traffic and 60/minute is far too generous for login.
    """

    # Validation first: reject malformed requests before spending a rate-limit
    # slot on them.
    app.add_middleware(RequestValidationMiddleware)

    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=settings.general_rate_limit_per_minute,
        requests_per_hour=settings.general_rate_limit_per_hour,
    )

    app.add_middleware(SecurityHeadersMiddleware)

    logger.info(
        f"Security middleware initialized: validation, headers, and general "
        f"rate limiting at {settings.general_rate_limit_per_minute}/min, "
        f"{settings.general_rate_limit_per_hour}/hour per client "
        f"(auth endpoints limited separately at "
        f"{settings.auth_rate_limit_per_minute}/min)"
    )
