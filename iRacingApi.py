from iracingdataapi.client import irDataClient
from iracingdataapi.exceptions import AccessTokenInvalid
import sqlCommands as sql
import os
import logging
import requests
import time
import json
import re
from datetime import datetime
from dotenv import load_dotenv
from collections import namedtuple
from iracing_oauth import mask_secret
from json.decoder import JSONDecodeError
import logging_config

load_dotenv()
logging_config.setup_logging()

raceAndDriverObj = namedtuple(
    "raceAndDriverData",
    [
        "display_name",
        "series_name",
        "series_id",
        "car_name",
        "session_start_time",
        "start_position",
        "finish_position",
        "laps",
        "incidents",
        "points",
        "sr_change",
        "ir_change",
        "track_name",
        "split_number",
        "series_logo",
        "fastest_lap",
        "average_lap",
        "user_license",
        "sof",
    ],
)

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
                    logging.info(
                        "Cached token expired, requesting fresh token from iRacing"
                    )
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

        logging.info(
            f"Rate limited! Blocking OAuth attempts for {resets_in} seconds "
            f"({resets_in // 60} minutes). Will retry after {datetime.fromtimestamp(self._rate_limit_until).strftime('%H:%M:%S')}"
        )
        print(
            f"[RATE LIMIT] OAuth blocked for {resets_in // 60} minutes until {datetime.fromtimestamp(self._rate_limit_until).strftime('%H:%M:%S')}"
        )

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
            logging.info(
                f"Skipping OAuth request - rate limited for {remaining} more seconds"
            )
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

            if response.status_code == 200:
                tokens = response.json()
                token = tokens.get("access_token")
                expires_in = tokens.get(
                    "expires_in", 86400
                )  # Default to 24 hours if not provided
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
                    if (
                        "rate limit exceeded"
                        in error_data.get("error_description", "").lower()
                    ):
                        self._set_rate_limit(response.text)
                        return None
                except:
                    pass

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

# Car data cache (cars rarely change, so cache them)
_cars_cache = None
_cars_cache_time = 0
CARS_CACHE_DURATION = 3600  # Cache for 1 hour


def get_cached_cars():
    """Get car data with caching to reduce API calls"""
    global _cars_cache, _cars_cache_time

    current_time = time.time()

    # Return cached data if still valid
    if (
        _cars_cache is not None
        and (current_time - _cars_cache_time) < CARS_CACHE_DURATION
    ):
        logging.debug("Using cached car data")
        return _cars_cache

    # Fetch fresh data
    try:
        ir_client = get_authenticated_client()
        if ir_client is None:
            logging.warning(
                "Not ready (rate limited or login failed) when fetching car data"
            )
            # Return stale cache if available
            return _cars_cache if _cars_cache is not None else []

        logging.info("Fetching fresh car data from API")
        _cars_cache = ir_client.get_cars()
        _cars_cache_time = current_time
        return _cars_cache
    except Exception as e:
        logging.error(f"Error fetching car data: {e}")
        # Return stale cache if available
        return _cars_cache if _cars_cache is not None else []


def login():
    """Get the iRacing client from the singleton manager with automatic token refresh"""
    try:
        return _client_manager.get_client()
    except AccessTokenInvalid:
        # Token expired - clear and retry once
        logging.debug(
            "Access token invalid during login - clearing client and retrying"
        )
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
                return attr(*args, **kwargs)
            except (AccessTokenInvalid, JSONDecodeError) as e:
                # Token expired or session issue, re-authenticate and retry
                logging.warning(
                    f"Token/session issue during {name} call ({type(e).__name__}) - re-authenticating"
                )
                # Clear both the inner client and the wrapper so we start fresh
                _client_manager._client = None
                _client_manager._token = None
                _client_manager._wrapped_client = None
                new_client = login()
                if new_client is None:
                    raise
                # Update the wrapped client's internal client reference to the new one
                self._client = new_client
                # Retry the call with the updated client
                return getattr(new_client, name)(*args, **kwargs)

        return method_wrapper


