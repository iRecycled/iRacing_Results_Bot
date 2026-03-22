from iracingdataapi.client import irDataClient
from iracingdataapi.exceptions import AccessTokenInvalid
import os
import logging
import requests
import time
import json
import re
from datetime import datetime
from dotenv import load_dotenv
from iracing_oauth import mask_secret
from json.decoder import JSONDecodeError
from logging_config import append_rate_limit_log

load_dotenv()


# Track API request count since last reset/startup
_api_request_count = 0
_api_request_count_reset_time = time.time()


def track_api_request():
    """Increment the API request counter. Call this before each iRacing API call."""
    global _api_request_count
    _api_request_count += 1


def get_api_request_count():
    """Get the current request count and how long the window has been open."""
    elapsed = int(time.time() - _api_request_count_reset_time)
    return _api_request_count, elapsed


def _reset_api_request_count():
    """Reset the counter (called when rate limit is hit, so next window starts fresh)."""
    global _api_request_count, _api_request_count_reset_time
    _api_request_count = 0
    _api_request_count_reset_time = time.time()


def _log_rate_limit_event(retry_after, resets_in, error_response):
    """Append rate limit event to a dedicated log file for easy monitoring."""
    try:
        count, elapsed_seconds = get_api_request_count()
        elapsed_minutes = elapsed_seconds / 60
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        append_rate_limit_log(
            f"{timestamp} | requests_made={count} in {elapsed_minutes:.1f}min "
            f"| retry_after={retry_after}s | resets_in={resets_in}s"
        )
        _reset_api_request_count()
    except Exception as e:
        logging.warning(f"Failed to write rate limit log: {e}")


def _log_token_rate_limit_headers(response):
    """Log the rate limit headers from the /token endpoint to rate_limits.log."""
    try:
        limit = response.headers.get("RateLimit-Limit")
        remaining = response.headers.get("RateLimit-Remaining")
        reset = response.headers.get("RateLimit-Reset")

        if limit or remaining or reset:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            append_rate_limit_log(
                f"{timestamp} | TOKEN endpoint | limit={limit} remaining={remaining} reset={reset}s"
            )
    except Exception as e:
        logging.warning(f"Failed to log token rate limit headers: {e}")


def _log_data_api_rate_limit(client, method_name):
    """Log the rate limit headers from data API calls to rate_limits.log.

    The iracingdataapi client exposes rate limit info via client.rate_limit
    which reads x-ratelimit-limit, x-ratelimit-remaining, x-ratelimit-reset headers.
    """
    try:
        rl = client.rate_limit
        if rl is None:
            return

        limit = rl.limit
        remaining = rl.remaining
        try:
            secs = rl.seconds_until_reset
            reset_seconds = max(0, int(secs)) if secs is not None else 0
        except (TypeError, ValueError):
            reset_seconds = 0

        if limit is not None or remaining is not None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            count, elapsed = get_api_request_count()
            append_rate_limit_log(
                f"{timestamp} | DATA API ({method_name}) | limit={limit} remaining={remaining} "
                f"reset={reset_seconds}s | requests_this_session={count}"
            )
    except Exception as e:
        logging.warning(f"Failed to log data API rate limit: {e}")


# OAuth credentials
CLIENT_ID = os.getenv("IRACING_CLIENT_ID")
CLIENT_SECRET = os.getenv("IRACING_CLIENT_SECRET")
TOKEN_URL = "https://oauth.iracing.com/oauth2/token"
ENV_FILE_PATH = ".env"


def _update_env_token(token, expires_in):
    """Update the cached token in .env file with expiration timestamp"""
    try:
        with open(ENV_FILE_PATH, "r") as f:
            lines = f.readlines()

        # Calculate expiration timestamp (with 5 minute buffer for safety)
        expiration_time = time.time() + expires_in - 300

        # Find and replace or add IRACING_TOKEN and IRACING_TOKEN_EXPIRES lines
        found_token = False
        found_expires = False
        new_lines = []
        for line in lines:
            if line.startswith("IRACING_TOKEN="):
                new_lines.append(f"IRACING_TOKEN={token}\n")
                found_token = True
            elif line.startswith("IRACING_TOKEN_EXPIRES="):
                new_lines.append(f"IRACING_TOKEN_EXPIRES={expiration_time}\n")
                found_expires = True
            else:
                new_lines.append(line)

        if not found_token:
            new_lines.append(f"IRACING_TOKEN={token}\n")
        if not found_expires:
            new_lines.append(f"IRACING_TOKEN_EXPIRES={expiration_time}\n")

        with open(ENV_FILE_PATH, "w") as f:
            f.writelines(new_lines)

        logging.debug("Updated cached token in .env")
    except Exception as e:
        logging.warning(f"Failed to update token in .env: {e}")


