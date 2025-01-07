import discord
from discord.ext import commands, tasks
from pymongo import MongoClient
from datetime import datetime, timedelta
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
    
    # Start the task to delete expired tasks
    delete_expired_tasks.start()

# Slash command to add a task
@bot.tree.command(name="add_task", description="Add a task to the database")
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

        # Now localize "now" to match the timezone (PST) of "due_datetime"
        now = datetime.now(timezone)

        # Ensure both `due_datetime` and `now` are timezone-aware
        time_diff = due_datetime - now
        if time_diff <= timedelta(days=1) and time_diff > timedelta():
            await interaction.channel.send(f"<@{interaction.user.id}>, your task '{assignment_name}' is due in less than 24 hours!")

        # Check if the due date matches today's date
        today = now.date()  # Get today's date in PST
        due_date_only = due_datetime.date()  # Extract the date part of the due date

        # If assignment due date is today, ping the user
        if due_date_only == today:
            await interaction.channel.send(f"<@{interaction.user.id}>, your assignment '{assignment_name}' is due today! Don't forget to submit it!")

    except Exception as e:
        # Check if the interaction has already been responded to
        if not interaction.response.is_done():
            await interaction.response.send_message(f"An error occurred: {e}")
        else:
            print(f"Error: {e}")

# Background task to delete expired tasks
@tasks.loop(hours=24)  # Runs every 24 hours
async def delete_expired_tasks():
    now = datetime.now(timezone)
    # Convert current time to ISO format string for comparison
    now_str = now.isoformat()

    # Find and delete tasks with due dates earlier than the current time
    expired_tasks = tasks_collection.find({"due_date": {"$lt": now_str}, "completed": False})
    for task in expired_tasks:
        tasks_collection.delete_one({"_id": task["_id"]})

    print(f"Checked for expired tasks at {now}.")

# Run the bot
if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_BOT_KEY"))