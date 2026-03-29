import discord
from discord.ext import commands, tasks
from pymongo import MongoClient
from datetime import datetime, timedelta
import pytz
import os
import asyncio
import sys
from dotenv import load_dotenv

load_dotenv()

# MongoDB
db_client = MongoClient(os.getenv("MONGODB_CONNECTION"))
tasks_collection = db_client.NotiTronDB.Tasks

# Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

GUILD_ID = int(os.getenv("GUILD_ID"))
TZ = pytz.timezone("America/Los_Angeles")

# Key: (str(task_id), notification_type), Value: scheduled item dict
scheduled_tasks = {}


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        await bot.tree.sync()
        print("Slash commands synced.")

        now = datetime.now(TZ)
        for task in tasks_collection.find({"completed": False}):
            bot.add_view(PersistentCompleteButton(task), message_id=task.get("message_id"))

            due_datetime = datetime.fromisoformat(task["due_date"])
            task_id_str = str(task["_id"])

            due_key = (task_id_str, "due_notification")
            if due_datetime > now and due_key not in scheduled_tasks:
                scheduled_tasks[due_key] = {
                    "type": "due_notification",
                    "task": task,
                    "scheduled_time": due_datetime,
                }

            if task.get("early_reminder") and not task.get("early_reminder_sent", False):
                # Prefer stored early_reminder_time; fall back to calculating it
                if task.get("early_reminder_time"):
                    early_time = datetime.fromisoformat(task["early_reminder_time"])
                else:
                    early_time = due_datetime - timedelta(hours=task["early_reminder"])

                reminder_key = (task_id_str, "early_reminder")
                if early_time <= now:
                    # Missed while bot was down — send immediately as catch-up
                    print(f"Catch-up: sending missed early reminder for '{task['assignment_name']}'")
                    await send_scheduled_notification({
                        "type": "early_reminder",
                        "task": task,
                        "reminder_hours": task["early_reminder"],
                        "scheduled_time": early_time,
                    })
                elif reminder_key not in scheduled_tasks:
                    scheduled_tasks[reminder_key] = {
                        "type": "early_reminder",
                        "task": task,
                        "reminder_hours": task["early_reminder"],
                        "scheduled_time": early_time,
                    }

        print("Persistent views restored and scheduled tasks loaded.")

        if not check_scheduled_notifications.is_running():
            check_scheduled_notifications.start()
        if not check_tasks_hourly.is_running():
            check_tasks_hourly.start()
        if not restart_server_daily.is_running():
            restart_server_daily.start()

        asyncio.create_task(watch_changes())
        print("Change stream watcher started.")
    except Exception as e:
        print(f"Error in on_ready: {e}")


class PersistentCompleteButton(discord.ui.View):
    def __init__(self, task):
        super().__init__(timeout=None)
        self.task = task
        self.add_item(CompleteButton(task))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.task["user_id"]:
            await interaction.response.send_message(
                "You are not authorized to interact with these buttons.", ephemeral=True
            )
            return False
        return True


class ReminderView(discord.ui.View):
    def __init__(self, task, interaction):
        super().__init__(timeout=3600)
        self.task = task
        self.interaction = interaction
        self.reminder_buttons = []

        for hours in [1, 3, 6, 12]:
            button = ReminderButton(task, hours)
            self.reminder_buttons.append(button)
            self.add_item(button)

        self.add_item(CompleteButton(task))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.task["user_id"]:
            await interaction.response.send_message(
                "You are not authorized to interact with these buttons.", ephemeral=True
            )
            return False
        return True

    async def handle_reminder_confirmation(self, selected_button):
        for button in self.reminder_buttons:
            if button == selected_button:
                button.style = discord.ButtonStyle.green
            else:
                button.disabled = True
        await self.interaction.edit_original_response(view=self)

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, ReminderButton):
                child.disabled = True
        await self.interaction.edit_original_response(
            content="Reminder buttons have expired. You can still mark the task as complete.",
            view=self,
        )


