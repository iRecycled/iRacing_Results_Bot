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


@bot.event
async def on_ready():
    print(f"We have logged in as {bot.user}")
    logging.info(f"logged in as {bot.user}")
    sql.init()
    # sql.delete_all_records()
    startLoopForUpdates.start()


@tasks.loop(seconds=60)
async def startLoopForUpdates():
    try:
        print("Running scheduled task to check races")
        logging.info("=== Starting scheduled race check ===")
        all_channel_ids = sql.get_all_channel_ids()
        logging.info(f"Found {len(all_channel_ids) if all_channel_ids else 0} channels to check")

        if all_channel_ids is not None:
            for channel_id in all_channel_ids:
                all_user_ids = sql.get_users_by_channel_id(channel_id)
                msg = f"Channel {channel_id}: checking {len(all_user_ids) if all_user_ids else 0} users"
                logging.info(msg)

                for user_id in all_user_ids:
                    logging.info(f"Processing user_id={user_id} in channel_id={channel_id}")
                    await processAndPostRace(channel_id, user_id)

        print("Finished scheduled task, waiting...")
        logging.info("=== Finished scheduled race check ===")
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
