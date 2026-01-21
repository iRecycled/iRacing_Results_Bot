from iracingdataapi.exceptions import AccessTokenInvalid
import iRacingApi as ira
import logging
import matplotlib.pyplot as plt
from rateLimit import RateLimitError
from iRacingAuthWrapper import is_rate_limited, get_rate_limit_remaining

# Chart styling constants
BACKGROUND_COLOR = "#40444B"  # Slightly lighter than Discord's dark mode


def getLapsChart(last_race, highlighted_cust_id):
    # Check rate limit before making API calls
    if is_rate_limited():
        raise RateLimitError(get_rate_limit_remaining())

    try:
        ir_client = ira.login()
        if ir_client is None:
            logging.error("Failed to login in getLapsChart")
            return False

        race_title = last_race.get("series_name")
        subsession_id = last_race.get("subsession_id")
        lap_data = ir_client.result_lap_chart_data(subsession_id, 0)

        # Get the full race results to get finishing positions
        race_result = ir_client.result(subsession_id)

        # Detect if this is a team race - will be determined after we load race results
        is_team_race = False

        # Extract finishing positions and car class info
        # For team races: map group_id -> (finishing position, car_class_id)
        # For individual races: map cust_id -> (finishing position, car_class_id)
        finishing_positions = {}
        car_class_map = {}  # Map entity_id -> car_class_id
        highlighted_car_class = None  # Store the car class of the highlighted driver
        highlighted_cust_id_int = int(highlighted_cust_id)

        all_race_type_results = race_result.get("session_results", [])
        race_session = [
            session for session in all_race_type_results if session.get("simsession_name") == "RACE"
        ]
        if race_session:
            results = race_session[0].get("results", [])
            # Detect if this is a team race by checking if results contain driver_results arrays
            is_team_race = len(results) > 0 and "driver_results" in results[0]
            if is_team_race:
                # For team races, map team names to car classes and team_ids
                team_name_to_car_class = {}  # Map team name -> car_class_id
                team_name_to_team_id = {}  # Map team name -> team_id

                for result in results:
                    team_id = result.get("team_id")
                    team_name = result.get("display_name")  # Team name
                    finish_pos = result.get("finish_position")
                    car_class_id = result.get("car_class_id")

                    if team_id and finish_pos is not None:
                        finishing_positions[team_id] = finish_pos + 1

                    # Store mapping from team name to car class and team_id
                    if team_name:
                        team_name_to_car_class[team_name] = car_class_id
                        team_name_to_team_id[team_name] = team_id

                    # Check if highlighted driver is in this team to get their car class
                    driver_results = result.get("driver_results", [])
                    for driver in driver_results:
                        if driver.get("cust_id") == highlighted_cust_id_int:
                            highlighted_car_class = car_class_id
            else:
                # For individual races, use cust_id
                for result in results:
                    cust_id = result.get("cust_id")
                    finish_pos = result.get("finish_position")
                    if cust_id and finish_pos is not None:
                        finishing_positions[cust_id] = finish_pos + 1

                    # Get car class for this driver
                    car_class_id = result.get("car_class_id")
                    car_class_map[cust_id] = car_class_id

                    # Check if this is the highlighted driver
                    if cust_id == highlighted_cust_id_int:
                        highlighted_car_class = car_class_id

        # Group lap data by team (group_id) for team races, or by driver (cust_id) for individual races
        race_laps_per_entity = {}
        leader_lap_numbers = []  # To store lap numbers of the race leader

        for driver in lap_data:
            # Use group_id for team races, cust_id for individual races
            if is_team_race:
                group_id = driver["group_id"]
                team_name = driver["name"]  # Team name from lap data
                # Map group_id to car class using team name
                entity_id = group_id
                if team_name in team_name_to_car_class:
                    car_class_map[entity_id] = team_name_to_car_class[team_name]
                    # Also update finishing_positions if needed
                    if entity_id not in finishing_positions and team_name in team_name_to_team_id:
                        team_id = team_name_to_team_id[team_name]
                        if team_id in finishing_positions:
                            finishing_positions[entity_id] = finishing_positions[team_id]
            else:
                entity_id = driver["cust_id"]

            lap_num = driver["lap_number"]
            lap_position = driver["lap_position"]

            if entity_id in race_laps_per_entity:
                race_laps_per_entity[entity_id]["lap_numbers"].append(int(lap_num))
                race_laps_per_entity[entity_id]["lap_positions"].append(int(lap_position))
                race_laps_per_entity[entity_id]["drivers"].add(driver.get("cust_id"))
            else:
                race_laps_per_entity[entity_id] = {
                    "lap_numbers": [int(lap_num)],
                    "lap_positions": [int(lap_position)],
                    "drivers": {driver.get("cust_id")},
                }

            # Capture lap numbers for the race leader (position 1)
            if lap_position == 1 and lap_num not in leader_lap_numbers:
                leader_lap_numbers.append(int(lap_num))

        # Determine the maximum lap number (race end)
        max_lap = max(leader_lap_numbers) if leader_lap_numbers else 0

        # Helper function to find the position just below the lead lap drivers
        def get_drop_position(dnf_lap, all_entities_data, finishing_pos, total_entities):
            """
            Find the best position to drop to - just below entities still on lead lap.
            Capped at the entity's actual finishing position to avoid going off the chart.
            """
            # Find positions of entities who completed more laps than the DNF entity
            positions_of_running_entities = []

            for other_entity_id, other_data in all_entities_data.items():
                other_laps = other_data["lap_numbers"]
                # If they completed more laps, check their position on the DNF lap
                if other_laps and other_laps[-1] >= dnf_lap:
                    # Find their position on the lap where the entity DNF'd
                    try:
                        lap_index = other_laps.index(dnf_lap)
                        positions_of_running_entities.append(other_data["lap_positions"][lap_index])
                    except (ValueError, IndexError):
                        pass

            if positions_of_running_entities:
                # Drop to just below the lowest running entity (highest position number)
                # But cap it at their finishing position (can't drop further than where they finished)
                intermediate = max(positions_of_running_entities) + 1
                # Also ensure we don't exceed total number of entities
                return min(intermediate, finishing_pos, total_entities)
            else:
                # No running entities found, use finishing position
                return finishing_pos

        plt.figure(figsize=(10, 6), facecolor=BACKGROUND_COLOR)

        # Get total number of entities (teams or drivers)
        total_entities = len(race_laps_per_entity)

        for entity_id, data in race_laps_per_entity.items():
            lap_numbers = data["lap_numbers"]
            lap_positions = data["lap_positions"]

            if is_team_race:
                # For team races, extend line only to the laps completed by the team (no DNF drops)
                # This shows which teams stayed on lead lap vs. fell behind
                lap_numbers_extended = lap_numbers
                lap_positions_extended = lap_positions
            elif entity_id not in finishing_positions:
                # No finishing position data, use lap data as-is
                lap_numbers_extended = lap_numbers
                lap_positions_extended = lap_positions
            elif lap_numbers and lap_numbers[-1] < max_lap:
                # Entity DNF'd - add intermediate drop and final position (individual races only)
                dnf_lap = lap_numbers[-1]
                finish_pos = finishing_positions[entity_id]

                # Calculate intermediate drop position (just below lead lap entities)
                intermediate_position = get_drop_position(
                    dnf_lap, race_laps_per_entity, finish_pos, total_entities
                )

                # Create a smooth drop: last position -> intermediate -> final
                # Add intermediate point right after DNF lap, then final position at race end
                lap_numbers_extended = lap_numbers + [dnf_lap + 0.5, max_lap]
                lap_positions_extended = lap_positions + [
                    intermediate_position,
                    finish_pos,
                ]
            else:
                # Entity completed the race
                # Gradually transition from last lap position to finishing position
                last_lap_position = lap_positions[-1]
                finish_pos = finishing_positions[entity_id]

                # If their position changed after the last lap (due to post-race classification)
                # create a smooth transition
                if last_lap_position != finish_pos:
                    # Add a point slightly after the last lap to start the transition
                    lap_numbers_extended = lap_numbers + [max_lap - 0.3, max_lap]
                    lap_positions_extended = lap_positions + [
                        last_lap_position,
                        finish_pos,
                    ]
                else:
                    # Position didn't change, just extend the line
                    lap_numbers_extended = lap_numbers + [max_lap]
                    lap_positions_extended = lap_positions + [finish_pos]

            # Determine line color and width based on car class and highlight status
            should_highlight = False
            if is_team_race:
                should_highlight = int(highlighted_cust_id) in data["drivers"]
            else:
                should_highlight = int(entity_id) == int(highlighted_cust_id)

            # Determine color based on car class
            entity_car_class = car_class_map.get(entity_id)

            # Color by car class - only color if same class as highlighted driver, otherwise gray
            if highlighted_car_class is not None and entity_car_class == highlighted_car_class:
                # Same car class - use a colored line
                line_color = None  # Let matplotlib use default color cycle
                line_width = 5 if should_highlight else 1.5
            else:
                # Different car class or unknown - use gray
                line_color = "#808080"  # Gray color
                line_width = 3 if should_highlight else 1

            plt.plot(
                lap_numbers_extended,
                lap_positions_extended,
                linestyle="-",
                linewidth=line_width,
                color=line_color,
                label=f"Entity: {entity_id}",
            )

        plt.title("{}".format(race_title), color="white")
        plt.xlabel("Lap Number", color="white")
        plt.ylabel("Position", color="white")

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
            elif total_laps <= 300:
                tick_interval = 50
            else:
                tick_interval = 100

            plt.xticks(range(min_lap, total_laps + 1, tick_interval), color="white")
            plt.xlim(min_lap, total_laps)

        # For Y-axis, use dynamic tick interval based on number of entities
        # For team races with ~60 teams, show every 5th position; for smaller races, show more ticks
        y_tick_interval = 1
        if total_entities > 60:
            y_tick_interval = 5
        elif total_entities > 30:
            y_tick_interval = 2

        y_ticks = range(1, total_entities + 1, y_tick_interval)
        plt.yticks(y_ticks, color="white")  # Show positions with appropriate spacing
        plt.gca().invert_yaxis()  # Invert y-axis to show higher positions at the top

        # Add y-axis labels on the right side as well
        ax = plt.gca()
        ax.tick_params(axis="y", labelcolor="white")
        ax2 = ax.twinx()  # Create a second y-axis on the right
        ax2.set_ylim(ax.get_ylim())  # Match the limits (already inverted)
        ax2.set_yticks(y_ticks)
        ax2.set_yticklabels(y_ticks, color="white")

        # Adjust layout to prevent label cutoff
        plt.tight_layout()

        plt.savefig("race_plot.png", facecolor=BACKGROUND_COLOR, bbox_inches="tight")
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