def _get_cached_token():
    """Retrieve cached token from .env file if it hasn't expired"""
    try:
        cached_token = os.getenv("IRACING_TOKEN")
        expires_at = os.getenv("IRACING_TOKEN_EXPIRES")

        if cached_token and expires_at:
            try:
                expiration_time = float(expires_at)
                # Check if token is still valid (with 5 minute buffer)
                if time.time() < expiration_time:
                    logging.info("Using cached OAuth token from .env file")
                    return cached_token
                else:
                    logging.info("Cached token expired, requesting fresh token from iRacing")
            except ValueError:
                logging.warning("Invalid token expiration time in .env")
    except Exception as e:
        logging.warning(f"Failed to read cached token: {e}")
    return None


# Singleton class to manage iRacing client
class iRacingClientManager:
    _instance = None
    _client = None
    _wrapped_client = None  # Cache the wrapped client
    _token = None
    _rate_limit_until = 0  # Timestamp when we can retry
    _rate_limit_reset = 0  # Timestamp when limit fully resets

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(iRacingClientManager, cls).__new__(cls)
        return cls._instance

    def _parse_rate_limit_error(self, error_response):
        """Parse the rate limit error to extract timing information"""
        try:
            if isinstance(error_response, str):
                data = json.loads(error_response)
            else:
                data = error_response

            error_desc = data.get("error_description", "")

            # Parse "retry after X seconds" and "resets in Y seconds"
            retry_match = re.search(r"retry after (\d+) seconds", error_desc)
            reset_match = re.search(r"resets in (\d+) seconds", error_desc)

            retry_after = int(retry_match.group(1)) if retry_match else 60
            resets_in = int(reset_match.group(1)) if reset_match else 3600

            return retry_after, resets_in
        except (json.JSONDecodeError, AttributeError, ValueError) as e:
            logging.info(f"Failed to parse rate limit error: {e}")
            return 60, 3600  # Default to 1 min retry, 1 hour reset

    def _set_rate_limit(self, error_response):
        """Set the rate limit timestamps based on error response"""
        retry_after, resets_in = self._parse_rate_limit_error(error_response)
        current_time = time.time()

        # Use the full reset time to be safe, add 10 second buffer
        self._rate_limit_until = current_time + resets_in + 10
        self._rate_limit_reset = current_time + resets_in

        msg = (
            f"Rate limited! Blocking OAuth attempts for {resets_in} seconds "
            f"({resets_in // 60} minutes). Will retry after {datetime.fromtimestamp(self._rate_limit_until).strftime('%H:%M:%S')}"
        )
        logging.info(msg)
        print(f"[RATE LIMIT] {msg}")

        # Log to dedicated rate limit file
        _log_rate_limit_event(retry_after, resets_in, error_response)

    def is_rate_limited(self):
        """Check if we're currently rate limited"""
        if time.time() < self._rate_limit_until:
            return True
        return False

    def get_rate_limit_remaining(self):
        """Get seconds remaining on rate limit, or 0 if not limited"""
        remaining = self._rate_limit_until - time.time()
        return max(0, int(remaining))

    def get_oauth_token(self):
        """Get OAuth access token using cached token or password-limited grant with rate limit handling"""
        # Try to use cached token first
        cached_token = _get_cached_token()
        if cached_token:
            logging.info("Using cached OAuth token")
            return cached_token

        # Check rate limit before attempting new token request
        if self.is_rate_limited():
            remaining = self.get_rate_limit_remaining()
            logging.info(f"Skipping OAuth request - rate limited for {remaining} more seconds")
            return None

        username = os.getenv("ir_username")
        password = os.getenv("ir_password")

        if not username or not password or not CLIENT_SECRET:
            logging.error("Missing OAuth credentials in environment variables")
            return None

        try:
            masked_client_secret = mask_secret(CLIENT_SECRET, CLIENT_ID)
            masked_password = mask_secret(password, username)

            data = {
                "grant_type": "password_limited",
                "client_id": CLIENT_ID,
                "client_secret": masked_client_secret,
                "username": username,
                "password": masked_password,
                "scope": "iracing.auth",
            }

            response = requests.post(TOKEN_URL, data=data, timeout=20)

            # Log rate limit headers from token endpoint
            _log_token_rate_limit_headers(response)

            if response.status_code == 200:
                tokens = response.json()
                token = tokens.get("access_token")
                expires_in = tokens.get("expires_in", 86400)  # Default to 24 hours if not provided
                logging.info(
                    "OAuth token obtained successfully - requesting new token from iRacing"
                )
                print("[OAUTH] Fresh token obtained from iRacing servers")
                # Cache the new token with expiration time
                _update_env_token(token, expires_in)
                return token
            elif response.status_code == 401:
                # Check if it's a rate limit error
                try:
                    error_data = response.json()
                    if "rate limit exceeded" in error_data.get("error_description", "").lower():
                        self._set_rate_limit(response.text)
                        return None
                except (JSONDecodeError, ValueError):
                    # Response body is not valid JSON, treat as generic auth failure
                    logging.debug(f"Failed to parse 401 response as JSON: {response.text}")

                logging.error(
                    f"OAuth authentication failed: {response.status_code} - {response.text}"
                )
                return None
            else:
                logging.error(
                    f"OAuth authentication failed: {response.status_code} - {response.text}"
                )
                return None

        except Exception as e:
            logging.exception(e)
            logging.error("Error getting OAuth token")
            return None

    def get_client(self):
        """Get or create the iRacing client with rate limit protection"""
        # Check rate limit FIRST before doing anything
        if self.is_rate_limited():
            remaining = self.get_rate_limit_remaining()
            logging.info(
                f"Skipping login attempt - rate limited for {remaining} more seconds ({remaining // 60} minutes)"
            )
            return None

        if self._client is not None:
            logging.debug("Reusing existing irDataClient instance")
            return self._client

        # Get OAuth token (this will check cache first)
        self._token = self.get_oauth_token()
        if not self._token:
            logging.error("Failed to get OAuth access token")
            return None

        # Initialize client with OAuth token
        logging.info("OAuth token received, initializing irDataClient")
        self._client = irDataClient(access_token=self._token)
        logging.info("Successfully initialized irDataClient with OAuth token")
        print("OAuth client created and ready")

        return self._client

    def clear_client(self):
        """Clear the cached client (useful if token expires)"""
        self._client = None
        self._wrapped_client = None
        self._token = None
        logging.info("Cleared cached iRacing client")


