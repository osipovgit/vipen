[Русский](README.md) | [English](README.en_EN.md)

# Vipen

Ansible pipeline that deploys [3x-ui](https://github.com/MHSanaei/3x-ui) (management panel + Xray VPN server) on a Debian/Ubuntu host.

The pipeline **does not create VPN connections or users** — you add them in the panel manually or restore them from a database backup.

## What it configures

- 🔒 **Server hardening** — SSH key-only access, UFW, fail2ban, panel closed to the outside (access via SSH tunnel).
- 🐳 **Docker environment** — log rotation, `live-restore`, database snapshot before updates.
- ⚡ **Network optimization** — `fq` + BBR, increased `nf_conntrack_max`.
- 💾 **Backups** — copy of `x-ui.db` to your local machine, restore on a new server.
- 📊 **Monitoring** — health check every minute, signs of hypervisor overload (CPU steal, VM latency), tools for manual diagnostics.

## Requirements

- **Target server:** Debian/Ubuntu with `root` access via SSH key.
- **Machine you run it from:** Ansible (core ≥ 2.16), Python 3. For remote runs — an SSH key to access the server.

## Quick start

The pipeline can be run in two ways: remotely over SSH from your machine, or locally on the server itself.

Pipeline parameters live in a single file — `inventories/production/group_vars/vpn_servers.yml`; server connection details are set in the inventory.

Do not put client UUIDs, REALITY keys, or subscription links into `group_vars` — they belong in the database, and a public fork would leak them.

**Run all commands from the repository root.**

### Scenario 1. Remotely over SSH

```bash
cp inventories/production/hosts.example.yml \
   inventories/production/hosts.yml
# add the server IP and the path to your SSH key to hosts.yml, then:
ansible-playbook site.yml --check --diff    # preview (dry run)
ansible-playbook site.yml                   # apply
```

See [“Before the first run”](docs/setup.md#перед-первым-запуском) and [“Applying”](docs/setup.md#применение) for details.

### Scenario 2. Locally on the server

Requires `ansible-core` and `python3` installed on the server; run as root.

```bash
cp inventories/production/hosts.local.example.yml \
   inventories/production/hosts.local.yml
# add your public SSH key to hosts.local.yml (ssh_authorized_keys), then:
ansible-playbook -i inventories/production/hosts.local.yml site.yml --check --diff  # preview (dry run)
ansible-playbook -i inventories/production/hosts.local.yml site.yml                 # apply
```

In this case the `x-ui.db` backup stays on the server itself — there will be no separate off-host copy.

See [“Running locally (on the server itself)”](docs/setup.md#локальный-запуск-на-самом-сервере) for details.

## Documentation

**Full setup, roles, and scenarios guide — in [`docs/setup.md`](docs/setup.md).**

It covers: pipeline parameters, panel access, updating and rolling back 3x-ui, restoring on a new server, monitoring and metrics, UFW and SSH.

Working conventions and diagnostic scenarios — in [`AGENTS.md`](AGENTS.md).
