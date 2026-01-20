import discord
import logging


async def postRaceToDiscord(channel, message, chart_path=None):
    """Post race results and optionally a chart to a Discord channel.

    Args:
        channel: Discord channel object
        message: Formatted race result message string
        chart_path: Optional path to chart image file

    Returns:
        bool: True if successfully posted, False otherwise
    """
    try:
        # Send the formatted message
        await channel.send(message)
        logging.info(f"Race message sent to channel {channel.id}")

        # If chart exists, send it
        if chart_path:
            with open(chart_path, "rb") as pic:
                await channel.send(file=discord.File(pic))
            logging.info(f"Race chart sent to channel {channel.id}")

        return True

    except discord.Forbidden:
        logging.error(f"Bot lacks permission to post in channel {channel.id}")
        return False
    except discord.HTTPException as e:
        logging.error(f"Discord HTTP error in channel {channel.id}: {e}")
        logging.exception(e)
        return False
    except FileNotFoundError:
        logging.error(f"Chart file not found: {chart_path}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error posting to channel {channel.id}: {e}")
        logging.exception(e)
        return False
