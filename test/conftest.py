"""
conftest.py — shared fixtures and mock setup for NotiTron test suite.

Sets up all necessary mocks before NotiTron.py is imported so that
discord, pymongo, and dotenv do not need to be installed.
"""

import sys
import os
import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock
import pytest
import pytz

# ---------------------------------------------------------------------------
# 1. Add parent directory to sys.path so `import NotiTron` works
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# 2. Set required environment variables before NotiTron is imported
# ---------------------------------------------------------------------------
os.environ.setdefault("MONGODB_CONNECTION", "mongodb://localhost:27017")
os.environ.setdefault("GUILD_ID", "111111111111111111")
os.environ.setdefault("DISCORD_BOT_KEY", "fake-discord-bot-key")

# ---------------------------------------------------------------------------
# 3. Build fake discord / ext / pymongo / dotenv modules
# ---------------------------------------------------------------------------

# --- dotenv ---
fake_dotenv = MagicMock()
fake_dotenv.load_dotenv = MagicMock(return_value=None)

# --- pymongo ---
fake_pymongo = MagicMock()
fake_mongo_client_instance = MagicMock()
fake_pymongo.MongoClient = MagicMock(return_value=fake_mongo_client_instance)

# --- FakeView / FakeButton base classes (used by NotiTron UI components) ---

class FakeView:
    def __init__(self, timeout=None):
        self.timeout = timeout
        self.children = []

    def add_item(self, item):
        self.children.append(item)


class FakeButton:
    def __init__(self, **kwargs):
        self.label = kwargs.get("label", "")
        self.style = kwargs.get("style", None)
        self.custom_id = kwargs.get("custom_id", None)
        self.disabled = kwargs.get("disabled", False)


# --- _make_loop factory ---

def _make_loop(**kwargs):
    """
    Returns a decorator that:
    - preserves the wrapped async function as-is
    - attaches .is_running, .start, .stop, .before_loop attributes
    """
    def decorator(func):
        func.is_running = MagicMock(return_value=False)
        func.start = MagicMock()
        func.stop = MagicMock()

        def before_loop(before_fn):
            func._before_loop_fn = before_fn
            return before_fn

        func.before_loop = before_loop
        return func

    return decorator


# --- FakeBot ---

class FakeBot:
    def __init__(self):
        self.tree = MagicMock()
        self.tree.command = MagicMock(side_effect=lambda **kw: lambda f: f)
        self.tree.sync = AsyncMock()
        self.user = MagicMock()
        self.add_view = MagicMock()
        self.get_channel = MagicMock()
        self.fetch_user = AsyncMock()
        self.run = MagicMock()

    def event(self, func):
        """Pass-through decorator for @bot.event."""
        return func


# --- discord.ButtonStyle mock ---
fake_button_style = MagicMock()
fake_button_style.primary = "primary"
fake_button_style.secondary = "secondary"
fake_button_style.success = "success"
fake_button_style.danger = "danger"

# --- discord.Embed mock ---
fake_embed_class = MagicMock()

# --- discord.Intents mock ---
fake_intents_instance = MagicMock()
fake_intents_instance.message_content = True
fake_intents_class = MagicMock()
fake_intents_class.default = MagicMock(return_value=fake_intents_instance)

# --- discord module ---
fake_discord = MagicMock()
fake_discord.Intents = fake_intents_class
fake_discord.ButtonStyle = fake_button_style
fake_discord.Embed = fake_embed_class
fake_discord.ui = MagicMock()
fake_discord.ui.View = FakeView
fake_discord.ui.Button = FakeButton
fake_discord.ui.button = MagicMock(side_effect=lambda **kw: lambda f: f)
fake_discord.app_commands = MagicMock()
fake_discord.app_commands.describe = MagicMock(side_effect=lambda **kw: lambda f: f)

# --- discord.ext.commands ---
fake_commands = MagicMock()
fake_commands.Bot = MagicMock(return_value=FakeBot())
fake_commands.Cog = MagicMock()

# --- discord.ext.tasks ---
fake_tasks = MagicMock()
fake_tasks.loop = _make_loop

