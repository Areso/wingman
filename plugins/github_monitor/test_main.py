import json
import sqlite3
import threading
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from main import (
    GitHubClient,
    GitHubError,
    collect_snapshot,
    configure_logger,
    configure_missing_permissions_logger,
    fetch_security,
    fetch_stargazers,
    open_database,
    save_snapshot,
)


def security(available=True, alerts=None):
    return {"available": available, "alerts": alerts or []}


def repository(
    *,
    github_id=10,
    name="octo/project",
    visibility="public",
    stargazers=None,
    stars_count=None,
    stargazers_available=True,
    forks=None,
    issues=None,
    pull_requests=None,
    security_data=None,
):
    stargazer_list = (stargazers or []) if stargazers_available else None
    return {
        "github_id": github_id,
        "name": name,
        "visibility": visibility,
        "stars_count": len(stargazer_list) if stars_count is None and stargazer_list is not None else (stars_count or 0),
        "stargazers": stargazer_list,
        "forks": forks or [],
        "issues": issues or [],
        "pull_requests": pull_requests or [],
        "security": security_data
        or {
            "dependabot": security(),
            "code_scanning": security(),
            "secret_scanning": security(),
        },
    }


class GitHubHandler(BaseHTTPRequestHandler):
    paths = []

    def do_GET(self):
        self.__class__.paths.append(self.path)
        path = self.path.split("?", 1)[0]
        if path == "/user":
            self.respond({"id": 1, "login": "octo"})
        elif path == "/paged":
            if "cursor=next" in self.path:
                self.respond([{"id": 101}])
            else:
                next_url = f"http://{self.headers['Host']}/paged?cursor=next"
                self.respond(
                    [{"id": item} for item in range(1, 101)],
                    headers={"Link": f'<{next_url}>; rel="next"'},
                )
        elif path == "/repos/octo/rate/dependabot/alerts":
            self.respond(
                {"message": "API rate limit exceeded"},
                status=403,
                headers={"X-RateLimit-Remaining": "0"},
            )
        elif path == "/repos/octo/restricted/stargazers":
            self.respond(
                {"message": "Resource not accessible by personal access token"},
                status=403,
                headers={"X-Accepted-GitHub-Permissions": "contents=write"},
            )
        elif path == "/repos/octo/restricted/stargazers/count":
            self.respond({"count": 42})
        elif path == "/repos/octo/restricted/dependabot/alerts":
            self.respond(
                {"message": "Resource not accessible by personal access token"},
                status=403,
                headers={
                    "X-Accepted-GitHub-Permissions": "vulnerability_alerts=read"
                },
            )
        elif path == "/restricted":
            self.respond(
                {"message": "Resource not accessible by personal access token"},
                status=403,
            )
        elif path == "/users/octo/followers":
            self.respond([{"login": "alice"}])
        elif path == "/user/repos":
            self.respond(
                [
                    {"id": 10, "full_name": "octo/project", "private": False, "fork": False},
                    {"id": 11, "full_name": "octo/private", "private": True, "fork": False},
                    {"id": 12, "full_name": "octo/fork", "private": False, "fork": True},
                ]
            )
        elif path.endswith("/stargazers"):
            self.respond([{"login": "star-user"}] if "/project/" in path else [])
        elif path.endswith("/forks"):
            self.respond([{"full_name": "someone/project"}] if "/project/" in path else [])
        elif path.endswith("/issues"):
            self.respond(
                [
                    {
                        "id": 100,
                        "number": 1,
                        "html_url": "https://github.test/octo/project/issues/1",
                        "state": "open",
                        "user": {"login": "reporter"},
                        "comments": 2,
                    },
                    {
                        "id": 101,
                        "number": 2,
                        "html_url": "https://github.test/octo/project/pull/2",
                        "state": "closed",
                        "user": {"login": "author"},
                        "comments": 3,
                        "pull_request": {},
                    },
                ]
                if "/project/" in path
                else []
            )
        elif path.endswith("/pulls"):
            self.respond(
                [
                    {
                        "id": 200,
                        "number": 2,
                        "html_url": "https://github.test/octo/project/pull/2",
                        "state": "closed",
                        "merged_at": "2026-01-01T00:00:00Z",
                        "draft": False,
                        "user": {"login": "author"},
                    }
                ]
                if "/project/" in path
                else []
            )
        elif path.endswith("/dependabot/alerts"):
            self.respond(
                [
                    {
                        "number": 7,
                        "html_url": "https://github.test/alert/7",
                        "state": "open",
                        "security_advisory": {"ghsa_id": "GHSA-test", "severity": "high"},
                    }
                ]
                if "/project/" in path
                else []
            )
        elif path.endswith("/code-scanning/alerts"):
            self.respond([], status=404 if "/private/" in path else 200)
        elif path.endswith("/secret-scanning/alerts"):
            self.respond([])
        else:
            self.respond({"message": "not found"}, status=404)

    def respond(self, payload, status=200, headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class GitHubClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), GitHubHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def setUp(self):
        GitHubHandler.paths.clear()

    def test_collects_owned_non_fork_repositories_and_security(self):
        client = GitHubClient(f"http://127.0.0.1:{self.server.server_port}", "test-token")

        snapshot = collect_snapshot(client)

        self.assertEqual(snapshot["profile"], "octo")
        self.assertEqual(snapshot["followers"], ["alice"])
        self.assertEqual([repo["name"] for repo in snapshot["repos"]], ["octo/project", "octo/private"])
        project = snapshot["repos"][0]
        self.assertEqual(project["stargazers"], ["star-user"])
        self.assertEqual(project["forks"], ["someone/project"])
        self.assertEqual(project["issues"][0]["comments"], 2)
        self.assertEqual(project["pull_requests"][0]["status"], "merged")
        self.assertEqual(project["pull_requests"][0]["comments"], 3)
        self.assertEqual(project["security"]["dependabot"]["alerts"][0]["identifier"], "GHSA-test")
        self.assertFalse(snapshot["repos"][1]["security"]["code_scanning"]["available"])
        self.assertTrue(any("affiliation=owner" in path and "visibility=all" in path for path in GitHubHandler.paths))
        self.assertEqual(client.request_count, 17)
        self.assertEqual(client.request_stats["issues"][0], 2)
        self.assertEqual(client.request_stats["code_scanning_alerts"][0], 2)

    def test_pagination_follows_link_header(self):
        client = GitHubClient(f"http://127.0.0.1:{self.server.server_port}", "test-token")

        items = client.paginate("/paged")

        self.assertEqual(len(items), 101)
        self.assertEqual(items[-1]["id"], 101)
        self.assertEqual(sum(path.startswith("/paged") for path in GitHubHandler.paths), 2)
        self.assertEqual(client.request_count, 2)

    def test_rate_limit_is_not_treated_as_unavailable_security(self):
        client = GitHubClient(f"http://127.0.0.1:{self.server.server_port}", "test-token")

        with self.assertRaises(GitHubError):
            fetch_security(client, "/repos/octo/rate", "dependabot")

    def test_http_error_identifies_inaccessible_resource(self):
        client = GitHubClient(f"http://127.0.0.1:{self.server.server_port}", "test-token")

        with self.assertRaisesRegex(
            GitHubError,
            r"resource /restricted\?detail=1 returned HTTP 403: "
            r"Resource not accessible by personal access token",
        ):
            client.request("/restricted", {"detail": 1})

    def test_stargazers_fall_back_to_count_when_list_is_forbidden(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "missing.log"
            permission_logger = configure_missing_permissions_logger(log_path)
            client = GitHubClient(
                f"http://127.0.0.1:{self.server.server_port}",
                "test-token",
                missing_permissions_logger=permission_logger,
            )

            count, stargazers = fetch_stargazers(client, "/repos/octo/restricted")
            second_count, second_stargazers = fetch_stargazers(
                client, "/repos/octo/restricted"
            )
            for handler in permission_logger.handlers:
                handler.flush()
            permission_log = log_path.read_text(encoding="utf-8")

        self.assertEqual(count, 42)
        self.assertIsNone(stargazers)
        self.assertEqual(second_count, 42)
        self.assertIsNone(second_stargazers)
        self.assertEqual(client.request_count, 3)
        self.assertEqual(client.request_stats["stargazers_list"][0], 1)
        self.assertEqual(client.request_stats["stargazers_count"][0], 2)
        self.assertEqual(client.skipped_permission_checks["stargazers_list"], 1)
        self.assertIn("required=contents=write", permission_log)
        self.assertEqual(permission_log.count("Missing GitHub permission"), 1)

    def test_dependabot_missing_permission_skips_later_repositories(self):
        client = GitHubClient(f"http://127.0.0.1:{self.server.server_port}", "test-token")

        first = fetch_security(client, "/repos/octo/restricted", "dependabot")
        second = fetch_security(client, "/repos/octo/another", "dependabot")

        self.assertFalse(first["available"])
        self.assertFalse(second["available"])
        self.assertEqual(client.request_count, 1)
        self.assertEqual(
            client.missing_permissions["dependabot_alerts"]["required"],
            "vulnerability_alerts=read",
        )
        self.assertEqual(client.skipped_permission_checks["dependabot_alerts"], 1)

    def test_file_log_contains_query_and_category_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "monitor.log"
            logger = configure_logger(log_path)
            client = GitHubClient(
                f"http://127.0.0.1:{self.server.server_port}",
                "test-token",
                logger=logger,
            )

            client.request("/user")
            client.log_summary(1.5)
            for handler in logger.handlers:
                handler.flush()
            contents = log_path.read_text(encoding="utf-8")

        self.assertIn("HTTP summary: queries=1", contents)
        self.assertIn("HTTP category: name=profile queries=1", contents)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.connection = open_database(":memory:")

    def tearDown(self):
        self.connection.close()

    def test_first_snapshot_is_reported_against_zero_and_persisted(self):
        snapshot = {
            "github_user_id": 1,
            "profile": "octo",
            "followers": ["alice"],
            "repos": [
                repository(
                    stargazers=["star-user"],
                    forks=["someone/project"],
                    issues=[
                        {
                            "github_id": 100,
                            "number": 1,
                            "link": "https://github.test/issues/1",
                            "status": "open",
                            "author": "reporter",
                            "comments": 2,
                        }
                    ],
                )
            ],
        }

        report = save_snapshot(self.connection, snapshot, "2026-01-01T00:00:00+00:00")

        self.assertIn("Followers: 1 (+1)", report)
        self.assertIn("Stars: 1 (+1)", report)
        self.assertIn("Issue added [open]", report)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        profile = self.connection.execute("SELECT * FROM profile_data").fetchone()
        self.assertEqual(profile["created_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(json.loads(profile["followers_list_diff"])["added"], ["alice"])

    def test_second_snapshot_reports_only_changes(self):
        initial = {
            "github_user_id": 1,
            "profile": "octo",
            "followers": ["alice"],
            "repos": [
                repository(
                    visibility="private",
                    stargazers=["one"],
                    issues=[
                        {
                            "github_id": 100,
                            "number": 1,
                            "link": "https://github.test/issues/1",
                            "status": "open",
                            "author": "reporter",
                            "comments": 0,
                        }
                    ],
                    pull_requests=[
                        {
                            "github_id": 200,
                            "number": 2,
                            "link": "https://github.test/pull/2",
                            "status": "open",
                            "draft": True,
                            "author": "author",
                            "comments": 1,
                        }
                    ],
                )
            ],
        }
        save_snapshot(self.connection, initial, "2026-01-01T00:00:00+00:00")
        changed = json.loads(json.dumps(initial))
        changed["followers"].append("bob")
        changed["repos"][0]["stargazers"].append("two")
        changed["repos"][0]["stars_count"] += 1
        changed["repos"][0]["issues"][0]["status"] = "closed"
        changed["repos"][0]["issues"][0]["comments"] = 1
        changed["repos"][0]["pull_requests"][0]["status"] = "merged"
        changed["repos"][0]["pull_requests"][0]["draft"] = False

        report = save_snapshot(self.connection, changed, "2026-01-02T00:00:00+00:00")

        self.assertIn("Followers added: @bob", report)
        self.assertIn("Private repositories:", report)
        self.assertIn("Stargazers added: @two", report)
        self.assertIn("Issue closed:", report)
        self.assertIn("Issue comments +1:", report)
        self.assertIn("PR merged:", report)
        latest = self.connection.execute("SELECT * FROM repos ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(latest["stars_count_diff"], 1)
        self.assertEqual(latest["issues_open_count_diff"], -1)
        self.assertEqual(latest["prs_open_count_diff"], -1)

    def test_unchanged_snapshot_has_empty_report(self):
        snapshot = {"github_user_id": 1, "profile": "octo", "followers": [], "repos": []}
        save_snapshot(self.connection, snapshot, "2026-01-01T00:00:00+00:00")

        report = save_snapshot(self.connection, snapshot, "2026-01-02T00:00:00+00:00")

        self.assertEqual(report, "")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 2)

    def test_new_empty_repository_is_reported(self):
        initial = {"github_user_id": 1, "profile": "octo", "followers": [], "repos": []}
        save_snapshot(self.connection, initial, "2026-01-01T00:00:00+00:00")
        changed = {
            "github_user_id": 1,
            "profile": "octo",
            "followers": [],
            "repos": [
                repository(
                    security_data={
                        "dependabot": security(False),
                        "code_scanning": security(False),
                        "secret_scanning": security(False),
                    }
                )
            ],
        }

        report = save_snapshot(self.connection, changed, "2026-01-02T00:00:00+00:00")

        self.assertIn("Repository added to monitoring", report)

    def test_count_only_stargazers_are_reported_and_stored_as_null(self):
        snapshot = {
            "github_user_id": 1,
            "profile": "octo",
            "followers": [],
            "repos": [
                repository(
                    stars_count=42,
                    stargazers_available=False,
                )
            ],
        }

        report = save_snapshot(self.connection, snapshot, "2026-01-01T00:00:00+00:00")

        self.assertIn("Stars: 42 (+42)", report)
        self.assertIn("Stargazer list: unavailable (count only)", report)
        row = self.connection.execute("SELECT * FROM repos").fetchone()
        self.assertEqual(row["stars_count"], 42)
        self.assertIsNone(row["stargazers_list"])
        self.assertIsNone(row["stargazers_list_diff"])

    def test_account_change_resets_comparison_baseline(self):
        initial = {"github_user_id": 1, "profile": "octo", "followers": ["alice"], "repos": []}
        save_snapshot(self.connection, initial, "2026-01-01T00:00:00+00:00")
        changed = {"github_user_id": 2, "profile": "other", "followers": ["bob"], "repos": []}

        report = save_snapshot(self.connection, changed, "2026-01-02T00:00:00+00:00")

        self.assertIn("GitHub account changed: @octo -> @other", report)
        self.assertIn("Followers: 1 (+1)", report)
        self.assertNotIn("Followers removed: @alice", report)

    def test_failed_insert_rolls_back_complete_run(self):
        snapshot = {
            "github_user_id": 1,
            "profile": "octo",
            "followers": [],
            "repos": [
                repository(
                    issues=[
                        {
                            "github_id": 100,
                            "number": 1,
                            "link": None,
                            "status": "invalid",
                            "author": None,
                            "comments": 0,
                        }
                    ]
                )
            ],
        }

        with self.assertRaises(sqlite3.IntegrityError):
            save_snapshot(self.connection, snapshot)

        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
