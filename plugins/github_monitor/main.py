#!/usr/bin/env python3

import argparse
import configparser
import json
import logging
import logging.handlers
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.toml")
SECURITY_TYPES = {
    "dependabot": "Dependabot",
    "code_scanning": "Code scanning",
    "secret_scanning": "Secret scanning",
}


class MonitorError(Exception):
    pass


class GitHubError(MonitorError):
    def __init__(self, message, status=None, headers=None, resource=None):
        super().__init__(message)
        self.status = status
        self.headers = headers or {}
        self.resource = resource


class GitHubClient:
    def __init__(
        self,
        base_url,
        token,
        timeout=30,
        logger=None,
        missing_permissions_logger=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.logger = logger
        self.missing_permissions_logger = missing_permissions_logger
        self.request_count = 0
        self.request_seconds = 0.0
        self.request_stats = {}
        self.rate_limit_remaining = None
        self.missing_permissions = {}
        self.skipped_permission_checks = {}

    @staticmethod
    def required_permissions(error):
        if error.status != 403 or "resource not accessible" not in str(error).casefold():
            return None
        return next(
            (
                value
                for key, value in error.headers.items()
                if key.casefold() == "x-accepted-github-permissions"
            ),
            None,
        )

    def remember_missing_permission(self, capability, error):
        required = self.required_permissions(error)
        if not required:
            return False
        if capability not in self.missing_permissions:
            self.missing_permissions[capability] = {
                "required": required,
                "resource": error.resource,
            }
            if self.missing_permissions_logger:
                self.missing_permissions_logger.warning(
                    "Missing GitHub permission: capability=%s required=%s resource=%s; "
                    "remaining repositories will skip this check",
                    capability,
                    required,
                    error.resource or "unknown",
                )
            if self.logger:
                self.logger.warning(
                    "Missing permission cached: capability=%s required=%s",
                    capability,
                    required,
                )
        return True

    def should_skip_permission_check(self, capability):
        if capability not in self.missing_permissions:
            return False
        self.skipped_permission_checks[capability] = (
            self.skipped_permission_checks.get(capability, 0) + 1
        )
        return True

    @staticmethod
    def request_category(path):
        path = urllib.parse.urlsplit(path).path.rstrip("/")
        categories = (
            ("/stargazers/count", "stargazers_count"),
            ("/stargazers", "stargazers_list"),
            ("/dependabot/alerts", "dependabot_alerts"),
            ("/code-scanning/alerts", "code_scanning_alerts"),
            ("/secret-scanning/alerts", "secret_scanning_alerts"),
            ("/followers", "followers"),
            ("/forks", "forks"),
            ("/issues", "issues"),
            ("/pulls", "pull_requests"),
        )
        for suffix, category in categories:
            if path.endswith(suffix):
                return category
        if path == "/user/repos":
            return "repositories"
        if path == "/user":
            return "profile"
        return "other"

    def record_request(self, resource, status, duration, headers=None):
        self.request_seconds += duration
        category = self.request_category(resource)
        count, seconds = self.request_stats.get(category, (0, 0.0))
        self.request_stats[category] = (count + 1, seconds + duration)
        normalized_headers = {
            key.casefold(): value for key, value in (headers or {}).items()
        }
        if "x-ratelimit-remaining" in normalized_headers:
            self.rate_limit_remaining = normalized_headers["x-ratelimit-remaining"]
        if self.logger and (self.request_count % 25 == 0 or status != 200):
            self.logger.info(
                "HTTP progress: queries=%d request_time=%.2fs last_category=%s "
                "last_status=%s last_duration=%.2fs resource=%s",
                self.request_count,
                self.request_seconds,
                category,
                status,
                duration,
                resource,
            )

    def log_summary(self, wall_seconds):
        if not self.logger:
            return
        self.logger.info(
            "HTTP summary: queries=%d request_time=%.2fs wall_time=%.2fs "
            "average=%.3fs rate_limit_remaining=%s",
            self.request_count,
            self.request_seconds,
            wall_seconds,
            self.request_seconds / self.request_count if self.request_count else 0,
            self.rate_limit_remaining if self.rate_limit_remaining is not None else "unknown",
        )
        for category, (count, seconds) in sorted(self.request_stats.items()):
            self.logger.info(
                "HTTP category: name=%s queries=%d request_time=%.2fs average=%.3fs",
                category,
                count,
                seconds,
                seconds / count,
            )
        for capability, details in sorted(self.missing_permissions.items()):
            self.logger.info(
                "Permission skip summary: capability=%s required=%s skipped_checks=%d",
                capability,
                details["required"],
                self.skipped_permission_checks.get(capability, 0),
            )

    def request(self, path, params=None, return_headers=False):
        if params:
            separator = "&" if "?" in path else "?"
            path += separator + urllib.parse.urlencode(params)
        url = path if path.startswith(("http://", "https://")) else self.base_url + path
        parsed_url = urllib.parse.urlsplit(url)
        resource = parsed_url.path
        if parsed_url.query:
            resource += "?" + parsed_url.query
        self.request_count += 1
        started_at = time.monotonic()
        response_status = None
        response_headers = {}
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "Wingman GitHub monitor/1.0",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                response_headers = dict(response.headers.items())
                response_status = response.status
        except urllib.error.HTTPError as error:
            response_status = error.code
            response_headers = dict(error.headers.items()) if error.headers else {}
            body = error.read().decode("utf-8", errors="replace")
            try:
                error_data = json.loads(body)
                message = error_data.get("message") if isinstance(error_data, dict) else None
            except json.JSONDecodeError:
                message = None
            raise GitHubError(
                f"GitHub API resource {resource} returned HTTP {error.code}: "
                f"{message or error.reason}",
                status=error.code,
                headers=response_headers,
                resource=resource,
            ) from error
        except urllib.error.URLError as error:
            response_status = "network_error"
            raise GitHubError(f"Could not reach GitHub API: {error.reason}") from error
        finally:
            self.record_request(
                resource,
                response_status,
                time.monotonic() - started_at,
                response_headers,
            )

        if not body:
            data = None
        else:
            try:
                data = json.loads(body)
            except json.JSONDecodeError as error:
                raise GitHubError("GitHub API returned invalid JSON") from error
        return (data, response_headers) if return_headers else data

    @staticmethod
    def next_link(headers):
        link_header = next(
            (value for key, value in headers.items() if key.casefold() == "link"), None
        )
        if not link_header:
            return None
        for link in link_header.split(","):
            parts = [part.strip() for part in link.split(";")]
            if len(parts) > 1 and 'rel="next"' in parts[1:] and parts[0].startswith("<"):
                return parts[0][1:-1]
        return None

    def paginate(self, path, params=None):
        items = []
        query = dict(params or {})
        query["per_page"] = 100
        next_url = path
        while True:
            batch, headers = self.request(next_url, query, return_headers=True)
            if not isinstance(batch, list):
                raise GitHubError(f"GitHub API did not return a list for {path}")
            items.extend(batch)
            next_url = self.next_link(headers)
            if not next_url:
                return items
            query = None


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_settings(config_path=DEFAULT_CONFIG_PATH):
    path = Path(config_path)
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open("r", encoding="utf-8") as config_file:
            parser.read_file(config_file)
        config = parser["github_monitor"]
    except FileNotFoundError as error:
        raise MonitorError(f"Config file not found: {path}") from error
    except (KeyError, configparser.Error) as error:
        raise MonitorError(f"Invalid config file: {path}") from error

    base_url = config.get("base_url", "").strip().strip('"\'')
    secret_value = config.get("secret_path", "").strip().strip('"\'')
    database_value = config.get("database_path", "github_monitor.db").strip().strip('"\'')
    log_value = config.get("log_path", "github_monitor.log").strip().strip('"\'')
    missing_permissions_log_value = config.get(
        "missing_permissions_log_path", "github_monitor_missing_permissions.log"
    ).strip().strip('"\'')
    if not base_url:
        raise MonitorError("base_url is missing from config.toml")
    if not secret_value:
        raise MonitorError("secret_path is missing from config.toml")
    if not database_value:
        raise MonitorError("database_path cannot be empty")
    if not log_value:
        raise MonitorError("log_path cannot be empty")
    if not missing_permissions_log_value:
        raise MonitorError("missing_permissions_log_path cannot be empty")

    secret_path = Path(secret_value).expanduser()
    try:
        token = secret_path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, IsADirectoryError) as error:
        raise MonitorError(f"GitHub token file not found: {secret_path}") from error
    if not token:
        raise MonitorError(f"GitHub token file is empty: {secret_path}")

    database_path = Path(database_value).expanduser()
    if not database_path.is_absolute():
        database_path = path.parent / database_path
    log_path = Path(log_value).expanduser()
    if not log_path.is_absolute():
        log_path = path.parent / log_path
    missing_permissions_log_path = Path(missing_permissions_log_value).expanduser()
    if not missing_permissions_log_path.is_absolute():
        missing_permissions_log_path = path.parent / missing_permissions_log_path
    try:
        timeout = config.getint("timeout_seconds", fallback=30)
    except ValueError as error:
        raise MonitorError("timeout_seconds must be an integer") from error
    if timeout <= 0:
        raise MonitorError("timeout_seconds must be greater than zero")
    return (
        base_url,
        token,
        database_path,
        log_path,
        missing_permissions_log_path,
        timeout,
    )