def is_rate_limited():
    """Check if we're currently rate limited (for external use)"""
    return _client_manager.is_rate_limited()


def get_rate_limit_remaining():
    """Get seconds remaining on rate limit (for external use)"""
    return _client_manager.get_rate_limit_remaining()


def getLastRaceIfNew(cust_id, channel_id):
    try:
        logging.info(
            f"Checking for new race: cust_id={cust_id}, channel_id={channel_id}"
        )
        last_race = getLastRaceByCustId(cust_id)

        if last_race is not None:
            last_race_time = last_race.get("session_start_time")
            logging.info(f"Found race with time: {last_race_time}")

            if not lastRaceTimeMatching(cust_id, last_race_time, channel_id):
                logging.info(
                    f"New race detected for cust_id={cust_id}! Saving and returning race data."
                )
                saveLastRaceTimeByCustId(cust_id, last_race_time, channel_id)
                return last_race
            else:
                logging.info(f"Race already posted for cust_id={cust_id}, skipping.")
                return None
        else:
            logging.info(f"No races found for cust_id={cust_id}")
            return None
    except Exception as e:
        logging.exception(e)
        logging.error(f"Error in 'getLastRaceIfNew' for cust_id={cust_id}")
        print(f"iRacingApi getLastRaceIfNew error: {e}")
        return None


def getLastRaceByCustId(cust_id):
    try:
        logging.info(f"Getting last race for cust_id={cust_id}")
        ir_client = get_authenticated_client()

        if ir_client is None:
            logging.error(f"Failed to login to iRacing API for cust_id={cust_id}")
            return None

        logging.info(
            f"Successfully logged in, fetching recent races for cust_id={cust_id}"
        )
        lastTenRaces = ir_client.stats_member_recent_races(cust_id=cust_id)

        if lastTenRaces is not None:
            races = lastTenRaces.get("races", [])
            logging.info(f"Found {len(races)} races for cust_id={cust_id}")
            if len(races) > 0:
                firstRace = races[0]
                logging.info(f"Returning most recent race for cust_id={cust_id}")
                return firstRace

        logging.info(f"No races found for cust_id={cust_id}")
        return None
    except AccessTokenInvalid:
        logging.warning(
            f"Access token invalid during API call for cust_id={cust_id} - clearing client"
        )
        _client_manager.clear_client()
        return None
    except Exception as e:
        logging.exception(e)
        logging.error(f"Error in getLastRaceByCustId for cust_id={cust_id}")
        return None


def saveLastRaceTimeByCustId(cust_id, race_time, channel_id):
    return sql.save_user_last_race_time(cust_id, race_time, channel_id)


def lastRaceTimeMatching(cust_id, race_time, channel_id):
    saved_last_race_time = sql.get_last_race_time(cust_id, channel_id)
    if saved_last_race_time is None:
        # No previous race record - this is a NEW race
        return False
    return saved_last_race_time == race_time


