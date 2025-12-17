from discord.ext import commands, tasks
import discord
import os
import iRacingApi as ira
import iRacingLaps as laps
import sqlCommands as sql
import logging
import asyncio

from dotenv import load_dotenv

load_dotenv()

# Use INFO for debugging, WARNING for production
LOG_LEVEL = logging.INFO if os.getenv('DEBUG_MODE', 'false').lower() == 'true' else logging.WARNING
logging.basicConfig(level=LOG_LEVEL, filename='bot.log', filemode='a', format='%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S')
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents, command_prefix="/")

bot = commands.Bot(command_prefix="/", intents=intents, case_insensitive=True) # Set the command prefix as '/'
@bot.event
async def on_ready():
    print(f'We have logged in as {bot.user}')
    logging.error(f'logged in as {bot.user}')
    sql.init()
    #sql.delete_all_records()
    startLoopForUpdates.start()

@tasks.loop(seconds=60)
async def startLoopForUpdates():
    try:
        # Check rate limit status before starting
        if ira.is_rate_limited():
            remaining = ira.get_rate_limit_remaining()
            minutes = remaining // 60
            print(f"[RATE LIMITED] Pausing race checks for {remaining} seconds ({minutes} minutes)")
            logging.warning(f"Rate limited - pausing loop for {remaining} seconds")

            # Change loop interval to wake up when rate limit expires
            startLoopForUpdates.change_interval(seconds=remaining + 5)  # +5 second buffer
            return

        # Reset to normal 60 second interval if we were rate limited before
        if startLoopForUpdates.seconds != 60:
            print(f"[RATE LIMIT EXPIRED] Resuming normal 60-second check interval")
            logging.info("Rate limit expired, resuming normal interval")
            startLoopForUpdates.change_interval(seconds=60)

        print("Running scheduled task to check races")
        logging.info("=== Starting scheduled race check ===")
        all_channel_ids = sql.get_all_channel_ids()
        logging.info(f"Found {len(all_channel_ids) if all_channel_ids else 0} channels to check")

        if(all_channel_ids is not None):
            for channel_id in all_channel_ids:
                all_user_ids = sql.get_users_by_channel_id(channel_id)
                logging.info(f"Channel {channel_id}: checking {len(all_user_ids) if all_user_ids else 0} users")

                for user_id in all_user_ids:
                    logging.info(f"Processing user_id={user_id} in channel_id={channel_id}")

                    # Check rate limit before processing each user
                    if ira.is_rate_limited():
                        remaining = ira.get_rate_limit_remaining()
                        minutes = remaining // 60
                        print(f"[RATE LIMITED] Rate limit hit mid-check - pausing for {remaining} seconds ({minutes} minutes)")
                        logging.warning(f"Rate limit hit during user processing - pausing for {remaining} seconds")

                        # Change loop interval to wake up when rate limit expires
                        startLoopForUpdates.change_interval(seconds=remaining + 5)  # +5 second buffer
                        return

                    await getUserRaceDataAndPost(channel_id, user_id)

        print("Finished scheduled task, waiting...")
        logging.info("=== Finished scheduled race check ===")
    except Exception as e:
        logging.exception(e)
        logging.error("Error in startLoopForUpdates")

async def getUserRaceDataAndPost(channel_id, user_id):
    logging.info(f"getUserRaceDataAndPost called for user_id={user_id}, channel_id={channel_id}")
    last_race = ira.getLastRaceIfNew(user_id, channel_id)

    if last_race is not None:
        logging.info(f"New race found for user_id={user_id}, preparing message")
        driver_race_result_msg = ira.raceAndDriverData(last_race, user_id)

        print(f"Attempting to send message to channel_id: {channel_id}")
        logging.info(f"Attempting to send message to channel_id={channel_id}")

        channel = bot.get_channel(int(channel_id))
        if channel is None:
            print(f"Channel with ID {channel_id} not found.")
            logging.error(f"Channel with ID {channel_id} not found.")
            return

        try:
            logging.info(f"Sending race result message to channel {channel_id}")
            await channel.send(driver_race_result_msg)

            logging.info(f"Generating lap chart for user_id={user_id}")
            if laps.getLapsChart(last_race, user_id):
                with open('race_plot.png', 'rb') as pic:
                    await channel.send(file=discord.File(pic))
                logging.info(f"Lap chart sent to channel {channel_id}")

            logging.info(f"Message successfully sent to channel {channel_id}")
            print(f"Message sent to channel {channel_id}")
        except discord.Forbidden:
            logging.error(f"Bot does not have permission to send messages in channel {channel_id}.")
            print(f"Bot does not have permission to send messages in channel {channel_id}.")
        except discord.HTTPException as e:
            logging.exception(e)
            logging.error(f"Failed to send message due to HTTP error: {e}")
            print(f"Failed to send message due to HTTP error: {e}")
    else:
        logging.info(f"No new race for user_id={user_id} in channel_id={channel_id}")


@bot.command()
async def addUser(ctx, arg):
    channel_id = ctx.channel.id  # Get the channel ID where the command was sent
    if channel_id:
        driver_name = ira.getDriverName(arg)
        if driver_name and sql.save_user_channel(arg, channel_id, driver_name):
            await ctx.send(f"Driver: {driver_name} ({arg}) has been added")
        else:
            await ctx.send(f"Failed to add User Id {arg}.")

@bot.command()
async def removeUser(ctx, arg):
    channel_id = ctx.channel.id  # Get the channel ID where the command was sent
    if channel_id:
        if sql.remove_user_from_channel(arg, channel_id):
            await ctx.send(f"User Id {arg} has been removed")
        else:
            await ctx.send(f"Failed to remove User Id {arg}.")

bot.run(TOKEN)
