import time
import functools
import logging
from typing import Any, Callable, Dict, Optional, TypeVar
from flask import current_app, request, has_request_context
from werkzeug.local import LocalProxy

# Type for decorators
F = TypeVar('F', bound=Callable[..., Any])

# Initialize the logger
logger = logging.getLogger(__name__)

# Custom Exception for Rate Limiting
class RateLimitExceeded(Exception):
    """Exception raised when a rate limit is exceeded."""
    pass

# Utility to generate cache keys
def _generate_cache_key(func: Callable, args: tuple, kwargs: Dict[str, Any]) -> str:
    """Generate a unique cache key based on function arguments."""
    arg_string = ':'.join(str(arg) for arg in args)
    kwarg_string = ':'.join(f"{k}={v}" for k, v in sorted(kwargs.items()))
    return f"{func.__module__}:{func.__name__}:{arg_string}:{kwarg_string}"

# Decorator for rate limiting functions
def rate_limit(limit_string: str) -> Callable[[F], F]:
    """Decorator to apply rate limiting to Flask route handlers."""
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                return current_app.limiter.limit(limit_string)(func)(*args, **kwargs)
            except Exception as e:
                logger.error(f"Rate limit exceeded for {func.__name__}: {str(e)}")
                raise RateLimitExceeded("Rate limit exceeded. Please try again later.")
        return wrapped  # type: ignore
    return decorator

# Decorator for caching function results
def cache(ttl: Optional[int] = None) -> Callable[[F], F]:
    """Decorator to cache the result of a function for a specified TTL (Time to Live)."""
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            cache_ttl = ttl or current_app.config.get('CACHE_TTL', 300)
            cache_key = _generate_cache_key(func, args, kwargs)
            result = current_app.cache_manager.get(cache_key)
            if result is None:
                result = func(*args, **kwargs)
                current_app.cache_manager.put(cache_key, result, cache_ttl)
            return result
        return wrapped  # type: ignore
    return decorator

# Decorator to track function performance
def track_performance(func: F) -> F:
    """Decorator to log the execution time of a function for performance tracking."""
    @functools.wraps(func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        start_time = time.time()
        result = func(*args, **kwargs)
        execution_time = time.time() - start_time
        logger.info(f"Function '{func.__name__}' executed in {execution_time:.4f} seconds.")
        return result
    return wrapped  # type: ignore

# Contextual logger for logging with request context
class ContextualLogger:
    """Contextual Logger for logging messages with additional context like request details."""

    def __init__(self):
        self.logger = None

    def init_app(self, app):
        """Initialize the contextual logger with the Flask app's logger."""
        self.logger = app.logger

    def _log(self, level: str, message: str, *args: Any, **kwargs: Any) -> None:
        """Log messages with contextual information if available."""
        if self.logger is None:
            return

        context = {}
        if has_request_context():
            context = {
                'ip': request.remote_addr,
                'path': request.path,
                'method': request.method,
                'user': getattr(request, 'user', None),
            }
        contextual_message = f"[{context}] {message}"
        getattr(self.logger, level)(contextual_message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an informational message with context."""
        self._log('info', message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a warning message with context."""
        self._log('warning', message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an error message with context."""
        self._log('error', message, *args, **kwargs)

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a critical message with context."""
        self._log('critical', message, *args, **kwargs)

# Initialize the contextual logger instance
contextual_logger = ContextualLogger()

# Service proxies for easier access to services within Flask routes
queue_handler = LocalProxy(lambda: current_app.queue_handler)
credit_service = LocalProxy(lambda: current_app.credit_service)
auth_service = LocalProxy(lambda: current_app.auth_service)
cache_manager = LocalProxy(lambda: current_app.cache_manager)
notification_service = LocalProxy(lambda: current_app.notification_service)

# Initialize application-wide utilities
def init_app(app) -> None:
    """Initialize the utilities with the provided Flask application."""
    contextual_logger.init_app(app)
    app.logger.info("Utilities initialized successfully.")