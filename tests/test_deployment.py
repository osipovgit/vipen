"""Exercise the real deployment transaction with a stateful Docker boundary double."""

import copy
import json
from pathlib import Path
import shutil
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from test_database import fixture, xui_db
import xui_deploy


class SimulatedDeployment(xui_deploy.Deployment):
    def __init__(self, config, fail_at=None):
        super().__init__(config)
        self.fail_at = fail_at
        self.commands = []
        self.migrations = 0
        self.state = {
            "Id": "original-container",
            "Image": "sha256:old",
            "State": {"Running": True},
            "Config": {
                "Labels": {
                    "com.docker.compose.project": config["project"],
                    "com.docker.compose.service": "3xui",
                }
            },
            "Mounts": [
                {"Destination": "/etc/x-ui", "Source": config["db_dir"]},
                {"Destination": "/root/cert", "Source": config["cert_dir"]},
            ],
        }

    def container(self):
        return copy.deepcopy(self.state)

    def run(self, args, **kwargs):
        args = [str(a) for a in args]
        self.commands.append(args)
        if "pull" in args and self.fail_at == "pull":
            raise xui_deploy.Refused("simulated pull failure")
        if args[:3] == ["docker", "image", "inspect"]:
            return CompletedProcess(args, 0, json.dumps([{"Id": "sha256:new"}]), "")
        if "stop" in args:
            self.state["State"]["Running"] = False
        if "up" in args:
            self.state["Id"] = "replacement-container"
            self.state["Image"] = (
                "sha256:old"
                if any("rollback-image.json" in a for a in args)
                else "sha256:new"
            )
            self.state["State"]["Running"] = True
        if args[:2] == ["docker", "start"]:
            self.state["State"]["Running"] = True
        return CompletedProcess(args, 0, "", "")

    def migrate(self, stage, fresh=False):
        self.migrations += 1
        if self.fail_at == "trial" or (
            self.fail_at == "migration" and self.migrations == 2
        ):
            raise xui_deploy.Refused("simulated migration failure")
        if fresh:
            fixture(stage / "x-ui.db")
        # Simulate an irreversible schema/data migration in the *staging* file.
        with xui_db.connect(stage / "x-ui.db", readonly=False) as db:
            db.execute("INSERT INTO settings VALUES ('migration-probe','new-format')")

    def healthy(self, port):
        if self.fail_at == "health" and self.state["Image"] == "sha256:new":
            raise xui_deploy.Refused("simulated unhealthy new image")


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("db", "cert", "backups"):
            (self.root / name).mkdir()
        self.database = self.root / "db/x-ui.db"
        fixture(self.database)
        self.compose = self.root / "docker-compose.yml"
        self.compose.write_text("previous compose")
        (self.root / "cert/tls.key").write_text("synthetic-certificate")
        self.original = self.database.read_bytes()
        self.config = {
            "directory": str(self.root),
            "db_dir": str(self.root / "db"),
            "cert_dir": str(self.root / "cert"),
            "backup_dir": str(self.root / "backups"),
            "project": "vipen",
            "container": "3xui_app",
            "image": "new-pinned-image",
            "compose": "new compose",
            "ssh_port": 22,
            "settings": {"listen": "127.0.0.1", "port": 54836, "reality_overrides": []},
        }

    def assert_old(self, deployment):
        self.assertEqual(self.compose.read_text(), "previous compose")
        self.assertEqual(deployment.state["Image"], "sha256:old")
        self.assertTrue(deployment.state["State"]["Running"])
        with xui_db.connect(self.database) as db:
            self.assertIsNone(
                db.execute(
                    "SELECT value FROM settings WHERE key='migration-probe'"
                ).fetchone()
            )

    def commit_before_stop(self, deployment):
        """Simulate a client update committed after hot backup, before writers stop."""
        run = deployment.run

        def late_write(args, **kwargs):
            if "stop" in args and not any(
                "stop" in command for command in deployment.commands
            ):
                with xui_db.connect(self.database, readonly=False) as db:
                    db.execute("INSERT INTO settings VALUES ('late-write','keep-me')")
            return run(args, **kwargs)

        deployment.run = late_write

    def assert_late_write(self, path):
        with xui_db.connect(path) as db:
            self.assertEqual(
                db.execute(
                    "SELECT value FROM settings WHERE key='late-write'"
                ).fetchone(),
                ("keep-me",),
            )

    def test_pull_failure_does_not_stop_or_write_database(self):
        deploy = SimulatedDeployment(self.config, "pull")
        with self.assertRaises(xui_deploy.Refused):
            deploy.apply()
        self.assertFalse(any("stop" in cmd for cmd in deploy.commands))
        self.assertEqual(self.database.read_bytes(), self.original)
        self.assert_old(deploy)

    def test_trial_migration_failure_has_no_downtime(self):
        deploy = SimulatedDeployment(self.config, "trial")
        with self.assertRaises(xui_deploy.Refused):
            deploy.apply()
        self.assertFalse(any("stop" in cmd for cmd in deploy.commands))
        self.assert_old(deploy)

    def test_invalid_restore_is_rejected_before_pull_or_stop(self):
        bad = self.root / "bad.db"
        bad.write_bytes(b"not a database")
        self.config.update(restore=str(bad), restore_force=True)
        deploy = SimulatedDeployment(self.config)
        with self.assertRaises(Exception):
            deploy.apply()
        self.assertEqual(deploy.commands, [])
        self.assert_old(deploy)

    def test_existing_database_requires_explicit_restore_force(self):
        self.config["restore"] = str(self.database)
        deploy = SimulatedDeployment(self.config)
        with self.assertRaisesRegex(xui_deploy.Refused, "restore_force"):
            deploy.apply()
        self.assertEqual(deploy.commands, [])

    def test_health_failure_restores_paired_database_and_image(self):
        deploy = SimulatedDeployment(self.config, "health")
        with self.assertRaisesRegex(xui_deploy.Refused, "previous state restored"):
            deploy.apply()
        self.assert_old(deploy)
        override = json.loads((deploy.recovery / "rollback-image.json").read_text())
        self.assertEqual(override["services"]["3xui"]["image"], "sha256:old")
        self.assertTrue((deploy.recovery / "cert/tls.key").exists())

    def test_migration_failure_preserves_database_and_original_container(self):
        deploy = SimulatedDeployment(self.config, "migration")
        original_inode = self.database.stat().st_ino
        with patch.object(xui_db, "restore", wraps=xui_db.restore) as restore:
            with self.assertRaisesRegex(xui_deploy.Refused, "previous state restored"):
                deploy.apply()
            restore.assert_not_called()
        self.assert_old(deploy)
        self.assertEqual(self.database.read_bytes(), self.original)
        self.assertEqual(self.database.stat().st_ino, original_inode)
        self.assertEqual(deploy.state["Id"], "original-container")
        self.assertIn(["docker", "start", "original-container"], deploy.commands)
        self.assertFalse(any("up" in command for command in deploy.commands))
        self.assertFalse(deploy.database_restore_started)
        self.assertFalse(deploy.database_published)

    def test_prepublication_failure_keeps_stopped_original_container_stopped(self):
        deploy = SimulatedDeployment(self.config, "migration")
        deploy.state["State"]["Running"] = False
        original_inode = self.database.stat().st_ino
        with self.assertRaisesRegex(xui_deploy.Refused, "previous state restored"):
            deploy.apply()
        self.assertEqual(self.database.read_bytes(), self.original)
        self.assertEqual(self.database.stat().st_ino, original_inode)
        self.assertEqual(self.compose.read_text(), "previous compose")
        self.assertEqual(deploy.state["Id"], "original-container")
        self.assertFalse(deploy.state["State"]["Running"])
        self.assertFalse(
            any("start" in command or "up" in command for command in deploy.commands)
        )

    def test_failed_final_snapshot_preserves_writes_after_hot_backup(self):
        deploy = SimulatedDeployment(self.config)
        self.commit_before_stop(deploy)
        snapshot = xui_db.snapshot
        recovery_snapshots = 0

        def fail_final_snapshot(source, destination, **kwargs):
            nonlocal recovery_snapshots
            if destination == deploy.recovery / "x-ui.db":
                recovery_snapshots += 1
                if recovery_snapshots == 2:
                    raise OSError("simulated full recovery filesystem")
            return snapshot(source, destination, **kwargs)

        original_inode = self.database.stat().st_ino
        with (
            patch.object(xui_db, "snapshot", side_effect=fail_final_snapshot),
            patch.object(xui_db, "restore", wraps=xui_db.restore) as restore,
        ):
            with self.assertRaisesRegex(xui_deploy.Refused, "previous state restored"):
                deploy.apply()
            restore.assert_not_called()
        self.assertEqual(recovery_snapshots, 2)
        self.assert_late_write(self.database)
        with xui_db.connect(deploy.recovery / "x-ui.db") as db:
            self.assertIsNone(
                db.execute(
                    "SELECT value FROM settings WHERE key='late-write'"
                ).fetchone()
            )
        self.assertEqual(self.database.stat().st_ino, original_inode)
        self.assert_old(deploy)
        self.assertEqual(deploy.state["Id"], "original-container")
        self.assertFalse(any("up" in command for command in deploy.commands))
        self.assertFalse(deploy.database_restore_started)
        self.assertFalse(deploy.database_published)

    def test_failed_replace_after_sidecar_removal_restores_final_snapshot(self):
        deploy = SimulatedDeployment(self.config)
        self.commit_before_stop(deploy)
        replace = xui_deploy.os.replace
        restore = xui_db.restore
        publication_failed = False
        sidecars = [Path(str(self.database) + suffix) for suffix in ("-wal", "-shm")]

        def create_stale_sidecars(source, destination):
            if destination == self.database and not publication_failed:
                # Model leftover sidecars at the destructive boundary of restore().
                for path in sidecars:
                    path.write_bytes(b"synthetic stale sidecar")
            return restore(source, destination)

        def fail_live_replace(source, destination):
            nonlocal publication_failed
            if (
                str(source) == str(self.database) + ".restore"
                and destination == self.database
                and not publication_failed
            ):
                publication_failed = True
                self.assertTrue(deploy.database_restore_started)
                self.assertFalse(deploy.database_published)
                self.assertTrue(all(not path.exists() for path in sidecars))
                raise OSError("simulated failed atomic publication")
            return replace(source, destination)

        with (
            patch.object(xui_db, "restore", side_effect=create_stale_sidecars) as calls,
            patch.object(xui_deploy.os, "replace", side_effect=fail_live_replace),
        ):
            with self.assertRaisesRegex(xui_deploy.Refused, "previous state restored"):
                deploy.apply()
            self.assertEqual(calls.call_count, 2)
        self.assertTrue(publication_failed)
        self.assertFalse(deploy.database_published)
        self.assert_late_write(self.database)
        self.assert_late_write(deploy.recovery / "x-ui.db")
        self.assert_old(deploy)
        self.assertEqual(deploy.state["Id"], "original-container")
        self.assertFalse(any("up" in command for command in deploy.commands))

    def test_compose_publish_failure_restores_published_database_without_recreation(
        self,
    ):
        deploy = SimulatedDeployment(self.config)
        self.commit_before_stop(deploy)
        replace = xui_deploy.os.replace

        def fail_compose_replace(source, destination):
            if destination == self.compose:
                self.assertTrue(deploy.database_published)
                self.assertFalse(deploy.compose_published)
                raise OSError("simulated failed compose publication")
            return replace(source, destination)

        with patch.object(xui_deploy.os, "replace", side_effect=fail_compose_replace):
            with self.assertRaisesRegex(xui_deploy.Refused, "previous state restored"):
                deploy.apply()
        self.assert_late_write(self.database)
        self.assert_old(deploy)
        self.assertEqual(deploy.state["Id"], "original-container")
        self.assertFalse(any("up" in command for command in deploy.commands))

    def test_success_publishes_only_after_trial_and_preserves_recovery(self):
        deploy = SimulatedDeployment(self.config)
        result = deploy.apply()
        self.assertTrue(result["changed"])
        self.assertEqual(deploy.migrations, 2)
        self.assertEqual(deploy.state["Image"], "sha256:new")
        self.assertEqual(self.compose.read_text(), "new compose")
        with xui_db.connect(deploy.recovery / "x-ui.db") as db:
            self.assertIsNone(
                db.execute(
                    "SELECT value FROM settings WHERE key='migration-probe'"
                ).fetchone()
            )
        self.assertTrue((deploy.recovery / "manifest.json").exists())
        self.assertFalse(
            any("--remove-orphans" in cmd or "down" in cmd for cmd in deploy.commands)
        )

    def test_foreign_container_is_never_stopped(self):
        deploy = SimulatedDeployment(self.config)
        deploy.state["Config"]["Labels"][
            "com.docker.compose.project"
        ] = "another-service"
        with self.assertRaisesRegex(xui_deploy.Refused, "not owned"):
            deploy.apply()
        self.assertEqual(deploy.commands, [])

    def test_unexpected_database_mount_is_refused(self):
        deploy = SimulatedDeployment(self.config)
        deploy.state["Mounts"][0]["Source"] = "/another-service"
        with self.assertRaisesRegex(xui_deploy.Refused, "storage mounts"):
            deploy.apply()
        self.assertEqual(deploy.commands, [])

    def test_migration_command_has_no_network_and_no_secret_in_argv(self):
        deploy = SimulatedDeployment(self.config)
        self.config.update(
            admin_username="synthetic-admin", admin_password="synthetic-long-password"
        )
        observed = {}

        def run(args, **kwargs):
            observed.setdefault("calls", []).append((args, kwargs))
            if "run" in args:
                fixture(self.root / "stage/x-ui.db")
                return CompletedProcess(args, 0, "hasDefaultCredential: false", "")
            return CompletedProcess(args, 0, "", "")

        deploy.run = run
        stage = self.root / "stage"
        stage.mkdir()
        xui_deploy.Deployment.migrate(deploy, stage, fresh=True)
        args, kwargs = observed["calls"][0]
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertNotIn("synthetic-long-password", " ".join(args))
        self.assertIn("synthetic-long-password", kwargs["input"])
        self.assertIn("rm", observed["calls"][-1][0])

    def test_identical_deployment_does_not_stop_or_migrate(self):
        self.compose.write_text(self.config["compose"])
        deploy = SimulatedDeployment(self.config)
        deploy.state["Image"] = "sha256:new"
        result = deploy.apply()
        self.assertFalse(result["changed"])
        self.assertEqual(deploy.migrations, 0)
        self.assertFalse(
            any("stop" in args or "up" in args for args in deploy.commands)
        )
        self.assertIsNone(deploy.recovery)
