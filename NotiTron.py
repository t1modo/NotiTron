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
@bot.tree.command(name="add_task", description="Set a reminder for upcoming assignments!")
async def add_task(interaction: discord.Interaction, class_name: str, assignment_name: str, due_date: str, due_time: str):
    # Ensure the command is only usable in your guild
    if interaction.guild.id != GUILD_ID:
        return  

    try:
        # Parse due_date
        try:
            due_date_parsed = datetime.strptime(due_date, "%m/%d/%y")
        except ValueError:
            due_date_parsed = datetime.strptime(due_date, "%m/%d/%Y")

        # Parse due_time
        due_time = due_time.strip().upper().replace(" ", "")
        due_time_parsed = datetime.strptime(due_time, "%I:%M%p")

        # Combine date and time, then localize to PST
        due_datetime = timezone.localize(due_date_parsed.replace(
            hour=due_time_parsed.hour,
            minute=due_time_parsed.minute
        ))

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

        # Format due date and time for the embed
        formatted_datetime = due_datetime.strftime("%m/%d/%Y at %I:%M %p")

        # Create an embed with the task info (red color for incomplete tasks)
        embed = discord.Embed(title=f"Task Added: {assignment_name}", color=discord.Color.red())
        embed.add_field(name="Class", value=class_name, inline=True)
        embed.add_field(name="Assignment", value=assignment_name, inline=True)
        embed.add_field(name="Due Date & Time", value=formatted_datetime, inline=True)
        embed.set_footer(
            text="React with a number for hours before the due date for a second reminder.\n"
                 "React with ✅ when completed (Only the author can react).\n"
                 "Note: If you don't react with a number, no second reminder will be sent."
        )

        # Send embed and add reactions
        reminder_message = await interaction.response.send_message(embed=embed)
        reminder_message = await interaction.original_response()
        await reminder_message.add_reaction("✅")

        def check(reaction, user):
            return user == interaction.user and reaction.message.id == reminder_message.id and (
                str(reaction.emoji).isdigit() or str(reaction.emoji) == "✅"
            )

        # Wait for reactions
        while True:
            reaction, user = await bot.wait_for("reaction_add", timeout=86400.0, check=check)  # Wait for 24 hours max
            if str(reaction.emoji).isdigit():  # Second reminder time in hours
                second_reminder_hours = int(str(reaction.emoji))
                task["second_reminder"] = second_reminder_hours
                tasks_collection.update_one({"_id": task["_id"]}, {"$set": {"second_reminder": second_reminder_hours}})
                await reminder_message.reply(f"Second reminder set {second_reminder_hours} hours before due.")
            elif str(reaction.emoji) == "✅":  # Mark as completed
                tasks_collection.delete_one({"_id": task["_id"]})

                # Change the embed color to green and update it
                embed.color = discord.Color.green()
                embed.title = f"Task Completed: {assignment_name}"
                await reminder_message.edit(embed=embed)
                await reminder_message.reply("Task marked as completed and removed from the database.")
                break

    except Exception as e:
        # Check if the interaction has already been responded to
        if not interaction.response.is_done():
            await interaction.response.send_message(f"An error occurred: {e}")
        else:
            print(f"Error: {e}")


# Background task to check tasks at 12 AM PST
@tasks.loop(time=time(0, 0, 0))
async def check_tasks_at_midnight():
    now = datetime.now(timezone)
    print(f"Running check_tasks_at_midnight at {now}")

    try:
        # Tasks due today
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

            # Fetch channel to send the notification
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

            # Schedule second reminder if applicable
            second_reminder = task.get("second_reminder")
            if second_reminder:
                reminder_time = datetime.fromisoformat(task["due_date"]) - timedelta(hours=second_reminder)
                if now < reminder_time:
                    # Call the task again in the future
                    await bot.wait_until(reminder_time)
                    if not task["completed"]:  # Check again if the task was not completed
                        if channel:
                            await channel.send(
                                f"<@{user_id}>, your second reminder for task '{assignment_name}' is here! Due in {second_reminder} hours!"
                            )
                        else:
                            await user.send(
                                f"Hi {user.name}, your second reminder for task '{assignment_name}' is here! Due in {second_reminder} hours!"
                            )

            # Remove expired tasks
            expired_tasks = tasks_collection.find({
                "completed": False,
                "due_date": {"$lt": (now - timedelta(days=1)).isoformat()}
            })

            for expired_task in expired_tasks:
                tasks_collection.delete_one({"_id": expired_task["_id"]})
                print(f"Deleted expired task: {expired_task}")

    except Exception as e:
        print(f"Error in check_tasks_at_midnight: {e}")


# Run the bot
if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_BOT_KEY"))
