from discord.ext import commands, tasks
import discord
import os
import iRacingApi as ira
import sqlCommands as sql
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents, command_prefix="/")

bot = commands.Bot(command_prefix="/", intents=intents)  # Set the command prefix as '/'

@bot.event
async def on_ready():
    print(f'We have logged in as {bot.user}')
    sql.init()
    #sql.delete_all_records()
    startLoopForUpdates.start() 

@tasks.loop(seconds=60)
async def startLoopForUpdates():
    print("Running scheduled task to check races")
    all_channel_ids = sql.get_all_channel_ids()
    if(all_channel_ids is not None):
        for channel_id in all_channel_ids:
            all_user_ids = sql.get_users_by_channel_id(channel_id)
            
            for user_id in all_user_ids:
                await getUserRaceDataAndPost(channel_id, user_id)
    print("Finished scheduled task, waiting...")

async def getUserRaceDataAndPost(channel_id, user_id):
    race_data = ira.main(user_id)
    if(race_data is not None):
        message = (
        f"Name: {race_data.display_name}\n"
        f"Series Name: {race_data.series_name}\n"
        #f"Series ID: {race_data.series_id}\n"
        f"Car: {race_data.car_name}\n"
        f"Session Start Time: {race_data.session_start_time}\n"
        f"Start Position: {race_data.start_position}\n"
        f"Finish Position: {race_data.finish_position}\n"
        f"Laps complete: {race_data.laps}\n"
        f"Incidents: {race_data.incidents}\n"
        f"Points: {race_data.points}\n"
        f"Strength of Field (SOF): {race_data.sof}\n"
        f"SR Change: {race_data.sr_change}\n"
        f"iRating Change: {race_data.ir_change}\n"
        f"User License: {race_data.user_license}\n"
        f"Track Name: {race_data.track_name}\n"
        f"Split Number: {race_data.split_number}\n"
        #f"Series Logo: {race_data.series_logo}\n"
        f"Fastest Lap: {race_data.fastest_lap}\n"
        f"Average Lap: {race_data.average_lap}\n"
        )
        msg = str(message)

        print(f"Attempting to send message to channel_id: {channel_id}")
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            print(f"Channel with ID {channel_id} not found.")
            return
        
        try:
            await channel.send(msg)
            print(f"Message sent to channel {channel_id}")
        except discord.Forbidden:
            print(f"Bot does not have permission to send messages in channel {channel_id}.")
        except discord.HTTPException as e:
            print(f"Failed to send message due to HTTP error: {e}")


@bot.command()
async def addUser(ctx, arg):
    channel_id = ctx.channel.id  # Get the channel ID where the command was sent
    if channel_id:
        driver_name = ira.getDriverName(arg)
        if driver_name and sql.save_user_channel(arg, channel_id, driver_name):
            await ctx.send(f"Driver: {driver_name} ({arg}) has been added")
        else:
            await ctx.send(f"Failed to add User Id {arg}")

@bot.command()
async def removeUser(ctx, arg):
    channel_id = ctx.channel.id  # Get the channel ID where the command was sent
    if channel_id:
        if sql.remove_user_from_channel(arg, channel_id):
            await ctx.send(f"User Id {arg} has been removed")
        else:
            await ctx.send(f"Failed to remove User Id {arg}")

bot.run(TOKEN)
