# Wingman
Wingman is a local-first personal automation runtime (assistant), which has a deterministic core scheduler and executes plugins, while channels like Telegram provide user interaction.  

While it was inspired by OpenClaw and Hermes, it is different.  

## Core idea:
1) From interaction channel (ad-hoc) or from the cron, the Core puts tasks to a queue
2) Dispatcher reads the queue and invokes plugins with optional parameters
3) Plugins provide results as stdout&stderr to the Core, where Core updates tasks with their results
4) The Core sends the result to user through the selected channel (for tasks with cron) or through the channel of the task origin

Lifecycle:  
```
channel ─┐
         ├─> task creator(Core) -> queue of tasks (SQLite) ─> 
cron    ─┘

-> task invoker(Core) ->plugin subprocess ─> stdout/stderr ->
-> enriches task with result (SQLite) -> channel
```

## Wingman includes:
- the Core: cron loop, adhoc task injector, tasks queue, tasks dispatcher (plugin runner)
- channels bridge: Telegram, more to come
- plugins: a weather plugin, more to come

## Cost to run in tokens:
- Wingman,  cost = f(actual invocations of *AI-enabled* plugins)
- OpenClaw, cost = f(time) [1] + f(actual invocations)
- Hermes,   cost = f(actual invocations * (system prompt [2] + ever-growing history as the AI-assistant context))

[1] OpenClaw tends to spend millions of tokens just checking whether it has some job to do or not, while Wingman does the polling through SELECT * FROM tasks_queued.  
[2] System Prompt for Hermes is about 14k tokens big. Every invocation uses the system prompt + ever-growing history context  

## Security model
1) Because AI lives only at the edges, system is harder to compromise at whole.
2) Because the plugins, where AI could be invoked, actually have only stdout/stderr, it prevents most of the attacks through the prompt injection or a model misbehaving, especially between the Core and the Plugins/Channels
3) so far, the Core and the channels/telegram run locally and listen only the localhost (127.0.0.1, from the config.toml and from the defaults inside the code), and thus they don't have authentication at the moment

## Routing logic
1) direct reply: if invoked_with matches a known channel id -> sendResult (with recipient)
2) fallback: unknown invoked_with (cron, HTTP endpoint) -> reads wingman_settings.default_channel -> sendResult (with nil instead of recipient)
3) if no default channel configured: sentinel "devnull" -> mark sent and drop, so the task isn't re-selected forever


## Installation notes
0. Setup secrets, preferably
```
~/.wingman/channels/telegram
~/.wingman/weather
```
1. Start the TG channel
```
export WINGMAN_SECRETS_DIR=~/.wingman
```
2. 
After installing and establishing the first communication with the bot, run the following against `wingman.db`
```
sqlite3 wingman.db
INSERT INTO wingman_settings (s_key, s_value) VALUES ('default_channel', 'telegram');
```
  
against the `telegram.db` in `channels/telegram` folder:
```
sqlite3 telegram.db
INSERT INTO known_ids (chat_id, role, is_default) VALUES (<CHANGENUMBER>, "owner", 1);
```

## Known tradeoffs
1. If a task was invoked but failed to run, it will never be invoked again. This no-retry logic is tied to `SELECT * FROM tasks_queued WHERE invoked_at IS NULL LIMIT 1` to prevent endless loops.
2. The very first cron check-up happens after waiting for the first loop iteration, so it could omit a task planned for the exact minute the application started. This is unlikely to happen in real-life. Furthermore, since the task is scheduled, it will trigger next time. To keep things simple, I am temporarily okay with that.
3. Invocation of plugins should not pass to the plugins any secrets. They are not meant to be send as part of the invocation. The Wingman Core should not handle secrets for the plugins, nor should user pass secrets as part of parameters / user input
4. The plugins output should not put secrets to STDOUT or STDERR, since that would be recorded by Wingman Core and then sent as a result to the user utilizing channels.
5. Wingman Settings from wingman.db table wingman_settings are read only once on startup: properties send_empty_results, default_channel
6. While Wingman could run on a laptop, it should be noted, it will not schedule cron tasks during the laptop's sleep. So, it better be runned on always-on box (physical, like a Mac Mini or virtual, like a VPS). There is no catch-up / no follow-up & log logic due to simplicity of the Core scheduler.
