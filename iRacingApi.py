from iracingdataapi.exceptions import AccessTokenInvalid
import sqlCommands as sql
import logging
import time
from datetime import datetime
from collections import namedtuple
from iRacingAuthWrapper import (
    login,
    get_authenticated_client,
    is_rate_limited,
    get_rate_limit_remaining,
    _client_manager,
)
from rateLimit import RateLimitError, retry_on_transient_error

raceAndDriverObj = namedtuple(
    "raceAndDriverData",
    [
        "display_name",
        "series_name",
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

# Car data cache (cars rarely change, so cache them)
_cars_cache = None
_cars_cache_time = 0
CARS_CACHE_DURATION = 3600  # Cache for 1 hour


@retry_on_transient_error(max_retries=3, base_delay=1)
def get_cached_cars():
    """Get car data with caching to reduce API calls"""
    global _cars_cache, _cars_cache_time

    # Check rate limit before making API calls
    if is_rate_limited():
        raise RateLimitError(get_rate_limit_remaining())

    current_time = time.time()

    # Return cached data if still valid
    if _cars_cache is not None and (current_time - _cars_cache_time) < CARS_CACHE_DURATION:
        logging.debug("Using cached car data")
        return _cars_cache

    # Fetch fresh data
    try:
        ir_client = get_authenticated_client()
        if ir_client is None:
            logging.warning("Not ready (rate limited or login failed) when fetching car data")
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


def getLastRaceIfNew(cust_id, channel_id):
    try:
        logging.info(f"Checking for new race: cust_id={cust_id}, channel_id={channel_id}")
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


@retry_on_transient_error(max_retries=3, base_delay=1)
def getLastRaceByCustId(cust_id):
    # Check rate limit before making API calls
    if is_rate_limited():
        raise RateLimitError(get_rate_limit_remaining())

    try:
        logging.info(f"Getting last race for cust_id={cust_id}")
        ir_client = get_authenticated_client()

        if ir_client is None:
            logging.error(f"Failed to login to iRacing API for cust_id={cust_id}")
            return None

        logging.info(f"Successfully logged in, fetching recent races for cust_id={cust_id}")
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


@retry_on_transient_error(max_retries=3, base_delay=1)
def getRaceBySubsessionId(subsession_id, cust_id):
    """Get race data from a subsession ID for posting.

    Uses stats_member_recent_races() to get pre-formatted driver data
    matching the subsession_id. This ensures compatibility with the
    main loop's data structure and avoids manual parsing of result() data.

    Args:
        subsession_id: The subsession ID to fetch
        cust_id: The customer ID (used to fetch recent races)

    Returns:
        Race data dict (from stats_member_recent_races) or None if not found
    """
    # Check rate limit before making API calls
    if is_rate_limited():
        raise RateLimitError(get_rate_limit_remaining())

    try:
        logging.info(f"Getting race for subsession_id={subsession_id}, cust_id={cust_id}")
        ir_client = get_authenticated_client()

        if ir_client is None:
            logging.error(f"Failed to login to iRacing API for subsession_id={subsession_id}")
            return None

        # Fetch member's recent races using stats API (same as main loop)
        recent_races_data = ir_client.stats_member_recent_races(cust_id=cust_id)
        if not recent_races_data:
            logging.error(f"No recent races found for cust_id={cust_id}")
            return None

        # Find the race matching this subsession_id in recent races
        races = recent_races_data.get("races", [])
        race_data = next((r for r in races if r.get("subsession_id") == subsession_id), None)

        if not race_data:
            # Fallback: For team events, try searching by team_id in search_series()
            logging.info(
                f"Subsession {subsession_id} not found in recent races, trying team search..."
            )

            # We need to search series results, but we don't have season info yet
            # So we'll search recent races for any race from this cust, get a season_year/quarter
            # and use that to search for the team's races
            if races:
                # Use season info from the most recent race as reference
                reference_race = races[0]
                season_year = reference_race.get("season_year")
                season_quarter = reference_race.get("season_quarter")

                if season_year and season_quarter:
                    logging.info(
                        f"Searching series results for season {season_year} Q{season_quarter}"
                    )
                    try:
                        # Search series results by cust_id to find the target subsession
                        series_results = ir_client.search_series(
                            season_year=season_year, season_quarter=season_quarter, cust_id=cust_id
                        )

                        if series_results:
                            # Results can be paginated, check all results
                            results_list = series_results.get("results", [])
                            race_data = next(
                                (
                                    r
                                    for r in results_list
                                    if r.get("subsession_id") == subsession_id
                                ),
                                None,
                            )

                            if race_data:
                                logging.info("Found race via series search")
                    except Exception as e:
                        logging.debug(f"Series search failed: {e}")

            if not race_data:
                # Final fallback: Get season info from result() API and search with that
                logging.info("Trying to get season info from result() API...")
                try:
                    result_data = ir_client.result(subsession_id)
                    if result_data:
                        season_year = result_data.get("season_year")
                        season_quarter = result_data.get("season_quarter")

                        if season_year and season_quarter:
                            logging.info(
                                f"Found season info: {season_year} Q{season_quarter}, searching series..."
                            )
                            series_results = ir_client.search_series(
                                season_year=season_year,
                                season_quarter=season_quarter,
                                cust_id=cust_id,
                            )

                            if series_results:
                                results_list = series_results.get("results", [])
                                race_data = next(
                                    (
                                        r
                                        for r in results_list
                                        if r.get("subsession_id") == subsession_id
                                    ),
                                    None,
                                )

                                if race_data:
                                    logging.info("Found race via season search")
                        else:
                            logging.warning("Race may be older than 90 days (search_series limit)")
                except Exception as e:
                    logging.debug(f"Result API fallback search failed: {e}")

            if not race_data:
                logging.error(
                    f"Subsession {subsession_id} not found in recent races, series search, or result API for cust_id={cust_id}"
                )
                return None

        # Add display_name from API since stats_member_recent_races doesn't include it
        # First try database (for users added via /addUser), then fall back to API
        import sqlCommands as sql

        display_name = sql.get_display_name(cust_id)
        if not display_name:
            # For arbitrary cust_ids (like in /postRace), query the API directly
            display_name = getDriverName(cust_id)
        race_data["display_name"] = display_name

        logging.info(f"Found race: {race_data.get('series_name')} (subsession_id={subsession_id})")
        return race_data

    except AccessTokenInvalid:
        logging.warning(
            f"Access token invalid during API call for subsession_id={subsession_id} - clearing client"
        )
        _client_manager.clear_client()
        return None
    except Exception as e:
        logging.exception(e)
        logging.error(f"Error in getRaceBySubsessionId for subsession_id={subsession_id}")
        return None


def saveLastRaceTimeByCustId(cust_id, race_time, channel_id):
    return sql.save_user_last_race_time(cust_id, race_time, channel_id)


def lastRaceTimeMatching(cust_id, race_time, channel_id):
    saved_last_race_time = sql.get_last_race_time(cust_id, channel_id)
    if saved_last_race_time is None:
        # No previous race record - this is a NEW race
        return False
    return saved_last_race_time == race_time


@retry_on_transient_error(max_retries=3, base_delay=1)
def raceAndDriverData(race, cust_id):
    # Check rate limit before making API calls
    if is_rate_limited():
        raise RateLimitError(get_rate_limit_remaining())

    try:
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

        # Use display_name from race data (from /postRace API) or fall back to database
        display_name = race.get("display_name")
        if not display_name:
            display_name = sql.get_display_name(cust_id)
        series_name = race.get("series_name")
        car_id = race.get("car_id")
        allCarsData = get_cached_cars()

        # Safe car lookup with error handling
        car_matches = list(filter(lambda obj: obj.get("car_id") == car_id, allCarsData))
        if not car_matches:
            logging.error(f"Car with ID {car_id} not found in car data for cust_id={cust_id}")
            return None
        car_name = car_matches[0].get("car_name")

        session_start_time_unfiltered = race.get("session_start_time")
        # Convert to Unix timestamp for Discord's dynamic timestamp format
        dt = datetime.fromisoformat(session_start_time_unfiltered.replace("Z", "+00:00"))
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
            indv_race_data.user_license.split()[-1] if indv_race_data.user_license else "?"
        )

        # Format SR with change and current rating: +0.03 (A2.47)
        sr_change_str = f"{'+' if sr_change > 0 else ''}{sr_change} ({license_class}{new_sr:.2f})"

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


@retry_on_transient_error(max_retries=3, base_delay=1)
def getDriverName(cust_id):
    # Check rate limit before making API calls
    if is_rate_limited():
        raise RateLimitError(get_rate_limit_remaining())

    try:
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


@retry_on_transient_error(max_retries=3, base_delay=1)
def getSubsessionDataByUserId(subsession_id, user_id):
    """Fetch subsession data for a specific driver.
    Handles both individual races and team races (e.g., Daytona 24h)."""
    # Check rate limit before making API calls
    if is_rate_limited():
        raise RateLimitError(get_rate_limit_remaining())

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
            logging.warning(f"No session_results found for subsession_id={subsession_id}")
            return None

        race_sessions = [s for s in all_race_type_results if s.get("simsession_name") == "RACE"]
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
        user_license = getDriverLicense(int(driver_data.get("old_license_level")), licenses)

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