class ReminderButton(discord.ui.Button):
    def __init__(self, task, hours):
        super().__init__(
            label=f"{hours} hour{'s' if hours > 1 else ''}",
            style=discord.ButtonStyle.blurple,
            custom_id=f"reminder_{hours}",
        )
        self.task = task
        self.hours = hours

    async def callback(self, interaction: discord.Interaction):
        due_datetime = datetime.fromisoformat(self.task["due_date"])
        early_reminder_time = due_datetime - timedelta(hours=self.hours)

        tasks_collection.update_one(
            {"_id": self.task["_id"]},
            {"$set": {
                "early_reminder": self.hours,
                "early_reminder_time": early_reminder_time.isoformat(),
            }}
        )
        self.task["early_reminder"] = self.hours
        self.task["early_reminder_time"] = early_reminder_time.isoformat()

        key = (str(self.task["_id"]), "early_reminder")
        scheduled_tasks[key] = {
            "type": "early_reminder",
            "task": self.task,
            "reminder_hours": self.hours,
            "scheduled_time": early_reminder_time,
        }

        formatted_time = early_reminder_time.strftime("%m/%d/%Y at %I:%M %p")
        await interaction.response.send_message(
            f"Early reminder set for {self.hours} hour{'s' if self.hours > 1 else ''} "
            f"before the due time, at **{formatted_time}**.",
            ephemeral=True,
        )
        await self.view.handle_reminder_confirmation(self)


class CompleteButton(discord.ui.Button):
    def __init__(self, task):
        super().__init__(
            label="Mark as Complete",
            style=discord.ButtonStyle.green,
            custom_id=f"complete_{task['_id']}",
        )
        self.task = task

    async def callback(self, interaction: discord.Interaction):
        tasks_collection.delete_one({"_id": self.task["_id"]})

        task_id_str = str(self.task["_id"])
        scheduled_tasks.pop((task_id_str, "due_notification"), None)
        scheduled_tasks.pop((task_id_str, "early_reminder"), None)

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.title = f"Task Completed: {self.task['assignment_name']}"

        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("Task marked as complete.", ephemeral=True)


@bot.tree.command(name="add_task", description="Set a reminder for an upcoming assignment!")
async def add_task(
    interaction: discord.Interaction,
    class_name: str,
    assignment_name: str,
    due_date: str,
    due_time: str,
):
    if interaction.guild.id != GUILD_ID:
        return

    try:
        for fmt in ("%m/%d/%y", "%m/%d/%Y"):
            try:
                due_date_parsed = datetime.strptime(due_date, fmt)
                break
            except ValueError:
                continue
        else:
            await interaction.response.send_message(
                "Invalid date format. Use MM/DD/YY or MM/DD/YYYY.", ephemeral=True
            )
            return

        try:
            due_time_parsed = datetime.strptime(due_time.strip().upper().replace(" ", ""), "%I:%M%p")
        except ValueError:
            await interaction.response.send_message(
                "Invalid time format. Use HH:MM AM/PM (e.g. `3:30 PM`). Please retry the command.",
                ephemeral=True,
            )
            return

        due_datetime = TZ.localize(due_date_parsed.replace(
            hour=due_time_parsed.hour,
            minute=due_time_parsed.minute,
            second=0,
            microsecond=0,
        ))

        if due_datetime <= datetime.now(TZ):
            await interaction.response.send_message(
                "The due date and time must be in the future. Please retry the command with a valid date.",
                ephemeral=True,
            )
            return

        task = {
            "class_name": class_name,
            "assignment_name": assignment_name,
            "due_date": due_datetime.isoformat(),
            "author": interaction.user.name,
            "user_id": interaction.user.id,
            "channel_id": interaction.channel.id,
            "completed": False,
            "early_reminder_sent": False,
        }
        result = tasks_collection.insert_one(task)
        task["_id"] = result.inserted_id

        scheduled_tasks[(str(task["_id"]), "due_notification")] = {
            "type": "due_notification",
            "task": task,
            "scheduled_time": due_datetime,
        }

        formatted_datetime = due_datetime.strftime("%m/%d/%Y at %I:%M %p")
        embed = discord.Embed(title=f"Task Added: {assignment_name}", color=discord.Color.red())
        embed.add_field(name="Class", value=class_name, inline=True)
        embed.add_field(name="Assignment", value=assignment_name, inline=True)
        embed.add_field(name="Due Date & Time", value=formatted_datetime, inline=True)
        embed.set_footer(text="Use the buttons below to set an early reminder or mark the task as complete.")

        await interaction.response.send_message(embed=embed, view=ReminderView(task, interaction))
        message = await interaction.original_response()
        tasks_collection.update_one({"_id": task["_id"]}, {"$set": {"message_id": message.id}})

    except Exception as e:
        if not interaction.response.is_done():
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
        else:
            print(f"Error in add_task: {e}")


