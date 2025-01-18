import discord
from discord.ext import commands, tasks
from pymongo import MongoClient
from datetime import datetime, timedelta
import pytz
import os
import asyncio
from dotenv import load_dotenv
import sys

# Load environment variables from .env file
load_dotenv()

# MongoDB Config
MONGO_URI = os.getenv("MONGODB_CONNECTION")
db_client = MongoClient(MONGO_URI)
db = db_client.NotiTronDB  # Database
tasks_collection = db.Tasks  # Collection

# Bot Configuration with Message Content Intent
intents = discord.Intents.default()
intents.message_content = True  # Enable the message content intent
bot = commands.Bot(command_prefix="/", intents=intents)

# Guild ID
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

        # Restore persistent views for incomplete tasks
        print("Restoring persistent views...")
        incomplete_tasks = tasks_collection.find({"completed": False})
        for task in incomplete_tasks:
            view = PersistentCompleteButton(task)
            bot.add_view(view, message_id=task.get("message_id"))

        # Start background tasks
        if not check_tasks_every_minute.is_running():
            check_tasks_every_minute.start()
        if not restart_server_every_hour.is_running():
            restart_server_every_hour.start()

    except Exception as e:
        print(f"Error in on_ready: {e}")


# Persistent View for "Mark as Complete"
class PersistentCompleteButton(discord.ui.View):
    def __init__(self, task):
        super().__init__(timeout=None)  # No timeout
        self.add_item(CompleteButton(task))


# Reminder View with Dynamic Buttons
class ReminderView(discord.ui.View):
    def __init__(self, task, interaction):
        super().__init__(timeout=3600)  # Buttons remain active for 1 hour
        self.task = task
        self.interaction = interaction
        self.reminder_buttons = []

        # Add buttons for selecting reminder times
        for hours in [1, 3, 6, 12]:  # Hour ranges for the user to select
            button = ReminderButton(task, hours)
            self.reminder_buttons.append(button)
            self.add_item(button)

        # Add "Mark as Complete" button
        self.add_item(CompleteButton(task))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Allow only the task author to interact with the buttons
        if interaction.user.id != self.task["user_id"]:
            await interaction.response.send_message("You are not allowed to interact with these buttons.", ephemeral=True)
            return False
        return True

    async def handle_reminder_confirmation(self, selected_button):
        # Disable all reminder buttons except the selected one
        for button in self.reminder_buttons:
            if button == selected_button:
                button.style = discord.ButtonStyle.green
            else:
                button.disabled = True  # Disable other buttons
        await self.interaction.edit_original_response(view=self)

    async def on_timeout(self):
        # Disable all buttons after timeout, except the "Mark as Complete" button
        for child in self.children:
            if isinstance(child, ReminderButton):
                child.disabled = True
            elif isinstance(child, CompleteButton):
                child.disabled = False  # Ensure "Mark as Complete" stays enabled
        await self.interaction.edit_original_response(
            content="The buttons have expired, but you can still mark the task as complete.",
            view=self,
        )


class ReminderButton(discord.ui.Button):
    def __init__(self, task, hours):
        super().__init__(label=f"{hours} hour{'s' if hours > 1 else ''}", style=discord.ButtonStyle.blurple, custom_id=f"reminder_{hours}")
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
            f"Second reminder set for {self.hours} hour{'s' if self.hours > 1 else ''} before the due time, at **{formatted_time}**.", ephemeral=True
        )

        # Update the view to reflect the selected button
        await self.view.handle_reminder_confirmation(self)


class CompleteButton(discord.ui.Button):
    def __init__(self, task):
        super().__init__(label="Mark as Complete", style=discord.ButtonStyle.green, custom_id=f"complete_{task['_id']}")
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


# Background task to check tasks every minute and align with whole-minute intervals
@tasks.loop(minutes=1)
async def check_tasks_every_minute():
    now = datetime.now(timezone)
    print(f"Running check_tasks_every_minute at {now}")

    try:
        # Check for second reminders and expired tasks
        tasks_due_soon = tasks_collection.find({
            "completed": False,
            "second_reminder": {"$exists": True},
            "second_reminder_sent": False
        })

        for task in tasks_due_soon:
            user_id = task["user_id"]
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


@check_tasks_every_minute.before_loop
async def before_check_tasks_every_minute():
    now = datetime.now(timezone)
    seconds_to_wait = 60 - now.second
    print(f"Waiting {seconds_to_wait} seconds to align with the next whole minute.")
    await asyncio.sleep(seconds_to_wait)


# Background task to restart the server every hour
@tasks.loop(hours=1)
async def restart_server_every_hour():
    print("Restarting server...")
    os.execv(sys.executable, ['python'] + sys.argv)


@restart_server_every_hour.before_loop
async def before_restart_server_every_hour():
    now = datetime.now(timezone)
    seconds_to_wait = (60 - now.minute) * 60 - now.second
    print(f"Waiting {seconds_to_wait} seconds to align with the next hour.")
    await asyncio.sleep(seconds_to_wait)


# Run the bot
if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_BOT_KEY"))