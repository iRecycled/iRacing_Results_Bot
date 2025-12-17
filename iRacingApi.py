from iracingdataapi.client import irDataClient
import sqlCommands as sql
import os
import logging
import requests
import time
import json
from datetime import datetime
from dotenv import load_dotenv
from collections import namedtuple
from iracing_oauth import mask_secret

load_dotenv()
# Use INFO for debugging, WARNING for production
LOG_LEVEL = logging.INFO if os.getenv('DEBUG_MODE', 'false').lower() == 'true' else logging.WARNING
logging.basicConfig(level=LOG_LEVEL, filename='bot.log', filemode='a', format='%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S')
raceAndDriverObj = namedtuple('raceAndDriverData', [
    'display_name', 'series_name', 'series_id', 'car_name', 'session_start_time',
    'start_position', 'finish_position', 'laps', 'incidents', 'points',
    'sr_change', 'ir_change', 'track_name',
    'split_number', 'series_logo',
    'fastest_lap', 'average_lap', 'user_license', 'sof'
])

# OAuth credentials
CLIENT_ID = os.getenv('IRACING_CLIENT_ID')
CLIENT_SECRET = os.getenv('IRACING_CLIENT_SECRET')
TOKEN_URL = "https://oauth.iracing.com/oauth2/token"