# Create singleton instance
_client_manager = iRacingClientManager()


class _AuthenticatedClientWrapper:
    """Wraps irDataClient to handle token expiration transparently."""

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        """Intercept method calls to handle AccessTokenInvalid."""
        attr = getattr(self._client, name)

        # Only wrap callable methods, not properties
        if not callable(attr):
            return attr

        def method_wrapper(*args, **kwargs):
            try:
                track_api_request()
                result = attr(*args, **kwargs)
                _log_data_api_rate_limit(self._client, name)
                return result
            except (AccessTokenInvalid, JSONDecodeError) as e:
                # Token expired or session issue, re-authenticate and retry
                logging.info(
                    f"Token/session issue during {name} call ({type(e).__name__}) - re-authenticating"
                )
                # Clear both the inner client and the wrapper so we start fresh
                _client_manager._client = None
                _client_manager._token = None
                _client_manager._wrapped_client = None
                from iRacingApi import login

                new_client = login()
                if new_client is None:
                    raise
                # Update the wrapped client's internal client reference to the new one
                self._client = new_client
                # Retry the call with the updated client
                return getattr(new_client, name)(*args, **kwargs)

        return method_wrapper


def login():
    """Get the iRacing client from the singleton manager with automatic token refresh"""
    try:
        return _client_manager.get_client()
    except AccessTokenInvalid:
        # Token expired - clear and retry once
        logging.debug("Access token invalid during login - clearing client and retrying")
        _client_manager.clear_client()
        try:
            return _client_manager.get_client()
        except Exception as retry_error:
            logging.error(f"Failed to get client after token refresh: {retry_error}")
            return None
    except Exception as e:
        logging.exception(e)
        logging.error("Error in login function")
        return None


def get_authenticated_client():
    """Get an authenticated iRacing client. Handles token expiration with automatic re-auth.
    Returns None if rate limited or authentication fails."""
    if is_rate_limited():
        return None

    # Return cached wrapped client if it exists
    if _client_manager._wrapped_client is not None:
        return _client_manager._wrapped_client

    ir_client = login()
    if ir_client is None:
        return None

    # Wrap the client to intercept AccessTokenInvalid and re-authenticate
    _client_manager._wrapped_client = _AuthenticatedClientWrapper(ir_client)
    return _client_manager._wrapped_client


def is_rate_limited():
    """Check if we're currently rate limited (for external use)"""
    return _client_manager.is_rate_limited()


def get_rate_limit_remaining():
    """Get seconds remaining on rate limit (for external use)"""
    return _client_manager.get_rate_limit_remaining()


def get_data_api_rate_limit():
    """Get the current data API rate limit info from the iracingdataapi client.

    Returns:
        dict with 'limit', 'remaining', and 'reset_seconds', or None if unavailable.
    """
    try:
        client = _client_manager._client
        if client is None:
            return None

        rl = client.rate_limit
        if rl is None:
            return None

        return {
            "limit": rl.limit,
            "remaining": rl.remaining,
            "reset_seconds": max(0, int(rl.seconds_until_reset)) if rl.seconds_until_reset else 0,
        }
    except Exception:
        return None
