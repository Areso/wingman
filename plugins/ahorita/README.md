# Ahorita task plugin

This plugin lets Wingman list, retrieve, and create tasks through the Ahorita
JSON API. It uses only the Python 3.9+ standard library.

The API key must be the only content in:

```text
~/.wingman/plugins/ahorita
```

The default API URL and secret path are configured in `config.toml`.

## Commands

```bash
python3 ahorita.py task_list
python3 ahorita.py task_list_today
python3 ahorita.py task_list_tomorrow
python3 ahorita.py task_get 42
python3 ahorita.py task_create --title "Buy milk"
python3 ahorita.py task_create \
  --title "Submit report" \
  --description "Send the final version" \
  --important \
  --asap \
  --due-at "2026-08-26T14:30"
```

`task_list` returns all tasks grouped by their board status. All commands write
JSON to standard output. `task_list_today` and `task_list_tomorrow` return
undated ASAP tasks together with tasks due on the requested day. Errors are
written as JSON to standard error and exit with status 1.
