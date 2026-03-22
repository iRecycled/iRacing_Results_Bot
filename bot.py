from discord.ext import commands, tasks
import discord
import os
import iRacingApi as irApi
import iRacingLaps as irLaps
import sqlCommands as sql
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging_config
from rateLimit import rate_limit_handler
from discordHelpers import postRaceToDiscord
from iRacingAuthWrapper import get_data_api_rate_limit

from dotenv import load_dotenv

load_dotenv()

# Setup rotating file handler logging (do this FIRST, before importing other modules)
logging_config.setup_logging()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents, command_prefix="/")

bot = commands.Bot(
    command_prefix="/", intents=intents, case_insensitive=True
)  # Set the command prefix as '/'

# Thread pool for running blocking iRacing API calls
executor = ThreadPoolExecutor(max_workers=3)

# Batching config
DEFAULT_BATCH_SIZE = 10  # Fallback if rate limit info is unavailable
API_CALLS_PER_DRIVER = 3  # Approximate API calls per driver (recent_races + result + get_cars)
REQUEST_DELAY = 2  # Seconds between API calls within a batch
_batch_index = 0  # Tracks which batch we're on across ticks


def _get_dynamic_batch_size(total_drivers):
    """Calculate batch size based on available API rate limit.

    Uses the remaining API requests to determine how many drivers
    we can safely process in this tick. Reserves 10% as a buffer.
    """
    rate_info = get_data_api_rate_limit()

    if rate_info and rate_info["remaining"] is not None:
        remaining = rate_info["remaining"]
        reset_seconds = rate_info.get("reset_seconds", 0)

        # Not enough requests left for even one driver — wait for reset
        if remaining < API_CALLS_PER_DRIVER:
            logging.info(
                f"Rate limit too low to process any drivers "
                f"(remaining={remaining}, need={API_CALLS_PER_DRIVER}, resets in {reset_seconds}s) — skipping tick"
            )
            return 0

        # Reserve 10% buffer for retries
        usable = int(remaining * 0.9)
        batch_size = max(1, usable // API_CALLS_PER_DRIVER)
        logging.info(
            f"Dynamic batch size: {batch_size} drivers "
            f"(API remaining={remaining}, usable={usable}, ~{API_CALLS_PER_DRIVER} calls/driver)"
        )
        return min(batch_size, total_drivers)

    logging.info(f"Rate limit info unavailable, using default batch size: {DEFAULT_BATCH_SIZE}")
    return DEFAULT_BATCH_SIZE


@bot.event
async def on_ready():
    print(f"We have logged in as {bot.user}")
    logging.info(f"logged in as {bot.user}")
    sql.init()
    # sql.delete_all_records()
    startLoopForUpdates.start()


@tasks.loop(seconds=60)
async def startLoopForUpdates():
    global _batch_index
    try:
        all_pairs = sql.get_all_user_channel_pairs()
        total = len(all_pairs)

        if total == 0:
            return

        # Dynamically size the batch based on API rate limit
        batch_size = _get_dynamic_batch_size(total)

        if batch_size == 0:
            logging.info("=== Skipping tick — waiting for rate limit reset ===")
            return

        # Get the batch for this tick
        start = _batch_index
        end = min(start + batch_size, total)
        batch = all_pairs[start:end]

        # Advance index for next tick, wrap around when we've covered everyone
        _batch_index = end if end < total else 0

        logging.info(
            f"=== Batch check: drivers {start + 1}-{end} of {total} (batch_size={batch_size}) ==="
        )
        print(f"Checking drivers {start + 1}-{end} of {total} (batch_size={batch_size})")

        for user_id, channel_id in batch:
            logging.info(f"Processing user_id={user_id} in channel_id={channel_id}")
            await processAndPostRace(channel_id, user_id)
            # Small delay between requests to avoid bursting
            await asyncio.sleep(REQUEST_DELAY)

        logging.info("=== Batch check complete ===")
    except Exception as e:
        logging.exception(e)
        logging.error("Error in startLoopForUpdates")


@rate_limit_handler
async def processAndPostRace(channel_id, user_id):
    """Process a user's latest race and post results to Discord.

    Pipeline:
    1. Check for new race
    2. Format race data
    3. Generate lap chart
    4. Post to Discord
    """
    logging.info(f"Processing race for user_id={user_id}, channel_id={channel_id}")

    # Step 1: Check for new race
    loop = asyncio.get_event_loop()
    last_race = await loop.run_in_executor(executor, irApi.getLastRaceIfNew, user_id, channel_id)

    if last_race is None:
        logging.info(f"No new race for user_id={user_id}")
        return

    logging.info(f"New race found for user_id={user_id}, processing...")

    # Step 2: Format race results message
    formatted_message = await loop.run_in_executor(
        executor, irApi.raceAndDriverData, last_race, user_id
    )

    if formatted_message is None:
        logging.warning(f"Failed to format race data for user_id={user_id}")
        return

    # Step 3: Generate lap chart
    chart_path = None
    chart_success = await loop.run_in_executor(executor, irLaps.getLapsChart, last_race, user_id)
    if chart_success:
        chart_path = "race_plot.png"
        logging.info(f"Chart generated for user_id={user_id}")
    else:
        logging.warning(f"Failed to generate chart for user_id={user_id}")

    # Step 4: Get Discord channel and post
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        logging.error(f"Channel {channel_id} not found")
        return

    # Post to Discord
    success = await postRaceToDiscord(channel, formatted_message, chart_path)
    if success:
        logging.info(f"Successfully posted race for user_id={user_id} to channel {channel_id}")
        print(f"Message sent to channel {channel_id}")
    else:
        logging.error(f"Failed to post race for user_id={user_id} to channel {channel_id}")


@bot.command()
@rate_limit_handler
async def addUser(ctx, arg):
    channel_id = ctx.channel.id  # Get the channel ID where the command was sent
    if channel_id:
        # Validate cust_id is a number
        try:
            cust_id = int(arg)
            if cust_id <= 0:
                await ctx.send(f"Invalid User ID: {arg}. Please provide a positive number.")
                return
        except ValueError:
            await ctx.send(f"Invalid User ID: {arg}. Please provide a valid number.")
            return

        # Run blocking API call in thread pool
        loop = asyncio.get_event_loop()
        driver_name = await loop.run_in_executor(executor, irApi.getDriverName, arg)
        if driver_name and sql.save_user_channel(arg, channel_id, driver_name):
            await ctx.send(f"Driver: {driver_name} ({arg}) has been added")
        else:
            await ctx.send(
                f"Failed to add User ID {arg}. Driver may not exist or API is unavailable."
            )


@bot.command()
async def removeUser(ctx, arg):
    channel_id = ctx.channel.id  # Get the channel ID where the command was sent
    if channel_id:
        if sql.remove_user_from_channel(arg, channel_id):
            await ctx.send(f"User Id {arg} has been removed")
        else:
            await ctx.send(f"Failed to remove User Id {arg}.")


@bot.command()
@rate_limit_handler
async def postRace(ctx, cust_id: str, subsession_id: str):
    """Post a specific race by cust_id and subsession_id.
    Usage: /postRace <cust_id> <subsession_id>
    """
    try:
        channel_id = ctx.channel.id

        # Validate inputs
        try:
            cust_id = int(cust_id)
            subsession_id = int(subsession_id)
            if cust_id <= 0 or subsession_id <= 0:
                await ctx.send("Invalid IDs: Please provide positive numbers.")
                return
        except ValueError:
            await ctx.send(
                "Invalid IDs: Please provide valid numbers for both cust_id and subsession_id."
            )
            return

        logging.info(f"Posting race: cust_id={cust_id}, subsession_id={subsession_id}")

        # Send initial status message
        status_msg = await ctx.send("⏳ Working on it... This may take a moment")

        try:
            # Step 1: Get race data for this subsession
            loop = asyncio.get_event_loop()
            race_data = await loop.run_in_executor(
                executor, irApi.getRaceBySubsessionId, subsession_id, cust_id
            )

            if race_data is None:
                await status_msg.delete()
                error_msg = (
                    f"❌ Could not find subsession {subsession_id} for cust_id {cust_id}.\n\n"
                    f"Possible reasons:\n"
                    f"• Race is older than 90 days (iRacing search limit)\n"
                    f"• Race was not found in your recent races\n"
                    f"• Driver did not participate in this subsession\n"
                    f"• Invalid subsession or customer ID"
                )
                await ctx.send(error_msg)
                logging.warning(
                    f"Failed to get race data for subsession_id={subsession_id}, cust_id={cust_id}"
                )
                return

            logging.info(f"Race data retrieved for subsession_id={subsession_id}")

            # Step 2: Format race results message
            formatted_message = await loop.run_in_executor(
                executor, irApi.raceAndDriverData, race_data, cust_id
            )

            if formatted_message is None:
                await status_msg.delete()
                error_msg = (
                    f"❌ Failed to format race data for subsession {subsession_id}.\n\n"
                    f"This usually means:\n"
                    f"• Car data is missing or invalid\n"
                    f"• Race data structure is incomplete\n"
                    f"• An unexpected error occurred during formatting"
                )
                await ctx.send(error_msg)
                logging.warning(
                    f"Failed to format race data for subsession_id={subsession_id}, cust_id={cust_id}"
                )
                return

            # Step 3: Generate lap chart
            chart_path = None
            chart_success = await loop.run_in_executor(
                executor, irLaps.getLapsChart, race_data, cust_id
            )
            if chart_success:
                chart_path = "race_plot.png"
                logging.info(f"Chart generated for subsession_id={subsession_id}")
            else:
                logging.warning(f"Failed to generate chart for subsession_id={subsession_id}")

            # Step 4: Delete status message and post to Discord
            channel = bot.get_channel(int(channel_id))
            if channel is None:
                await status_msg.delete()
                await ctx.send("Failed to find the channel to post to.")
                logging.error(f"Channel {channel_id} not found")
                return

            await status_msg.delete()
            success = await postRaceToDiscord(channel, formatted_message, chart_path)
            if success:
                logging.info(
                    f"Successfully posted race for subsession_id={subsession_id}, cust_id={cust_id}"
                )
            else:
                await ctx.send("Failed to post race to Discord.")
                logging.error(
                    f"Failed to post race for subsession_id={subsession_id}, cust_id={cust_id}"
                )

        except Exception:
            # Clean up status message if an error occurs
            try:
                await status_msg.delete()
            except Exception:
                pass
            raise

    except Exception as e:
        logging.exception(e)
        logging.error(f"Error in postRace command: {e}")
        await ctx.send(f"An error occurred while posting the race: {str(e)}")


bot.run(TOKEN)
