"""
test_scheduling.py — tests for the per-minute and per-hour scheduling loops.

Covers:
  - check_scheduled_notifications (per-minute loop)
  - before_check_scheduled_notifications (alignment sleep)
  - check_tasks_hourly (per-hour loop)
"""

import pytest
from datetime import datetime, timedelta
import pytz
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import NotiTron

TZ = pytz.timezone("America/Los_Angeles")


# ===========================================================================
# Group 1 — check_scheduled_notifications
# ===========================================================================

@pytest.mark.asyncio
async def test_fires_item_due_in_past(mock_send):
    """Verifies that an item whose scheduled_time is in the past triggers send."""
    now = datetime.now(TZ)
    item = {
        "task_id": "t1",
        "type": "due_notification",
        "scheduled_time": now - timedelta(minutes=2),
    }
    NotiTron.scheduled_tasks[("t1", "due_notification")] = item

    await NotiTron.check_scheduled_notifications()

    mock_send.assert_called_once()
    assert ("t1", "due_notification") not in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_fires_item_due_exactly_now(mock_send):
    """Verifies that an item due exactly at the current minute fires."""
    now = datetime.now(TZ).replace(second=0, microsecond=0)
    item = {
        "task_id": "t2",
        "type": "due_notification",
        "scheduled_time": now,
    }
    NotiTron.scheduled_tasks[("t2", "due_notification")] = item

    await NotiTron.check_scheduled_notifications()

    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_does_not_fire_future_item(mock_send):
    """Verifies that a future-scheduled item is not fired and remains in the dict."""
    now = datetime.now(TZ)
    item = {
        "task_id": "t3",
        "type": "due_notification",
        "scheduled_time": now + timedelta(minutes=5),
    }
    NotiTron.scheduled_tasks[("t3", "due_notification")] = item

    await NotiTron.check_scheduled_notifications()

    mock_send.assert_not_called()
    assert ("t3", "due_notification") in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_removes_item_after_firing(mock_send):
    """Verifies that a fired item's key is removed from scheduled_tasks."""
    now = datetime.now(TZ)
    item = {
        "task_id": "t4",
        "type": "due_notification",
        "scheduled_time": now - timedelta(seconds=30),
    }
    NotiTron.scheduled_tasks[("t4", "due_notification")] = item

    await NotiTron.check_scheduled_notifications()

    assert ("t4", "due_notification") not in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_fires_early_reminder_type(mock_send):
    """Verifies early_reminder type items are passed correctly to send."""
    now = datetime.now(TZ)
    item = {
        "task_id": "t5",
        "type": "early_reminder",
        "reminder_hours": 3,
        "scheduled_time": now - timedelta(minutes=1),
    }
    NotiTron.scheduled_tasks[("t5", "early_reminder")] = item

    await NotiTron.check_scheduled_notifications()

    mock_send.assert_called_once_with(item)


@pytest.mark.asyncio
async def test_fires_multiple_due_items(mock_send):
    """Verifies that multiple past-due items all fire and are removed."""
    now = datetime.now(TZ)
    item_a = {
        "task_id": "ta",
        "type": "due_notification",
        "scheduled_time": now - timedelta(minutes=1),
    }
    item_b = {
        "task_id": "tb",
        "type": "due_notification",
        "scheduled_time": now - timedelta(minutes=3),
    }
    NotiTron.scheduled_tasks[("ta", "due_notification")] = item_a
    NotiTron.scheduled_tasks[("tb", "due_notification")] = item_b

    await NotiTron.check_scheduled_notifications()

    assert mock_send.call_count == 2
    assert ("ta", "due_notification") not in NotiTron.scheduled_tasks
    assert ("tb", "due_notification") not in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_empty_scheduled_tasks(mock_send):
    """Verifies that an empty scheduled_tasks dict causes no send calls."""
    await NotiTron.check_scheduled_notifications()
    mock_send.assert_not_called()


# ===========================================================================
# Group 2 — before_check_scheduled_notifications (alignment)
# ===========================================================================