def raceAndDriverData(race, cust_id):
    try:
        # Check rate limit before making API calls
        if is_rate_limited():
            logging.warning(
                f"Skipping raceAndDriverData for cust_id={cust_id} - rate limited"
            )
            return None

        ir_client = login()
        if ir_client is None:
            logging.warning(
                f"Skipping raceAndDriverData for cust_id={cust_id} - not ready (rate limited or login failed)"
            )
            return None

        subsession_id = race.get("subsession_id")
        indv_race_data = getSubsessionDataByUserId(subsession_id, cust_id)

        # Check if subsession data retrieval failed
        if indv_race_data is None:
            logging.error(f"Failed to get subsession data for cust_id={cust_id}")
            return None

        display_name = sql.get_display_name(cust_id)
        series_name = race.get("series_name")
        series_id = race.get("series_id")
        car_id = race.get("car_id")
        allCarsData = get_cached_cars()

        # Safe car lookup with error handling
        car_matches = list(filter(lambda obj: obj.get("car_id") == car_id, allCarsData))
        if not car_matches:
            logging.error(
                f"Car with ID {car_id} not found in car data for cust_id={cust_id}"
            )
            return None
        car_name = car_matches[0].get("car_name")

        session_start_time_unfiltered = race.get("session_start_time")
        # Convert to Unix timestamp for Discord's dynamic timestamp format
        dt = datetime.fromisoformat(
            session_start_time_unfiltered.replace("Z", "+00:00")
        )
        unix_timestamp = int(dt.timestamp())
        # Discord format: <t:timestamp:f> shows short date/time in user's local timezone
        session_start_time = f"<t:{unix_timestamp}:f>"
        start_position = race.get("start_position")
        finish_position = race.get("finish_position")
        laps = race.get("laps")
        incidents = race.get("incidents")
        points = race.get("points")
        old_sr = race.get("old_sub_level") / 100
        new_sr = race.get("new_sub_level") / 100
        sr_change = round(new_sr - old_sr, 2)

        # Extract license class letter from user_license (e.g., "A" from "Class A")
        license_class = (
            indv_race_data.user_license.split()[-1]
            if indv_race_data.user_license
            else "?"
        )

        # Format SR with change and current rating: +0.03 (A2.47)
        sr_change_str = (
            f"{'+' if sr_change > 0 else ''}{sr_change} ({license_class}{new_sr:.2f})"
        )

        old_ir = race.get("oldi_rating")
        new_ir = race.get("newi_rating")
        ir_change = new_ir - old_ir
        ir_change_str = f"{'+' if ir_change > 0 else ''}{ir_change} ({new_ir})"
        track_name = race.get("track").get("track_name")

        return formatRaceData(
            display_name,
            series_name,
            car_name,
            session_start_time,
            start_position,
            finish_position,
            laps,
            incidents,
            points,
            sr_change_str,
            ir_change_str,
            track_name,
            indv_race_data.split_number,
            indv_race_data.series_logo,
            indv_race_data.fastest_lap,
            indv_race_data.average_lap,
            indv_race_data.user_license,
            indv_race_data.sof,
            indv_race_data.team_total_laps,
            indv_race_data.team_total_incidents,
        )
    except AccessTokenInvalid:
        logging.warning(
            f"Access token invalid during API call in raceAndDriverData for cust_id={cust_id} - clearing client"
        )
        _client_manager.clear_client()
        return None
    except Exception as e:
        logging.exception(e)
        logging.error(f"Error in raceAndDriverData for cust_id={cust_id}")
        return None


def getDriverName(cust_id):
    try:
        # Check rate limit before making API calls
        if is_rate_limited():
            logging.warning(
                f"Skipping getDriverName for cust_id={cust_id} - rate limited"
            )
            return None

        ir_client = login()
        if ir_client is None:
            logging.warning(
                f"Skipping getDriverName for cust_id={cust_id} - not ready (rate limited or login failed)"
            )
            return None

        data = ir_client.member_profile(cust_id=cust_id)
        if data is None:
            return None
        driver_name = data.get("member_info").get("display_name")
        return driver_name
    except AccessTokenInvalid:
        logging.warning(
            f"Access token invalid during API call in getDriverName for cust_id={cust_id} - clearing client"
        )
        _client_manager.clear_client()
        return None
    except Exception as e:
        logging.exception(e)
        print(f"exception hit: {e}")
        return None


SubsessionData = namedtuple(
    "SubsessionData",
    [
        "split_number",
        "series_logo",
        "fastest_lap",
        "average_lap",
        "user_license",
        "sof",
        "team_total_laps",
        "team_total_incidents",
    ],
)


