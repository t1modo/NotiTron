# NotiTron

## Overview

NotiTron is a Discord bot designed to help students and users keep track of upcoming assignments and due dates. With NotiTron, users can add tasks, receive notifications when assignments are nearing their due dates (within 24 hours), and have expired tasks automatically removed from the system. The bot is powered by MongoDB for storage, and it provides seamless notifications and reminders to help users stay on top of their academic responsibilities.

## Features

- **Add Task**: Users can easily add assignments by providing the class name, assignment name, and due date in either MM/DD/YY or MM/DD/YYYY format.
- **Task Notifications**: The bot sends reminders when an assignment is due within 24 hours or if it is due on the same day.
- **Automatic Task Cleanup**: Tasks that have passed their due dates are automatically deleted from the system every 24 hours to keep the task list up to date.
- **Customizable Timezone Support**: The bot is set to the Pacific Standard Time (PST) timezone, ensuring that deadlines are tracked correctly for users in that region.

## Technologies Used

- **discord.py**: A Python library that allows for the development of Discord bots, providing tools to interact with the Discord API.
- **pymongo**: A Python driver for MongoDB that allows the bot to interact with a MongoDB database to store and manage task data.
- **MongoDB**: A NoSQL database used to store tasks, assignments, and their due dates.
- **pytz**: A Python library for handling timezone-aware datetime objects.
