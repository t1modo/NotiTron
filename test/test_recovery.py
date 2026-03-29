"""
test_recovery.py — tests for on_ready startup recovery logic.

Verifies that NotiTron correctly restores state from the database
on bot startup, schedules future notifications, and fires catch-up
sends for missed reminders.
"""

import pytest
from datetime import datetime, timedelta
import pytz
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import NotiTron

TZ = pytz.timezone("America/Los_Angeles")


@pytest.mark.asyncio
async def test_future_due_notification_scheduled(mock_db, mock_bot, mock_send, make_task):
    """Verifies that a task due in the future gets its due_notification scheduled on startup."""
    task = make_task(hours_until_due=24)
    mock_db.find.return_value = iter([task])

    with patch("NotiTron.asyncio.create_task", MagicMock()):
        await NotiTron.on_ready()

    key = (str(task["task_id"]), "due_notification")
    assert key in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_past_due_notification_not_scheduled(mock_db, mock_bot, mock_send, make_task):
    """Verifies that a task already past due does NOT get a due_notification scheduled."""
    task = make_task(hours_until_due=-1)
    mock_db.find.return_value = iter([task])

    with patch("NotiTron.asyncio.create_task", MagicMock()):
        await NotiTron.on_ready()

    key = (str(task["task_id"]), "due_notification")
    assert key not in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_future_early_reminder_scheduled(mock_db, mock_bot, mock_send, make_task):
    """
    Verifies that a task with a future early_reminder_time gets the early_reminder
    scheduled and send is NOT called (no catch-up needed).
    """
    now = datetime.now(TZ)
    task = make_task(hours_until_due=5, early_reminder=3)
    # early_reminder_time is 5-3=2 hours from now (future)
    mock_db.find.return_value = iter([task])

    with patch("NotiTron.asyncio.create_task", MagicMock()):
        await NotiTron.on_ready()

    key = (str(task["task_id"]), "early_reminder")
    assert key in NotiTron.scheduled_tasks
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_missed_early_reminder_triggers_catchup(mock_db, mock_bot, mock_send, make_task):
    """
    Verifies that if early_reminder_time is in the past and not yet sent,
    on_ready immediately fires send_scheduled_notification as a catch-up.
    """
    now = datetime.now(TZ)
    task = make_task(hours_until_due=24, early_reminder=3, early_reminder_sent=False)
    # Override early_reminder_time to be in the past
    task["early_reminder_time"] = (now - timedelta(minutes=10)).isoformat()
    mock_db.find.return_value = iter([task])

    with patch("NotiTron.asyncio.create_task", MagicMock()):
        await NotiTron.on_ready()

    # Should have been called with type="early_reminder"
    mock_send.assert_called()
    call_args = mock_send.call_args[0][0]
    assert call_args.get("type") == "early_reminder"


@pytest.mark.asyncio
async def test_already_sent_reminder_not_rescheduled(mock_db, mock_bot, mock_send, make_task):
    """
    Verifies that if early_reminder_sent=True, the reminder is neither
    rescheduled nor re-sent on startup.
    """
    task = make_task(hours_until_due=5, early_reminder=3, early_reminder_sent=True)
    mock_db.find.return_value = iter([task])

    with patch("NotiTron.asyncio.create_task", MagicMock()):
        await NotiTron.on_ready()

    key = (str(task["task_id"]), "early_reminder")
    assert key not in NotiTron.scheduled_tasks
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_uses_stored_early_reminder_time_not_recalculated(mock_db, mock_bot, mock_send, make_task):
    """
    Verifies that the scheduled_time in scheduled_tasks matches the stored
    early_reminder_time from the DB, not a value recalculated from due_date - hours.
    """
    now = datetime.now(TZ)
    specific_future_time = now + timedelta(hours=2, minutes=7, seconds=13)
    task = make_task(hours_until_due=5, early_reminder=3)
    # Override with a very specific time that wouldn't match a simple recalculation
    task["early_reminder_time"] = specific_future_time.isoformat()
    mock_db.find.return_value = iter([task])

    with patch("NotiTron.asyncio.create_task", MagicMock()):
        await NotiTron.on_ready()

    key = (str(task["task_id"]), "early_reminder")
    assert key in NotiTron.scheduled_tasks
    stored = NotiTron.scheduled_tasks[key]["scheduled_time"]
    assert abs((stored - specific_future_time).total_seconds()) < 1


@pytest.mark.asyncio
async def test_loops_are_started(mock_db, mock_bot, mock_send):
    """
    Verifies that check_scheduled_notifications.start() and
    check_tasks_hourly.start() are called during on_ready.
    """
    mock_db.find.return_value = iter([])

    with patch("NotiTron.asyncio.create_task", MagicMock()):
        await NotiTron.on_ready()

    NotiTron.check_scheduled_notifications.start.assert_called()
    NotiTron.check_tasks_hourly.start.assert_called()


@pytest.mark.asyncio
async def test_multiple_tasks_all_loaded(mock_db, mock_bot, mock_send, make_task):
    """
    Verifies that when DB returns two tasks, both due_notification keys
    appear in scheduled_tasks after on_ready.
    """
    task_a = make_task(hours_until_due=10, task_id="task_a")
    task_b = make_task(hours_until_due=20, task_id="task_b")
    mock_db.find.return_value = iter([task_a, task_b])

    with patch("NotiTron.asyncio.create_task", MagicMock()):
        await NotiTron.on_ready()

    assert ("task_a", "due_notification") in NotiTron.scheduled_tasks
    assert ("task_b", "due_notification") in NotiTron.scheduled_tasks