async def send_scheduled_notification(item):
    task = item["task"]
    notification_type = item["type"]
    user_id = task["user_id"]
    channel_id = task.get("channel_id")

    try:
        if notification_type == "early_reminder":
            reminder_hours = item["reminder_hours"]
            message = (
                f"<@{user_id}>, reminder: **{task['assignment_name']}** is due in "
                f"{reminder_hours} hour{'s' if reminder_hours > 1 else ''}!"
            )
        else:
            message = f"<@{user_id}>, **{task['assignment_name']}** is due now!"

        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send(message)
        else:
            user = await bot.fetch_user(user_id)
            await user.send(message.replace(f"<@{user_id}>", user.name))

        if notification_type == "early_reminder":
            tasks_collection.update_one(
                {"_id": task["_id"]},
                {"$set": {"early_reminder_sent": True}}
            )
    except Exception as e:
        print(f"Error sending notification for '{task.get('assignment_name')}': {e}")


async def handle_change(change):
    op = change["operationType"]

    if op == "insert":
        task = change["fullDocument"]
        task_id_str = str(task["_id"])
        due_key = (task_id_str, "due_notification")
        if due_key not in scheduled_tasks:
            due_datetime = datetime.fromisoformat(task["due_date"])
            scheduled_tasks[due_key] = {
                "type": "due_notification",
                "task": task,
                "scheduled_time": due_datetime,
            }
            print(f"[ChangeStream] Scheduled due notification for '{task['assignment_name']}'")

    elif op == "delete":
        task_id_str = str(change["documentKey"]["_id"])
        scheduled_tasks.pop((task_id_str, "due_notification"), None)
        scheduled_tasks.pop((task_id_str, "early_reminder"), None)
        print(f"[ChangeStream] Removed scheduled tasks for deleted document {task_id_str}")

    elif op == "update":
        updated_fields = change.get("updateDescription", {}).get("updatedFields", {})
        if "early_reminder" in updated_fields:
            task = change.get("fullDocument")
            if task:
                task_id_str = str(task["_id"])
                key = (task_id_str, "early_reminder")
                if key not in scheduled_tasks:
                    # Use stored early_reminder_time if available; otherwise calculate it
                    if task.get("early_reminder_time"):
                        early_time = datetime.fromisoformat(task["early_reminder_time"])
                    else:
                        due_datetime = datetime.fromisoformat(task["due_date"])
                        hours = task["early_reminder"]
                        early_time = due_datetime - timedelta(hours=hours)
                    scheduled_tasks[key] = {
                        "type": "early_reminder",
                        "task": task,
                        "reminder_hours": task["early_reminder"],
                        "scheduled_time": early_time,
                    }
                    print(f"[ChangeStream] Scheduled early reminder for '{task['assignment_name']}' at {early_time}")


async def watch_changes():
    loop = asyncio.get_event_loop()
    pipeline = [{"$match": {"operationType": {"$in": ["insert", "delete", "update"]}}}]

    def _watch():
        try:
            with tasks_collection.watch(pipeline, full_document="updateLookup") as stream:
                for change in stream:
                    asyncio.run_coroutine_threadsafe(handle_change(change), loop)
        except Exception as e:
            print(f"[ChangeStream] Error: {e}")

    while True:
        await loop.run_in_executor(None, _watch)
        print("[ChangeStream] Stream ended, restarting in 5s...")
        await asyncio.sleep(5)


