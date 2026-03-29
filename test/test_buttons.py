"""
test_buttons.py — tests for CompleteButton and ReminderButton callbacks.

Covers:
  - CompleteButton: DB deletion, scheduled_tasks cleanup, confirmation message,
    no side-effects on other tasks
  - PersistentCompleteButton: authorization check (wrong user / correct user)
  - ReminderButton: DB update, scheduled_tasks scheduling, timing accuracy,
    confirmation message
"""

import pytest
from datetime import datetime, timedelta
import pytz
from unittest.mock import MagicMock, AsyncMock
import NotiTron

TZ = pytz.timezone("America/Los_Angeles")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_complete_interaction(make_interaction):
    """Extends make_interaction() with interaction.message containing a fake embed."""
    interaction = make_interaction()
    fake_embed = MagicMock()
    fake_message = MagicMock()
    fake_message.embeds = [fake_embed]
    fake_message.edit = AsyncMock()
    interaction.message = fake_message
    return interaction


def _make_reminder_button(task, hours=3):
    """Creates a ReminderButton with view injected so callback won't raise."""
    button = NotiTron.ReminderButton(task, hours=hours)
    button.view = MagicMock()
    button.view.handle_reminder_confirmation = AsyncMock()
    return button


# ===========================================================================
# CompleteButton — DB deletion
# ===========================================================================

@pytest.mark.asyncio
async def test_complete_button_calls_delete_one_with_task_id(mock_db, make_task, make_interaction):
    """Clicking Complete calls delete_one with the task's _id."""
    task = make_task()
    button = NotiTron.CompleteButton(task)
    interaction = _make_complete_interaction(make_interaction)

    await button.callback(interaction)

    mock_db.delete_one.assert_called_once_with({"_id": task["_id"]})


# ===========================================================================
# CompleteButton — scheduled_tasks cleanup
# ===========================================================================

@pytest.mark.asyncio
async def test_complete_button_removes_due_notification_key(mock_db, make_task, make_interaction):
    """Clicking Complete removes the due_notification key from scheduled_tasks."""
    task = make_task()
    NotiTron.scheduled_tasks[(str(task["_id"]), "due_notification")] = {"type": "due_notification"}
    button = NotiTron.CompleteButton(task)
    interaction = _make_complete_interaction(make_interaction)

    await button.callback(interaction)

    assert (str(task["_id"]), "due_notification") not in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_complete_button_removes_early_reminder_key(mock_db, make_task, make_interaction):
    """Clicking Complete removes the early_reminder key from scheduled_tasks."""
    task = make_task()
    NotiTron.scheduled_tasks[(str(task["_id"]), "early_reminder")] = {"type": "early_reminder"}
    button = NotiTron.CompleteButton(task)
    interaction = _make_complete_interaction(make_interaction)

    await button.callback(interaction)

    assert (str(task["_id"]), "early_reminder") not in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_complete_button_removes_both_keys_when_both_exist(mock_db, make_task, make_interaction):
    """Clicking Complete removes both due_notification and early_reminder keys."""
    task = make_task()
    task_id = str(task["_id"])
    NotiTron.scheduled_tasks[(task_id, "due_notification")] = {"type": "due_notification"}
    NotiTron.scheduled_tasks[(task_id, "early_reminder")] = {"type": "early_reminder"}
    button = NotiTron.CompleteButton(task)
    interaction = _make_complete_interaction(make_interaction)

    await button.callback(interaction)

    assert (task_id, "due_notification") not in NotiTron.scheduled_tasks
    assert (task_id, "early_reminder") not in NotiTron.scheduled_tasks


@pytest.mark.asyncio
async def test_complete_button_does_not_affect_other_task_keys(mock_db, make_task, make_interaction):
    """Completing task_001 must not touch scheduled entries for any other task."""
    task_a = make_task(task_id="task_a")
    task_b = make_task(task_id="task_b")
    NotiTron.scheduled_tasks[("task_a", "due_notification")] = {"type": "due_notification"}
    NotiTron.scheduled_tasks[("task_b", "due_notification")] = {"type": "due_notification"}
    button = NotiTron.CompleteButton(task_a)
    interaction = _make_complete_interaction(make_interaction)

    await button.callback(interaction)

    assert ("task_a", "due_notification") not in NotiTron.scheduled_tasks
    assert ("task_b", "due_notification") in NotiTron.scheduled_tasks


# ===========================================================================
# CompleteButton — confirmation message
# ===========================================================================

