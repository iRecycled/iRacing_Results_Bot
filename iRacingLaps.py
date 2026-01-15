from iracingdataapi.client import irDataClient
from iracingdataapi.exceptions import AccessTokenInvalid
import iRacingApi as ira
import logging
import logging_config
import matplotlib.pyplot as plt

# Setup rotating file handler logging
logging_config.setup_logging()

# Chart styling constants
BACKGROUND_COLOR = '#40444B'  # Slightly lighter than Discord's dark mode

def getLapsChart(last_race, highlighted_cust_id):
    try:
        ir_client = ira.login()
        if ir_client is None:
            logging.error("Failed to login in getLapsChart")
            return False

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
                    # finish_position is 0-indexed in the API (0 = 1st place), so add 1
                    finishing_positions[cust_id] = finish_pos + 1

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

        # Helper function to find the position just below the lead lap drivers
        def get_drop_position(dnf_lap, all_drivers_data, finishing_pos, total_drivers):
            """
            Find the best position to drop to - just below drivers still on lead lap.
            Capped at the driver's actual finishing position to avoid going off the chart.
            """
            # Find positions of drivers who completed more laps than the DNF driver
            positions_of_running_drivers = []

            for other_cust_id, other_data in all_drivers_data.items():
                other_laps = other_data['lap_numbers']
                # If they completed more laps, check their position on the DNF lap
                if other_laps and other_laps[-1] >= dnf_lap:
                    # Find their position on the lap where the driver DNF'd
                    try:
                        lap_index = other_laps.index(dnf_lap)
                        positions_of_running_drivers.append(other_data['lap_positions'][lap_index])
                    except (ValueError, IndexError):
                        pass

            if positions_of_running_drivers:
                # Drop to just below the lowest running driver (highest position number)
                # But cap it at their finishing position (can't drop further than where they finished)
                intermediate = max(positions_of_running_drivers) + 1
                # Also ensure we don't exceed total number of drivers
                return min(intermediate, finishing_pos, total_drivers)
            else:
                # No running drivers found, use finishing position
                return finishing_pos

        plt.figure(figsize=(10, 6), facecolor=BACKGROUND_COLOR)

        # Get total number of drivers
        total_drivers = len(race_laps_per_driver)

        for cust_id, data in race_laps_per_driver.items():
            lap_numbers = data['lap_numbers']
            lap_positions = data['lap_positions']

            if cust_id not in finishing_positions:
                # No finishing position data, use lap data as-is
                lap_numbers_extended = lap_numbers
                lap_positions_extended = lap_positions
            elif lap_numbers and lap_numbers[-1] < max_lap:
                # Driver DNF'd - add intermediate drop and final position
                dnf_lap = lap_numbers[-1]
                last_position = lap_positions[-1]
                finish_pos = finishing_positions[cust_id]

                # Calculate intermediate drop position (just below lead lap drivers)
                intermediate_position = get_drop_position(dnf_lap, race_laps_per_driver, finish_pos, total_drivers)

                # Create a smooth drop: last position -> intermediate -> final
                # Add intermediate point right after DNF lap, then final position at race end
                lap_numbers_extended = lap_numbers + [dnf_lap + 0.5, max_lap]
                lap_positions_extended = lap_positions + [intermediate_position, finish_pos]
            else:
                # Driver completed the race
                # Gradually transition from last lap position to finishing position
                last_lap_position = lap_positions[-1]
                finish_pos = finishing_positions[cust_id]

                # If their position changed after the last lap (due to post-race classification)
                # create a smooth transition
                if last_lap_position != finish_pos:
                    # Add a point slightly after the last lap to start the transition
                    lap_numbers_extended = lap_numbers + [max_lap - 0.3, max_lap]
                    lap_positions_extended = lap_positions + [last_lap_position, finish_pos]
                else:
                    # Position didn't change, just extend the line
                    lap_numbers_extended = lap_numbers + [max_lap]
                    lap_positions_extended = lap_positions + [finish_pos]

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

        # Add y-axis labels on the right side as well
        ax = plt.gca()
        ax.tick_params(axis='y', labelcolor='white')
        ax2 = ax.twinx()  # Create a second y-axis on the right
        ax2.set_ylim(ax.get_ylim())  # Match the limits (already inverted)
        ax2.set_yticks(range(1, len(race_laps_per_driver) + 1))
        ax2.set_yticklabels(range(1, len(race_laps_per_driver) + 1), color="white")

        # Adjust layout to prevent label cutoff
        plt.tight_layout()

        plt.savefig('race_plot.png', facecolor=BACKGROUND_COLOR, bbox_inches='tight')
        plt.close()  # Close figure to prevent memory leaks
        return True
    except AccessTokenInvalid:
        logging.debug("Access token invalid during API call in getLapsChart - clearing client")
        ira._client_manager.clear_client()
        plt.close()  # Clean up any partial figure
        return False
    except Exception as e:
        logging.error(f"Exception in iRacingLaps: {e}")
        logging.exception(e)
        print(f"Exception: {e}")
        plt.close()  # Clean up any partial figure
        return False
