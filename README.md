# NotiTron

## Overview

NotiTron is a Discord bot designed to assist students and users in managing their assignments and due dates. It allows users to add tasks, receive timely reminders for upcoming deadlines, and ensures expired tasks are automatically cleaned from the system. The bot provides an efficient and reliable way to stay organized and meet academic or personal deadlines.

## Features

- **Add Task**: Add assignments with class name, assignment name, and due date in multiple formats.
- **Task Notifications**: Receive reminders when assignments are due within 24 hours or on the same day.
- **Automatic Task Cleanup**: Automatically removes expired tasks to keep the task list up to date.
- **Custom Time Zone Support**: Supports Pacific Standard Time (PST) for accurate deadline tracking.

## Technologies Used

- **discord.py**: For seamless interaction with the Discord API and bot development.
- **pymongo**: To integrate MongoDB for task storage and management.
- **MongoDB**: A NoSQL database for storing assignments and due dates.
- **pytz**: For timezone-aware datetime management in Python.
