# Unix time converter

Converts Unix timestamps (seconds since `1970-01-01T00:00:00Z`) to UTC and UTC
date-times back to Unix timestamps. The plugin uses only Python's standard
library and does not use the host's local timezone.

## Usage

```text
to-utc 0
to-utc 1710000000.5
to-unix 1970-01-01T00:00:00Z
to-unix 2024-03-09 16:00:00 UTC
```

UTC date-times must be ISO 8601 values ending in `Z`, `UTC`, or `+00:00`.
Fractional Unix timestamps are rounded to Python's microsecond precision.

The Wingman core passes the complete request as one argument. For direct use,
the script also accepts a request split across arguments:

```sh
python3 main.py to-utc 1710000000
python3 main.py to-unix 2024-03-09T16:00:00Z
```
