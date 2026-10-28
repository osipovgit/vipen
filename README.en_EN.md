[Русский](README.md) | [English](README.en_EN.md)

# Vipen

[![CI](https://github.com/osipovgit/vipen/actions/workflows/ci.yml/badge.svg)](https://github.com/osipovgit/vipen/actions/workflows/ci.yml)

Ansible deployment and monitoring for [3x-ui](https://github.com/MHSanaei/3x-ui) on Debian/Ubuntu. Create VPN inbounds and clients through the panel or restore an existing database.

The default mode updates the application on an already prepared host. SSH, UFW, Docker Engine and host configuration require `vipen_manage_host: true`. OS upgrades and kernel tuning are separate opt-ins.

## Deployment properties

- 3x-ui **3.11.0**, pinned by tag and OCI digest. A database copy is migrated in a network-disabled container before stopping the service.
- A recovery directory retains the previous database, Compose file, certificates and local image ID. Failed deployment attempts restore the previous database/image pair. Updates require a short maintenance window.
- The panel binds to loopback. Fresh installations require explicit administrator credentials and start with subscriptions disabled. Public subscriptions require TLS.
- Existing REALITY settings are preserved. An override must select exactly one inbound; ambiguous port selectors fail atomically.
- UFW is never reset. Only labelled Vipen rules are reconciled, with a systemd rollback watchdog and a fresh SSH connection check.
- Daily consistent database snapshots and recovery archives. Remote full playbook runs fetch a pre-update archive to the controller; scheduled backups remain on the server.
- Monitoring discovers enabled listeners from the database, bounds external commands and reports unavailable data. Telegram alerts are optional.

## Requirements and use

Controller: Python 3.12–3.14 and `ansible-core==2.21.5` (CI uses Python 3.14). Target: key-based root access, Python 3, systemd, Debian 12/13 or Ubuntu 24.04/26.04 on amd64/arm64. See [test coverage](docs/testing.md) before assuming platform certification.

Read the [migration guide](docs/migration.md) before upgrading an existing server, or [setup guide](docs/setup.md) for a new host. Detailed operational documentation is currently in Russian.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp inventories/production/hosts.example.yml inventories/production/hosts.yml
cp inventories/production/secrets.example.yml inventories/production/secrets.yml
# Fill both files, then encrypt the secrets:
ansible-vault encrypt inventories/production/secrets.yml
ansible-playbook site.yml --syntax-check --ask-vault-pass
ansible-playbook site.yml --check --ask-vault-pass
# After isolated rehearsal and a reviewed recovery plan:
ansible-playbook site.yml --ask-vault-pass --limit vipen
```

Check mode does not perform image pulls, migrations, restoration or network changes. Applying without a real secrets file is refused. Keep databases, client identifiers, private keys and subscription URLs out of Git and public issues.

See [contributing](CONTRIBUTING.md), [security](SECURITY.md), [changelog](CHANGELOG.md), [review fixes](docs/review-fixes-2026-10-26.md) and [MIT license](LICENSE).

Local port/process checks cannot prove a working client handshake. Production operation also needs an external VPN probe, missed-heartbeat alerting and tested off-host backups; these are configured separately.
