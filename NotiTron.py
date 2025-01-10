import discord
from discord.ext import commands, tasks
from pymongo import MongoClient
from datetime import datetime, timedelta, time
import pytz
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# MongoDB Config
MONGO_URI = os.getenv("MONGODB_CONNECTION")
db_client = MongoClient(MONGO_URI)
db = db_client.NotiTronDB  # DB
tasks_collection = db.Tasks  # Collection

# Bot Configuration with Message Content Intent
intents = discord.Intents.default()
intents.message_content = True  # Enable the message content intent
bot = commands.Bot(command_prefix="/", intents=intents)

# Load Guild ID from .env file
GUILD_ID = int(os.getenv("GUILD_ID"))

# Timezone setup (PST)
timezone = pytz.timezone("America/Los_Angeles")


# Slash command setup
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    # Register commands after bot is ready
    guild = bot.get_guild(GUILD_ID)
    if guild:
        await bot.tree.sync(guild=guild)  # Sync commands to a specific guild

    # Start the background task if not already running
    if not check_tasks_at_midnight.is_running():
        check_tasks_at_midnight.start()

    # Handle missed checks (if the bot reconnects after midnight)
    now = datetime.now(timezone)
    if now.hour == 0:  # If reconnecting after 12 AM
        print("Reconnecting after midnight, running task check.")
        await check_tasks_at_midnight()


# Slash command to add a task
@bot.tree.command(name="add_task", description="All field types are strings. The due date should be entered as MM/DD/YY.")
async def add_task(interaction: discord.Interaction, class_name: str, assignment_name: str, due_date: str):
    # Ensure the command is only usable in your guild
    if interaction.guild.id != GUILD_ID:
        return  

    try:
        # Try parsing both MM/DD/YY and MM/DD/YYYY formats
        try:
            due_datetime = datetime.strptime(due_date, "%m/%d/%y")
        except ValueError:
            due_datetime = datetime.strptime(due_date, "%m/%d/%Y")

        # Localize due_datetime to PST
        due_datetime = timezone.localize(due_datetime)

        # Convert datetime to ISO format (string) for MongoDB
        due_datetime_str = due_datetime.isoformat()

        # Store task in MongoDB
        task = {
            "class_name": class_name,
            "assignment_name": assignment_name,
            "due_date": due_datetime_str,
            "author": interaction.user.name,
            "user_id": interaction.user.id,
            "channel_id": interaction.channel.id,  # Save the channel ID where the task was added
            "completed": False,
        }
        tasks_collection.insert_one(task)  # Insert into MongoDB

        # Format due date for the embed (MM/DD/YY or MM/DD/YYYY)
        formatted_date = due_datetime.strftime("%m/%d/%y") if len(str(due_datetime.year)) == 2 else due_datetime.strftime("%m/%d/%Y")

        # Create an embed with the task info
        embed = discord.Embed(title=f"Task Added: {assignment_name}", color=discord.Color.green())
        embed.add_field(name="Class", value=class_name, inline=True)
        embed.add_field(name="Assignment", value=assignment_name, inline=True)
        embed.add_field(name="Due Date", value=formatted_date, inline=True)
        embed.set_footer(text=f"Assigned by {interaction.user.name}")

        await interaction.response.send_message(embed=embed)

    except Exception as e:
        # Check if the interaction has already been responded to
        if not interaction.response.is_done():
            await interaction.response.send_message(f"An error occurred: {e}")
        else:
            print(f"Error: {e}")


# Background task to check tasks at 12 AM PST
@tasks.loop(time=time(0, 0, 0))  # Runs daily at 12 AM PST
async def check_tasks_at_midnight():
    now = datetime.now(timezone)
    print(f"Running check_tasks_at_midnight at {now}")

    try:
        # Tasks due today (at 11:59 PM)
        tasks_due_today = tasks_collection.find({
            "completed": False,
            "due_date": {
                "$gte": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                "$lte": now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
            }
        })

        # Notify authors of tasks due today
        for task in tasks_due_today:
            user_id = task["user_id"]
            class_name = task["class_name"]
            assignment_name = task["assignment_name"]

            # Fetch channel to send the notification (requires a valid channel_id in your database)
            channel_id = task.get("channel_id")
            channel = bot.get_channel(channel_id) if channel_id else None
            user = await bot.fetch_user(user_id)

            if channel:
                await channel.send(
                    f"<@{user_id}>, your task '{assignment_name}' for class '{class_name}' is due today! Don't forget to submit it!"
                )
            else:  # Send DM if no channel is provided
                await user.send(
                    f"Hi {user.name}, your task '{assignment_name}' for class '{class_name}' is due today! Don't forget to submit it!"
                )

            # Remove the task from the database after sending the notification
            tasks_collection.delete_one({"_id": task["_id"]})
            print(f"Task '{assignment_name}' for class '{class_name}' has been notified and removed from the database.")

        # Handle overdue tasks
        expired_tasks = tasks_collection.find({
            "completed": False,
            "due_date": {"$lt": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()}
        })

        for task in expired_tasks:
            tasks_collection.delete_one({"_id": task["_id"]})
            print(f"Deleted expired task: {task}")

        print(f"Task check completed at {now}. Notified users and removed expired tasks.")

    except Exception as e:
        print(f"Error in check_tasks_at_midnight: {e}")


# Run the bot
if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_BOT_KEY"))
