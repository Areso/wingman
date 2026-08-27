import json
import re
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from digitalocean_vms import (
    APIError,
    DigitalOceanClient,
    generate_name,
    normalize_droplet,
    parse_args,
)


class TestHandler(BaseHTTPRequestHandler):
    requests = []
    droplets = [
        {
            "id": 10,
            "name": "wing-a1b2",
            "status": "active",
            "region": {"slug": "fra1"},
            "size_slug": "s-1vcpu-1gb",
            "image": {"slug": "ubuntu-24-04-x64"},
            "networks": {
                "v4": [
                    {"type": "private", "ip_address": "10.0.0.2"},
                    {"type": "public", "ip_address": "192.0.2.10"},
                ]
            },
            "tags": ["wingman"],
        },
        {"id": 11, "name": "other", "tags": []},
    ]
    gpu_droplets = [{"id": 20, "name": "gpu-worker", "tags": ["gpu"]}]
    ssh_keys = [
        {"id": 101, "name": "areso-dell11"},
        {"id": 102, "name": "areso-mac"},
    ]

    def do_GET(self):
        self.__class__.requests.append(("GET", self.path, None, self.headers))
        if self.path.startswith("/account/keys?"):
            self.respond({"ssh_keys": self.ssh_keys})
            return
        droplets = self.droplets
        if "type=gpus" in self.path:
            droplets = self.gpu_droplets
        if "name=wing-a1b2" in self.path:
            droplets = [droplet for droplet in droplets if droplet["name"] == "wing-a1b2"]
        self.respond({"droplets": droplets})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.__class__.requests.append(("POST", self.path, body, self.headers))
        self.respond({"droplet": {"id": 12, **body}}, status=202)

    def do_DELETE(self):
        self.__class__.requests.append(("DELETE", self.path, None, self.headers))
        self.send_response(204)
        self.end_headers()

    def respond(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class DigitalOceanClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), TestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = DigitalOceanClient(
            f"http://127.0.0.1:{cls.server.server_port}",
            "test-token",
            ssh_key_names=["areso-dell11", "areso-mac"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def setUp(self):
        TestHandler.requests.clear()

    def test_list_can_filter_by_wingman_tag(self):
        droplets = self.client.list_droplets("wingman")

        self.assertEqual([droplet["name"] for droplet in droplets], ["wing-a1b2"])
        self.assertIn("type=droplets", TestHandler.requests[0][1])
        self.assertIn("type=gpus", TestHandler.requests[1][1])
        self.assertEqual(TestHandler.requests[0][3]["Authorization"], "Bearer test-token")

    def test_list_combines_cpu_and_gpu_droplets(self):
        droplets = self.client.list_droplets()

        self.assertEqual(
            [droplet["name"] for droplet in droplets],
            ["wing-a1b2", "other", "gpu-worker"],
        )

    def test_create_uses_requested_shape_and_fixed_image_and_tag(self):
        droplet = self.client.create_droplet("ams3", "s-1vcpu-2gb", "wing-z9y8")

        self.assertEqual(droplet["id"], 12)
        self.assertTrue(TestHandler.requests[0][1].startswith("/account/keys?"))
        payload = TestHandler.requests[1][2]
        self.assertEqual(payload["name"], "wing-z9y8")
        self.assertEqual(payload["region"], "ams3")
        self.assertEqual(payload["size"], "s-1vcpu-2gb")
        self.assertEqual(payload["image"], "ubuntu-24-04-x64")
        self.assertEqual(payload["tags"], ["wingman"])
        self.assertEqual(payload["ssh_keys"], [101, 102])

    def test_create_fails_before_post_when_named_ssh_key_is_missing(self):
        client = DigitalOceanClient(
            f"http://127.0.0.1:{self.server.server_port}",
            "test-token",
            ssh_key_names=["missing-key"],
        )

        with self.assertRaisesRegex(APIError, "DigitalOcean SSH key not found: missing-key"):
            client.create_droplet("fra1", "s-1vcpu-512mb-10gb", "wing-a2b3")

        self.assertEqual([request[0] for request in TestHandler.requests], ["GET"])

    def test_delete_resolves_exact_name_then_deletes_id(self):
        result = self.client.delete_droplet_by_name("wing-a1b2")

        self.assertEqual(result, {"deleted": "wing-a1b2", "id": 10})
        self.assertIn("name=wing-a1b2", TestHandler.requests[0][1])
        self.assertEqual(TestHandler.requests[-1][0:2], ("DELETE", "/droplets/10"))

    def test_normalize_droplet_only_returns_public_ips(self):
        result = normalize_droplet(TestHandler.droplets[0])

        self.assertEqual(result["public_ips"], ["192.0.2.10"])
        self.assertEqual(result["region"], "fra1")

    def test_generated_name_has_wing_prefix_and_four_alphanumeric_characters(self):
        self.assertIsNotNone(re.fullmatch(r"wing-[a-z0-9]{4}", generate_name()))

    def test_wingman_single_argument_is_split_into_command_arguments(self):
        args = parse_args(["create FRA 512MB"])

        self.assertEqual(args.command, "create")
        self.assertEqual(args.location, "FRA")
        self.assertEqual(args.flavor, "512MB")

    def test_plugin_registration_exposes_all_operations_to_owner(self):
        plugin_path = Path(__file__).with_name("plugin.json")
        plugin = json.loads(plugin_path.read_text(encoding="utf-8"))

        self.assertEqual(plugin["invocation_file"], "digitalocean_vms.py")
        self.assertEqual(plugin["min_allowed_role"], "owner")
        self.assertTrue(plugin["adhoc"])
        self.assertTrue(plugin["user_input"])
        self.assertIn("create FRA 512MB", plugin["options"])
        self.assertIn("create AMS 1GB", plugin["options"])
        self.assertIn("create NYC 2GB", plugin["options"])
        self.assertIn("list wingman", plugin["options"])


if __name__ == "__main__":
    unittest.main()
