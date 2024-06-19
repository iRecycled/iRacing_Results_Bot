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
    race_data_message = ira.main(user_id)
    if(race_data_message is not None):
        
        print(f"Attempting to send message to channel_id: {channel_id}")
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            print(f"Channel with ID {channel_id} not found.")
            return
        
        try:
            await channel.send(race_data_message)
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
