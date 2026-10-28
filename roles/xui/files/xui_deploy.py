#!/usr/bin/env python3
"""Transactional single-service deployment. JSON input and redacted JSON output only."""

import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import xui_db


class Refused(RuntimeError):
    pass


class Deployment:
    def __init__(self, config):
        self.c = config
        self.directory = Path(config["directory"])
        self.database = Path(config["db_dir"]) / "x-ui.db"
        self.compose = self.directory / "docker-compose.yml"
        self.recovery = None
        self.phase = "preflight"
        self.old = None
        self.database_restore_started = False
        self.database_published = False
        self.compose_published = False
        self.container_replacement_started = False
        self.timeout = config.get("timeout", 120)
        self.init_name = "vipen-migrate-" + uuid.uuid4().hex[:12]

    def run(self, args, *, input=None, allow_failure=False, timeout=None):
        result = subprocess.run(
            args,
            input=input,
            text=True,
            capture_output=True,
            timeout=timeout or self.timeout,
            check=False,
        )
        if result.returncode and not allow_failure:
            # Docker / application output can contain paths, credentials and DB records.
            raise Refused("External command failed")
        return result

    def compose_command(self, file, *args):
        return [
            "docker",
            "compose",
            "--project-directory",
            str(self.directory),
            "-p",
            self.c["project"],
            "-f",
            str(file),
            *args,
        ]

    def container(self):
        result = self.run(
            ["docker", "container", "inspect", self.c["container"]],
            allow_failure=True,
            timeout=15,
        )
        if result.returncode:
            # Distinguish an absent container from an unreachable daemon.
            self.run(["docker", "info"], timeout=15)
            if "No such" not in result.stderr:
                raise Refused("Cannot inspect existing container")
            return None
        return json.loads(result.stdout)[0]

    def stopped(self):
        state = self.container()
        if state and (state["State"]["Running"] or state["State"].get("Restarting")):
            raise Refused("Database writer is still running")

    def migrate(self, stage, fresh=False):
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            self.init_name,
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "128",
            "-v",
            str(stage) + ":/etc/x-ui",
            "-i",
            "--entrypoint",
            "/bin/sh",
            self.c["image"],
            "-c",
        ]
        data = None
        if fresh:
            username, password = self.c.get("admin_username", ""), self.c.get(
                "admin_password", ""
            )
            if (
                not username
                or len(password) < 16
                or any("\n" in s or "\r" in s for s in (username, password))
            ):
                raise Refused(
                    "Fresh installation requires explicit administrator credentials"
                )
            # Secrets cross the host process boundary on stdin, not Docker argv/env.
            command += [
                'IFS= read -r u; IFS= read -r p; exec /app/x-ui setting -username "$u" -password "$p" -show'
            ]
            data = username + "\n" + password + "\n"
        else:
            command += ["exec /app/x-ui setting -show"]
        try:
            result = self.run(command, input=data)
            # The upstream CLI can return 0 even when initialization failed.
            if "hasDefaultCredential: false" not in result.stdout:
                raise Refused(
                    "Migration failed or default administrator credentials remain"
                )
            xui_db.validate(stage / "x-ui.db")
        finally:
            # A client timeout does not stop the Docker container by itself.
            self.run(
                ["docker", "rm", "-f", self.init_name], allow_failure=True, timeout=15
            )

    def stage_database(self, stage, source):
        stage.mkdir(mode=0o700, exist_ok=True)
        if source:
            xui_db.snapshot(source, stage / "x-ui.db")
        self.migrate(stage, fresh=source is None)
        config = dict(self.c["settings"])
        if source is None and config.get("subscription") is None:
            config["subscription"] = {"enabled": False}
        xui_db.configure(stage / "x-ui.db", config)
        effective = xui_db.inspect(stage / "x-ui.db")
        if effective["subscription_enabled"]:
            if not effective["subscription_tls"]:
                raise Refused(
                    "Existing HTTP subscription requires an explicit TLS migration or disabling"
                )
            with xui_db.connect(stage / "x-ui.db") as db:
                settings = dict(db.execute("SELECT key,value FROM settings"))
            for key in ("subCertFile", "subKeyFile"):
                path = Path(settings[key])
                # Only the explicitly mounted certificate directory is available.
                relative = path.relative_to("/root/cert")
                actual = (Path(self.c["cert_dir"]) / relative).resolve()
                if (
                    not actual.is_relative_to(Path(self.c["cert_dir"]).resolve())
                    or not actual.is_file()
                ):
                    raise Refused(
                        "Subscription TLS file is missing or outside the certificate mount"
                    )
            if effective["subscription_port"] in [
                self.c["settings"]["port"],
                self.c["ssh_port"],
                *effective["tcp_ports"],
            ]:
                raise Refused("Subscription port collision")
        return effective

    def healthy(self, port):
        until = time.monotonic() + self.timeout
        consecutive = 0
        while time.monotonic() < until:
            state = self.container()
            running = (
                state
                and state["State"]["Running"]
                and not state["State"].get("Restarting")
            )
            try:
                with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
                    listening = True
            except OSError:
                listening = False
            consecutive = consecutive + 1 if running and listening else 0
            if consecutive >= 3:
                return
            time.sleep(2)
        raise Refused("Application did not become stable")

    def rollback(self):
        self.phase = "rollback"
        if self.container_replacement_started:
            # Stop only our named service. Never remove unrelated resources.
            self.run(
                self.compose_command(self.compose, "stop", "-t", "30", "3xui"),
                timeout=45,
            )
        if self.database_restore_started:
            self.stopped()
            # restore() can remove SQLite sidecars before replacing the main file.
            # The final stopped snapshot is complete before this flag can be set.
            if (self.recovery / "x-ui.db").exists():
                xui_db.restore(self.recovery / "x-ui.db", self.database)
            else:
                for suffix in ("", "-wal", "-shm", "-journal"):
                    Path(str(self.database) + suffix).unlink(missing_ok=True)
        previous = self.recovery / "docker-compose.yml"
        if self.compose_published and previous.exists():
            shutil.copy2(previous, self.compose)
        if not self.container_replacement_started:
            # Before recreation, retain the original container and writable layer.
            # In particular, a failed final snapshot must not replay the hot backup.
            if self.old:
                current = self.container()
                if not current or current["Id"] != self.old["Id"]:
                    raise Refused("Original container is no longer available")
                if self.old["State"]["Running"]:
                    if not current["State"]["Running"]:
                        self.run(["docker", "start", self.old["Id"]])
                    self.healthy(self.old_port)
                elif current["State"]["Running"]:
                    raise Refused("Previously stopped container changed state")
            if self.compose_published and not previous.exists():
                self.compose.unlink(missing_ok=True)
            return
        if self.old:
            # Pin the previous *local image ID*, even if its old tag was mutable.
            override = self.recovery / "rollback-image.json"
            override.write_text(
                json.dumps({"services": {"3xui": {"image": self.old["Image"]}}})
            )
            self.run(
                self.compose_command(
                    self.compose,
                    "-f",
                    override,
                    "up",
                    "-d",
                    "--no-deps",
                    "--pull",
                    "never",
                    "--force-recreate",
                    "3xui",
                )
            )
            if self.old["State"]["Running"]:
                self.healthy(self.old_port)
            else:
                self.run(self.compose_command(self.compose, "stop", "-t", "30", "3xui"))
        else:
            self.run(
                ["docker", "rm", "-f", self.c["container"]],
                allow_failure=True,
                timeout=30,
            )
        if not previous.exists():
            self.compose.unlink(missing_ok=True)

    def apply(self):
        os.umask(0o077)
        self.old = self.container()
        existing = self.database.exists()
        if self.old:
            labels = self.old["Config"].get("Labels") or {}
            if (
                not self.compose.exists()
                or labels.get("com.docker.compose.project") != self.c["project"]
                or labels.get("com.docker.compose.service") != "3xui"
            ):
                raise Refused("Existing container is not owned by this Compose project")
            mounts = {m["Destination"]: m["Source"] for m in self.old["Mounts"]}
            if (
                mounts.get("/etc/x-ui") != str(self.database.parent)
                or mounts.get("/root/cert") != self.c["cert_dir"]
            ):
                raise Refused("Existing container has unexpected storage mounts")
        restore = self.c.get("restore")
        if existing:
            xui_db.validate(self.database)
        if restore:
            if existing and not self.c.get("restore_force"):
                raise Refused(
                    "Restore over an existing database requires restore_force"
                )
            xui_db.validate(restore)
        source = Path(restore) if restore else self.database if existing else None
        self.recovery = Path(
            tempfile.mkdtemp(prefix="deploy-", dir=self.c["backup_dir"])
        )
        candidate = self.recovery / "candidate.yml"
        candidate.write_text(self.c["compose"])
        self.run(self.compose_command(candidate, "config", "--quiet"), timeout=30)
        self.phase = "pull"
        self.run(self.compose_command(candidate, "pull", "3xui"))
        image = json.loads(
            self.run(["docker", "image", "inspect", self.c["image"]], timeout=15).stdout
        )[0]["Id"]
        settings_change = True
        if source:
            settings_change = xui_db.configure(source, self.c["settings"], check=True)[
                "changed"
            ]
        if (
            not restore
            and existing
            and self.old
            and self.old["State"]["Running"]
            and self.old["Image"] == image
            and self.compose.read_text() == self.c["compose"]
            and not settings_change
        ):
            self.healthy(self.c["settings"]["port"])
            effective = xui_db.inspect(self.database)
            if effective["subscription_enabled"] and not effective["subscription_tls"]:
                raise Refused("HTTP subscriptions require TLS")
            shutil.rmtree(self.recovery)
            self.recovery = None
            return {"changed": False, "effective": effective}
        self.phase = "trial_migration"
        self.stage_database(self.recovery / "trial", source)
        shutil.rmtree(self.recovery / "trial")
        if self.compose.exists():
            shutil.copy2(self.compose, self.recovery / "docker-compose.yml")
        shutil.copytree(self.c["cert_dir"], self.recovery / "cert", symlinks=True)
        if self.old:
            self.run(
                [
                    "docker",
                    "image",
                    "tag",
                    self.old["Image"],
                    "vipen-rollback:" + self.recovery.name,
                ]
            )
        manifest = {
            "image_id": self.old["Image"] if self.old else None,
            "running": bool(self.old and self.old["State"]["Running"]),
            "database": str(self.database),
            "project": self.c["project"],
            "created": int(time.time()),
        }
        (self.recovery / "manifest.json").write_text(json.dumps(manifest, indent=2))
        if existing:
            with xui_db.connect(self.database) as db:
                self.old_port = int(
                    dict(db.execute("SELECT key,value FROM settings")).get(
                        "webPort", 2053
                    )
                )
        else:
            self.old_port = self.c["settings"]["port"]
        # Prepare a valid recovery DB before any stop, then refresh after writers stop.
        if existing:
            xui_db.snapshot(self.database, self.recovery / "x-ui.db")
        self.phase = "stop"
        try:
            if self.old:
                self.run(
                    self.compose_command(self.compose, "stop", "-t", "30", "3xui"),
                    timeout=45,
                )
            self.stopped()
            if existing:
                xui_db.snapshot(self.database, self.recovery / "x-ui.db")
            self.phase = "migration"
            stage = self.recovery / "staged"
            effective = self.stage_database(stage, source)
            self.phase = "publish"
            # Final recovery snapshot succeeded; restore may now touch live sidecars.
            self.database_restore_started = True
            xui_db.restore(stage / "x-ui.db", self.database)
            self.database_published = True
            temporary = self.directory / ".docker-compose.yml.next"
            shutil.copy2(candidate, temporary)
            os.replace(temporary, self.compose)
            self.compose_published = True
            # A failed Docker client can still have replaced the container.
            self.container_replacement_started = True
            self.run(
                self.compose_command(
                    self.compose,
                    "up",
                    "-d",
                    "--no-deps",
                    "--pull",
                    "never",
                    "--force-recreate",
                    "3xui",
                )
            )
            self.phase = "health"
            self.healthy(self.c["settings"]["port"])
        except Exception:
            failed_phase = self.phase
            try:
                self.rollback()
            except Exception:
                raise Refused(
                    "Deployment failed; automatic rollback also failed; use the recovery bundle"
                ) from None
            self.phase = failed_phase
            raise Refused("Deployment failed; previous state restored") from None
        shutil.rmtree(stage)
        return {"changed": True, "effective": effective, "recovery": str(self.recovery)}


def main():
    os.umask(0o077)
    deployment = None
    try:
        config = json.load(sys.stdin)
        with open("/run/vipen-deploy.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            deployment = Deployment(config)
            result = deployment.apply()
        print(json.dumps(result))
    except Exception as exc:
        result = {
            "failed": True,
            "phase": deployment.phase if deployment else "lock_or_input",
            "recovery": (
                str(deployment.recovery) if deployment and deployment.recovery else ""
            ),
            "message": (
                str(exc)
                if isinstance(exc, Refused)
                else "Deployment refused; inspect permissions, schema and configuration"
            ),
        }
        print(json.dumps(result))
        sys.exit(1)


if __name__ == "__main__":
    main()