@pytest.mark.asyncio
async def test_alignment_no_sleep_at_second_zero():
    """Verifies that no sleep is performed when the current second is exactly 0."""
    from datetime import datetime as real_datetime

    fake_now = MagicMock()
    fake_now.second = 0

    fake_dt = MagicMock()
    fake_dt.now = MagicMock(return_value=fake_now)
    fake_dt.fromisoformat = real_datetime.fromisoformat

    with patch("NotiTron.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("NotiTron.datetime", fake_dt):
        await NotiTron.before_check_scheduled_notifications()
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_alignment_sleeps_correct_duration():
    """Verifies that sleep is called with 60 - current_second when second=30."""
    from datetime import datetime as real_datetime

    fake_now = MagicMock()
    fake_now.second = 30

    fake_dt = MagicMock()
    fake_dt.now = MagicMock(return_value=fake_now)
    fake_dt.fromisoformat = real_datetime.fromisoformat

    with patch("NotiTron.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("NotiTron.datetime", fake_dt):
        await NotiTron.before_check_scheduled_notifications()
        mock_sleep.assert_called_once_with(30)


@pytest.mark.asyncio
async def test_alignment_sleeps_one_second_at_59():
    """Verifies that sleep is called with 1 when the current second is 59."""
    from datetime import datetime as real_datetime

    fake_now = MagicMock()
    fake_now.second = 59

    fake_dt = MagicMock()
    fake_dt.now = MagicMock(return_value=fake_now)
    fake_dt.fromisoformat = real_datetime.fromisoformat

    with patch("NotiTron.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("NotiTron.datetime", fake_dt):
        await NotiTron.before_check_scheduled_notifications()
        mock_sleep.assert_called_once_with(1)


# ===========================================================================
# Group 3 — check_tasks_hourly
# ===========================================================================

@pytest.mark.asyncio
async def test_hourly_schedules_early_reminder_from_db_field(mock_db):
    """
    Verifies that check_tasks_hourly picks up early_reminder_time from the DB
    and schedules it without recalculating.
    """
    now = datetime.now(TZ)
    stored_time = now + timedelta(minutes=30)
    task = {
        "_id": "task_er1",
        "task_id": "task_er1",
        "class_name": "CS101",
        "assignment_name": "HW",
        "due_date": (now + timedelta(hours=3)).isoformat(),
        "user_id": 111,
        "channel_id": 222,
        "completed": False,
        "early_reminder": 3,
        "early_reminder_sent": False,
        "early_reminder_time": stored_time.isoformat(),
    }

    # Order: (1) due within hour, (2) early reminders, (3) expired
    mock_db.find.side_effect = [iter([]), iter([task]), iter([])]

    await NotiTron.check_tasks_hourly()

    key = ("task_er1", "early_reminder")
    assert key in NotiTron.scheduled_tasks
    scheduled = NotiTron.scheduled_tasks[key]["scheduled_time"]
    # Should match stored time, not a recalculated one
    assert abs((scheduled - stored_time).total_seconds()) < 1


@pytest.mark.asyncio
async def test_hourly_skips_already_scheduled_reminder(mock_db):
    """
    Verifies that check_tasks_hourly does not add a duplicate entry if the
    early_reminder key is already present in scheduled_tasks.
    """
    now = datetime.now(TZ)
    stored_time = now + timedelta(minutes=30)
    task = {
        "_id": "task_er2",
        "task_id": "task_er2",
        "class_name": "CS101",
        "assignment_name": "HW",
        "due_date": (now + timedelta(hours=3)).isoformat(),
        "user_id": 111,
        "channel_id": 222,
        "completed": False,
        "early_reminder": 3,
        "early_reminder_sent": False,
        "early_reminder_time": stored_time.isoformat(),
    }

    existing_entry = {"scheduled_time": stored_time, "type": "early_reminder"}
    NotiTron.scheduled_tasks[("task_er2", "early_reminder")] = existing_entry

    mock_db.find.side_effect = [iter([]), iter([task]), iter([])]

    await NotiTron.check_tasks_hourly()

    # Still only one entry for this key
    assert len([k for k in NotiTron.scheduled_tasks if k[0] == "task_er2"]) == 1


@pytest.mark.asyncio
async def test_hourly_deletes_expired_tasks(mock_db):
    """
    Verifies that check_tasks_hourly calls delete_one for expired tasks
    returned by the expired-tasks query.
    """
    now = datetime.now(TZ)
    expired_task = {
        "_id": "task_exp1",
        "task_id": "task_exp1",
        "class_name": "CS202",
        "assignment_name": "Final",
        "due_date": (now - timedelta(hours=2)).isoformat(),
        "user_id": 333,
        "channel_id": 444,
        "completed": False,
        "early_reminder": None,
        "early_reminder_sent": False,
    }

    # Order: (1) due within hour, (2) early reminders, (3) expired
    mock_db.find.side_effect = [iter([]), iter([]), iter([expired_task])]

    await NotiTron.check_tasks_hourly()

    mock_db.delete_one.assert_called()


# ===========================================================================
# Group 4 — before_check_tasks_hourly (hourly alignment)
# ===========================================================================

@pytest.mark.asyncio
async def test_hourly_alignment_no_sleep_at_minute_zero_second_zero():
    """No sleep when already at the top of the hour (minute=0, second=0)."""
    from datetime import datetime as real_datetime

    fake_now = MagicMock()
    fake_now.minute = 0
    fake_now.second = 0

    fake_dt = MagicMock()
    fake_dt.now = MagicMock(return_value=fake_now)
    fake_dt.fromisoformat = real_datetime.fromisoformat

    with patch("NotiTron.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("NotiTron.datetime", fake_dt):
        await NotiTron.before_check_tasks_hourly()
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_hourly_alignment_sleeps_1800s_at_minute_30_second_0():
    """Sleeps 1800s (30 minutes) when at minute=30, second=0."""
    from datetime import datetime as real_datetime

    fake_now = MagicMock()
    fake_now.minute = 30
    fake_now.second = 0

    fake_dt = MagicMock()
    fake_dt.now = MagicMock(return_value=fake_now)
    fake_dt.fromisoformat = real_datetime.fromisoformat

    with patch("NotiTron.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("NotiTron.datetime", fake_dt):
        await NotiTron.before_check_tasks_hourly()
        mock_sleep.assert_called_once_with(1800)


@pytest.mark.asyncio
async def test_hourly_alignment_sleeps_60s_at_minute_59_second_0():
    """Sleeps 60s when at minute=59, second=0 — one minute until top of hour."""
    from datetime import datetime as real_datetime

    fake_now = MagicMock()
    fake_now.minute = 59
    fake_now.second = 0

    fake_dt = MagicMock()
    fake_dt.now = MagicMock(return_value=fake_now)
    fake_dt.fromisoformat = real_datetime.fromisoformat

    with patch("NotiTron.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("NotiTron.datetime", fake_dt):
        await NotiTron.before_check_tasks_hourly()
        mock_sleep.assert_called_once_with(60)


# ===========================================================================
# Group 5 — check_tasks_hourly due_notification scheduling
# ===========================================================================

@pytest.mark.asyncio
async def test_hourly_schedules_due_notification_for_task_due_within_next_hour(mock_db):
    """check_tasks_hourly schedules a due_notification for tasks due within the next hour."""
    now = datetime.now(TZ)
    task = {
        "_id": "task_dn1",
        "task_id": "task_dn1",
        "class_name": "CS101",
        "assignment_name": "Quiz",
        "due_date": (now + timedelta(minutes=30)).isoformat(),
        "user_id": 111,
        "channel_id": 222,
        "completed": False,
        "early_reminder": None,
        "early_reminder_sent": False,
    }

    # Order: (1) due within hour, (2) early reminders, (3) expired
    mock_db.find.side_effect = [iter([task]), iter([]), iter([])]

    await NotiTron.check_tasks_hourly()

    key = ("task_dn1", "due_notification")
    assert key in NotiTron.scheduled_tasks
    assert NotiTron.scheduled_tasks[key]["type"] == "due_notification"
    expected_time = datetime.fromisoformat(task["due_date"])
    actual_time = NotiTron.scheduled_tasks[key]["scheduled_time"]
    assert abs((actual_time - expected_time).total_seconds()) < 1


# ===========================================================================
# Group 6 — sub-minute timing and expired cleanup
# ===========================================================================

@pytest.mark.asyncio
async def test_same_minute_item_fires(mock_send):
    """
    An item scheduled 30s into the future (same minute as now) fires because
    check_scheduled_notifications truncates scheduled_time to the minute.
    """
    base_minute = datetime.now(TZ).replace(second=0, microsecond=0)
    item = {
        "task_id": "t_submin",
        "type": "due_notification",
        "scheduled_time": base_minute + timedelta(seconds=30),
    }
    NotiTron.scheduled_tasks[("t_submin", "due_notification")] = item

    await NotiTron.check_scheduled_notifications()

    mock_send.assert_called_once_with(item)
    assert ("t_submin", "due_notification") not in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_hourly_expired_task_clears_both_scheduled_task_keys(mock_db):
    """
    When check_tasks_hourly deletes an expired task, both its due_notification
    and early_reminder keys are also removed from scheduled_tasks.
    """
    now = datetime.now(TZ)
    expired_task = {
        "_id": "task_exp2",
        "task_id": "task_exp2",
        "class_name": "CS202",
        "assignment_name": "Expired HW",
        "due_date": (now - timedelta(hours=2)).isoformat(),
        "user_id": 333,
        "channel_id": 444,
        "completed": False,
        "early_reminder": 1,
        "early_reminder_sent": False,
    }

    NotiTron.scheduled_tasks[("task_exp2", "due_notification")] = {"type": "due_notification"}
    NotiTron.scheduled_tasks[("task_exp2", "early_reminder")] = {"type": "early_reminder"}

    mock_db.find.side_effect = [iter([]), iter([]), iter([expired_task])]

    await NotiTron.check_tasks_hourly()

    mock_db.delete_one.assert_called_once_with({"_id": expired_task["_id"]})
    assert ("task_exp2", "due_notification") not in NotiTron.scheduled_tasks
    assert ("task_exp2", "early_reminder") not in NotiTron.scheduled_tasks
