# DigitalOcean VM plugin

This owner-only plugin manages DigitalOcean Droplets through the DigitalOcean
JSON API using only the Python 3.9+ standard library.

New VMs use Ubuntu 24.04 LTS, receive the `wingman` tag, and are named
`wing-****`, where each `*` is a lowercase letter or number. Available locations
are FRA (`fra1`), AMS (`ams3`), and NYC (`nyc1`). Available sizes are 512 MB,
1 GB, and 2 GB, all with one vCPU.

## Configuration

Put a DigitalOcean API token in:

```text
~/.wingman/plugins/digitalocean
```

The token must be the only content in the file. A custom-scoped token needs
`droplet:read`, `droplet:create`, `droplet:delete`, `image:read`, `tag:read`, and
`tag:create`, and `ssh_key:read`.

Configure SSH keys by their DigitalOcean display names in `config.toml`:

```toml
ssh_keys = ["areso-dell11"]
```

Before creating a VM, the plugin lists the account's SSH keys, resolves every
configured name, and sends the corresponding numeric IDs to DigitalOcean. More
than one name can be configured. Creation stops with an explicit error if any
name cannot be found. Set `ssh_keys = []` to create without an SSH key.

## Commands

```bash
python3 digitalocean_vms.py create FRA 512MB
python3 digitalocean_vms.py create AMS 1GB
python3 digitalocean_vms.py create NYC 2GB
python3 digitalocean_vms.py list
python3 digitalocean_vms.py list wingman
python3 digitalocean_vms.py delete wing-a1b2
```

The Telegram channel presents the create and list commands as buttons. Choose
`Custom input` and enter `delete wing-a1b2` to delete a VM by its exact name.
Droplet creation is asynchronous: a successful create response means
DigitalOcean accepted provisioning, not that the VM is active yet. Use `list`
to check its status and public IP. All successful output is JSON on standard
output. Errors are JSON on standard error and exit with status 1.
