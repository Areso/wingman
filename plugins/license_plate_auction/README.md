# License plate auction plugin

Checks the Cyprus Road Transport Department website for an available license
plate auction series.

The plugin is silent when no auction is available. When an auction is available,
it prints an alert with the source page URL. Network failures and unrecognized
pages are written to stderr and return a nonzero exit code.

Wingman runs the check every day at 07:30 in the host's local timezone. The
plugin is also available for ad-hoc invocation to all roles.

## Smoke test

```sh
python3 main.py
```

No output and an exit code of zero means that the site was checked successfully
and no auction is currently available.

## Tests

From the repository root:

```sh
python3 -m unittest discover -s plugins/license_plate_auction -p 'test*.py'
```