def configure_file_logger(name, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def configure_logger(log_path):
    return configure_file_logger("github_monitor", log_path)


def configure_missing_permissions_logger(log_path):
    return configure_file_logger("github_monitor_missing_permissions", log_path)


def repo_path(full_name):
    try:
        owner, name = full_name.split("/", 1)
    except ValueError as error:
        raise GitHubError(f"Invalid repository name returned by GitHub: {full_name}") from error
    return "/repos/" + urllib.parse.quote(owner, safe="") + "/" + urllib.parse.quote(name, safe="")


def normalize_security_alert(kind, alert):
    details = {}
    if kind == "dependabot":
        details = alert.get("security_advisory") or {}
        identifier = details.get("ghsa_id")
        severity = details.get("severity")
    elif kind == "code_scanning":
        details = alert.get("rule") or {}
        identifier = details.get("id")
        severity = details.get("security_severity_level") or details.get("severity")
    else:
        identifier = alert.get("secret_type_display_name") or alert.get("secret_type")
        severity = None
    return {
        "type": kind,
        "number": int(alert["number"]),
        "link": alert.get("html_url"),
        "status": alert.get("state") or "open",
        "severity": severity,
        "identifier": identifier,
    }


def fetch_security(client, base_path, kind):
    endpoints = {
        "dependabot": "/dependabot/alerts",
        "code_scanning": "/code-scanning/alerts",
        "secret_scanning": "/secret-scanning/alerts",
    }
    capability = kind + "_alerts"
    if client.should_skip_permission_check(capability):
        return {"available": False, "alerts": []}
    try:
        alerts = client.paginate(base_path + endpoints[kind], {"state": "open"})
    except GitHubError as error:
        if client.remember_missing_permission(capability, error):
            return {"available": False, "alerts": []}
        if error.status in {403, 404} and not is_rate_limited(error):
            return {"available": False, "alerts": []}
        raise
    return {
        "available": True,
        "alerts": [normalize_security_alert(kind, alert) for alert in alerts],
    }


def is_rate_limited(error):
    headers = {key.casefold(): value for key, value in error.headers.items()}
    return (
        headers.get("x-ratelimit-remaining") == "0"
        or "retry-after" in headers
        or "rate limit" in str(error).casefold()
    )


def fetch_stargazers(client, base_path):
    capability = "stargazers_list"
    if not client.should_skip_permission_check(capability):
        try:
            stargazers = sorted(
                item["login"]
                for item in client.paginate(base_path + "/stargazers")
                if item.get("login")
            )
            return len(stargazers), stargazers
        except GitHubError as error:
            client.remember_missing_permission(capability, error)
            if error.status not in {403, 404} or is_rate_limited(error):
                raise

    data = client.request(base_path + "/stargazers/count")
    if not isinstance(data, dict) or not isinstance(data.get("count"), int):
        raise GitHubError("GitHub API did not return a valid stargazer count")
    return data["count"], None


def collect_snapshot(client):
    user = client.request("/user")
    if not isinstance(user, dict) or not user.get("login") or user.get("id") is None:
        raise GitHubError("GitHub API did not return the authenticated profile")
    login = user["login"]
    followers = sorted(
        item["login"]
        for item in client.paginate(f"/users/{urllib.parse.quote(login, safe='')}/followers")
        if item.get("login")
    )
    repositories = client.paginate(
        "/user/repos",
        {
            "affiliation": "owner",
            "visibility": "all",
            "sort": "full_name",
            "direction": "asc",
        },
    )
    repositories = [repository for repository in repositories if not repository.get("fork")]
    if client.logger:
        client.logger.info(
            "Collection started: profile=%s followers=%d owned_non_fork_repositories=%d",
            login,
            len(followers),
            len(repositories),
        )

    repos = []
    for index, repository in enumerate(repositories, start=1):
        full_name = repository.get("full_name")
        github_id = repository.get("id")
        if not full_name or github_id is None:
            raise GitHubError("GitHub API returned a repository without an ID or full name")
        base_path = repo_path(full_name)
        stars_count, stargazers = fetch_stargazers(client, base_path)
        forks = sorted(
            item["full_name"]
            for item in client.paginate(base_path + "/forks", {"sort": "newest"})
            if item.get("full_name")
        )
        all_issue_items = client.paginate(
            base_path + "/issues", {"state": "all", "sort": "created", "direction": "asc"}
        )
        issue_items = [item for item in all_issue_items if "pull_request" not in item]
        pr_comment_counts = {
            item.get("number"): int(item.get("comments") or 0)
            for item in all_issue_items
            if "pull_request" in item
        }
        pull_items = client.paginate(
            base_path + "/pulls", {"state": "all", "sort": "created", "direction": "asc"}
        )

        issues = []
        for issue in issue_items:
            author = issue.get("user") or {}
            issues.append(
                {
                    "github_id": int(issue["id"]),
                    "number": int(issue["number"]),
                    "link": issue.get("html_url"),
                    "status": issue.get("state") or "open",
                    "author": author.get("login"),
                    "comments": int(issue.get("comments") or 0),
                }
            )

        pull_requests = []
        for pull in pull_items:
            author = pull.get("user") or {}
            status = "merged" if pull.get("merged_at") else (pull.get("state") or "open")
            pull_requests.append(
                {
                    "github_id": int(pull["id"]),
                    "number": int(pull["number"]),
                    "link": pull.get("html_url"),
                    "status": status,
                    "draft": bool(pull.get("draft")),
                    "author": author.get("login"),
                    "comments": pr_comment_counts.get(pull.get("number"), 0),
                }
            )

        security = {
            kind: fetch_security(client, base_path, kind) for kind in SECURITY_TYPES
        }
        repos.append(
            {
                "github_id": int(github_id),
                "name": full_name,
                "visibility": "private" if repository.get("private") else "public",
                "stars_count": stars_count,
                "stargazers": stargazers,
                "forks": forks,
                "issues": issues,
                "pull_requests": pull_requests,
                "security": security,
            }
        )
        if client.logger and (
            index == 1 or index % 10 == 0 or index == len(repositories)
        ):
            client.logger.info(
                "Repository progress: completed=%d total=%d queries=%d repository=%s",
                index,
                len(repositories),
                client.request_count,
                full_name,
            )

    return {
        "github_user_id": int(user["id"]),
        "profile": login,
        "followers": followers,
        "repos": repos,
    }


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    github_user_id INTEGER NOT NULL,
    profile TEXT NOT NULL,
    created_at TEXT NOT NULL,
    followers_count INTEGER NOT NULL,
    followers_list TEXT NOT NULL,
    followers_count_diff INTEGER NOT NULL,
    followers_list_diff TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    github_repo_id INTEGER NOT NULL,
    reponame TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
    created_at TEXT NOT NULL,
    stars_count INTEGER NOT NULL,
    stars_count_diff INTEGER NOT NULL,
    stargazers_list TEXT,
    stargazers_list_diff TEXT,
    forks_count INTEGER NOT NULL,
    forks_count_diff INTEGER NOT NULL,
    forks_list TEXT NOT NULL,
    forks_list_diff TEXT NOT NULL,
    issues_total_count INTEGER NOT NULL,
    issues_total_count_diff INTEGER NOT NULL,
    issues_open_count INTEGER NOT NULL,
    issues_open_count_diff INTEGER NOT NULL,
    prs_total_count INTEGER NOT NULL,
    prs_total_count_diff INTEGER NOT NULL,
    prs_open_count INTEGER NOT NULL,
    prs_open_count_diff INTEGER NOT NULL,
    dependabot_available INTEGER NOT NULL,
    dependabot_open_count INTEGER,
    dependabot_open_count_diff INTEGER,
    code_scanning_available INTEGER NOT NULL,
    code_scanning_open_count INTEGER,
    code_scanning_open_count_diff INTEGER,
    secret_scanning_available INTEGER NOT NULL,
    secret_scanning_open_count INTEGER,
    secret_scanning_open_count_diff INTEGER,
    UNIQUE (run_id, github_repo_id)
);

CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_snapshot_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    github_issue_id INTEGER NOT NULL,
    issue_number INTEGER NOT NULL,
    issue_link TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    issue_author TEXT,
    comments INTEGER NOT NULL,
    UNIQUE (repo_snapshot_id, github_issue_id)
);

CREATE TABLE IF NOT EXISTS pull_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_snapshot_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    github_pr_id INTEGER NOT NULL,
    pr_number INTEGER NOT NULL,
    pr_link TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'merged')),
    is_draft INTEGER NOT NULL,
    pr_author TEXT,
    comments INTEGER NOT NULL,
    UNIQUE (repo_snapshot_id, github_pr_id)
);

CREATE TABLE IF NOT EXISTS security_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_snapshot_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    alert_type TEXT NOT NULL CHECK (alert_type IN ('dependabot', 'code_scanning', 'secret_scanning')),
    github_alert_number INTEGER NOT NULL,
    alert_link TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    severity TEXT,
    identifier TEXT,
    UNIQUE (repo_snapshot_id, alert_type, github_alert_number)
);

CREATE INDEX IF NOT EXISTS repos_run_idx ON repos(run_id);
CREATE INDEX IF NOT EXISTS issues_repo_idx ON issues(repo_snapshot_id);
CREATE INDEX IF NOT EXISTS pull_requests_repo_idx ON pull_requests(repo_snapshot_id);
CREATE INDEX IF NOT EXISTS security_alerts_repo_idx ON security_alerts(repo_snapshot_id);
"""


def migrate_empty_stargazer_schema(connection):
    columns = {
        row[1]: row for row in connection.execute("PRAGMA table_info(repos)")
    }
    if not columns or not columns["stargazers_list"][3]:
        return
    repo_count = connection.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
    if repo_count:
        raise MonitorError(
            "The GitHub monitor database uses the old stargazer schema and contains "
            "snapshots; migrate or archive it before running this version"
        )
    connection.executescript(
        """
        DROP TABLE security_alerts;
        DROP TABLE pull_requests;
        DROP TABLE issues;
        DROP TABLE repos;
        """
    )
    connection.executescript(SCHEMA)


def open_database(path):
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    migrate_empty_stargazer_schema(connection)
    return connection


def decode_json(value):
    return json.loads(value) if value is not None else None


def load_previous_snapshot(connection):
    run = connection.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if run is None:
        return None
    run_id = run["id"]
    profile_row = connection.execute(
        "SELECT * FROM profile_data WHERE run_id = ?", (run_id,)
    ).fetchone()
    previous = {
        "github_user_id": profile_row["github_user_id"],
        "profile": profile_row["profile"],
        "followers": decode_json(profile_row["followers_list"]),
        "repos": [],
    }
    repo_rows = connection.execute(
        "SELECT * FROM repos WHERE run_id = ? ORDER BY reponame", (run_id,)
    ).fetchall()
    for row in repo_rows:
        issues = [
            {
                "github_id": item["github_issue_id"],
                "number": item["issue_number"],
                "link": item["issue_link"],
                "status": item["status"],
                "author": item["issue_author"],
                "comments": item["comments"],
            }
            for item in connection.execute(
                "SELECT * FROM issues WHERE repo_snapshot_id = ?", (row["id"],)
            )
        ]
        pull_requests = [
            {
                "github_id": item["github_pr_id"],
                "number": item["pr_number"],
                "link": item["pr_link"],
                "status": item["status"],
                "draft": bool(item["is_draft"]),
                "author": item["pr_author"],
                "comments": item["comments"],
            }
            for item in connection.execute(
                "SELECT * FROM pull_requests WHERE repo_snapshot_id = ?", (row["id"],)
            )
        ]
        security = {}
        for kind in SECURITY_TYPES:
            alerts = [
                {
                    "type": item["alert_type"],
                    "number": item["github_alert_number"],
                    "link": item["alert_link"],
                    "status": item["status"],
                    "severity": item["severity"],
                    "identifier": item["identifier"],
                }
                for item in connection.execute(
                    "SELECT * FROM security_alerts WHERE repo_snapshot_id = ? AND alert_type = ?",
                    (row["id"], kind),
                )
            ]
            security[kind] = {
                "available": bool(row[f"{kind}_available"]),
                "alerts": alerts,
            }
        previous["repos"].append(
            {
                "github_id": row["github_repo_id"],
                "name": row["reponame"],
                "visibility": row["visibility"],
                "stars_count": row["stars_count"],
                "stargazers": decode_json(row["stargazers_list"]),
                "forks": decode_json(row["forks_list"]),
                "issues": issues,
                "pull_requests": pull_requests,
                "security": security,
            }
        )
    return previous


def list_diff(current, previous):
    current_set = set(current)
    previous_set = set(previous)
    return {
        "added": sorted(current_set - previous_set),
        "removed": sorted(previous_set - current_set),
    }


def signed(value):
    return f"{value:+d}"


def object_map(items):
    return {item["github_id"]: item for item in items}


def alert_map(items):
    return {item["number"]: item for item in items}


def object_changes(current_items, previous_items):
    current = object_map(current_items)
    previous = object_map(previous_items)
    return (
        [current[key] for key in sorted(current.keys() - previous.keys())],
        [previous[key] for key in sorted(previous.keys() - current.keys())],
        [
            (previous[key], current[key])
            for key in sorted(current.keys() & previous.keys())
            if current[key] != previous[key]
        ],
    )


def describe_actor(actor):
    return f" by @{actor}" if actor else ""


def append_issue_changes(lines, current, previous):
    added, removed, changed = object_changes(current, previous)
    for issue in added:
        lines.append(
            f"  Issue added [{issue['status']}]: {issue['link'] or '#' + str(issue['number'])}"
            f"{describe_actor(issue['author'])} (comments {issue['comments']})"
        )
    for issue in removed:
        lines.append(f"  Issue removed: {issue['link'] or '#' + str(issue['number'])}")
    for old, new in changed:
        reference = new["link"] or "#" + str(new["number"])
        if old["status"] != new["status"]:
            lines.append(f"  Issue {new['status']}: {reference}")
        if old["comments"] != new["comments"]:
            lines.append(
                f"  Issue comments {signed(new['comments'] - old['comments'])}: "
                f"{reference} ({new['comments']} total)"
            )


def append_pr_changes(lines, current, previous):
    added, removed, changed = object_changes(current, previous)
    for pull in added:
        draft = ", draft" if pull["draft"] else ""
        lines.append(
            f"  PR added [{pull['status']}{draft}]: {pull['link'] or '#' + str(pull['number'])}"
            f"{describe_actor(pull['author'])} (comments {pull['comments']})"
        )
    for pull in removed:
        lines.append(f"  PR removed: {pull['link'] or '#' + str(pull['number'])}")
    for old, new in changed:
        reference = new["link"] or "#" + str(new["number"])
        if old["status"] != new["status"]:
            lines.append(f"  PR {new['status']}: {reference}")
        if old["draft"] != new["draft"]:
            lines.append(f"  PR draft status: {reference} -> {str(new['draft']).lower()}")
        if old["comments"] != new["comments"]:
            lines.append(
                f"  PR comments {signed(new['comments'] - old['comments'])}: "
                f"{reference} ({new['comments']} total)"
            )


def append_security_changes(lines, current, previous, first_run):
    for kind, label in SECURITY_TYPES.items():
        current_data = current[kind]
        previous_data = previous.get(kind, {"available": False, "alerts": []})
        if not current_data["available"]:
            if first_run or previous_data["available"]:
                lines.append(f"  {label} alerts: unavailable")
            continue
        current_alerts = alert_map(current_data["alerts"])
        previous_alerts = alert_map(previous_data["alerts"]) if previous_data["available"] else {}
        count_diff = len(current_alerts) - len(previous_alerts)
        availability_changed = not previous_data["available"]
        if first_run or availability_changed or current_alerts != previous_alerts:
            lines.append(
                f"  {label} alerts: {len(current_alerts)} ({signed(count_diff)})"
            )
        for number in sorted(current_alerts.keys() - previous_alerts.keys()):
            alert = current_alerts[number]
            details = alert.get("identifier") or f"alert {number}"
            suffix = f" [{alert['severity']}]" if alert.get("severity") else ""
            lines.append(f"    Added: {details}{suffix} {alert.get('link') or ''}".rstrip())
        for number in sorted(previous_alerts.keys() - current_alerts.keys()):
            alert = previous_alerts[number]
            details = alert.get("identifier") or f"alert {number}"
            lines.append(f"    Resolved: {details} {alert.get('link') or ''}".rstrip())
        for number in sorted(previous_alerts.keys() & current_alerts.keys()):
            if previous_alerts[number] != current_alerts[number]:
                alert = current_alerts[number]
                details = alert.get("identifier") or f"alert {number}"
                lines.append(f"    Updated: {details} {alert.get('link') or ''}".rstrip())


def repo_report(repo, previous, first_run):
    lines = []
    old = previous or {
        "name": repo["name"],
        "visibility": repo["visibility"],
        "stars_count": 0,
        "stargazers": [],
        "forks": [],
        "issues": [],
        "pull_requests": [],
        "security": {},
    }
    star_diff = (
        list_diff(repo["stargazers"], old["stargazers"])
        if repo["stargazers"] is not None and old["stargazers"] is not None
        else None
    )
    stars_count_diff = repo["stars_count"] - old["stars_count"]
    fork_diff = list_diff(repo["forks"], old["forks"])
    issue_total_diff = len(repo["issues"]) - len(old["issues"])
    issue_open = sum(item["status"] == "open" for item in repo["issues"])
    old_issue_open = sum(item["status"] == "open" for item in old["issues"])
    pr_total_diff = len(repo["pull_requests"]) - len(old["pull_requests"])
    pr_open = sum(item["status"] == "open" for item in repo["pull_requests"])
    old_pr_open = sum(item["status"] == "open" for item in old["pull_requests"])

    details = []
    if previous is None:
        details.append("  Repository added to monitoring")
    if previous and old["name"] != repo["name"]:
        details.append(f"  Renamed from: {old['name']}")
    if previous and old["visibility"] != repo["visibility"]:
        details.append(f"  Visibility: {old['visibility']} -> {repo['visibility']}")
    stargazer_availability_changed = (
        previous is not None
        and (repo["stargazers"] is None) != (old["stargazers"] is None)
    )
    if (
        first_run
        or stars_count_diff
        or stargazer_availability_changed
        or (star_diff and (star_diff["added"] or star_diff["removed"]))
    ):
        details.append(
            f"  Stars: {repo['stars_count']} ({signed(stars_count_diff)})"
        )
        if repo["stargazers"] is None:
            details.append("  Stargazer list: unavailable (count only)")
        elif old["stargazers"] is None:
            details.append("  Stargazer list became available")
        if star_diff and star_diff["added"]:
            details.append("  Stargazers added: " + ", ".join("@" + item for item in star_diff["added"]))
        if star_diff and star_diff["removed"]:
            details.append("  Stargazers removed: " + ", ".join("@" + item for item in star_diff["removed"]))
    if first_run or fork_diff["added"] or fork_diff["removed"]:
        details.append(
            f"  Forks: {len(repo['forks'])} ({signed(len(repo['forks']) - len(old['forks']))})"
        )
        if fork_diff["added"]:
            details.append("  Forks added: " + ", ".join(fork_diff["added"]))
        if fork_diff["removed"]:
            details.append("  Forks removed: " + ", ".join(fork_diff["removed"]))
    if first_run or issue_total_diff or issue_open != old_issue_open:
        details.append(
            f"  Issues: {len(repo['issues'])} total ({signed(issue_total_diff)}), "
            f"{issue_open} open ({signed(issue_open - old_issue_open)})"
        )
    append_issue_changes(details, repo["issues"], old["issues"])
    if first_run or pr_total_diff or pr_open != old_pr_open:
        details.append(
            f"  PRs: {len(repo['pull_requests'])} total ({signed(pr_total_diff)}), "
            f"{pr_open} open ({signed(pr_open - old_pr_open)})"
        )
    append_pr_changes(details, repo["pull_requests"], old["pull_requests"])
    append_security_changes(details, repo["security"], old["security"], first_run)
    if details:
        lines.append(repo["name"])
        lines.extend(details)
    return lines


def build_report(snapshot, previous):
    account_changed = previous is not None and (
        snapshot["github_user_id"] != previous["github_user_id"]
    )
    original_previous = previous
    if account_changed:
        previous = None
    first_run = previous is None
    old_followers = [] if first_run else previous["followers"]
    follower_diff = list_diff(snapshot["followers"], old_followers)
    lines = []
    if account_changed:
        lines.append(
            f"GitHub account changed: @{original_previous['profile']} -> @{snapshot['profile']}"
        )
    profile_renamed = (
        previous is not None and snapshot["profile"] != previous["profile"]
    )
    if first_run or profile_renamed or follower_diff["added"] or follower_diff["removed"]:
        if profile_renamed:
            lines.append(
                f"GitHub profile renamed: @{previous['profile']} -> @{snapshot['profile']}"
            )
        lines.append(f"GitHub profile @{snapshot['profile']}")
        lines.append(
            f"Followers: {len(snapshot['followers'])} "
            f"({signed(len(snapshot['followers']) - len(old_followers))})"
        )
        if follower_diff["added"]:
            lines.append("Followers added: " + ", ".join("@" + item for item in follower_diff["added"]))
        if follower_diff["removed"]:
            lines.append("Followers removed: " + ", ".join("@" + item for item in follower_diff["removed"]))

    previous_repos = {} if first_run else {repo["github_id"]: repo for repo in previous["repos"]}
    current_repos = {repo["github_id"]: repo for repo in snapshot["repos"]}
    reports = {"public": [], "private": []}
    for repo in sorted(snapshot["repos"], key=lambda item: item["name"].casefold()):
        report = repo_report(repo, previous_repos.get(repo["github_id"]), first_run)
        if report:
            reports[repo["visibility"]].append(report)
    for github_id in previous_repos.keys() - current_repos.keys():
        old = previous_repos[github_id]
        reports[old["visibility"]].append([f"{old['name']}", "  Repository removed from monitoring"])

    for visibility, label in (("public", "Public repositories"), ("private", "Private repositories")):
        if reports[visibility]:
            if lines:
                lines.append("")
            lines.append(label + ":")
            for index, report in enumerate(reports[visibility]):
                if index:
                    lines.append("")
                lines.extend(report)
    return "\n".join(lines)


def security_count_data(repo, old, kind):
    current = repo["security"][kind]
    if not current["available"]:
        return 0, None, None
    current_count = len(current["alerts"])
    old_data = old["security"].get(kind) if old else None
    old_count = len(old_data["alerts"]) if old_data and old_data["available"] else 0
    return 1, current_count, current_count - old_count


def save_snapshot(connection, snapshot, created_at=None, transaction_started=False):
    timestamp = created_at or utc_now()
    if not transaction_started:
        connection.execute("BEGIN IMMEDIATE")
    try:
        previous = load_previous_snapshot(connection)
        report = build_report(snapshot, previous)
        if previous is not None and snapshot["github_user_id"] != previous["github_user_id"]:
            previous = None
        cursor = connection.execute("INSERT INTO runs (created_at) VALUES (?)", (timestamp,))
        run_id = cursor.lastrowid
        old_followers = previous["followers"] if previous else []
        follower_diff = list_diff(snapshot["followers"], old_followers)
        connection.execute(
            """
            INSERT INTO profile_data (
                run_id, github_user_id, profile, created_at, followers_count, followers_list,
                followers_count_diff, followers_list_diff
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot["github_user_id"],
                snapshot["profile"],
                timestamp,
                len(snapshot["followers"]),
                json.dumps(snapshot["followers"]),
                len(snapshot["followers"]) - len(old_followers),
                json.dumps(follower_diff),
            ),
        )
        previous_repos = {} if previous is None else {
            repo["github_id"]: repo for repo in previous["repos"]
        }
        for repo in snapshot["repos"]:
            old = previous_repos.get(repo["github_id"])
            old_stargazers = old["stargazers"] if old else []
            old_stars_count = old["stars_count"] if old else 0
            old_forks = old["forks"] if old else []
            old_issues = old["issues"] if old else []
            old_prs = old["pull_requests"] if old else []
            issue_open = sum(item["status"] == "open" for item in repo["issues"])
            old_issue_open = sum(item["status"] == "open" for item in old_issues)
            pr_open = sum(item["status"] == "open" for item in repo["pull_requests"])
            old_pr_open = sum(item["status"] == "open" for item in old_prs)
            security_values = {}
            for kind in SECURITY_TYPES:
                security_values[kind] = security_count_data(repo, old, kind)
            cursor = connection.execute(
                """
                INSERT INTO repos (
                    run_id, github_repo_id, reponame, visibility, created_at,
                    stars_count, stars_count_diff, stargazers_list, stargazers_list_diff,
                    forks_count, forks_count_diff, forks_list, forks_list_diff,
                    issues_total_count, issues_total_count_diff,
                    issues_open_count, issues_open_count_diff,
                    prs_total_count, prs_total_count_diff, prs_open_count, prs_open_count_diff,
                    dependabot_available, dependabot_open_count, dependabot_open_count_diff,
                    code_scanning_available, code_scanning_open_count, code_scanning_open_count_diff,
                    secret_scanning_available, secret_scanning_open_count, secret_scanning_open_count_diff
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    repo["github_id"],
                    repo["name"],
                    repo["visibility"],
                    timestamp,
                    repo["stars_count"],
                    repo["stars_count"] - old_stars_count,
                    json.dumps(repo["stargazers"]) if repo["stargazers"] is not None else None,
                    (
                        json.dumps(list_diff(repo["stargazers"], old_stargazers))
                        if repo["stargazers"] is not None and old_stargazers is not None
                        else None
                    ),
                    len(repo["forks"]),
                    len(repo["forks"]) - len(old_forks),
                    json.dumps(repo["forks"]),
                    json.dumps(list_diff(repo["forks"], old_forks)),
                    len(repo["issues"]),
                    len(repo["issues"]) - len(old_issues),
                    issue_open,
                    issue_open - old_issue_open,
                    len(repo["pull_requests"]),
                    len(repo["pull_requests"]) - len(old_prs),
                    pr_open,
                    pr_open - old_pr_open,
                    *security_values["dependabot"],
                    *security_values["code_scanning"],
                    *security_values["secret_scanning"],
                ),
            )
            repo_snapshot_id = cursor.lastrowid
            for issue in repo["issues"]:
                connection.execute(
                    """
                    INSERT INTO issues (
                        repo_snapshot_id, github_issue_id, issue_number, issue_link,
                        created_at, status, issue_author, comments
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        repo_snapshot_id,
                        issue["github_id"],
                        issue["number"],
                        issue["link"],
                        timestamp,
                        issue["status"],
                        issue["author"],
                        issue["comments"],
                    ),
                )
            for pull in repo["pull_requests"]:
                connection.execute(
                    """
                    INSERT INTO pull_requests (
                        repo_snapshot_id, github_pr_id, pr_number, pr_link,
                        created_at, status, is_draft, pr_author, comments
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        repo_snapshot_id,
                        pull["github_id"],
                        pull["number"],
                        pull["link"],
                        timestamp,
                        pull["status"],
                        int(pull["draft"]),
                        pull["author"],
                        pull["comments"],
                    ),
                )
            for kind, data in repo["security"].items():
                if not data["available"]:
                    continue
                for alert in data["alerts"]:
                    connection.execute(
                        """
                        INSERT INTO security_alerts (
                            repo_snapshot_id, alert_type, github_alert_number,
                            alert_link, created_at, status, severity, identifier
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            repo_snapshot_id,
                            kind,
                            alert["number"],
                            alert["link"],
                            timestamp,
                            alert["status"],
                            alert["severity"],
                            alert["identifier"],
                        ),
                    )
        connection.commit()
        return report
    except Exception:
        connection.rollback()
        raise


