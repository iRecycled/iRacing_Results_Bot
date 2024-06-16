import sqlCommands as sql
import sqlite3
from discord.ext import commands

conn = sqlite3.connect('discord_bot.db')
cursor = conn.cursor()

@commands.command()
async def addUser(ctx):
    # Get the user_id from the message content
    user_id = ctx.message.content.split()[1]  # format is "/addUser <user_id>"
    
    # Ensure there is a channel_id associated with the current channel
    channel_id = ctx.channel.id  # Get the channel ID where the command was sent
    await channel_id.send("TEST!")
    if channel_id:
        if sql.save_user_channel(user_id, channel_id):
            await ctx.send(f"User ID {user_id} added for channel {channel_id}")
        else:
            await ctx.send(f"Failed to add User ID {user_id} for channel {channel_id}")
