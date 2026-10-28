#!/usr/bin/env python3
"""Reconcile only hash-labelled Vipen rules. No UFW reset or unowned rule deletion."""

import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

DEADLINE = None


def desired_rules(config):
    rules = []
    for cidr in config["ssh_cidrs"]:
        network = str(ipaddress.ip_network(cidr, strict=False))
        rules.append(
            [
                "allow",
                "from",
                network,
                "to",
                "any",
                "port",
                str(config["ssh_port"]),
                "proto",
                "tcp",
            ]
        )
    for protocol in ("tcp", "udp"):
        for port in sorted(set(config[protocol])):
            if not 1 <= int(port) <= 65535:
                raise ValueError("Invalid port")
            rules.append(["allow", f"{int(port)}/{protocol}"])
    rules.append(["deny", f"{int(config['panel_port'])}/tcp"])
    return {
        "vipen-" + hashlib.sha256(json.dumps(r).encode()).hexdigest()[:12]: r
        for r in rules
    }


def owned_rules(status):
    return [
        (int(n), key)
        for n, key in re.findall(
            r"^\[\s*(\d+)\].*#\s*(vipen-[0-9a-f]{12})\s*$", status, re.M
        )
    ]


def command(args):
    remaining = min(30, DEADLINE - time.monotonic()) if DEADLINE else 30
    if remaining <= 0:
        raise subprocess.TimeoutExpired("ufw reconciliation", 90)
    return subprocess.run(
        ["ufw", *args], check=True, text=True, capture_output=True, timeout=remaining
    ).stdout


def reconcile(
    cfg,
    config_dir=Path("/etc/ufw"),
    backup_root=Path("/var/lib/vipen/firewall-backups"),
    default_file=Path("/etc/default/ufw"),
):
    global DEADLINE
    DEADLINE = time.monotonic() + 90
    rules = desired_rules(cfg)
    status = command(["status", "numbered"])
    # Old Vipen comments are generic and cannot safely be distinguished from
    # another administrator's rules. Refuse rather than guess ownership.
    if re.search(r"#\s*(ssh|vpn|xui-panel)\s*$", status, re.M):
        raise ValueError("Legacy rules need reviewed migration; see docs/migration.md")
    active = "Status: active" in status
    if not active and not cfg["manage_defaults"]:
        raise ValueError(
            "UFW is inactive; explicitly enable firewall_manage_defaults for a dedicated host"
        )
    current = owned_rules(status)
    existing = {key for _, key in current}
    missing = [(key, args) for key, args in rules.items() if key not in existing]
    stale = [(n, key) for n, key in current if key not in rules]
    policy_change = cfg[
        "manage_defaults"
    ] and "Default: deny (incoming), allow (outgoing)" not in command(
        ["status", "verbose"]
    )
    changed = bool(missing or stale or not active or policy_change)
    if cfg.get("check") or not changed:
        return {"changed": changed}
    root = backup_root
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="rules-", dir=root))
    shutil.copytree(config_dir, backup / "ufw", symlinks=True)
    if default_file.exists():
        shutil.copy2(default_file, backup / "ufw.defaults")
    (backup / "state.json").write_text(json.dumps({"active": active}))
    # The watchdog survives loss of the Ansible SSH connection. Only a verified
    # fresh connection in the role is allowed to cancel it.
    subprocess.run(
        [
            "systemd-run",
            "--unit=vipen-firewall-rollback",
            "--on-active=180s",
            "--property=Type=oneshot",
            "--collect",
            sys.executable,
            str(Path(__file__).resolve()),
            "--rollback",
            str(backup),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    try:
        # Add the complete new access set BEFORE removing obsolete rules.
        for key, args in missing:
            command([*args, "comment", key])
        # Re-read numbers: UFW can add an IPv6 companion and renumber entries.
        for number, _ in sorted(
            owned_rules(command(["status", "numbered"])), reverse=True
        ):
            now = dict(owned_rules(command(["status", "numbered"])))
            if number in now and now[number] not in rules:
                command(["--force", "delete", str(number)])
        if policy_change:
            command(["default", "deny", "incoming"])
            command(["default", "allow", "outgoing"])
        if not active:
            command(["--force", "enable"])
        if "Status: active" not in command(["status"]):
            raise ValueError("UFW did not become active")
        return {"changed": changed, "recovery_directory": str(backup)}
    except Exception:
        DEADLINE = time.monotonic() + 30
        if (backup / "ufw.defaults").exists():
            shutil.copy2(backup / "ufw.defaults", default_file)
        shutil.copytree(backup / "ufw", config_dir, symlinks=True, dirs_exist_ok=True)
        command(["reload"] if active else ["--force", "disable"])
        raise


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--rollback":
        backup = Path(sys.argv[2]).resolve()
        if not backup.is_relative_to("/var/lib/vipen/firewall-backups"):
            raise ValueError("Invalid recovery directory")
        state = json.loads((backup / "state.json").read_text())
        if (backup / "ufw.defaults").exists():
            shutil.copy2(backup / "ufw.defaults", "/etc/default/ufw")
        shutil.copytree(backup / "ufw", "/etc/ufw", symlinks=True, dirs_exist_ok=True)
        command(["reload"] if state["active"] else ["--force", "disable"])
        print("Firewall watchdog restored the previous rules")
    else:
        print(json.dumps(reconcile(json.load(sys.stdin))))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(
            f"Firewall change refused or rollback requested: {type(error).__name__}. Inspect recovery files and watchdog status.",
            file=sys.stderr,
        )
        raise SystemExit(1)
