from iracingdataapi.client import irDataClient
import iRacingApi as ira
import logging
from json.decoder import JSONDecodeError

logging.basicConfig(level=logging.INFO, filename='bot.log', filemode='a', format='%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S')
import matplotlib.pyplot as plt

def getLapsChart(last_race, highlighted_cust_id):
    try:
        ir_client = ira.login()
        race_title = last_race.get('series_name')
        subsession_id = last_race.get('subsession_id')
        lap_data = ir_client.result_lap_chart_data(subsession_id, 0)

        # Get the full race results to get finishing positions
        race_result = ir_client.result(subsession_id)

        # Extract finishing positions for each driver
        finishing_positions = {}
        all_race_type_results = race_result.get('session_results', [])
        race_session = [session for session in all_race_type_results if session.get('simsession_name') == "RACE"]
        if race_session:
            results = race_session[0].get('results', [])
            for result in results:
                cust_id = result.get('cust_id')
                finish_pos = result.get('finish_position')
                if cust_id and finish_pos is not None:
                    finishing_positions[cust_id] = finish_pos

        race_laps_per_driver = {}
        leader_lap_numbers = []  # To store lap numbers of the race leader

        for driver in lap_data:
            cust_id = driver['cust_id']
            lap_num = driver['lap_number']
            lap_position = driver['lap_position']

            if cust_id in race_laps_per_driver:
                race_laps_per_driver[cust_id]['lap_numbers'].append(int(lap_num))
                race_laps_per_driver[cust_id]['lap_positions'].append(int(lap_position))
            else:
                race_laps_per_driver[cust_id] = {
                    'lap_numbers': [int(lap_num)],
                    'lap_positions': [int(lap_position)]
                }

            # Capture lap numbers for the race leader (position 1)
            if lap_position == 1 and lap_num not in leader_lap_numbers:
                leader_lap_numbers.append(int(lap_num))

        # Determine the maximum lap number (race end)
        max_lap = max(leader_lap_numbers) if leader_lap_numbers else 0

        background_color = '#40444B'  # Slightly lighter than Discord's dark mode
        plt.figure(figsize=(10, 6), facecolor=background_color)

        for cust_id, data in race_laps_per_driver.items():
            lap_numbers = data['lap_numbers']
            lap_positions = data['lap_positions']

            # If driver didn't complete all laps, add a drop to their finishing position
            if lap_numbers and lap_numbers[-1] < max_lap and cust_id in finishing_positions:
                # Add the final position at the end of the race
                lap_numbers_extended = lap_numbers + [max_lap]
                lap_positions_extended = lap_positions + [finishing_positions[cust_id]]
            else:
                lap_numbers_extended = lap_numbers
                lap_positions_extended = lap_positions

            if int(cust_id) == int(highlighted_cust_id):
                plt.plot(lap_numbers_extended, lap_positions_extended, linestyle='-', linewidth=5, label=f'Cust ID: {cust_id}')
            else:
                plt.plot(lap_numbers_extended, lap_positions_extended, linestyle='-', linewidth=1.5, label=f'Cust ID: {cust_id}')

        plt.title('{}'.format(race_title), color="white")
        plt.xlabel('Lap Number', color="white")
        plt.ylabel('Position', color="white")

        # Determine the x-axis tick interval based on the number of laps
        if leader_lap_numbers:
            total_laps = max(leader_lap_numbers)
            min_lap = min(leader_lap_numbers)

            # Dynamic tick interval based on total laps
            if total_laps <= 15:
                tick_interval = 1
            elif total_laps <= 30:
                tick_interval = 2
            elif total_laps <= 60:
                tick_interval = 5
            elif total_laps <= 100:
                tick_interval = 10
            else:
                tick_interval = 20

            plt.xticks(range(min_lap, total_laps + 1, tick_interval), color="white")
            plt.xlim(min_lap, total_laps)

        plt.yticks(range(1, len(race_laps_per_driver) + 1), color="white")  # Show positions from 1 to max_position
        plt.gca().invert_yaxis()  # Invert y-axis to show higher positions at the top

        # Adjust layout to prevent label cutoff
        plt.tight_layout()

        plt.savefig('race_plot.png', facecolor="#40444B", bbox_inches='tight')
        plt.close()  # Close figure to prevent memory leaks
        return True
    except JSONDecodeError as e:
        logging.error(f"JSONDecodeError in getLapsChart: API returned empty/invalid response - {str(e)}")
        logging.warning("Clearing cached client due to JSONDecodeError in lap chart generation")
        ira._client_manager.clear_client()
        plt.close()  # Clean up any partial figure
        return False
    except Exception as e:
        logging.error(f"Exception in iRacingLaps: {e}")
        logging.exception(e)
        print(f"Exception: {e}")
        plt.close()  # Clean up any partial figure
        return False
