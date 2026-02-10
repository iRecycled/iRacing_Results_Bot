import functools
import asyncio
import logging
import time


class RateLimitError(Exception):
    """Raised when an API call is blocked due to rate limiting.

    Attributes:
        seconds_remaining: Number of seconds to wait before retry
    """

    def __init__(self, seconds_remaining):
        self.seconds_remaining = seconds_remaining
        super().__init__(f"Rate limited. Retry after {seconds_remaining} seconds.")


def retry_on_transient_error(max_retries=4, base_delay=60):
    """Decorator that retries on transient HTTP errors (503, 504, 429, etc.).

    Uses exponential backoff: delay = base_delay * (2 ^ attempt_number)
    Total max wait time: ~15 minutes before giving up.

    Starts with 60s delay to minimize OAuth token consumption while giving
    the iRacing API time to recover from brief outages.

    Args:
        max_retries: Maximum number of retry attempts (default 4)
        base_delay: Initial delay in seconds before first retry (default 60)
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while attempt < max_retries:
                try:
                    return func(*args, **kwargs)
                except RuntimeError as e:
                    # Check if this is an HTTP error we should retry
                    error_str = str(e)
                    should_retry = False

                    # Look for 503, 504, 429 in the error message
                    if any(code in error_str for code in ["503", "504", "429", "502"]):
                        should_retry = True

                    if should_retry and attempt < max_retries - 1:
                        attempt += 1
                        delay = base_delay * (2 ** (attempt - 1))
                        logging.warning(
                            f"Transient API error in {func.__name__} ({error_str}) - Retrying in {delay}s (attempt {attempt}/{max_retries - 1})"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        raise

        return wrapper

    return decorator


def rate_limit_handler(func):
    """Decorator that automatically handles rate limiting by waiting and retrying.

    Works with async functions that call sync functions via run_in_executor().
    When a RateLimitError is raised from a sync function, this decorator:
    1. Logs the rate limit status
    2. Sleeps for the specified duration + 5 second buffer
    3. Automatically retries the function

    The decorator uses a max retry count to prevent infinite loops in edge cases.

    Args:
        func: An async function that may raise RateLimitError

    Returns:
        An async wrapper function with retry logic
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        retry_count = 0
        max_retries = 3  # Prevent infinite loops in edge cases

        while retry_count < max_retries:
            try:
                return await func(*args, **kwargs)
            except RateLimitError as e:
                retry_count += 1
                remaining = e.seconds_remaining
                minutes = remaining // 60
                msg = f"[RATE LIMITED] Waiting {remaining} seconds ({minutes} minutes) before retrying {func.__name__} (attempt {retry_count}/{max_retries})"
                print(msg)
                logging.info(msg)

                # Wait for rate limit duration + 5 second buffer to ensure it has cleared
                await asyncio.sleep(remaining + 5)
                # Loop continues to retry

        # If we hit max retries, raise exception to prevent infinite loops
        logging.error(
            f"Max retries ({max_retries}) exceeded for {func.__name__} due to rate limiting"
        )
        raise RateLimitError(0)

    return wrapper
