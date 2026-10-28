"""Regression tests use synthetic credentials and SQLite files in temporary directories."""

import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HELPERS = Path(__file__).resolve().parents[1] / "roles/xui/files"
sys.path.insert(0, str(HELPERS))
import xui_db  # noqa: E402


def fixture(path, duplicate_port=False):
    with contextlib.closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
        CREATE TABLE settings (key TEXT, value TEXT);
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password TEXT);
        CREATE TABLE inbounds (id INTEGER PRIMARY KEY, tag TEXT, port INTEGER, protocol TEXT,
          listen TEXT, stream_settings TEXT, sniffing TEXT, enable INTEGER);
        INSERT INTO settings VALUES ('webPort','54836'),('webListen','127.0.0.1'),('subEnable','false');
        INSERT INTO users VALUES (1,'test-user','synthetic-not-a-password');
        """)
        for identity in range(1, 3 if duplicate_port else 2):
            stream = {
                "security": "reality",
                "realitySettings": {
                    "privateKey": f"synthetic-key-{identity}",
                    "serverNames": ["original.invalid"],
                },
            }
            db.execute(
                "INSERT INTO inbounds VALUES (?,?,?,?,?,?,?,?)",
                (
                    identity,
                    f"test-{identity}",
                    443,
                    "vless",
                    "",
                    json.dumps(stream),
                    "{}",
                    1,
                ),
            )


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "x-ui.db"
        fixture(self.path, duplicate_port=True)
        self.config = {"listen": "127.0.0.1", "port": 54836, "reality_overrides": []}

    def rows(self, query):
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
            return db.execute(query).fetchall()

    def test_default_preserves_all_inbounds_and_subscription(self):
        before = self.rows("SELECT * FROM inbounds")
        self.assertFalse(xui_db.configure(self.path, self.config)["changed"])
        self.assertEqual(before, self.rows("SELECT * FROM inbounds"))
        self.assertFalse(xui_db.inspect(self.path)["subscription_enabled"])

    def test_ambiguous_port_rolls_back_entire_transaction(self):
        self.config.update(
            port=50001,
            reality_overrides=[{"port": 443, "target": "target.invalid:443"}],
        )
        before = self.rows("SELECT * FROM settings"), self.rows(
            "SELECT * FROM inbounds"
        )
        with self.assertRaises(ValueError):
            xui_db.configure(self.path, self.config)
        self.assertEqual(
            before,
            (self.rows("SELECT * FROM settings"), self.rows("SELECT * FROM inbounds")),
        )

    def test_id_selector_does_not_copy_private_key_between_rows(self):
        self.config["reality_overrides"] = [{"id": 2, "target": "target.invalid:443"}]
        self.assertTrue(xui_db.configure(self.path, self.config)["changed"])
        first, second = [
            json.loads(r[0])["realitySettings"]
            for r in self.rows("SELECT stream_settings FROM inbounds ORDER BY id")
        ]
        self.assertNotIn("target", first)
        self.assertEqual(first["privateKey"], "synthetic-key-1")
        self.assertEqual(second["privateKey"], "synthetic-key-2")
        self.assertEqual(second["target"], "target.invalid:443")
        self.assertFalse(xui_db.configure(self.path, self.config)["changed"])

    def test_tag_selector_changes_only_selected_row(self):
        self.config["reality_overrides"] = [{"tag": "test-1", "sniffing_enabled": True}]
        xui_db.configure(self.path, self.config)
        self.assertEqual(
            self.rows("SELECT sniffing FROM inbounds ORDER BY id"),
            [('{"enabled":true}',), ("{}",)],
        )

    def test_missing_selector_is_not_silently_skipped(self):
        self.config["reality_overrides"] = [{"id": 300, "target": "target.invalid:443"}]
        with self.assertRaises(ValueError):
            xui_db.configure(self.path, self.config)

    def test_plaintext_subscription_is_refused(self):
        self.config["subscription"] = {"enabled": True, "port": 2096}
        with self.assertRaises(ValueError):
            xui_db.configure(self.path, self.config)
        self.assertFalse(xui_db.inspect(self.path)["subscription_enabled"])

    def test_custom_tls_subscription_port_and_disable(self):
        self.config["subscription"] = {
            "enabled": True,
            "port": 2097,
            "cert": "/root/cert/t.crt",
            "key": "/root/cert/t.key",
        }
        xui_db.configure(self.path, self.config)
        state = xui_db.inspect(self.path)
        self.assertEqual(state["subscription_port"], 2097)
        self.assertTrue(state["subscription_tls"])
        self.config["subscription"] = {"enabled": False}
        xui_db.configure(self.path, self.config)
        self.assertFalse(xui_db.inspect(self.path)["subscription_enabled"])

    def test_check_reports_change_without_persisting(self):
        self.config["port"] = 50001
        self.assertTrue(xui_db.configure(self.path, self.config, check=True)["changed"])
        self.assertEqual(
            self.rows("SELECT value FROM settings WHERE key='webPort'"), [("54836",)]
        )

    def test_invalid_schema_is_not_accepted_for_restore(self):
        bad = self.path.with_name("bad.db")
        with contextlib.closing(sqlite3.connect(bad)) as db, db:
            db.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            xui_db.restore(bad, self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_settings_are_rejected(self):
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO settings VALUES ('webPort','1')")
        with self.assertRaises(ValueError):
            xui_db.validate(self.path)

    def test_online_backup_includes_committed_wal_and_is_private(self):
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("INSERT INTO settings VALUES ('wal-probe','committed')")
            db.commit()
            snapshot = self.path.with_name("snapshot.db")
            xui_db.snapshot(self.path, snapshot)
            with contextlib.closing(sqlite3.connect(snapshot)) as saved:
                self.assertEqual(
                    saved.execute(
                        "SELECT value FROM settings WHERE key='wal-probe'"
                    ).fetchone(),
                    ("committed",),
                )
                self.assertEqual(
                    saved.execute("PRAGMA journal_mode").fetchone()[0], "delete"
                )
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)

    def test_backup_deadline_leaves_previous_snapshot_intact(self):
        destination = self.path.with_name("snapshot.db")
        destination.write_bytes(b"previous")
        with self.assertRaises(TimeoutError):
            xui_db.snapshot(self.path, destination, deadline=0)
        self.assertEqual(destination.read_bytes(), b"previous")
        self.assertEqual(list(destination.parent.glob(".vipen-db-*")), [])

    def test_restore_removes_stale_sidecars_only_after_validation(self):
        backup = self.path.with_name("backup.db")
        xui_db.snapshot(self.path, backup)
        self.config["port"] = 50001
        xui_db.configure(self.path, self.config)
        Path(str(self.path) + "-wal").write_bytes(b"stale")
        xui_db.restore(backup, self.path)
        self.assertFalse(Path(str(self.path) + "-wal").exists())
        self.assertEqual(
            self.rows("SELECT value FROM settings WHERE key='webPort'"), [("54836",)]
        )

    def test_cli_never_echoes_sensitive_config_on_failure(self):
        self.config["subscription"] = {
            "enabled": True,
            "path": "/synthetic-secret-value/",
        }
        result = subprocess.run(
            [sys.executable, str(HELPERS / "xui_db.py"), "configure", str(self.path)],
            input=json.dumps(self.config),
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("synthetic-secret-value", result.stdout + result.stderr)

    def test_missing_database_is_never_created_by_inspection(self):
        missing = self.path.with_name("missing.db")
        with self.assertRaises(sqlite3.Error):
            xui_db.inspect(missing)
        self.assertFalse(missing.exists())

    def test_only_enabled_listeners_are_discovered(self):
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE inbounds SET enable=0 WHERE id=1")
            db.execute("UPDATE inbounds SET listen='::', port=8443 WHERE id=2")
        state = xui_db.inspect(self.path)
        self.assertEqual(state["tcp_listeners"], [{"host": "::1", "port": 8443}])
        self.assertEqual(state["inbound_count"], 1)

    def test_subscription_listener_uses_saved_bind_and_normalizes_wildcards(self):
        self.assertEqual(xui_db.inspect(self.path)["subscription_listen"], "127.0.0.1")
        for saved, expected in (
            ("", "127.0.0.1"),
            ("0.0.0.0", "127.0.0.1"),
            ("::", "::1"),
            ("[::]", "::1"),
            ("127.0.0.2", "127.0.0.2"),
            ("::1", "::1"),
            ("2001:db8::42", "2001:db8::42"),
        ):
            with self.subTest(saved=saved):
                with xui_db.connect(self.path, readonly=False) as db:
                    db.execute("DELETE FROM settings WHERE key='subListen'")
                    db.execute("INSERT INTO settings VALUES ('subListen', ?)", (saved,))
                self.assertEqual(
                    xui_db.inspect(self.path)["subscription_listen"], expected
                )
                self.assertEqual(
                    self.rows("SELECT value FROM settings WHERE key='subListen'"),
                    [(saved,)],
                )

    def test_json_and_clash_subscriptions_are_detected_and_explicitly_disabled(self):
        with xui_db.connect(self.path, readonly=False) as db:
            db.execute(
                "INSERT INTO settings VALUES ('subJsonEnable','true'),('subClashEnable','true')"
            )
        self.assertTrue(xui_db.inspect(self.path)["subscription_enabled"])
        self.config["subscription"] = {"enabled": False}
        xui_db.configure(self.path, self.config)
        self.assertFalse(xui_db.inspect(self.path)["subscription_enabled"])
