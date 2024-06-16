from iracingdataapi.client import irDataClient
import sqlCommands as sql
import os
from datetime import datetime
from dotenv import load_dotenv
from collections import namedtuple
load_dotenv()
cust_id_last_race_dict = {}
raceAndDriverObj = namedtuple('raceAndDriverData', ['display_name', 'series_name', 'series_id', 'car_name', 'session_start_time', 'start_position', 'finish_position', 'laps', 'incidents', 'points', 'sof', 'sr_change', 'ir_change', 'track_name' ])

def login():
    ir_client = irDataClient(username=os.getenv('ir_username'), password=os.getenv('ir_password'))
    return ir_client

def main(cust_id):
    last_race = getLastRaceByCustId(cust_id)
    if last_race is not None:
        last_race_time = last_race.get('session_start_time')
        if not lastRaceTimeMatching(cust_id, last_race_time):
            saveLastRaceTimeByCustId(cust_id, last_race_time)
            return raceAndDriverData(last_race, cust_id)
    else:
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

def saveLastRaceTimeByCustId(cust_id, race_time):
    return sql.save_user_last_race_time(cust_id, race_time)

def lastRaceTimeMatching(cust_id, race_time):
    saved_last_race_time = sql.get_last_race_time(cust_id)
    print(race_time)
    print(saved_last_race_time)
    if saved_last_race_time is None:
        saveLastRaceTimeByCustId(cust_id, race_time)
        return True
    return saved_last_race_time == race_time

def raceAndDriverData(race, cust_id):
    ir_client = login()
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
    sof = race.get('strength_of_field')
    old_sr = race.get('old_sub_level') /100
    new_sr = race.get('new_sub_level') /100
    sr_change = round(new_sr - old_sr, 2)
    old_ir = race.get('oldi_rating')
    new_ir = race.get('newi_rating')
    ir_change = new_ir - old_ir
    track_name = race.get('track').get('track_name')
    return raceAndDriverObj(display_name, series_name, series_id, car_name, session_start_time, start_position, finish_position, laps, incidents, points, sof, sr_change, ir_change, track_name)

def saveDriverName(cust_id):
    ir_client = login()
    try :
        data = ir_client.member_profile(cust_id = cust_id)
    except: 
        return None
    if data is None:
        return None
    driver_name = data.get('member_info').get('display_name')
    sql.save_user_display_name(cust_id, driver_name)
    return driver_name
