"""Resolve the exact role payload with Ansible, including secret-free example values."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("ansible-playbook"), "requires requirements-dev.txt")
class RenderingTests(unittest.TestCase):
    def test_deployment_payload_and_compose_resolve_with_real_ansible(self):
        tasks = yaml.safe_load((ROOT / "roles/xui/tasks/main.yml").read_text())
        block = next(t for t in tasks if t["name"].startswith("Deploy with isolated"))[
            "block"
        ]
        stdin = block[0]["ansible.builtin.command"]["stdin"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            play = [
                {
                    "name": "Resolve deployment data",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": {"role_path": str(ROOT / "roles/xui"), "ansible_port": 22},
                    "vars_files": [
                        str(ROOT / "inventories/production/group_vars/vpn_servers.yml"),
                        str(ROOT / "inventories/production/secrets.example.yml"),
                    ],
                    "tasks": [
                        {
                            "name": "Render input",
                            "ansible.builtin.copy": {
                                "content": stdin,
                                "dest": str(directory / "payload.json"),
                                "mode": "0600",
                            },
                        }
                    ],
                }
            ]
            (directory / "play.yml").write_text(yaml.safe_dump(play))
            env = os.environ.copy()
            env["ANSIBLE_LOCAL_TEMP"] = str(directory / "ansible")
            env["ANSIBLE_REMOTE_TEMP"] = str(directory / "remote")
            result = subprocess.run(
                ["ansible-playbook", "-i", "localhost,", str(directory / "play.yml")],
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            config = json.loads((directory / "payload.json").read_text())
            self.assertIsNone(config["settings"]["subscription"])
            self.assertEqual(config["settings"]["reality_overrides"], [])
            self.assertIsNone(config["restore"])
            compose = yaml.safe_load(config["compose"])["services"]["3xui"]
            self.assertIn("@sha256:", compose["image"])
            self.assertNotIn("NET_ADMIN", compose.get("cap_add", []))
            self.assertIn("NET_RAW", compose["cap_drop"])
            self.assertEqual(compose["environment"]["XUI_ENABLE_FAIL2BAN"], "false")
            self.assertEqual(compose["logging"]["options"]["max-size"], "10m")
