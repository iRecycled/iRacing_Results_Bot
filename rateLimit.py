import functools
import asyncio
import logging


class RateLimitError(Exception):
    """Raised when an API call is blocked due to rate limiting.

    Attributes:
        seconds_remaining: Number of seconds to wait before retry
    """

    def __init__(self, seconds_remaining):
        self.seconds_remaining = seconds_remaining
        super().__init__(f"Rate limited. Retry after {seconds_remaining} seconds.")


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
