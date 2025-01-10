import discord
from discord.ext import commands, tasks
from pymongo import MongoClient
from datetime import datetime, timedelta, time
import pytz
import os
from dotenv import load_dotenv
import asyncio

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

    try:
        # Sync the slash commands with Discord API
        await bot.tree.sync()
        print("Slash commands synced successfully.")

        # Start background tasks
        if not check_tasks_every_minute.is_running():
            check_tasks_every_minute.start()

    except Exception as e:
        print(f"Error in on_ready: {e}")


# Slash command to add a task
@bot.tree.command(name="add_task", description="Set a reminder for upcoming assignments!")
async def add_task(interaction: discord.Interaction, class_name: str, assignment_name: str, due_date: str, due_time: str):
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
            "channel_id": interaction.channel.id,
            "completed": False,
            "second_reminder_sent": False,  # New flag to track if second reminder has been sent
        }
        tasks_collection.insert_one(task)

        # Format due date and time for the embed
        formatted_datetime = due_datetime.strftime("%m/%d/%Y at %I:%M %p")

        # Create an embed with the task info
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

        # Add number reactions from 1️⃣ to 9️⃣ and checkmark ✅
        for emoji in ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "✅"]:
            await reminder_message.add_reaction(emoji)

        def check(reaction, user):
            return user == interaction.user and reaction.message.id == reminder_message.id and (
                str(reaction.emoji) in ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"] or str(reaction.emoji) == "✅"
            )

        # Wait for reactions
        while True:
            try:
                reaction, user = await bot.wait_for("reaction_add", timeout=86400.0, check=check)
                if str(reaction.emoji) in ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]:  # Match the emoji directly
                    second_reminder_hours = int(str(reaction.emoji)[0])  # Extract the number from the emoji
                    task["second_reminder"] = second_reminder_hours
                    tasks_collection.update_one({"_id": task["_id"]}, {"$set": {"second_reminder": second_reminder_hours}})

                    # Calculate and format the second reminder time
                    due_datetime = datetime.fromisoformat(task["due_date"])  # Retrieve the due date
                    second_reminder_time = due_datetime - timedelta(hours=second_reminder_hours)
                    formatted_second_reminder_time = second_reminder_time.strftime("%m/%d/%Y at %I:%M %p")

                    # Send confirmation message to the channel
                    confirmation_message = (
                        f"You will receive a second reminder **{second_reminder_hours} hours** before "
                        f"**{task['assignment_name']}** is due, at **{formatted_second_reminder_time}**."
                    )
                    await reminder_message.reply(confirmation_message)

                elif str(reaction.emoji) == "✅":  # If the reaction is ✅ (mark task as completed)
                    tasks_collection.delete_one({"_id": task["_id"]})

                    # Change the embed color to green and update it
                    embed.color = discord.Color.green()
                    embed.title = f"Task Completed: {task['assignment_name']}"
                    await reminder_message.edit(embed=embed)
                    await reminder_message.reply("Task marked as completed and removed from the database.")
                    break
            except asyncio.TimeoutError:
                print("Timeout reached waiting for reaction.")
                break

    except Exception as e:
        if not interaction.response.is_done():
            await interaction.response.send_message(f"An error occurred: {e}")
        else:
            print(f"Error: {e}")


# Background task to check tasks every minute and at 12 AM PST
@tasks.loop(minutes=1)
async def check_tasks_every_minute():
    now = datetime.now(timezone)
    print(f"Running check_tasks_every_minute at {now}")

    try:
        # 1. Check if it's 12 AM PST, and send the first reminder
        if now.hour == 0 and now.minute == 0:
            print("It's 12 AM PST - sending reminders for tasks due today.")

            tasks_due_today = tasks_collection.find({
                "completed": False,
                "due_date": {
                    "$gte": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                    "$lte": now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
                }
            })

            for task in tasks_due_today:
                user_id = task["user_id"]
                class_name = task["class_name"]
                assignment_name = task["assignment_name"]

                channel_id = task.get("channel_id")
                channel = bot.get_channel(channel_id) if channel_id else None
                user = await bot.fetch_user(user_id)

                if channel:
                    await channel.send(
                        f"<@{user_id}>, your task '{assignment_name}' for class '{class_name}' is due today!"
                    )
                else:
                    await user.send(
                        f"Hi {user.name}, your task '{assignment_name}' for class '{class_name}' is due today!"
                    )

        # 2. Check for second reminders due within 5 minutes
        tasks_due_soon = tasks_collection.find({
            "completed": False,
            "second_reminder": {"$exists": True},
            "second_reminder_sent": False,  # Only send if the second reminder hasn't been sent
            "due_date": {"$lt": (now + timedelta(minutes=5)).isoformat()}
        })

        for task in tasks_due_soon:
            user_id = task["user_id"]
            class_name = task["class_name"]
            assignment_name = task["assignment_name"]
            second_reminder = task.get("second_reminder")

            # Send second reminder if it's time
            if second_reminder:
                reminder_time = datetime.fromisoformat(task["due_date"]) - timedelta(hours=second_reminder)
                if now >= reminder_time:
                    channel_id = task.get("channel_id")
                    channel = bot.get_channel(channel_id) if channel_id else None
                    user = await bot.fetch_user(user_id)

                    if channel:
                        await channel.send(
                            f"<@{user_id}>, your second reminder for task '{assignment_name}' is here! Due in {second_reminder} hours!"
                        )
                    else:
                        await user.send(
                            f"Hi {user.name}, your second reminder for task '{assignment_name}' is here! Due in {second_reminder} hours!"
                        )

                    # Mark the second reminder as sent
                    tasks_collection.update_one({"_id": task["_id"]}, {"$set": {"second_reminder_sent": True}})

    except Exception as e:
        print(f"Error in check_tasks_every_minute: {e}")


# Run the bot
if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_BOT_KEY"))
