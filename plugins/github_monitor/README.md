# GitHub monitor

Monitors the authenticated GitHub profile and all non-fork repositories owned by
that account. Public and private repositories are reported separately. Each run
stores a complete SQLite snapshot and prints changes from the previous successful
run. The first run compares the account with an empty baseline, so all current
objects are reported as additions.

The monitor records followers, stargazers, forks, issues, pull requests, comment
counts, and open Dependabot, code-scanning, and secret-scanning alerts. GitHub does
not expose a reliable repository-level generic malware count, so the plugin does
not report one.

GitHub now restricts individual stargazer lists. The plugin first requests the
list; if GitHub denies access, it requests `/stargazers/count` instead and stores
SQL `NULL` for the stargazer list and list difference. Count changes continue to
be tracked without requiring `Contents: write` permission.

For pull requests, `comments` means conversation comments reported by the issue
API; inline code-review comments are not included in that count.

## Setup

1. Create a GitHub token that can read every repository you want to monitor.
2. Grant read access to metadata, Dependabot alerts, code-scanning alerts, and
   secret-scanning alerts where those features are available.
3. Put only the token in `~/.wingman/plugins/github` and restrict the file's
   permissions, for example with `chmod 600 ~/.wingman/plugins/github`.
4. Adjust `config.toml` if the token or database should use another path.

Fine-grained tokens must be granted access to the private repositories being
monitored. A classic token needs sufficient `repo` and security-event access.
Unavailable security features are recorded as unavailable and do not fail a run.

The plugin is registered for daily execution at 08:00 and is also available for
ad hoc execution. Wingman's `owner` role is required because output can contain
private repository names and links.

## Manual run

```sh
python3 main.py
```

The local `github_monitor.db` is ignored by Git. Lists and list differences are
stored as JSON. Object rows are associated with immutable run and repository
snapshot IDs, allowing repository renames and issue or pull-request transitions
to be compared reliably.

## Logging

Runtime diagnostics are written to `github_monitor.log` next to the plugin. The
log rotates at 5 MiB and retains three older files. It does not go to stdout or
stderr, so successful no-change runs remain silent in Wingman.

The log includes:

- progress after every 25 HTTP requests
- progress after every 10 repositories
- the total HTTP query count and cumulative request time
- query counts and timing grouped by endpoint category
- the last observed GitHub rate-limit balance
- failed resource paths and stack traces

Follow a run with:

```sh
tail -f github_monitor.log
```

For an account with many repositories, the category summary makes the cost of
stargazers, forks, issues, pull requests, and each security service visible
separately.

Missing token permissions are also written to
`github_monitor_missing_permissions.log`. When GitHub explicitly identifies a
missing fine-grained permission, the plugin records it once and skips that check
for all remaining repositories in the same run. For example:

```text
Missing GitHub permission: capability=dependabot_alerts required=vulnerability_alerts=read ...
Missing GitHub permission: capability=secret_scanning_alerts required=secret_scanning_alerts=read ...
Missing GitHub permission: capability=stargazers_list required=contents=write ...
```

The cache lasts for one run only, so a later run tests the first repository again
and automatically notices when token permissions have been changed. Feature-level
errors such as `code scanning is not enabled` are not cached because availability
can differ between repositories.

## The permissions needed for fine-graining token
all repos
permissions:
- code quality
- code scanning alerts
- contents
- discussions
- issues
- metadata
- pull requests

also need:
Dependabot: vulnerability_alerts=read
Secret scanning: secret_scanning_alerts=read
