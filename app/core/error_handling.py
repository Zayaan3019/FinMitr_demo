"""
Advanced error handling and resilience patterns.
Includes custom exceptions, circuit breaker, and error recovery strategies.
"""

from typing import Optional, Any, Callable
from enum import Enum
from datetime import datetime
import functools
import asyncio
import time

from app.core.logging import get_logger

logger = get_logger(__name__)


# ==================== Custom Exception Hierarchy ====================


class FinGuruException(Exception):
    """Base exception for all FinGuru errors."""

    def __init__(self, message: str, details: Optional[dict] = None):
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.now()
        super().__init__(self.message)


class ValidationError(FinGuruException):
    """Raised when input validation fails."""

    pass


class DatabaseError(FinGuruException):
    """Raised when database operations fail."""

    pass


class LLMError(FinGuruException):
    """Raised when LLM operations fail."""

    pass


class RateLimitError(FinGuruException):
    """Raised when rate limits are exceeded."""

    pass


class AuthenticationError(FinGuruException):
    """Raised when authentication fails."""

    pass


class InsufficientDataError(FinGuruException):
    """Raised when insufficient data is available for analysis."""

    pass


class CacheError(FinGuruException):
    """Raised when cache operations fail."""

    pass


# ==================== Circuit Breaker Pattern ====================


class CircuitState(Enum):
    """States for circuit breaker pattern."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """
    Circuit breaker pattern implementation for resilient external service calls.
    Prevents cascading failures by failing fast when service is unavailable.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exceptions: tuple = (Exception,),
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before trying again
            expected_exceptions: Tuple of exceptions to catch
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exceptions = expected_exceptions

        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = CircuitState.CLOSED

        logger.info(
            f"Circuit breaker initialized: "
            f"threshold={failure_threshold}, timeout={recovery_timeout}s"
        )

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.

        Args:
            func: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            Exception: If circuit is open or function fails
        """
        # Check if circuit is open
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker entering HALF_OPEN state")
            else:
                raise Exception(
                    "Circuit breaker is OPEN. "
                    "Service unavailable. Try again after recovery timeout."
                )

        try:
            # Execute function
            result = func(*args, **kwargs)

            # Success - reset if in half-open state
            if self.state == CircuitState.HALF_OPEN:
                self._reset()

            return result

        except self.expected_exceptions as e:
            # Record failure
            self._record_failure()
            logger.warning(
                f"Circuit breaker recorded failure: {e}. "
                f"Count: {self.failure_count}/{self.failure_threshold}"
            )
            raise

    async def call_async(self, func: Callable, *args, **kwargs) -> Any:
        """Async version of call method."""
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker entering HALF_OPEN state")
            else:
                raise Exception("Circuit breaker is OPEN. Service unavailable.")

        try:
            result = await func(*args, **kwargs)

            if self.state == CircuitState.HALF_OPEN:
                self._reset()

            return result

        except self.expected_exceptions as e:
            self._record_failure()
            logger.warning(
                f"Circuit breaker recorded failure: {e}. "
                f"Count: {self.failure_count}/{self.failure_threshold}"
            )
            raise

    def _record_failure(self):
        """Record a failure and potentially open the circuit."""
        self.failure_count += 1
        self.last_failure_time = datetime.now()

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error(f"Circuit breaker OPENED after {self.failure_count} failures")

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_failure_time is None:
            return True

        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout

    def _reset(self):
        """Reset circuit breaker to closed state."""
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        logger.info("Circuit breaker CLOSED - service recovered")

    def get_state(self) -> dict:
        """Get current circuit breaker state."""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "last_failure": self.last_failure_time.isoformat() if self.last_failure_time else None,
        }


# ==================== Retry with Exponential Backoff ====================


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """
    Decorator for retrying functions with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
        exponential_base: Base for exponential backoff calculation
        exceptions: Tuple of exceptions to catch and retry

    Example:
        @retry_with_backoff(max_attempts=3, initial_delay=1.0)
        def unreliable_function():
            # May fail sometimes
            pass
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            attempt = 0
            delay = initial_delay

            while attempt < max_attempts:
                try:
                    return await func(*args, **kwargs)

                except exceptions as e:
                    attempt += 1

                    if attempt >= max_attempts:
                        logger.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    # Calculate delay with exponential backoff
                    delay = min(initial_delay * (exponential_base ** (attempt - 1)), max_delay)

                    logger.warning(
                        f"Function {func.__name__} failed (attempt {attempt}/{max_attempts}). "
                        f"Retrying in {delay:.2f}s... Error: {e}"
                    )

                    await asyncio.sleep(delay)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            attempt = 0
            delay = initial_delay

            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)

                except exceptions as e:
                    attempt += 1

                    if attempt >= max_attempts:
                        logger.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    delay = min(initial_delay * (exponential_base ** (attempt - 1)), max_delay)

                    logger.warning(
                        f"Function {func.__name__} failed (attempt {attempt}/{max_attempts}). "
                        f"Retrying in {delay:.2f}s... Error: {e}"
                    )

                    time.sleep(delay)

        # Return appropriate wrapper
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


# ==================== Timeout Decorator ====================


def with_timeout(seconds: float):
    """
    Decorator to enforce timeout on async functions.

    Args:
        seconds: Timeout in seconds

    Example:
        @with_timeout(30.0)
        async def slow_operation():
            # Long running operation
            pass
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError:
                logger.error(f"Function {func.__name__} exceeded timeout of {seconds}s")
                raise TimeoutError(f"Operation timed out after {seconds} seconds")

        return wrapper

    return decorator


# ==================== Fallback Decorator ====================


def with_fallback(fallback_value: Any = None, fallback_func: Optional[Callable] = None):
    """
    Decorator to provide fallback value/function on error.

    Args:
        fallback_value: Value to return on error
        fallback_func: Function to call on error (takes exception as argument)

    Example:
        @with_fallback(fallback_value=[])
        def may_fail():
            # Risky operation
            pass
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Function {func.__name__} failed, using fallback. Error: {e}")

                if fallback_func:
                    return fallback_func(e)
                return fallback_value

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Function {func.__name__} failed, using fallback. Error: {e}")

                if fallback_func:
                    return fallback_func(e)
                return fallback_value

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


# ==================== Error Recovery Strategies ====================


class ErrorRecoveryStrategy:
    """Strategies for recovering from errors."""

    @staticmethod
    def log_and_continue(error: Exception) -> None:
        """Log error and continue execution."""
        logger.error(f"Error occurred but continuing: {error}", exc_info=True)

    @staticmethod
    def log_and_retry(error: Exception, func: Callable, *args, **kwargs) -> Any:
        """Log error and retry once."""
        logger.warning(f"Error occurred, retrying: {error}")
        return func(*args, **kwargs)

    @staticmethod
    def log_and_fallback(error: Exception, fallback: Any) -> Any:
        """Log error and return fallback value."""
        logger.error(f"Error occurred, using fallback: {error}")
        return fallback

    @staticmethod
    def log_and_raise(error: Exception) -> None:
        """Log error with full context and re-raise."""
        logger.error(f"Critical error occurred: {error}", exc_info=True)
        raise


# Global circuit breakers for critical services
llm_circuit_breaker = CircuitBreaker(
    failure_threshold=5, recovery_timeout=60, expected_exceptions=(Exception,)
)

database_circuit_breaker = CircuitBreaker(
    failure_threshold=3, recovery_timeout=30, expected_exceptions=(Exception,)
)