@pytest.mark.asyncio
async def test_complete_button_sends_ephemeral_confirmation(mock_db, make_task, make_interaction):
    """Clicking Complete sends an ephemeral confirmation message."""
    task = make_task()
    button = NotiTron.CompleteButton(task)
    interaction = _make_complete_interaction(make_interaction)

    await button.callback(interaction)

    interaction.response.send_message.assert_called_once()
    call_kwargs = interaction.response.send_message.call_args
    assert call_kwargs.kwargs.get("ephemeral") is True
    message_text = call_kwargs.args[0].lower()
    assert "complete" in message_text


# ===========================================================================
# PersistentCompleteButton — authorization
# ===========================================================================

@pytest.mark.asyncio
async def test_persistent_complete_button_wrong_user_returns_false_and_sends_error(
    make_task, make_interaction
):
    """A user who did not create the task is rejected with an ephemeral error."""
    task = make_task()  # user_id=123456789
    view = NotiTron.PersistentCompleteButton(task)
    interaction = make_interaction(user_id=999999999)  # different user

    result = await view.interaction_check(interaction)

    assert result is False
    interaction.response.send_message.assert_called_once()
    call_kwargs = interaction.response.send_message.call_args
    assert call_kwargs.kwargs.get("ephemeral") is True
    assert "not authorized" in call_kwargs.args[0].lower()


@pytest.mark.asyncio
async def test_persistent_complete_button_correct_user_returns_true_no_message(
    make_task, make_interaction
):
    """The task owner passes the authorization check without any message sent."""
    task = make_task()  # user_id=123456789
    view = NotiTron.PersistentCompleteButton(task)
    interaction = make_interaction(user_id=123456789)  # matching owner

    result = await view.interaction_check(interaction)

    assert result is True
    interaction.response.send_message.assert_not_called()


# ===========================================================================
# ReminderButton — DB update
# ===========================================================================

@pytest.mark.asyncio
async def test_reminder_button_calls_update_one_with_correct_fields(mock_db, make_task, make_interaction):
    """Clicking a reminder button calls update_one with early_reminder and early_reminder_time."""
    task = make_task(hours_until_due=24)
    button = _make_reminder_button(task, hours=3)
    interaction = make_interaction()

    await button.callback(interaction)

    mock_db.update_one.assert_called_once()
    filter_arg, update_arg = mock_db.update_one.call_args.args
    assert filter_arg == {"_id": task["_id"]}
    assert update_arg["$set"]["early_reminder"] == 3
    assert "early_reminder_time" in update_arg["$set"]


# ===========================================================================
# ReminderButton — scheduled_tasks scheduling
# ===========================================================================

@pytest.mark.asyncio
async def test_reminder_button_adds_early_reminder_key_to_scheduled_tasks(mock_db, make_task, make_interaction):
    """Clicking a reminder button adds an early_reminder entry to scheduled_tasks."""
    task = make_task(hours_until_due=24)
    button = _make_reminder_button(task, hours=3)
    interaction = make_interaction()

    await button.callback(interaction)

    key = (str(task["_id"]), "early_reminder")
    assert key in NotiTron.scheduled_tasks
    assert NotiTron.scheduled_tasks[key]["type"] == "early_reminder"
    assert NotiTron.scheduled_tasks[key]["reminder_hours"] == 3


@pytest.mark.asyncio
async def test_reminder_button_scheduled_time_equals_due_date_minus_hours(mock_db, make_task, make_interaction):
    """scheduled_time stored in scheduled_tasks equals due_date minus the reminder hours."""
    task = make_task(hours_until_due=24)
    button = _make_reminder_button(task, hours=3)
    interaction = make_interaction()

    due_datetime = datetime.fromisoformat(task["due_date"])
    expected_time = due_datetime - timedelta(hours=3)

    await button.callback(interaction)

    key = (str(task["_id"]), "early_reminder")
    actual_time = NotiTron.scheduled_tasks[key]["scheduled_time"]
    assert abs((actual_time - expected_time).total_seconds()) < 1


# ===========================================================================
# ReminderButton — confirmation message
# ===========================================================================

@pytest.mark.asyncio
async def test_reminder_button_sends_ephemeral_confirmation(mock_db, make_task, make_interaction):
    """Clicking a reminder button sends an ephemeral confirmation referencing the hours."""
    task = make_task(hours_until_due=24)
    button = _make_reminder_button(task, hours=3)
    interaction = make_interaction()

    await button.callback(interaction)

    interaction.response.send_message.assert_called_once()
    call_kwargs = interaction.response.send_message.call_args
    assert call_kwargs.kwargs.get("ephemeral") is True
    message_text = call_kwargs.args[0]
    assert "3" in message_text
    assert "hour" in message_text.lower()
