from iracingdataapi.client import irDataClient
import iRacingApi as ira

import matplotlib.pyplot as plt

def getLapsChart(last_race, highlighted_cust_id):
    try:
        ir_client = ira.login()
        race_title = last_race.get('series_name')
        subsession_id = last_race.get('subsession_id')
        lap_data = ir_client.result_lap_chart_data(subsession_id, 0)

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

        background_color = '#40444B'  # Slightly lighter than Discord's dark mode
        plt.figure(figsize=(10, 6), facecolor=background_color)

        for cust_id, data in race_laps_per_driver.items():
            lap_numbers = data['lap_numbers']
            lap_positions = data['lap_positions']

            if int(cust_id) == int(highlighted_cust_id):
                plt.plot(lap_numbers, lap_positions, linestyle='-', linewidth=5, label=f'Cust ID: {cust_id}')
            else:
                plt.plot(lap_numbers, lap_positions, linestyle='-', linewidth=1.5, label=f'Cust ID: {cust_id}')

        plt.title('{}'.format(race_title), color="white")
        plt.xlabel('Lap Number', color="white")
        plt.ylabel('Position', color="white")

        # Determine the x-axis tick interval based on the number of laps
        if leader_lap_numbers:
            total_laps = max(leader_lap_numbers)
            if total_laps > 30:
                tick_interval = 5
            else:
                tick_interval = 1

            plt.xticks(range(min(leader_lap_numbers), total_laps + 1, tick_interval), color="white")
            plt.xlim(min(leader_lap_numbers), total_laps)

        plt.yticks(range(1, len(race_laps_per_driver) + 1), color="white")  # Show positions from 1 to max_position
        plt.gca().invert_yaxis()  # Invert y-axis to show higher positions at the top

        plt.savefig('race_plot.png', facecolor="#40444B")
        return True
    except Exception as e:
        print(f"Exception: {e}")
        return False
