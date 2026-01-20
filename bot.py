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

# Setup rotating file handler logging
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


# @bot.command()
# async def test(ctx):
#     channel_id = ctx.channel.id  # Get the channel ID where the command was sent
#     if channel_id:
#         # Run blocking API call in thread pool
#         loop = asyncio.get_event_loop()
#         result = await loop.run_in_executor(executor, irApi.test_api_request)
#         await ctx.send(result)

bot.run(TOKEN)