def build_parser():
    parser = argparse.ArgumentParser(description="Monitor a GitHub profile and owned repositories")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.toml")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    connection = None
    client = None
    logger = None
    missing_permissions_logger = None
    succeeded = False
    started_at = time.monotonic()
    try:
        (
            base_url,
            token,
            database_path,
            log_path,
            missing_permissions_log_path,
            timeout,
        ) = load_settings(args.config)
        logger = configure_logger(log_path)
        missing_permissions_logger = configure_missing_permissions_logger(
            missing_permissions_log_path
        )
        logger.info("GitHub monitor run started")
        client = GitHubClient(
            base_url,
            token,
            timeout,
            logger=logger,
            missing_permissions_logger=missing_permissions_logger,
        )
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = open_database(database_path)
        # Serialize collection and persistence so a slower overlapping run cannot
        # overwrite a newer snapshot with stale API data.
        connection.execute("BEGIN IMMEDIATE")
        snapshot = collect_snapshot(client)
        report = save_snapshot(connection, snapshot, transaction_started=True)
        if report:
            print(report)
        succeeded = True
        return 0
    except (MonitorError, OSError, sqlite3.Error) as error:
        if logger:
            logger.exception("GitHub monitor run failed: %s", error)
        print(f"GitHub monitor failed: {error}", file=sys.stderr)
        return 1
    finally:
        wall_seconds = time.monotonic() - started_at
        if client is not None:
            client.log_summary(wall_seconds)
        if logger:
            logger.info(
                "GitHub monitor run finished: status=%s wall_time=%.2fs",
                "success" if succeeded else "failed",
                wall_seconds,
            )
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    sys.exit(main())
