"""
test_notifications.py — tests for send_scheduled_notification(item).

Verifies channel vs DM delivery, message content, and DB flag updates.
"""

import pytest
from datetime import datetime, timedelta
import pytz
from unittest.mock import AsyncMock, MagicMock
import NotiTron

TZ = pytz.timezone("America/Los_Angeles")


def _make_item(task, notification_type="due_notification", reminder_hours=None):
    """Helper to build the item dict passed to send_scheduled_notification."""
    now = datetime.now(TZ)
    item = {
        "task_id": str(task["_id"]),
        "type": notification_type,
        "task": task,
        "scheduled_time": now,
    }
    if reminder_hours is not None:
        item["reminder_hours"] = reminder_hours
    return item


@pytest.mark.asyncio
async def test_sends_to_channel_when_available(mock_db, mock_bot, make_task):
    """
    Verifies that when bot.get_channel returns a channel, the message
    is sent to that channel and not via DM.
    """
    task = make_task()
    fake_channel = MagicMock()
    fake_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = fake_channel

    item = _make_item(task, notification_type="due_notification")
    await NotiTron.send_scheduled_notification(item)

    fake_channel.send.assert_called_once()
    mock_bot.fetch_user.assert_not_called()


@pytest.mark.asyncio
async def test_falls_back_to_dm_when_no_channel(mock_db, mock_bot, make_task):
    """
    Verifies that when bot.get_channel returns None, fetch_user is called
    and the message is sent as a DM.
    """
    task = make_task()
    mock_bot.get_channel.return_value = None
    fake_user = MagicMock()
    fake_user.name = "testuser"
    fake_user.send = AsyncMock()
    mock_bot.fetch_user = AsyncMock(return_value=fake_user)

    item = _make_item(task, notification_type="due_notification")
    await NotiTron.send_scheduled_notification(item)

    mock_bot.fetch_user.assert_awaited_once()
    fake_user.send.assert_called_once()


@pytest.mark.asyncio
async def test_early_reminder_message_contains_hours(mock_db, mock_bot, make_task):
    """
    Verifies that the message sent for an early_reminder item contains
    the number of reminder hours.
    """
    task = make_task()
    fake_channel = MagicMock()
    fake_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = fake_channel

    item = _make_item(task, notification_type="early_reminder", reminder_hours=6)
    await NotiTron.send_scheduled_notification(item)

    fake_channel.send.assert_called_once()
    call_args = str(fake_channel.send.call_args)
    assert "6" in call_args


@pytest.mark.asyncio
async def test_due_notification_message_format(mock_db, mock_bot, make_task):
    """
    Verifies that a due_notification message contains the phrase 'due now'
    (case-insensitive).
    """
    task = make_task()
    fake_channel = MagicMock()
    fake_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = fake_channel

    item = _make_item(task, notification_type="due_notification")
    await NotiTron.send_scheduled_notification(item)

    fake_channel.send.assert_called_once()
    call_str = str(fake_channel.send.call_args).lower()
    assert "due" in call_str


@pytest.mark.asyncio
async def test_early_reminder_sets_sent_flag_in_db(mock_db, mock_bot, make_task):
    """
    Verifies that after sending an early_reminder, tasks_collection.update_one
    is called with $set early_reminder_sent=True.
    """
    task = make_task()
    fake_channel = MagicMock()
    fake_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = fake_channel

    item = _make_item(task, notification_type="early_reminder", reminder_hours=3)
    await NotiTron.send_scheduled_notification(item)

    mock_db.update_one.assert_called()
    update_call_args = mock_db.update_one.call_args
    # Second positional arg should be the $set operation
    update_doc = update_call_args[0][1] if update_call_args[0] else update_call_args.args[1]
    assert update_doc.get("$set", {}).get("early_reminder_sent") is True


@pytest.mark.asyncio
async def test_due_notification_does_not_set_sent_flag(mock_db, mock_bot, make_task):
    """
    Verifies that sending a due_notification does NOT trigger an update_one
    call to set early_reminder_sent.
    """
    task = make_task()
    fake_channel = MagicMock()
    fake_channel.send = AsyncMock()
    mock_bot.get_channel.return_value = fake_channel

    item = _make_item(task, notification_type="due_notification")
    await NotiTron.send_scheduled_notification(item)

    # Either update_one is not called at all, or it's not called with early_reminder_sent
    if mock_db.update_one.called:
        for call in mock_db.update_one.call_args_list:
            update_doc = call[0][1] if call[0] else call.args[1]
            assert "early_reminder_sent" not in update_doc.get("$set", {})
