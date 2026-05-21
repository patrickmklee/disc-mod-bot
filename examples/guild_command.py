#!/usr/bin/env python
"""
Source: https://docs.discord.com/developers/interactions/application-commands#making-a-guild-command
Guild commands are available only within the guild specified on creation. Guild commands update instantly. We recommend you use guild commands for quick testing, and global commands when they’re ready for public use.
To make a guild command, make a similar HTTP POST call, but scope it to a specific guild_id
"""

import requests


url = "https://discord.com/api/v10/applications/<my_application_id>/guilds/<guild_id>/commands"

# This is an example USER command, with a type of 2
json = {
    "name": "High Five",
    "type": 2
}

# For authorization, you can use either your bot token
headers = {
    "Authorization": "Bot <my_bot_token>"
}

# or a client credentials token for your app with the applications.commands.update scope
headers = {
    "Authorization": "Bearer <my_credentials_token>"
}

r = requests.post(url, headers=headers, json=json)


