"""Execute actual Ansible-rendered Bash scripts with local service-boundary doubles.
No system configuration is written; no notification or network connection is sent.
"""

import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

from test_database import fixture, xui_db

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("ansible-playbook"), "requires requirements-dev.txt")
class ScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.render = cls.root / "render"
        cls.render.mkdir()
        cls.bin = cls.root / "bin"
        cls.bin.mkdir()
        cls.db_dir = cls.root / "db"
        cls.db_dir.mkdir()
        cls.db = cls.db_dir / "x-ui.db"
        variables = {
            "render_dir": str(cls.render),
            "vpn_monitor_db_dir": str(cls.db_dir),
            "vpn_monitor_db_tool_path": str(ROOT / "roles/xui/files/xui_db.py"),
            "vpn_monitor_deploy_lock_file": str(cls.root / "deploy.lock"),
            "vpn_monitor_command_timeout_seconds": 1,
            "vpn_monitor_backup_max_age_hours": 0,
            "vpn_monitor_alert_enabled": False,
            "vpn_monitor_auto_restart": True,
            "vpn_monitor_failure_threshold": 1,
            "vpn_monitor_state_file": str(cls.root / "health.state"),
            "vpn_monitor_alert_state_file": str(cls.root / "alert.state"),
            "vpn_monitor_digest_state_dir": str(cls.root / "digest"),
            "vpn_monitor_alert_script_path": str(cls.root / "notify-stub"),
            "vpn_monitor_alert_env_file": str(cls.root / "alert.env"),
            "healthcheck_report_path": str(cls.root / "health-report.log"),
            "healthcheck_command_timeout_seconds": 1,
            "healthcheck_monitor_script_path": str(
                cls.render / "vpn_monitor-vpn-health-check"
            ),
        }
        env = os.environ.copy()
        env["ANSIBLE_LOCAL_TEMP"] = str(cls.root / "ansible")
        env["ANSIBLE_REMOTE_TEMP"] = str(cls.root / "remote")
        result = subprocess.run(
            [
                "ansible-playbook",
                str(ROOT / "tests/render-shell-templates.yml"),
                "-e",
                json.dumps(variables),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.mock("logger", 'printf "%s\\n" "$*" >> "$TEST_ROOT/logger.log"')
        cls.mock(
            "timeout",
            '''if [ "$1" = 5 ] && [ "${2:-}" = bash ]; then
  printf '%s %s\\n' "$6" "$7" >> "$TEST_ROOT/tcp.log"
  if [ "${MOCK_REAL_TCP_PORT:-}" != "$7" ]; then exit "${MOCK_TCP_RC:-0}"; fi
fi
exec /usr/bin/timeout "$@"''',
        )
        cls.mock(
            "docker",
            """printf '%s\\n' "$*" >> "$TEST_ROOT/docker.log"
if [ "${MOCK_PROBE_DEPLOY_LOCK:-0}" = 1 ]; then
  if flock -x -n "$TEST_ROOT/deploy.lock" true; then
    printf '%s\\n' "$1" >> "$TEST_ROOT/lock-escaped.log"
  else
    printf '%s\\n' "$1" >> "$TEST_ROOT/lock-held.log"
  fi
fi
if [ "${MOCK_DOCKER_HANG:-0}" = 1 ]; then sleep 10; fi
case "$1" in
  inspect)
    if [[ "$*" == *OOMKilled* ]]; then echo 'running=true oom=false exit=0 restarts=0';
    elif [[ "$*" == *RestartCount* ]]; then echo 0; else echo true; fi ;;
  logs) exit "${MOCK_LOGS_RC:-0}" ;;
  *) exit 0 ;;
esac""",
        )
        cls.mock(
            "systemctl",
            """case "$*" in
 *xui-backup.service*) echo inactive; exit 3 ;;
 *is-active*fail2ban*) echo active ;;
 *list-units*|*--failed*) exit "${MOCK_SYSTEMD_RC:-0}" ;;
 *) exit 0 ;;
esac""",
        )
        cls.mock("ufw", 'echo "Status: active"')
        cls.mock("timedatectl", "echo yes")
        cls.mock(
            "df",
            'printf "Filesystem 1024-blocks Used Available Capacity Mounted on\\nmock 100 20 80 ${MOCK_DISK:-20}%% /\\n"',
        )
        cls.mock("ss", "exit 0")
        cls.mock(
            "journalctl",
            """[ "${MOCK_JOURNAL_RC:-0}" = 0 ] || exit "$MOCK_JOURNAL_RC"
case "$*" in
 *vps-host-monitor*)
   if [ "${MOCK_ANOMALY:-0}" = 1 ]; then echo '2026-10-25T10:00:00+00:00 node vps-host-monitor: status=WARN scheduler_delay_ms=1500 cpu_steal_pct=6.0';
   elif [ "${MOCK_DAILY_COVERAGE:-1}" = 1 ]; then echo 'status=SUMMARY period_seconds=3600 anomalies=0'; fi ;;
 *'-t vpn-health '*)
   if [ "${MOCK_NO_SAMPLES:-0}" = 1 ]; then echo '-- No entries --'; else echo 'status=OK failures=0 container=running'; fi ;;
esac""",
        )
        cls.mock(
            "curl",
            """cat > "$TEST_ROOT/curl-stdin"
printf '%s\\n' "$*" > "$TEST_ROOT/curl-args"
if [ "${MOCK_CURL_FAIL:-0}" = 1 ]; then printf '{"ok":false}\\nhttp=200'; else printf '{"ok":true}\\nhttp=200'; fi""",
        )
        stub = cls.root / "notify-stub"
        stub.write_text(
            '#!/bin/sh\necho sent >> "$TEST_ROOT/sends"\nexit "${MOCK_SEND_RC:-0}"\n'
        )
        stub.chmod(0o755)

    @classmethod
    def mock(cls, name, script):
        path = cls.bin / name
        path.write_text("#!/usr/bin/env bash\n" + script + "\n")
        path.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        for path in self.db_dir.glob("*"):
            path.unlink()
        fixture(self.db)
        for name in (
            "health.state",
            "alert.state",
            "logger.log",
            "docker.log",
            "tcp.log",
            "lock-escaped.log",
            "lock-held.log",
            "sends",
            "curl-stdin",
            "curl-args",
        ):
            (self.root / name).unlink(missing_ok=True)
        shutil.rmtree(self.root / "digest", ignore_errors=True)
        (self.root / "alert.env").write_text(
            "ALERT_TELEGRAM_TOKEN='123:synthetic'\nALERT_TELEGRAM_CHAT_ID='1'\n"
        )

    def run_script(self, name, args=(), **values):
        env = os.environ.copy()
        env.update({k: str(v) for k, v in values.items()})
        env.update(TEST_ROOT=str(self.root), PATH=str(self.bin) + ":" + env["PATH"])
        return subprocess.run(
            ["bash", str(self.render / name), *args],
            text=True,
            capture_output=True,
            env=env,
            timeout=15,
        )

    def test_healthy_database_and_listeners(self):
        result = self.run_script("vpn_monitor-vpn-health-check", ["--no-restart"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status=OK", result.stdout)
        self.assertIn("local_tcp=127.0.0.1:443 ok", result.stdout)
        self.assertFalse((self.root / "health.state").exists())
        self.assertIn("vpn-health-diagnostic", (self.root / "logger.log").read_text())

    def test_fresh_install_reports_bootstrap_without_missing_xray_failure(self):
        with xui_db.connect(self.db, readonly=False) as db:
            db.execute("DELETE FROM inbounds")
        result = self.run_script("vpn_monitor-vpn-health-check", ["--no-restart"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("bootstrap_no_enabled_inbounds", result.stdout)
        self.assertNotIn("xray process missing", result.stdout)
        self.assertNotIn("exec", (self.root / "docker.log").read_text())

    def test_listener_changes_are_seen_without_redeploy(self):
        with xui_db.connect(self.db, readonly=False) as db:
            db.execute("UPDATE inbounds SET port=12443")
        result = self.run_script("vpn_monitor-vpn-health-check", ["--no-restart"])
        self.assertIn("local_tcp=127.0.0.1:12443 ok", result.stdout)

    def test_subscription_probes_actual_ipv4_and_ipv6_listeners(self):
        for bind, expected, family in (
            ("127.0.0.2", "127.0.0.2", socket.AF_INET),
            ("::1", "::1", socket.AF_INET6),
            ("::", "::1", socket.AF_INET6),
        ):
            with self.subTest(bind=bind), socket.socket(family) as listener:
                listener.bind((bind, 0))
                listener.listen(1)
                port = listener.getsockname()[1]
                with xui_db.connect(self.db, readonly=False) as db:
                    db.execute("UPDATE settings SET value='true' WHERE key='subEnable'")
                    db.execute(
                        "DELETE FROM settings WHERE key IN ('subListen','subPort')"
                    )
                    db.executemany(
                        "INSERT INTO settings VALUES (?, ?)",
                        [("subListen", bind), ("subPort", str(port))],
                    )
                (self.root / "tcp.log").unlink(missing_ok=True)
                result = self.run_script(
                    "vpn_monitor-vpn-health-check",
                    ["--no-restart"],
                    MOCK_REAL_TCP_PORT=port,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"local_tcp={expected}:{port} ok", result.stdout)
                self.assertIn(
                    f"{expected} {port}",
                    (self.root / "tcp.log").read_text().splitlines(),
                )

    def test_monitor_holds_shared_lock_through_automatic_restart(self):
        result = self.run_script(
            "vpn_monitor-vpn-health-check", MOCK_TCP_RC=1, MOCK_PROBE_DEPLOY_LOCK=1
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("auto_restart=triggered", result.stdout)
        held = (self.root / "lock-held.log").read_text().splitlines()
        self.assertIn("inspect", held)
        self.assertIn("restart", held)
        self.assertFalse((self.root / "lock-escaped.log").exists())
        # A different open file description can acquire exclusive access once
        # the monitor (and its restart command) has finished.
        with (self.root / "deploy.lock").open("a") as deployment:
            fcntl.flock(deployment, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_backup_shared_lock_does_not_skip_monitor(self):
        with (self.root / "deploy.lock").open("a") as backup:
            fcntl.flock(backup, fcntl.LOCK_SH | fcntl.LOCK_NB)
            result = self.run_script("vpn_monitor-vpn-health-check", ["--no-restart"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status=OK", result.stdout)
        self.assertNotIn("maintenance=", result.stdout)
        self.assertTrue((self.root / "docker.log").exists())

    def test_deployment_exclusive_lock_skips_all_monitor_actions(self):
        with (self.root / "deploy.lock").open("a") as deployment:
            fcntl.flock(deployment, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_script("vpn_monitor-vpn-health-check", MOCK_TCP_RC=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("maintenance=deployment_in_progress", result.stdout)
        self.assertFalse((self.root / "docker.log").exists())
        self.assertFalse((self.root / "health.state").exists())

    def test_broken_database_does_not_report_green(self):
        self.db.write_bytes(b"corrupted")
        result = self.run_script("vpn_monitor-vpn-health-check", ["--no-restart"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("database_listener_discovery_failed", result.stdout)

    def test_docker_hang_is_bounded(self):
        start = time.monotonic()
        result = self.run_script(
            "vpn_monitor-vpn-health-check", ["--no-restart"], MOCK_DOCKER_HANG=1
        )
        self.assertLess(time.monotonic() - start, 6)
        self.assertEqual(result.returncode, 1)

    def test_systemd_query_failure_is_visible(self):
        result = self.run_script(
            "vpn_monitor-vpn-health-check", ["--no-restart"], MOCK_SYSTEMD_RC=1
        )
        self.assertIn("systemd_query_failed", result.stdout)
        self.assertIn("status=WARN", result.stdout)

    def test_manual_check_rejects_no_entries_and_protects_report(self):
        result = self.run_script("healthcheck-vpn-healthcheck", MOCK_NO_SAMPLES=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no fresh scheduled health samples", result.stdout)
        self.assertEqual(
            (self.root / "health-report.log").stat().st_mode & 0o777, 0o600
        )

    def test_manual_check_rejects_unreadable_journal(self):
        result = self.run_script("healthcheck-vpn-healthcheck", MOCK_JOURNAL_RC=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("health journal query failed", result.stdout)

    def test_daily_digest_does_not_mark_failed_journal_healthy(self):
        result = self.run_script("vpn_monitor-vps-host-daily", MOCK_JOURNAL_RC=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("journal_unavailable", (self.root / "logger.log").read_text())
        self.assertFalse(list((self.root / "digest").glob("*.sent")))

    def test_daily_digest_requires_coverage(self):
        result = self.run_script("vpn_monitor-vps-host-daily", MOCK_DAILY_COVERAGE=0)
        self.assertEqual(result.returncode, 1)
        self.assertIn("monitor_samples_missing", (self.root / "logger.log").read_text())

    def test_daily_digest_is_not_sent_twice_after_success(self):
        for _ in range(2):
            result = self.run_script("vpn_monitor-vps-host-daily", MOCK_ANOMALY=1)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "sends").read_text().splitlines(), ["sent"])

    def test_daily_digest_retries_failed_send(self):
        self.assertEqual(
            self.run_script(
                "vpn_monitor-vps-host-daily", MOCK_ANOMALY=1, MOCK_SEND_RC=1
            ).returncode,
            1,
        )
        self.assertFalse(list((self.root / "digest").glob("*.sent")))
        self.assertEqual(
            self.run_script("vpn_monitor-vps-host-daily", MOCK_ANOMALY=1).returncode, 0
        )

    def test_report_mode_surfaces_api_failure(self):
        result = self.run_script(
            "vpn_monitor-vpn-notify",
            ["--report", "test", "synthetic message"],
            MOCK_CURL_FAIL=1,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(
            "123:synthetic",
            result.stdout + result.stderr + (self.root / "curl-args").read_text(),
        )

    def test_notification_token_uses_stdin(self):
        result = self.run_script("vpn_monitor-vpn-notify", ["--test"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("123:synthetic", (self.root / "curl-stdin").read_text())
        self.assertNotIn("123:synthetic", (self.root / "curl-args").read_text())

    def test_manual_check_detects_docker_log_failure(self):
        result = self.run_script("healthcheck-vpn-healthcheck", MOCK_LOGS_RC=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("container logs could not be read", result.stdout)
