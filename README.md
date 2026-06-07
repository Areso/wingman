# Wingman
Wingman is a service to cover one's everyday needs.  
While it was inspired by OpenClaw, it is different.  
It has the core loop, which:
1) listens queue and dispatches tasks
2) checks every minute against "croned" plugins, and invokes them
3) collaborate with communications

## Installation notes
After installing and getting first communication with the bot, run against wingman.db
```
INSERT INTO wingman_settings (s_key, s_value) VALUES ('default_channel', 'telegram');
```
against telegram.db
```
INSERT INTO known_ids (chat_id, level, is_default) VALUES (<CHANGENUMBER>, "owner", 1);
```