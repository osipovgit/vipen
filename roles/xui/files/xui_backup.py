#!/usr/bin/env python3
"""Private, consistent recovery bundle. Does not send or upload anything."""

import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

import xui_db


def create(config):
    os.umask(0o077)
    keep = int(config["keep"])
    if keep < 1:
        raise ValueError("Retention must be positive")
    directory = Path(config["backup_dir"])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = (
        time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        + f"-{time.time_ns() % 1000000000:09d}"
    )
    destination = directory / ("x-ui.db.auto-" + stamp)
    bundle = directory / ("recovery.auto-" + stamp + ".tar.gz")
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=directory) as tmp:
        stage = Path(tmp)
        xui_db.validate(config["database"])
        xui_db.snapshot(config["database"], stage / "x-ui.db")
        compose = Path(config["compose"])
        if compose.exists():
            shutil.copy2(compose, stage / "docker-compose.yml")
        cert = Path(config["cert_dir"])
        if cert.exists():
            shutil.copytree(cert, stage / "cert", symlinks=True)
        result = subprocess.run(
            ["docker", "container", "inspect", config["container"]],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        state = json.loads(result.stdout)[0]
        manifest = {
            "created": int(time.time()),
            "image_id": state["Image"],
            "image_reference": state["Config"]["Image"],
            "database": config["database"],
            "cert_dir": config["cert_dir"],
            "compose": config["compose"],
            "note": "Image layers are not included; retain or export the recorded image separately.",
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2))
        archive = stage / "bundle.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for name in ("x-ui.db", "docker-compose.yml", "cert", "manifest.json"):
                if (stage / name).exists():
                    tar.add(stage / name, arcname=name)
        os.replace(archive, bundle)
        os.replace(stage / "x-ui.db", destination)
    for mask in ("x-ui.db.auto-*", "recovery.auto-*.tar.gz"):
        for old in sorted(directory.glob(mask), reverse=True)[keep:]:
            if old.is_file() and not old.is_symlink():
                old.unlink()
    return {"database": str(destination), "bundle": str(bundle)}


def main():
    os.umask(0o077)
    try:
        config = json.load(sys.stdin)
        if int(config["keep"]) < 1:
            raise ValueError("Invalid retention")
        with open("/run/vipen-deploy.lock", "a") as deployment, open(
            "/run/vipen-backup.lock", "a"
        ) as backup:
            fcntl.flock(deployment, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(backup, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(json.dumps(create(config)))
    except Exception:
        print(
            "Backup failed or deployment in progress; no incomplete snapshot was published.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
