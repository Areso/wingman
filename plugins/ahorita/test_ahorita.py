import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from ahorita import AhoritaClient


class TestHandler(BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):
        self.__class__.requests.append(("GET", self.path, self.headers, None))
        if self.path == "/view":
            self.respond(
                {
                    "user": "user@example.com",
                    "statuses": [
                        {"status_id": 1, "name": "created"},
                        {"status_id": 2, "name": "wip"},
                    ],
                    "tasks": [{"task_id": 7, "title": "Test", "status_id": 1}],
                }
            )
        elif self.path in ("/task_list_today", "/task_list_tomorrow"):
            self.respond(
                {
                    "user": "user@example.com",
                    "tasks": [{"task_id": 7, "title": "Test", "status_id": 1}],
                }
            )
        else:
            self.respond({"task_id": 7, "title": "Test"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.__class__.requests.append(("POST", self.path, self.headers, body))
        self.respond({"task_id": 8}, status=201)

    def respond(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class AhoritaClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), TestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = AhoritaClient(
            f"http://127.0.0.1:{cls.server.server_port}", "ajorita_test"
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def setUp(self):
        TestHandler.requests.clear()

    def test_task_list_groups_tasks_by_status(self):
        result = self.client.task_list()

        self.assertEqual(result["user"], "user@example.com")
        self.assertEqual(result["board"][0]["tasks"][0]["task_id"], 7)
        self.assertEqual(result["board"][1]["tasks"], [])
        self.assertEqual(
            TestHandler.requests[0][2]["Authorization"], "Bearer ajorita_test"
        )

    def test_task_get_uses_task_id(self):
        result = self.client.task_get(7)

        self.assertEqual(result["task_id"], 7)
        self.assertEqual(TestHandler.requests[0][1], "/task_get?task_id=7")

    def test_task_list_today_uses_today_endpoint(self):
        result = self.client.task_list_today()

        self.assertEqual(result["tasks"][0]["task_id"], 7)
        self.assertEqual(TestHandler.requests[0][1], "/task_list_today")

    def test_task_list_tomorrow_uses_tomorrow_endpoint(self):
        result = self.client.task_list_tomorrow()

        self.assertEqual(result["tasks"][0]["task_id"], 7)
        self.assertEqual(TestHandler.requests[0][1], "/task_list_tomorrow")

    def test_task_create_sends_flags_and_due_time(self):
        result = self.client.task_create(
            "Test", "Description", is_asap=True, due_at="2026-08-26T14:30"
        )

        self.assertEqual(result["task_id"], 8)
        request = TestHandler.requests[0]
        self.assertEqual((request[0], request[1]), ("POST", "/task_create"))
        self.assertEqual(request[3]["title"], "Test")
        self.assertTrue(request[3]["is_asap"])
        self.assertTrue(request[3]["has_time"])


if __name__ == "__main__":
    unittest.main()
