#!/usr/bin/env python3

import argparse
import configparser
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


class APIError(Exception):
    pass


class AhoritaClient:
    def __init__(self, base_url, api_key, timeout=15):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def request(self, path, method="GET", payload=None):
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            response_body = error.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(response_body).get("error")
            except json.JSONDecodeError:
                message = None
            raise APIError(message or f"API request failed with HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise APIError(f"Could not reach API: {error.reason}") from error

        if not response_body:
            return {}
        try:
            return json.loads(response_body)
        except json.JSONDecodeError as error:
            raise APIError("API returned invalid JSON") from error

    def task_list(self):
        data = self.request("/view")
        tasks_by_status = {}
        for task in data.get("tasks", []):
            tasks_by_status.setdefault(task.get("status_id"), []).append(task)

        board = []
        for status in data.get("statuses", []):
            status_id = status.get("status_id")
            board.append(
                {
                    "status_id": status_id,
                    "status": status.get("name"),
                    "tasks": tasks_by_status.pop(status_id, []),
                }
            )

        for status_id, tasks in tasks_by_status.items():
            board.append(
                {"status_id": status_id, "status": "unknown", "tasks": tasks}
            )
        return {"user": data.get("user"), "board": board}

    def task_list_today(self):
        return self.request("/task_list_today")

    def task_list_tomorrow(self):
        return self.request("/task_list_tomorrow")

    def task_get(self, task_id):
        return self.request(f"/task_get?task_id={task_id}")

    def task_create(self, title, description=None, is_asap=False, is_important=False, due_at=None):
        return self.request(
            "/task_create",
            method="POST",
            payload={
                "title": title,
                "text": description,
                "is_asap": is_asap,
                "is_important": is_important,
                "due_at": due_at,
                "has_time": bool(due_at and "T" in due_at),
            },
        )


def load_client(config_path=None):
    path = config_path or Path(__file__).with_name("config.toml")
    parser = configparser.ConfigParser()
    try:
        with Path(path).open("r", encoding="utf-8") as config_file:
            parser.read_file(config_file)
        config = parser["ahorita_plugin"]
    except FileNotFoundError as error:
        raise APIError(f"Config file not found: {path}") from error
    except (KeyError, configparser.Error) as error:
        raise APIError(f"Invalid config file: {path}") from error

    secret_path = Path(config.get("secret_path", "").strip('"\'')).expanduser()
    try:
        api_key = secret_path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, IsADirectoryError) as error:
        raise APIError(f"API key file not found: {secret_path}") from error
    if not api_key:
        raise APIError(f"API key file is empty: {secret_path}")

    base_url = config.get("base_url", "").strip().strip('"\'')
    if not base_url:
        raise APIError("base_url is missing from config.toml")
    timeout = config.get("timeout_seconds", "15").strip().strip('"\'')
    try:
        timeout = int(timeout)
    except ValueError as error:
        raise APIError("timeout_seconds must be an integer") from error
    return AhoritaClient(base_url, api_key, timeout)


def build_parser():
    parser = argparse.ArgumentParser(description="Manage Ahorita tasks")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("task_list", help="List all tasks grouped as a board")
    commands.add_parser(
        "task_list_today", help="List undated ASAP tasks and tasks due today"
    )
    commands.add_parser(
        "task_list_tomorrow", help="List undated ASAP tasks and tasks due tomorrow"
    )

    get_parser = commands.add_parser("task_get", help="Get one task")
    get_parser.add_argument("task_id", type=int, help="Task ID")

    create_parser = commands.add_parser("task_create", help="Create a task")
    create_parser.add_argument("--title", required=True, help="Task title")
    create_parser.add_argument("--description", help="Task description")
    create_parser.add_argument("--asap", action="store_true", help="Mark as ASAP")
    create_parser.add_argument("--important", action="store_true", help="Mark as important")
    create_parser.add_argument(
        "--due-at",
        help="Due date as YYYY-MM-DD or local date and time as YYYY-MM-DDTHH:MM",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        client = load_client()
        if args.command == "task_list":
            result = client.task_list()
        elif args.command == "task_list_today":
            result = client.task_list_today()
        elif args.command == "task_list_tomorrow":
            result = client.task_list_tomorrow()
        elif args.command == "task_get":
            if args.task_id <= 0:
                raise APIError("task_id must be greater than zero")
            result = client.task_get(args.task_id)
        else:
            title = args.title.strip()
            if not title:
                raise APIError("title cannot be empty")
            result = client.task_create(
                title=title,
                description=args.description,
                is_asap=args.asap,
                is_important=args.important,
                due_at=args.due_at,
            )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except APIError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
