#!/usr/bin/env python3

import argparse
import configparser
import json
import secrets
import shlex
import string
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


TAG = "wingman"
IMAGE = "ubuntu-24-04-x64"
REGIONS = {"FRA": "fra1", "AMS": "ams3", "NYC": "nyc1"}
SIZES = {
    "512MB": "s-1vcpu-512mb-10gb",
    "1GB": "s-1vcpu-1gb",
    "2GB": "s-1vcpu-2gb",
}


class APIError(Exception):
    pass


class DigitalOceanClient:
    def __init__(self, base_url, token, timeout=20, ssh_key_names=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.ssh_key_names = ssh_key_names or []

    def request(self, path, method="GET", payload=None):
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
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
                error_data = json.loads(response_body)
                message = error_data.get("message") or error_data.get("id")
            except json.JSONDecodeError:
                message = None
            raise APIError(
                message or f"DigitalOcean API request failed with HTTP {error.code}"
            ) from error
        except urllib.error.URLError as error:
            raise APIError(f"Could not reach DigitalOcean API: {error.reason}") from error

        if not response_body:
            return {}
        try:
            return json.loads(response_body)
        except json.JSONDecodeError as error:
            raise APIError("DigitalOcean API returned invalid JSON") from error

    def _list_droplets_by_type(self, droplet_type, name=None):
        droplets = []
        page = 1
        while True:
            query = {"page": page, "per_page": 200, "type": droplet_type}
            if name:
                query["name"] = name
            data = self.request("/droplets?" + urllib.parse.urlencode(query))
            batch = data.get("droplets")
            if not isinstance(batch, list):
                raise APIError("DigitalOcean API response did not contain a droplet list")
            droplets.extend(batch)
            if len(batch) < 200:
                return droplets
            page += 1

    def list_droplets(self, tag=None, name=None):
        if tag and name:
            raise APIError("tag and name filters cannot be combined")
        droplets = self._list_droplets_by_type("droplets", name=name)
        droplets.extend(self._list_droplets_by_type("gpus", name=name))
        if tag:
            droplets = [
                droplet for droplet in droplets if tag in droplet.get("tags", [])
            ]
        return droplets

    def list_ssh_keys(self):
        ssh_keys = []
        page = 1
        while True:
            query = urllib.parse.urlencode({"page": page, "per_page": 200})
            data = self.request("/account/keys?" + query)
            batch = data.get("ssh_keys")
            if not isinstance(batch, list):
                raise APIError("DigitalOcean API response did not contain an SSH key list")
            ssh_keys.extend(batch)
            if len(batch) < 200:
                return ssh_keys
            page += 1

    def resolve_ssh_key_ids(self):
        if not self.ssh_key_names:
            return []

        ids_by_name = {}
        for ssh_key in self.list_ssh_keys():
            name = ssh_key.get("name")
            key_id = ssh_key.get("id")
            if isinstance(name, str) and isinstance(key_id, int):
                ids_by_name.setdefault(name, []).append(key_id)

        missing = [name for name in self.ssh_key_names if name not in ids_by_name]
        if missing:
            raise APIError("DigitalOcean SSH key not found: " + ", ".join(missing))
        return [
            key_id
            for name in self.ssh_key_names
            for key_id in ids_by_name[name]
        ]

    def create_droplet(self, region, size, name):
        payload = {
            "name": name,
            "region": region,
            "size": size,
            "image": IMAGE,
            "backups": False,
            "tags": [TAG],
        }
        ssh_key_ids = self.resolve_ssh_key_ids()
        if ssh_key_ids:
            payload["ssh_keys"] = ssh_key_ids
        data = self.request("/droplets", method="POST", payload=payload)
        droplet = data.get("droplet")
        if not isinstance(droplet, dict):
            raise APIError("DigitalOcean API response did not contain the created droplet")
        return droplet

    def delete_droplet_by_name(self, name):
        matches = [
            droplet
            for droplet in self.list_droplets(name=name)
            if str(droplet.get("name", "")).casefold() == name.casefold()
        ]
        if not matches:
            raise APIError(f"VM not found: {name}")
        if len(matches) > 1:
            raise APIError(f"More than one VM is named {name}; refusing to delete")
        droplet_id = matches[0].get("id")
        if droplet_id is None:
            raise APIError(f"VM {name} has no ID")
        self.request(f"/droplets/{droplet_id}", method="DELETE")
        return {"deleted": name, "id": droplet_id}


def load_client(config_path=None):
    path = Path(config_path) if config_path else Path(__file__).with_name("config.toml")
    parser = configparser.ConfigParser()
    try:
        with path.open("r", encoding="utf-8") as config_file:
            parser.read_file(config_file)
        config = parser["digitalocean_plugin"]
    except FileNotFoundError as error:
        raise APIError(f"Config file not found: {path}") from error
    except (KeyError, configparser.Error) as error:
        raise APIError(f"Invalid config file: {path}") from error

    secret_path_value = config.get("secret_path", "").strip().strip('"\'')
    if not secret_path_value:
        raise APIError("secret_path is missing from config.toml")
    secret_path = Path(secret_path_value).expanduser()
    try:
        token = secret_path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, IsADirectoryError) as error:
        raise APIError(f"API token file not found: {secret_path}") from error
    if not token:
        raise APIError(f"API token file is empty: {secret_path}")

    base_url = config.get("base_url", "").strip().strip('"\'')
    if not base_url:
        raise APIError("base_url is missing from config.toml")
    try:
        timeout = config.getint("timeout_seconds", fallback=20)
    except ValueError as error:
        raise APIError("timeout_seconds must be an integer") from error
    if timeout <= 0:
        raise APIError("timeout_seconds must be greater than zero")
    raw_ssh_keys = config.get("ssh_keys", "[]").strip()
    try:
        ssh_key_names = json.loads(raw_ssh_keys)
    except json.JSONDecodeError as error:
        raise APIError("ssh_keys must be a JSON array of SSH key names") from error
    if not isinstance(ssh_key_names, list) or any(
        not isinstance(name, str) or not name.strip() for name in ssh_key_names
    ):
        raise APIError("ssh_keys must be a JSON array of non-empty SSH key names")
    ssh_key_names = [name.strip() for name in ssh_key_names]
    return DigitalOceanClient(base_url, token, timeout, ssh_key_names)