# --- discord.ext ---
fake_ext = MagicMock()
fake_ext.commands = fake_commands
fake_ext.tasks = fake_tasks

# Attach ext to discord
fake_discord.ext = fake_ext

# ---------------------------------------------------------------------------
# 4. Register mocks in sys.modules (use setdefault so real discord isn't
#    overwritten if it happens to be installed)
# ---------------------------------------------------------------------------
sys.modules.setdefault("discord", fake_discord)
sys.modules.setdefault("discord.ext", fake_ext)
sys.modules.setdefault("discord.ext.commands", fake_commands)
sys.modules.setdefault("discord.ext.tasks", fake_tasks)
sys.modules.setdefault("pymongo", fake_pymongo)
sys.modules.setdefault("dotenv", fake_dotenv)

# ---------------------------------------------------------------------------
# 5. Now import NotiTron (all mocks are in place)
# ---------------------------------------------------------------------------
import NotiTron  # noqa: E402

# Expose GUILD_ID for use in fixtures
GUILD_ID = int(os.environ["GUILD_ID"])

# ---------------------------------------------------------------------------
# 6. Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_scheduled_tasks():
    """Clears NotiTron.scheduled_tasks before and after each test."""
    NotiTron.scheduled_tasks.clear()
    yield
    NotiTron.scheduled_tasks.clear()


@pytest.fixture
def mock_db(monkeypatch):
    """Replaces NotiTron.tasks_collection with a MagicMock."""
    db_mock = MagicMock()
    monkeypatch.setattr(NotiTron, "tasks_collection", db_mock)
    return db_mock


@pytest.fixture
def mock_bot(monkeypatch):
    """Replaces NotiTron.bot with a fresh FakeBot instance."""
    bot = FakeBot()
    monkeypatch.setattr(NotiTron, "bot", bot)
    return bot


@pytest.fixture
def mock_send(monkeypatch):
    """Replaces NotiTron.send_scheduled_notification with an AsyncMock."""
    send_mock = AsyncMock()
    monkeypatch.setattr(NotiTron, "send_scheduled_notification", send_mock)
    return send_mock


@pytest.fixture
def make_task():
    """
    Factory fixture that creates task dicts.

    Parameters
    ----------
    hours_until_due : int, default 24
    early_reminder  : int or None
    early_reminder_sent : bool, default False
    completed       : bool, default False
    task_id         : str, default "task_001"
    """
    TZ = pytz.timezone("America/Los_Angeles")

    def _factory(
        hours_until_due=24,
        early_reminder=None,
        early_reminder_sent=False,
        completed=False,
        task_id="task_001",
    ):
        now = datetime.now(TZ)
        due_datetime = now + timedelta(hours=hours_until_due)

        task = {
            "_id": task_id,
            "task_id": task_id,
            "class_name": "CS101",
            "assignment_name": "Homework 1",
            "due_date": due_datetime.isoformat(),
            "user_id": 123456789,
            "user_name": "testuser",
            "channel_id": 987654321,
            "completed": completed,
            "early_reminder": early_reminder,
            "early_reminder_sent": early_reminder_sent,
        }

        if early_reminder is not None:
            early_reminder_time = due_datetime - timedelta(hours=early_reminder)
            task["early_reminder_time"] = early_reminder_time.isoformat()

        return task

    return _factory


@pytest.fixture
def make_interaction():
    """
    Factory fixture that creates MagicMock Discord interaction objects.
    """
    def _factory(user_id=123456789, user_name="testuser", channel_id=987654321, guild_id=None):
        if guild_id is None:
            guild_id = GUILD_ID

        interaction = MagicMock()
        interaction.guild = MagicMock()
        interaction.guild.id = guild_id

        interaction.user = MagicMock()
        interaction.user.id = user_id
        interaction.user.name = user_name

        interaction.channel = MagicMock()
        interaction.channel.id = channel_id

        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.response.is_done = MagicMock(return_value=False)
        interaction.original_response = AsyncMock(return_value=MagicMock(id=444555666))

        return interaction

    return _factory
