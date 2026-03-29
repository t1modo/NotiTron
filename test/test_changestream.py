"""
test_changestream.py — tests for handle_change (MongoDB ChangeStream handler).

Covers insert, delete, and update operation types, verifying that
scheduled_tasks is updated correctly for each.
"""

import pytest
from datetime import datetime, timedelta
import pytz
from unittest.mock import MagicMock
import NotiTron

TZ = pytz.timezone("America/Los_Angeles")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_change(task):
    return {"operationType": "insert", "fullDocument": task}


def _delete_change(task_id):
    return {"operationType": "delete", "documentKey": {"_id": task_id}}


def _update_change(task, updated_fields):
    return {
        "operationType": "update",
        "fullDocument": task,
        "updateDescription": {"updatedFields": updated_fields},
    }


def _make_task_doc(task_id="cs_task_1", hours_until_due=24, early_reminder=None,
                   early_reminder_time=None):
    now = datetime.now(TZ)
    doc = {
        "_id": task_id,
        "class_name": "CS101",
        "assignment_name": "HW1",
        "due_date": (now + timedelta(hours=hours_until_due)).isoformat(),
        "user_id": 111,
        "channel_id": 222,
        "completed": False,
        "early_reminder_sent": False,
    }
    if early_reminder is not None:
        doc["early_reminder"] = early_reminder
    if early_reminder_time is not None:
        doc["early_reminder_time"] = early_reminder_time
    return doc


# ===========================================================================
# Insert
# ===========================================================================

@pytest.mark.asyncio
async def test_handle_change_insert_schedules_due_notification():
    """Insert op adds a due_notification entry to scheduled_tasks."""
    task = _make_task_doc()
    await NotiTron.handle_change(_insert_change(task))

    key = ("cs_task_1", "due_notification")
    assert key in NotiTron.scheduled_tasks
    assert NotiTron.scheduled_tasks[key]["type"] == "due_notification"
    assert NotiTron.scheduled_tasks[key]["task"] is task


@pytest.mark.asyncio
async def test_handle_change_insert_skips_if_due_notification_already_scheduled():
    """Insert op does not overwrite an existing due_notification entry."""
    task = _make_task_doc()
    existing = {"type": "due_notification", "sentinel": True}
    NotiTron.scheduled_tasks[("cs_task_1", "due_notification")] = existing

    await NotiTron.handle_change(_insert_change(task))

    assert NotiTron.scheduled_tasks[("cs_task_1", "due_notification")] is existing


# ===========================================================================
# Delete
# ===========================================================================

@pytest.mark.asyncio
async def test_handle_change_delete_removes_due_notification():
    """Delete op removes the due_notification key from scheduled_tasks."""
    NotiTron.scheduled_tasks[("del_task_1", "due_notification")] = {"type": "due_notification"}

    await NotiTron.handle_change(_delete_change("del_task_1"))

    assert ("del_task_1", "due_notification") not in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_handle_change_delete_removes_early_reminder():
    """Delete op removes the early_reminder key from scheduled_tasks."""
    NotiTron.scheduled_tasks[("del_task_2", "early_reminder")] = {"type": "early_reminder"}

    await NotiTron.handle_change(_delete_change("del_task_2"))

    assert ("del_task_2", "early_reminder") not in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_handle_change_delete_removes_both_keys_when_both_exist():
    """Delete op removes both due_notification and early_reminder keys."""
    NotiTron.scheduled_tasks[("del_task_3", "due_notification")] = {"type": "due_notification"}
    NotiTron.scheduled_tasks[("del_task_3", "early_reminder")] = {"type": "early_reminder"}

    await NotiTron.handle_change(_delete_change("del_task_3"))

    assert ("del_task_3", "due_notification") not in NotiTron.scheduled_tasks
    assert ("del_task_3", "early_reminder") not in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_handle_change_delete_safe_when_no_keys_exist():
    """Delete op on a task with no scheduled entries does not raise."""
    await NotiTron.handle_change(_delete_change("nonexistent_task"))
    assert len(NotiTron.scheduled_tasks) == 0


# ===========================================================================
# Update
# ===========================================================================

@pytest.mark.asyncio
async def test_handle_change_update_early_reminder_schedules_entry():
    """Update op with early_reminder in updatedFields schedules an early_reminder entry."""
    now = datetime.now(TZ)
    future_reminder_time = now + timedelta(hours=2)
    task = _make_task_doc(task_id="upd_task_1", hours_until_due=5, early_reminder=3,
                          early_reminder_time=future_reminder_time.isoformat())

    await NotiTron.handle_change(_update_change(task, {"early_reminder": 3}))

    key = ("upd_task_1", "early_reminder")
    assert key in NotiTron.scheduled_tasks
    assert NotiTron.scheduled_tasks[key]["type"] == "early_reminder"
    assert NotiTron.scheduled_tasks[key]["reminder_hours"] == 3


@pytest.mark.asyncio
async def test_handle_change_update_uses_stored_early_reminder_time():
    """Update op uses early_reminder_time from fullDocument instead of recalculating."""
    now = datetime.now(TZ)
    specific_time = now + timedelta(hours=2, minutes=17, seconds=19)
    task = _make_task_doc(task_id="upd_task_2", hours_until_due=5, early_reminder=3,
                          early_reminder_time=specific_time.isoformat())

    await NotiTron.handle_change(_update_change(task, {"early_reminder": 3}))

    key = ("upd_task_2", "early_reminder")
    stored = NotiTron.scheduled_tasks[key]["scheduled_time"]
    assert abs((stored - specific_time).total_seconds()) < 1


@pytest.mark.asyncio
async def test_handle_change_update_skips_if_early_reminder_already_scheduled():
    """Update op does not overwrite an existing early_reminder entry."""
    now = datetime.now(TZ)
    task = _make_task_doc(task_id="upd_task_3", hours_until_due=5, early_reminder=3)
    existing = {"type": "early_reminder", "sentinel": True}
    NotiTron.scheduled_tasks[("upd_task_3", "early_reminder")] = existing

    await NotiTron.handle_change(_update_change(task, {"early_reminder": 3}))

    assert NotiTron.scheduled_tasks[("upd_task_3", "early_reminder")] is existing


@pytest.mark.asyncio
async def test_handle_change_update_ignored_when_field_is_not_early_reminder():
    """Update op that changes a field other than early_reminder leaves scheduled_tasks unchanged."""
    task = _make_task_doc(task_id="upd_task_4")

    await NotiTron.handle_change(_update_change(task, {"completed": True}))

    assert ("upd_task_4", "early_reminder") not in NotiTron.scheduled_tasks
    assert len(NotiTron.scheduled_tasks) == 0
