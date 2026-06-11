# Wingman
Wingman is a local-first personal automation runtime (assistant), which has a deterministic core scheduler and executes plugins, while channels like Telegram provide user interaction.  

While it was inspired by OpenClaw and Hermes, it is different.  

## Core idea:
1) From interaction channel (ad-hoc) or from the cron, the Core put tasks to a queue
2) Dispatcher reads the queue and invokes plugins with optional parameters
3) Plugins provide results as stdout&stderr to the Core, where Core updates tasks with their results
4) The Core sends the result to user through the selected channel (for tasks with cron) or through the channel of the task origin

```
channel -> queue -> plugin process -> stdout&stderr of a plugin -> channel
cron    -> queue -> plugin process -> stdout&stderr of a plugin -> channel
```

## Wingman includes:
- the Core: cron loop, adhoc task injector, tasks queue, tasks dispatcher (plugin runner)
- channels bridge: Telegram, more to come
- plugins: a weather plugin, more to come

## Cost to run in tokens:
- Wingman,  cost = f(actual invocations of *AI-enabled* plugins)
- OpenClaw, cost = f(time) ^1 + f(actual invocations)
- Hermes,   cost = f(actual invocations * (system prompt ^2 + ever-growing history as the AI-assistant context))

^1 OpenClaw tends to spend millions of tokens just checking whether it has some job to do or not, while Wingman does the polling through SELECT * FROM tasks_queued.  
^2 System Prompt for Hermes is about 14k tokens big. Every invocation uses the system prompt + evergrowing history context  

## Security model
1) Because AI lives only at the edges, system is harder to comprosize at whole.
2) Because the plugins, where AI could be invoked, actually has only stdout/stderr, it prevents most of the attacks through the prompt injection or a model bisheaving, especially between the Core and the Plugins/Channels
3) so far, the Core and the channels/telegram runs locally and listens only the localhost (127.0.0.1, from the config.toml and from the defaults inside the code), and thus they don't have authentication at the moment

## Routing logic
1) direct reply: if invoked_with matches a known channel id -> sendResult (with recipient)
2) fallback: unknown invoked_with (cron, HTTP endpoint) -> reads wingman_settings.default_channel -> sendResult (with nil instead of recipient)
3) if no default channel configured: sentinel "devnull" -> mark sent and drop, so the task isn't re-selected forever


## Installation notes
0. Setup secrets, preferrably
```
~/.wingman/channels/telegram
~/.wingman/weather
```
1. Start the TG channel
```
export WINGMAN_SECRETS_DIR=~/.wingman
```
2. 
After installing and getting first communication with the bot against the `wingman.db`
```
sqlite3 wingman.db
INSERT INTO wingman_settings (s_key, s_value) VALUES ('default_channel', 'telegram');
```
  
against the `telegram.db` in `channels/telegram` folder:
```
sqlite3 telegram.db
INSERT INTO known_ids (chat_id, role, is_default) VALUES (<CHANGENUMBER>, "owner", 1);
```