def _find_driver_in_race_session(race_session, user_id):
    """Find driver data in a RACE session. Handles both individual and team races.
    Returns: (driver_data dict, team_entry dict or None) or (None, None) if not found"""
    if not race_session:
        return None, None

    results = race_session.get("results")
    if not results:
        logging.warning("No results found in RACE session")
        return None, None

    user_id_int = int(user_id)

    # Check each result entry
    for result in results:
        # Case 1: Individual race - cust_id is directly in result
        if result.get("cust_id") == user_id_int:
            logging.debug(f"Found user {user_id} in individual race result")
            return result, None

        # Case 2: Team race - cust_id is in driver_results array
        driver_results = result.get("driver_results")
        if driver_results and isinstance(driver_results, list):
            for driver in driver_results:
                if driver.get("cust_id") == user_id_int:
                    logging.debug(f"Found user {user_id} in team race driver_results")
                    return driver, result  # Return both driver data and team entry

    logging.warning(f"User {user_id} not found in RACE session results")
    return None, None


def _calculate_team_totals(team_entry):
    """Calculate team-wide totals from a team race entry.
    Returns: dict with team_total_laps and team_total_incidents"""
    team_totals = {"team_total_laps": 0, "team_total_incidents": 0}

    driver_results = team_entry.get("driver_results")
    if driver_results and isinstance(driver_results, list):
        for driver in driver_results:
            team_totals["team_total_laps"] += driver.get("laps_complete", 0)
            team_totals["team_total_incidents"] += driver.get("incidents", 0)

    return team_totals


def getSubsessionDataByUserId(subsession_id, user_id):
    """Fetch subsession data for a specific driver.
    Handles both individual races and team races (e.g., Daytona 24h)."""
    try:
        ir_client = get_authenticated_client()
        if ir_client is None:
            logging.error("Failed to login in getSubsessionDataByUserId")
            return None

        # Fetch race result data
        race_result = ir_client.result(subsession_id)
        if not race_result:
            logging.error(f"No race result found for subsession_id={subsession_id}")
            return None

        # Extract common race metadata
        licenses = race_result.get("allowed_licenses")
        all_splits = race_result.get("associated_subsession_ids")
        split = getSplitNumber(all_splits, subsession_id)
        split_number = (
            f"{split} of {len(all_splits)}"
            if split is not None and all_splits is not None
            else "N/A"
        )
        series_logo = race_result.get("series_logo")
        sof = race_result.get("event_strength_of_field")

        # Find RACE session in session_results
        all_race_type_results = race_result.get("session_results")
        if not all_race_type_results:
            logging.warning(
                f"No session_results found for subsession_id={subsession_id}"
            )
            return None

        race_sessions = [
            s for s in all_race_type_results if s.get("simsession_name") == "RACE"
        ]
        if not race_sessions:
            session_names = [s.get("simsession_name") for s in all_race_type_results]
            logging.warning(
                f"No RACE session found for subsession_id={subsession_id}. Available sessions: {session_names}"
            )
            return None

        # Find the driver in the race session
        race_session = race_sessions[0]
        driver_data, team_entry = _find_driver_in_race_session(race_session, user_id)

        if not driver_data:
            return None

        # Extract driver-specific stats
        fastest_lap = convert_time(driver_data.get("best_lap_time"))
        average_lap = convert_time(driver_data.get("average_lap"))
        user_license = getDriverLicense(
            int(driver_data.get("old_license_level")), licenses
        )

        # Calculate team totals if this is a team race
        team_totals = {"team_total_laps": 0, "team_total_incidents": 0}
        if team_entry:
            team_totals = _calculate_team_totals(team_entry)

        # Build and return subsession data
        data = SubsessionData(
            split_number,
            series_logo,
            fastest_lap,
            average_lap,
            user_license,
            sof,
            team_totals["team_total_laps"],
            team_totals["team_total_incidents"],
        )
        return data
    except AccessTokenInvalid:
        logging.warning(
            f"Access token invalid during API call in getSubsessionDataByUserId for subsession_id={subsession_id} - clearing client"
        )
        _client_manager.clear_client()
        return None
    except Exception as e:
        logging.exception(e)
        logging.error(
            f"Error in getSubsessionDataByUserId for subsession_id={subsession_id}, user_id={user_id}"
        )
        return None


