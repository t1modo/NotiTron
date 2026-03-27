# NotiTron

## Overview

NotiTron is a Discord bot designed to help students manage assignments and due dates. Users can add tasks with a due date and time, receive scheduled reminders, set an early reminder at a custom interval before the deadline, and mark tasks as complete — all through Discord slash commands and interactive buttons.

## Features

- **Add Task**: Add assignments with class name, assignment name, due date (MM/DD/YY or MM/DD/YYYY), and due time via `/add_task`.
- **Due Notifications**: Automatically pings the user when their assignment is due.
- **Early Reminders**: After adding a task, choose an early reminder 1, 3, 6, or 12 hours before the deadline.
- **Mark as Complete**: Use the "Mark as Complete" button on any task message to delete the task and close out the reminder.
- **Persistent Buttons**: Button views are restored on bot restart so in-progress tasks remain interactive.
- **Automatic Task Cleanup**: Expired (past-due) tasks are automatically removed from the database during hourly checks.
- **Timezone Support**: All times use America/Los_Angeles (Pacific Time).
- **Daily Restart**: The bot performs a daily restart at midnight PT for reliability.

## Technologies Used

- **discord.py**: Discord API interaction and bot development.
- **pymongo**: MongoDB integration for task storage.
- **MongoDB**: NoSQL database for persisting assignments and reminders.
- **pytz**: Timezone-aware datetime handling.
- **python-dotenv**: Environment variable management.