# Telegram bot for Wingman

This is a communication channel for Wingman  
It exposes ad-hoc plugins for owner, registered users, and guests of Wingman installation  
It provides a menu of available plugins accoding to a user's known role

## Setup
0. make sure you already has ~/.wingman directory ("WINGMAN_SECRETS_DIR")
1. create directory ~/.wingman/channels
2. echo "YOUR_BOT_TOKEN_HERE" > ~/.wignman/channels/telegram

## Smoke test
```
export WINGMAN_SECRETS_DIR=~/.wingman
.telegram
```
or
```
export WINGMAN_SECRETS_DIR=~/.wingman
go run channels/telegram/main.go
```

## Setup continues
```
sqlite3 telegram.db
INSERT INTO known_ids (chat_id,role,is_default) VALUES (<YOUR CHAT ID WITH THE BOT>,'owner',1);

```
This is needed to have "owner" privileges and get access to all ad-hoc plug-ins.  
Please note, there should be only ONE user with 'owner' value and is_default 1 value.  
Other supported roles are: "guest" (but you don't need to add guests to the database) and "user" (these should be added with <CHAT_ID>,'user',0 values)   

Finally, check `config.toml` for host and port settings both for the channel/telegram and Wingman (Core)
## Usage

- Send `/start` or `/help` to see the plugin menu
- Click on plugin buttons to execute them
- Send `/plugins` to see a list of available plugins