def getSplitNumber(all_splits, subsession_id):
    try:
        index = all_splits.index(subsession_id)
        split_number = index + 1
        return split_number
    except Exception as e:
        logging.exception(e)
        print("get split error")
        return None


def convert_time(time):
    # Handle None, empty string, or invalid time values
    if time is None or time == "" or time == 0:
        return "N/A"

    try:
        time_str = str(time)
        # Handle negative times (DNS/DNF cases)
        if time_str.startswith("-"):
            return "N/A"

        # Need at least 4 characters for the format
        if len(time_str) < 4:
            return "N/A"

        minutes = int(time_str[:-4]) // 60
        seconds = int(time_str[:-4]) % 60
        milliseconds = int(time_str[-4:-1])

        if minutes == 0:
            return "{:02d}.{:03d}".format(seconds, milliseconds)

        return "{}:{:02d}.{:03d}".format(minutes, seconds, milliseconds)
    except (ValueError, IndexError) as e:
        logging.debug(f"convert_time: Invalid time value '{time}': {e}")
        return "N/A"


def getDriverLicense(license_level, allowed_licenses):
    for license_info in allowed_licenses:
        min_level = license_info["min_license_level"]
        max_level = license_info["max_license_level"]
        group_name = license_info["group_name"]

        if min_level <= license_level <= max_level:
            return group_name

    return None


def formatRaceData(
    display_name,
    series_name,
    car_name,
    session_start_time,
    start_position,
    finish_position,
    laps,
    incidents,
    points,
    sr_change_str,
    ir_change_str,
    track_name,
    split_number,
    series_logo,
    fastest_lap,
    average_lap,
    user_license,
    sof,
    team_total_laps,
    team_total_incidents,
):
    message = (
        f"Name: {display_name}\n"
        f"Series Name: {series_name}\n"
        f"Car: {car_name}\n"
        f"Track Name: {track_name}\n"
        f"Session Start Time: {session_start_time}\n"
        f"Start Position: {start_position}\n"
        f"Finish Position: {finish_position}\n"
        f"Laps complete: {laps}\n"
        f"Incidents: {incidents}\n"
    )

    # Add team stats if this is a team race (team_total_laps will be > 0 for team races)
    if team_total_laps > 0:
        message += f"Team Total Laps: {team_total_laps}\n"
        message += f"Team Total Incidents: {team_total_incidents}\n"

    message += (
        f"Points: {points}\n"
        f"Strength of Field (SOF): {sof}\n"
        f"SR Change: {sr_change_str}\n"
        f"iRating Change: {ir_change_str}\n"
        f"User License: {user_license}\n"
        f"Split Number: {split_number}\n"
        # f"Series Logo: {series_logo}\n"
        f"Fastest Lap: {fastest_lap}\n"
        f"Average Lap: {average_lap}\n"
    )

    return message


# def test_api_request():
#     ir_client = get_authenticated_client()
#     if ir_client is None:
#         return "Not ready - rate limited or login failed"

#     try:
#         # Fetch the specific Daytona 24h subsession results
#         subsession_id = 82799848
#         data = ir_client.result(subsession_id)
#         logging.info(f"API response type: {type(data)}")
#         logging.info(f"API response: {data}")

#         # Write to test2.json file
#         try:
#             with open('test2.json', 'w') as f:
#                 json.dump(data, f, indent=2, default=str)
#             return "Test data written to test2.json"
#         except Exception as json_e:
#             logging.error(f"Failed to serialize to JSON: {json_e}")
#             # Return a string representation instead
#             with open('test2.json', 'w') as f:
#                 f.write(str(data))
#             return "Test data written to test2.json (as string)"
#     except Exception as e:
#         logging.exception(e)
#         logging.error(f"Error in test_api_request: {type(e).__name__}: {e}")
#         return f"Error fetching data: {e}"