def normalize_droplet(droplet):
    public_ips = [
        network.get("ip_address")
        for network in droplet.get("networks", {}).get("v4", [])
        if network.get("type") == "public" and network.get("ip_address")
    ]
    region = droplet.get("region") or {}
    image = droplet.get("image") or {}
    return {
        "id": droplet.get("id"),
        "name": droplet.get("name"),
        "status": droplet.get("status"),
        "region": region.get("slug") if isinstance(region, dict) else region,
        "size": droplet.get("size_slug") or droplet.get("size"),
        "image": image.get("slug") if isinstance(image, dict) else image,
        "public_ips": public_ips,
        "tags": droplet.get("tags", []),
    }


def generate_name():
    alphabet = string.ascii_lowercase + string.digits
    return "wing-" + "".join(secrets.choice(alphabet) for _ in range(4))


def build_parser():
    parser = argparse.ArgumentParser(description="Manage DigitalOcean VMs")
    commands = parser.add_subparsers(dest="command", required=True)

    create_parser = commands.add_parser("create", help="Create an Ubuntu 24.04 VM")
    create_parser.add_argument("location", help="FRA, AMS, or NYC")
    create_parser.add_argument("flavor", help="512MB, 1GB, or 2GB")

    list_parser = commands.add_parser("list", help="List VMs")
    list_parser.add_argument(
        "scope", nargs="?", choices=(TAG,), help="Only list VMs tagged wingman"
    )

    delete_parser = commands.add_parser("delete", help="Delete a VM by exact name")
    delete_parser.add_argument("name", help="VM name")
    return parser


def parse_args(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) == 1:
        try:
            arguments = shlex.split(arguments[0])
        except ValueError as error:
            raise APIError(f"Invalid command: {error}") from error
    return build_parser().parse_args(arguments)


def main(argv=None):
    try:
        args = parse_args(argv)
        client = load_client()
        if args.command == "create":
            location = args.location.upper()
            flavor = args.flavor.upper().replace(" ", "")
            if location not in REGIONS:
                raise APIError("location must be FRA, AMS, or NYC")
            if flavor not in SIZES:
                raise APIError("flavor must be 512MB, 1GB, or 2GB")
            result = {
                "accepted": normalize_droplet(
                    client.create_droplet(REGIONS[location], SIZES[flavor], generate_name())
                ),
                "message": "DigitalOcean accepted the VM for asynchronous provisioning",
            }
        elif args.command == "list":
            result = {
                "vms": [
                    normalize_droplet(droplet)
                    for droplet in client.list_droplets(tag=args.scope)
                ]
            }
        else:
            result = client.delete_droplet_by_name(args.name)
        print(json.dumps(result, indent=2))
        return 0
    except APIError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
