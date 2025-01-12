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

    try:
        # Sync the slash commands with Discord API
        await bot.tree.sync()
        print("Slash commands synced successfully.")

        # Start background tasks
        if not check_tasks_every_minute.is_running():
            check_tasks_every_minute.start()

    except Exception as e:
        print(f"Error in on_ready: {e}")


# Reminder View with Buttons
class ReminderView(discord.ui.View):
    def __init__(self, task, interaction):
        super().__init__(timeout=3600)  # Buttons remain active for 1 hour
        self.task = task
        self.interaction = interaction

        # Add buttons for selecting reminder times
        for hours in [1, 3, 6, 12]:  # Customize as needed
            self.add_item(ReminderButton(task, hours))

        # Add "Mark as Complete" button
        self.add_item(CompleteButton(task))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Allow only the task author to interact with the buttons
        if interaction.user.id != self.task["user_id"]:
            await interaction.response.send_message("You are not allowed to interact with these buttons.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        # Edit the message to disable the buttons after the timeout
        for child in self.children:
            child.disabled = True
        await self.interaction.edit_original_response(content="The buttons have expired.", view=self)


class ReminderButton(discord.ui.Button):
    def __init__(self, task, hours):
        super().__init__(label=f"{hours} hours", style=discord.ButtonStyle.blurple, custom_id=f"reminder_{hours}")
        self.task = task
        self.hours = hours

    async def callback(self, interaction: discord.Interaction):
        # Save the second reminder time to the database
        tasks_collection.update_one(
            {"_id": self.task["_id"]},
            {"$set": {"second_reminder": self.hours}}
        )
        self.task["second_reminder"] = self.hours

        # Confirm the second reminder time to the user
        due_datetime = datetime.fromisoformat(self.task["due_date"])
        second_reminder_time = due_datetime - timedelta(hours=self.hours)
        formatted_time = second_reminder_time.strftime("%m/%d/%Y at %I:%M %p")
        await interaction.response.send_message(
            f"Second reminder set for {self.hours} hours before the due time, at **{formatted_time}**.", ephemeral=True
        )


class CompleteButton(discord.ui.Button):
    def __init__(self, task):
        super().__init__(label="Mark as Complete", style=discord.ButtonStyle.green, custom_id="complete_task")
        self.task = task

    async def callback(self, interaction: discord.Interaction):
        # Mark the task as completed and remove it from the database
        tasks_collection.delete_one({"_id": self.task["_id"]})

        # Update the embed to indicate completion
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.title = f"Task Completed: {self.task['assignment_name']}"

        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("Task marked as completed and removed from the database.", ephemeral=True)


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
            "second_reminder_sent": False,  # Flag for second reminder
        }
        result = tasks_collection.insert_one(task)
        task["_id"] = result.inserted_id  # Add the ID to the task dictionary

        # Format due date and time for the embed
        formatted_datetime = due_datetime.strftime("%m/%d/%Y at %I:%M %p")

        # Create an embed with the task info
        embed = discord.Embed(title=f"Task Added: {assignment_name}", color=discord.Color.red())
        embed.add_field(name="Class", value=class_name, inline=True)
        embed.add_field(name="Assignment", value=assignment_name, inline=True)
        embed.add_field(name="Due Date & Time", value=formatted_datetime, inline=True)
        embed.set_footer(text="Use the buttons below to set a second reminder or mark the task as complete.")

        # Create the button view
        reminder_view = ReminderView(task, interaction)

        # Send the embed with the buttons
        await interaction.response.send_message(embed=embed, view=reminder_view)

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
        # Check if it's 12 AM PST, and send the first reminder
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

        # Check for second reminders due soon
        tasks_due_soon = tasks_collection.find({
            "completed": False,
            "second_reminder": {"$exists": True},
            "second_reminder_sent": False
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

        # Automatically delete expired tasks
        expired_tasks = tasks_collection.find({
            "completed": False,
            "due_date": {"$lt": now.isoformat()}
        })

        for task in expired_tasks:
            print(f"Deleting expired task: {task['assignment_name']} (due {task['due_date']})")
            tasks_collection.delete_one({"_id": task["_id"]})

    except Exception as e:
        print(f"Error in check_tasks_every_minute: {e}")


# Run the bot
if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_BOT_KEY"))