@tasks.loop(minutes=1)
async def check_scheduled_notifications():
    now = datetime.now(TZ).replace(second=0, microsecond=0)

    due = [
        (key, item) for key, item in list(scheduled_tasks.items())
        if item["scheduled_time"].replace(second=0, microsecond=0) <= now
    ]

    for key, item in due:
        await send_scheduled_notification(item)
        scheduled_tasks.pop(key, None)


@tasks.loop(hours=1)
async def check_tasks_hourly():
    now = datetime.now(TZ)
    next_hour = now + timedelta(hours=1)
    print(f"Hourly check at {now.strftime('%m/%d/%Y %I:%M %p')}")

    try:
        for task in tasks_collection.find({
            "completed": False,
            "due_date": {"$gte": now.isoformat(), "$lt": next_hour.isoformat()}
        }):
            due_datetime = datetime.fromisoformat(task["due_date"])
            due_key = (str(task["_id"]), "due_notification")

            if due_key not in scheduled_tasks:
                scheduled_tasks[due_key] = {
                    "type": "due_notification",
                    "task": task,
                    "scheduled_time": due_datetime,
                }
                print(f"Scheduled due notification for '{task['assignment_name']}' at {due_datetime}")

        for task in tasks_collection.find({
            "completed": False,
            "early_reminder": {"$exists": True},
            "early_reminder_sent": {"$ne": True},
        }):
            if task.get("early_reminder_time"):
                early_time = datetime.fromisoformat(task["early_reminder_time"])
            else:
                due_datetime = datetime.fromisoformat(task["due_date"])
                early_time = due_datetime - timedelta(hours=task["early_reminder"])
            reminder_key = (str(task["_id"]), "early_reminder")

            if now <= early_time < next_hour and reminder_key not in scheduled_tasks:
                scheduled_tasks[reminder_key] = {
                    "type": "early_reminder",
                    "task": task,
                    "reminder_hours": task["early_reminder"],
                    "scheduled_time": early_time,
                }
                print(f"Scheduled early reminder for '{task['assignment_name']}' at {early_time}")

        for task in tasks_collection.find({"completed": False, "due_date": {"$lt": now.isoformat()}}):
            print(f"Removing expired task: '{task['assignment_name']}' (due {task['due_date']})")
            tasks_collection.delete_one({"_id": task["_id"]})
            task_id_str = str(task["_id"])
            scheduled_tasks.pop((task_id_str, "due_notification"), None)
            scheduled_tasks.pop((task_id_str, "early_reminder"), None)

    except Exception as e:
        print(f"Error in check_tasks_hourly: {e}")


@check_tasks_hourly.before_loop
async def before_check_tasks_hourly():
    now = datetime.now(TZ)
    seconds_to_wait = ((60 - now.minute) % 60) * 60 - now.second
    if seconds_to_wait > 0:
        print(f"Waiting {seconds_to_wait}s to align hourly check with the clock.")
        await asyncio.sleep(seconds_to_wait)


@check_scheduled_notifications.before_loop
async def before_check_scheduled_notifications():
    now = datetime.now(TZ)
    seconds_to_wait = (60 - now.second) % 60
    if seconds_to_wait > 0:
        print(f"Waiting {seconds_to_wait}s to align notification check with the minute.")
        await asyncio.sleep(seconds_to_wait)


@tasks.loop(hours=24)
async def restart_server_daily():
    print("Performing daily restart...")
    os.execv(sys.executable, ["python"] + sys.argv)


@restart_server_daily.before_loop
async def before_restart_server_daily():
    now = datetime.now(TZ)
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    seconds_to_wait = (next_midnight - now).total_seconds()
    print(f"Daily restart scheduled in {seconds_to_wait:.0f}s.")
    await asyncio.sleep(seconds_to_wait)


if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_BOT_KEY"))
