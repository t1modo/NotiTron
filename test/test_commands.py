"""
test_commands.py — tests for the add_task slash command.

Verifies input validation, DB insertion, and scheduled_tasks population
for the add_task interaction handler.
"""

import pytest
from datetime import datetime, timedelta
import pytz
import os
from unittest.mock import AsyncMock, MagicMock
import NotiTron

TZ = pytz.timezone("America/Los_Angeles")
GUILD_ID = int(os.environ["GUILD_ID"])


@pytest.mark.asyncio
async def test_wrong_guild_returns_early(mock_db, make_interaction):
    """
    Verifies that add_task returns early without touching the DB or
    sending a response when called from the wrong guild.
    """
    interaction = make_interaction(guild_id=999999999999999999)

    await NotiTron.add_task(
        interaction,
        class_name="CS101",
        assignment_name="HW1",
        due_date="12/31/99",
        due_time="11:59 PM",
    )

    mock_db.insert_one.assert_not_called()
    interaction.response.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_date_format_sends_error(mock_db, make_interaction):
    """
    Verifies that an invalid date string causes an ephemeral error
    message containing 'Invalid date' to be sent.
    """
    interaction = make_interaction()

    await NotiTron.add_task(
        interaction,
        class_name="CS101",
        assignment_name="HW1",
        due_date="13/32/25",
        due_time="11:59 PM",
    )

    interaction.response.send_message.assert_called_once()
    call_kwargs = interaction.response.send_message.call_args
    # Check ephemeral flag
    assert call_kwargs.kwargs.get("ephemeral") is True
    # Check error text
    sent_content = str(call_kwargs)
    assert "Invalid date" in sent_content or "invalid" in sent_content.lower()


@pytest.mark.asyncio
async def test_invalid_time_format_sends_error(mock_db, make_interaction):
    """
    Verifies that an invalid time string triggers an ephemeral error response.
    """
    interaction = make_interaction()

    await NotiTron.add_task(
        interaction,
        class_name="CS101",
        assignment_name="HW1",
        due_date="12/31/99",
        due_time="25:00 PM",
    )

    interaction.response.send_message.assert_called_once()
    call_kwargs = interaction.response.send_message.call_args
    assert call_kwargs.kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_past_due_date_rejected(mock_db, make_interaction):
    """
    Verifies that a due date in the past causes add_task to send a
    rejection message without inserting into the DB.
    """
    yesterday = datetime.now(TZ) - timedelta(days=1)
    due_date_str = yesterday.strftime("%m/%d/%y")
    interaction = make_interaction()

    await NotiTron.add_task(
        interaction,
        class_name="CS101",
        assignment_name="HW1",
        due_date=due_date_str,
        due_time="11:59 PM",
    )

    mock_db.insert_one.assert_not_called()
    interaction.response.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_valid_mmddyy_format_accepted(mock_db, make_interaction):
    """
    Verifies that a valid future date in MM/DD/YY format is accepted,
    inserted into the DB, and the due_notification key added to scheduled_tasks.
    """
    mock_db.insert_one.return_value = MagicMock(inserted_id="inserted_id_001")
    interaction = make_interaction()

    await NotiTron.add_task(
        interaction,
        class_name="CS101",
        assignment_name="HW1",
        due_date="12/31/27",
        due_time="11:59 PM",
    )

    mock_db.insert_one.assert_called_once()
    # At least one due_notification key should be in scheduled_tasks
    due_keys = [k for k in NotiTron.scheduled_tasks if k[1] == "due_notification"]
    assert len(due_keys) >= 1


@pytest.mark.asyncio
async def test_valid_mmddyyyy_format_accepted(mock_db, make_interaction):
    """
    Verifies that a valid future date in MM/DD/YYYY format is accepted
    and results in a DB insert and scheduled_tasks entry.
    """
    mock_db.insert_one.return_value = MagicMock(inserted_id="inserted_id_002")
    interaction = make_interaction()

    await NotiTron.add_task(
        interaction,
        class_name="CS101",
        assignment_name="HW1",
        due_date="12/31/2099",
        due_time="11:59 PM",
    )

    mock_db.insert_one.assert_called_once()
    due_keys = [k for k in NotiTron.scheduled_tasks if k[1] == "due_notification"]
    assert len(due_keys) >= 1


@pytest.mark.asyncio
async def test_task_stored_in_db(mock_db, make_interaction):
    """
    Verifies that the dict passed to insert_one contains the expected
    fields: class_name, assignment_name, completed=False, early_reminder_sent=False.
    """
    mock_db.insert_one.return_value = MagicMock(inserted_id="inserted_id_003")
    interaction = make_interaction()

    await NotiTron.add_task(
        interaction,
        class_name="CS101",
        assignment_name="Midterm",
        due_date="12/31/27",
        due_time="11:59 PM",
    )

    mock_db.insert_one.assert_called_once()
    inserted_doc = mock_db.insert_one.call_args[0][0]
    assert inserted_doc.get("class_name") == "CS101"
    assert inserted_doc.get("assignment_name") == "Midterm"
    assert inserted_doc.get("completed") is False
    assert inserted_doc.get("early_reminder_sent") is False


@pytest.mark.asyncio
async def test_due_notification_added_to_scheduled_tasks(mock_db, make_interaction):
    """
    Verifies that after a successful add_task call, a key of the form
    (str(task_id), 'due_notification') exists in NotiTron.scheduled_tasks.
    """
    mock_db.insert_one.return_value = MagicMock(inserted_id="inserted_id_004")
    interaction = make_interaction()

    await NotiTron.add_task(
        interaction,
        class_name="CS101",
        assignment_name="Final",
        due_date="12/31/27",
        due_time="11:59 PM",
    )

    due_keys = [k for k in NotiTron.scheduled_tasks if k[1] == "due_notification"]
    assert len(due_keys) == 1
    assert due_keys[0] == (str("inserted_id_004"), "due_notification")
