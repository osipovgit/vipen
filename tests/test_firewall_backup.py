"""Real recovery archives and scoped firewall reconciliation with mocked UFW/systemd."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_database import fixture, xui_db
import xui_backup

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ufw_rules", ROOT / "roles/firewall/files/ufw_rules.py"
)
ufw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ufw)


class FirewallTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "ssh_port": 22,
            "ssh_cidrs": ["192.0.2.0/24", "2001:db8::/32"],
            "tcp": [443],
            "udp": [],
            "panel_port": 54836,
            "manage_defaults": False,
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config_dir = self.root / "ufw"
        self.config_dir.mkdir()
        self.defaults = self.root / "default.ufw"
        self.defaults.write_text("previous defaults")
        (self.config_dir / "user.rules").write_text("previous rules")
        self.commands = []
        self.rows = [(1, "unowned-manual-rule"), (2, "vipen-000000000000")]
        self.watchdog = False
        self.fail_add = False

    def command(self, args):
        self.commands.append(args)
        if args == ["status", "verbose"]:
            return "Default: deny (incoming), allow (outgoing)"
        if args[0] == "status":
            return "Status: active\n" + "\n".join(
                f"[{n}] fake rule # {key}" for n, key in self.rows
            )
        if "comment" in args:
            self.assertTrue(
                self.watchdog, "Recovery timer must exist before UFW mutations"
            )
            if self.fail_add:
                (self.config_dir / "user.rules").write_text("partial change")
                raise subprocess.CalledProcessError(1, "ufw")
            self.rows.append((max(n for n, _ in self.rows) + 1, args[-1]))
        if args[:2] == ["--force", "delete"]:
            number = int(args[2])
            self.rows = [(n, k) for n, k in self.rows if n != number]
        return ""

    def schedule(self, args, **kwargs):
        self.assertEqual(args[0], "systemd-run")
        self.assertIn("--rollback", args)
        self.watchdog = True
        return subprocess.CompletedProcess(args, 0, "", "")

    def test_legacy_rules_are_refused_without_any_mutation(self):
        with patch.object(
            ufw, "command", return_value="Status: active\n[1] 22/tcp ALLOW # ssh"
        ), patch.object(ufw.subprocess, "run") as process:
            with self.assertRaises(ValueError):
                ufw.reconcile(
                    self.config, self.config_dir, self.root / "backups", self.defaults
                )
            process.assert_not_called()
        self.assertEqual((self.config_dir / "user.rules").read_text(), "previous rules")

    def test_only_owned_stale_rules_removed_after_new_ssh_rules(self):
        with patch.object(ufw, "command", self.command), patch.object(
            ufw.subprocess, "run", self.schedule
        ):
            result = ufw.reconcile(
                self.config, self.config_dir, self.root / "backups", self.defaults
            )
        self.assertTrue(result["changed"])
        self.assertIn((1, "unowned-manual-rule"), self.rows)
        self.assertNotIn((2, "vipen-000000000000"), self.rows)
        delete_index = next(
            i for i, a in enumerate(self.commands) if a[:2] == ["--force", "delete"]
        )
        ssh_indexes = [i for i, a in enumerate(self.commands) if "from" in a]
        self.assertTrue(ssh_indexes and max(ssh_indexes) < delete_index)
        self.assertFalse(any("reset" in args for args in self.commands))

    def test_unchanged_rules_do_not_create_backups_or_watchdogs(self):
        self.rows = [(i + 1, k) for i, k in enumerate(ufw.desired_rules(self.config))]
        with patch.object(ufw, "command", self.command), patch.object(
            ufw.subprocess, "run"
        ) as process:
            result = ufw.reconcile(
                self.config, self.config_dir, self.root / "backups", self.defaults
            )
            process.assert_not_called()
        self.assertFalse(result["changed"])
        self.assertFalse((self.root / "backups").exists())

    def test_rule_failure_restores_configuration_and_reloads(self):
        self.fail_add = True
        with patch.object(ufw, "command", self.command), patch.object(
            ufw.subprocess, "run", self.schedule
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                ufw.reconcile(
                    self.config, self.config_dir, self.root / "backups", self.defaults
                )
        self.assertEqual((self.config_dir / "user.rules").read_text(), "previous rules")
        self.assertEqual(self.commands[-1], ["reload"])

    def test_failed_watchdog_setup_prevents_firewall_mutation(self):
        with patch.object(ufw, "command", self.command), patch.object(
            ufw.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "systemd-run"),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                ufw.reconcile(
                    self.config, self.config_dir, self.root / "backups", self.defaults
                )
        self.assertTrue(all(args[0] == "status" for args in self.commands))

    def test_invalid_cidr_is_rejected(self):
        self.config["ssh_cidrs"] = ["not-a-network"]
        with self.assertRaises(ValueError):
            ufw.desired_rules(self.config)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "cert").mkdir()
        (self.root / "cert/test.key").write_text("synthetic-key")
        (self.root / "compose.yml").write_text("previous compose")
        fixture(self.root / "x-ui.db")
        self.config = {
            "database": str(self.root / "x-ui.db"),
            "backup_dir": str(self.root / "backups"),
            "cert_dir": str(self.root / "cert"),
            "compose": str(self.root / "compose.yml"),
            "container": "test",
            "keep": 2,
        }
        self.inspect = subprocess.CompletedProcess(
            [],
            0,
            json.dumps([{"Image": "sha256:old-image", "Config": {"Image": "old-tag"}}]),
            "",
        )

    def test_complete_private_bundle_and_rotation_preserve_manual_recovery(self):
        with patch.object(xui_backup.subprocess, "run", return_value=self.inspect):
            first = xui_backup.create(self.config)
            manual = self.root / "backups/manual.db"
            manual.write_text("manual")
            xui_backup.create(self.config)
            result = xui_backup.create(self.config)
        with tarfile.open(result["bundle"]) as bundle:
            self.assertTrue(
                {"x-ui.db", "docker-compose.yml", "cert/test.key", "manifest.json"}
                <= set(bundle.getnames())
            )
            manifest = json.load(bundle.extractfile("manifest.json"))
            self.assertEqual(manifest["image_id"], "sha256:old-image")
        self.assertEqual(Path(result["bundle"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list((self.root / "backups").glob("x-ui.db.auto-*"))), 2)
        self.assertEqual(len(list((self.root / "backups").glob("recovery.auto-*"))), 2)
        self.assertFalse(Path(first["database"]).exists())
        self.assertTrue(manual.exists())

    def test_docker_metadata_failure_publishes_no_incomplete_backup(self):
        with patch.object(
            xui_backup.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("docker", 15),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                xui_backup.create(self.config)
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_invalid_keep_is_rejected_before_rotation(self):
        self.config["keep"] = 0
        with patch.object(xui_backup.subprocess, "run", return_value=self.inspect):
            with self.assertRaises(ValueError):
                xui_backup.create(self.config)
        # The function must validate configuration before creating or pruning files.
        self.assertFalse((self.root / "backups").exists())