# Singleton class to manage iRacing client
class iRacingClientManager:
    _instance = None
    _client = None
    _token = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(iRacingClientManager, cls).__new__(cls)
        return cls._instance

    def get_oauth_token(self):
        """Get OAuth access token using password-limited grant with rate limit handling"""
        username = os.getenv('ir_username')
        password = os.getenv('ir_password')

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
                "scope": "iracing.auth"
            }

            response = requests.post(TOKEN_URL, data=data, timeout=20)

            if response.status_code == 200:
                tokens = response.json()
                return tokens.get('access_token')
            elif response.status_code == 401:
                # Check if it's a rate limit error
                try:
                    error_data = response.json()
                    if "rate limit exceeded" in error_data.get("error_description", ""):
                        retry_after = error_data.get("error_description", "").split("retry after ")[1].split(" seconds")[0] if "retry after" in error_data.get("error_description", "") else None
                        resets_in = error_data.get("error_description", "").split("resets in ")[1].split(" seconds")[0] if "resets in" in error_data.get("error_description", "") else None

                        logging.warning(f"Rate limit exceeded. Retry after {retry_after}s, resets in {resets_in}s")
                        logging.warning("Bot will skip OAuth requests until rate limit resets")
                        return None
                except:
                    pass

                logging.error(f"OAuth authentication failed: {response.status_code} - {response.text}")
                return None
            else:
                logging.error(f"OAuth authentication failed: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logging.exception(e)
            logging.error("Error getting OAuth token")
            return None

    def get_client(self):
        """Get or create the iRacing client"""
        if self._client is not None:
            logging.debug("Reusing existing irDataClient instance")
            return self._client

        logging.info("No existing client found, creating new OAuth session")
        print("Signing into iRacing with OAuth.")

        # Get OAuth token
        self._token = self.get_oauth_token()
        if not self._token:
            logging.error("Failed to get OAuth access token")
            return None

        logging.info("OAuth token received, initializing irDataClient")
        # Initialize client with OAuth token
        self._client = irDataClient(access_token=self._token)
        logging.info("Successfully initialized irDataClient with OAuth token")
        print(f"OAuth client created and cached")

        return self._client

# Create singleton instance
_client_manager = iRacingClientManager()

def login():
    """Get the iRacing client from the singleton manager"""
    try:
        return _client_manager.get_client()
    except Exception as e:
        logging.exception(e)
        logging.error("Error in login function")
        return None

def getLastRaceIfNew(cust_id, channel_id):
    try:
        logging.info(f"Checking for new race: cust_id={cust_id}, channel_id={channel_id}")
        last_race = getLastRaceByCustId(cust_id)

        if last_race is not None:
            last_race_time = last_race.get('session_start_time')
            logging.info(f"Found race with time: {last_race_time}")

            if not lastRaceTimeMatching(cust_id, last_race_time, channel_id):
                logging.info(f"New race detected for cust_id={cust_id}! Saving and returning race data.")
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
        print(f'iRacingApi getLastRaceIfNew error: {e}')
        return None

def getLastRaceByCustId(cust_id):
    try:
        logging.info(f"Getting last race for cust_id={cust_id}")
        ir_client = login()

        if ir_client is None:
            logging.error(f"Failed to login to iRacing API for cust_id={cust_id}")
            return None

        logging.info(f"Successfully logged in, fetching recent races for cust_id={cust_id}")
        lastTenRaces = ir_client.stats_member_recent_races(cust_id = cust_id)

        if lastTenRaces is not None:
            races = lastTenRaces.get('races', [])
            logging.info(f"Found {len(races)} races for cust_id={cust_id}")
            if len(races) > 0:
                firstRace = races[0]
                logging.info(f"Returning most recent race for cust_id={cust_id}")
                return firstRace

        logging.info(f"No races found for cust_id={cust_id}")
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
        saveLastRaceTimeByCustId(cust_id, race_time, channel_id)
        return True
    return saved_last_race_time == race_time

def raceAndDriverData(race, cust_id):
    ir_client = login()
    subsession_id = race.get('subsession_id')
    indv_race_data = getSubsessionDataByUserId(subsession_id ,cust_id)
    display_name = sql.get_display_name(cust_id)
    series_name = race.get('series_name')
    series_id = race.get('series_id')
    car_id = race.get('car_id')
    allCarsData = ir_client.get_cars()
    car_name = list(filter(lambda obj: obj.get('car_id') == car_id, allCarsData))[0].get('car_name')
    session_start_time_unfiltered = race.get('session_start_time')
    # Convert to Unix timestamp for Discord's dynamic timestamp format
    dt = datetime.fromisoformat(session_start_time_unfiltered.replace('Z', '+00:00'))
    unix_timestamp = int(dt.timestamp())
    # Discord format: <t:timestamp:f> shows short date/time in user's local timezone
    session_start_time = f"<t:{unix_timestamp}:f>"
    start_position = race.get('start_position')
    finish_position = race.get('finish_position')
    laps = race.get('laps')
    incidents = race.get('incidents')
    points = race.get('points')
    old_sr = race.get('old_sub_level') /100
    new_sr = race.get('new_sub_level') /100
    sr_change = round(new_sr - old_sr, 2)

    # Extract license class letter from user_license (e.g., "A" from "Class A")
    license_class = indv_race_data.user_license.split()[-1] if indv_race_data.user_license else "?"

    # Format SR with change and current rating: +0.03 (A2.47)
    sr_change_str = f"{'+' if sr_change > 0 else ''}{sr_change} ({license_class}{new_sr:.2f})"

    old_ir = race.get('oldi_rating')
    new_ir = race.get('newi_rating')
    ir_change = new_ir - old_ir
    ir_change_str = f"{'+' if ir_change > 0 else ''}{ir_change} ({new_ir})"
    track_name = race.get('track').get('track_name')

    return formatRaceData(display_name, series_name, car_name, session_start_time, start_position, finish_position, laps, incidents, points, sr_change_str, ir_change_str, track_name, indv_race_data.split_number, indv_race_data.series_logo, indv_race_data.fastest_lap, indv_race_data.average_lap, indv_race_data.user_license, indv_race_data.sof,)

def getDriverName(cust_id):
    try :
        ir_client = login()
        data = ir_client.member_profile(cust_id = cust_id)
        if data is None:
            return None
        driver_name = data.get('member_info').get('display_name')
        return driver_name
    except Exception as e: 
        logging.exception(e)
        print('exception hit: ' + e)
        return None

SubsessionData = namedtuple("SubsessionData", ["split_number", "series_logo", "fastest_lap", "average_lap", "user_license", "sof"])

def getSubsessionDataByUserId(subsession_id, user_id):
    try:
        ir_client = login()
        race_result = ir_client.result(subsession_id)
        licenses = race_result.get('allowed_licenses')
        all_splits = race_result.get('associated_subsession_ids')
        split = getSplitNumber(all_splits, subsession_id)
        split_number = f"{split} of {len(all_splits)}" if split is not None and all_splits is not None else "N/A"
        series_logo = race_result.get('series_logo')
        sof = race_result.get('event_strength_of_field')
        all_race_type_results = race_result.get('session_results')
        all_driver_race_results = [session_results for session_results in all_race_type_results if session_results.get('simsession_name') == "RACE"]
        if all_driver_race_results:
            races = all_driver_race_results[0].get('results')
            drivers_results = [result for result in races if result.get('cust_id') == int(user_id)]
            if drivers_results:
                fastest_lap = convert_time(drivers_results[0].get('best_lap_time'))
                average_lap = convert_time(drivers_results[0].get('average_lap'))
                user_license = getDriverLicense(int(drivers_results[0].get('old_license_level')), licenses)

                data = SubsessionData(
                    split_number,
                    series_logo,
                    fastest_lap,
                    average_lap,
                    user_license,
                    sof
                )
                return data
    except Exception as e:
        logging.exception(e)
        logging.error("Error in getSubsessionDataByUserId")
        print('getSubsessionDataByUserId exception')
        print(e)
        return None
    return None

def getSplitNumber(all_splits, subsession_id):
    try:
        index = all_splits.index(subsession_id)
        split_number = index + 1
        return split_number
    except Exception as e:
        logging.exception(e)
        print('get split error')
        return None

def convert_time(time):
    time_str = str(time)
    minutes = int(time_str[:-4]) // 60
    seconds = int(time_str[:-4]) % 60
    milliseconds = int(time_str[-4:-1])
    
    if minutes == 0:
        return "{:02d}.{:03d}".format(seconds, milliseconds)
    
    return "{}:{:02d}.{:03d}".format(minutes, seconds, milliseconds)

def getDriverLicense(license_level, allowed_licenses):
    for license_info in allowed_licenses:
        min_level = license_info['min_license_level']
        max_level = license_info['max_license_level']
        group_name = license_info['group_name']
        
        if min_level <= license_level <= max_level:
            return group_name
    
    return None

def formatRaceData(display_name, series_name, car_name, session_start_time, start_position, finish_position, laps, incidents, points, sr_change_str, ir_change_str, track_name, split_number, series_logo, fastest_lap, average_lap, user_license, sof):
    message = (
        f"Name: {display_name}\n"
        f"Series Name: {series_name}\n"
        f"Car: {car_name}\n"
        f"Track Name: {track_name}\n"
        f"Session Start Time: {session_start_time}\n"
        f"Start Position: {start_position}\n"
        f"Finish Position: {finish_position}\n"
        f"Laps complete: {laps}\n"
        f"Points: {points}\n"
        f"Strength of Field (SOF): {sof}\n"
        f"Incidents: {incidents}\n"
        f"SR Change: {sr_change_str}\n"
        f"iRating Change: {ir_change_str}\n"
        f"User License: {user_license}\n"
        f"Split Number: {split_number}\n"
        #f"Series Logo: {series_logo}\n"
        f"Fastest Lap: {fastest_lap}\n"
        f"Average Lap: {average_lap}\n"
    )
    return message
