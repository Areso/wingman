# Telegram Bot for Wingman

This is a Telegram bot client that integrates with the wingman system to provide plugin execution via Telegram interface.

## Features

- Uses systemd LoadCredential mechanism for reading bot token
- Reads plugins from `plugins/*` directory
- Registers only plugins with `ad_hoc: true`
- Provides menu interface with registered plugins
- Invokes plugins via HTTP endpoint at `127.0.0.1:8080/invoke_plugin`

## Setup

### 1. Systemd Credential Setup

Create a systemd credential file:
```
sudo mkdir -p /run/credentials/wingman.service
sudo echo "YOUR_BOT_TOKEN_HERE" > /run/credentials/wingman.service/bot_token
```

### 2. Run the Bot

```
cd /path/to/wingman
go run comms/telegram/main.go
```

## Usage

- Send `/start` or `/help` to see the plugin menu
- Click on plugin buttons to execute them
- Send `/plugins` to see a list of available plugins

## Plugin Structure

Plugins in the `plugins/` directory must have a `plugin.json` file with at least:
```json
{
  "id": "unique_plugin_id",
  "name": "Plugin Name",
  "adhoc": "true",
  "invocation_with": "command",
  "invocation_file": "script.sh"
}
```

## to run (working_dir: wingman/comms/telegram %)
```
go run main.go


curl -X POST localhost:8085/send_message_to_chat_id \
-d '{"chat_id":360650628,"message":"privet"}'

curl -X POST localhost:8085/invoke_plugin \
-d '{"id":"check_mysql_port_open_on_localhost","params":{}}'

```
360650628