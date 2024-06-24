from iracingdataapi.client import irDataClient
import sqlCommands as sql
import os
from datetime import datetime
from dotenv import load_dotenv
from collections import namedtuple
load_dotenv()
raceAndDriverObj = namedtuple('raceAndDriverData', [
    'display_name', 'series_name', 'series_id', 'car_name', 'session_start_time', 
    'start_position', 'finish_position', 'laps', 'incidents', 'points', 
    'sr_change', 'ir_change', 'track_name', 
    'split_number', 'series_logo', 
    'fastest_lap', 'average_lap', 'user_license', 'sof'
])
ir_client = None

def login():
    global ir_client
    if ir_client is None or (hasattr(ir_client, 'authenticated') and not ir_client.authenticated):
        print("Signing into iRacing.")
        ir_client = irDataClient(username=os.getenv('ir_username'), password=os.getenv('ir_password'))
    return ir_client

def getLastRaceIfNew(cust_id, channel_id):
    try:
        last_race = getLastRaceByCustId(cust_id)
        if last_race is not None:
            last_race_time = last_race.get('session_start_time')
            if not lastRaceTimeMatching(cust_id, last_race_time, channel_id):
                saveLastRaceTimeByCustId(cust_id, last_race_time, channel_id)
                return last_race
                #return raceAndDriverData(last_race, cust_id)
        else:
            return None
    except Exception as e:
        print('iRacingApi main function error')
        print(e)
        return None

def getLastRaceByCustId(cust_id):
    ir_client = login()
    lastTenRaces = ir_client.stats_member_recent_races(cust_id = cust_id)
    
    if lastTenRaces is not None:
        races = lastTenRaces.get('races', [])
        if len(races) > 0:
            firstRace = races[0]
            return firstRace

    print("No races found")
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
    session_start_time = datetime.fromisoformat(session_start_time_unfiltered.replace('Z', '')).strftime('%Y-%m-%d %H:%M:%S GMT')
    start_position = race.get('start_position')
    finish_position = race.get('finish_position')
    laps = race.get('laps')
    incidents = race.get('incidents')
    points = race.get('points')
    old_sr = race.get('old_sub_level') /100
    new_sr = race.get('new_sub_level') /100
    sr_change = round(new_sr - old_sr, 2)
    sr_change_str = f"{'+' if sr_change > 0 else ''}{sr_change}"
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
        split_number = f"{split} of {len(all_splits)}"
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
        print('getSubsessionDataByUserId exception')
        print(e)
        return None
    return None

def getSplitNumber(all_splits, subsession_id):
    try:
        index = all_splits.index(subsession_id)
        split_number = index + 1
        return split_number
    except ValueError:
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
    
    return None  # Return None if no group matches the license_level

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
