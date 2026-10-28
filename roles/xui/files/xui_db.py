#!/usr/bin/env python3
"""Bounded SQLite operations for Vipen. Configuration arrives on stdin, never argv."""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

SCHEMA = {
    "settings": {"key", "value"},
    "inbounds": {
        "id",
        "port",
        "protocol",
        "listen",
        "stream_settings",
        "sniffing",
        "enable",
    },
    "users": {"username", "password"},
}


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path, readonly=True):
    db = sqlite3.connect(
        Path(path).resolve().as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
        uri=True,
        timeout=10,
        factory=ClosingConnection,
    )
    deadline = time.monotonic() + 60
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    return db


def validate(path):
    with connect(path) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")
        for table, required in SCHEMA.items():
            found = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if not required <= found:
                raise ValueError(f"Unsupported database schema: {table}")
        if db.execute(
            "SELECT key FROM settings GROUP BY key HAVING count(*) > 1"
        ).fetchone():
            raise ValueError("Duplicate settings keys")


def listener_host(host):
    """Use a local address to probe a listener bound to a wildcard address."""
    if not host or host == "0.0.0.0":
        return "127.0.0.1"
    return "::1" if host in ("::", "[::]") else host


def inspect(path):
    validate(path)
    db = connect(path)
    try:
        settings = dict(db.execute("SELECT key,value FROM settings"))
        ports = sorted(
            {
                row[0]
                for row in db.execute(
                    "SELECT port FROM inbounds WHERE enable = 1 AND protocol IN ('vless','vmess','trojan','http','mixed','shadowsocks')"
                )
            }
        )
        return {
            "tcp_ports": ports,
            "subscription_enabled": any(
                settings.get(key, default) == "true"
                for key, default in [
                    ("subEnable", "true"),
                    ("subJsonEnable", "false"),
                    ("subClashEnable", "false"),
                ]
            ),
            "subscription_port": int(settings.get("subPort", "2096")),
            "subscription_listen": listener_host(settings.get("subListen", "")),
            "subscription_tls": bool(
                settings.get("subCertFile") and settings.get("subKeyFile")
            ),
            "tcp_listeners": [
                {
                    "host": listener_host(host),
                    "port": port,
                }
                for host, port in db.execute(
                    "SELECT listen,port FROM inbounds WHERE enable = 1 AND protocol IN ('vless','vmess','trojan','http','mixed','shadowsocks')"
                )
            ],
            "inbound_count": db.execute(
                "SELECT count(*) FROM inbounds WHERE enable=1"
            ).fetchone()[0],
        }
    finally:
        db.close()


def snapshot(source, destination, deadline=60):
    """Online backup, verified before atomic publication. The deadline includes busy retries."""
    destination = Path(destination)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".vipen-db-", dir=destination.parent)
    os.close(fd)
    start = time.monotonic()

    def progress(_status, _remaining, _total):
        if time.monotonic() - start >= deadline:
            raise TimeoutError("SQLite backup deadline exceeded")

    src = dst = None
    try:
        src, dst = connect(source), sqlite3.connect(temp)
        src.backup(dst, pages=128, progress=progress, sleep=0.05)
        if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Snapshot integrity check failed")
        dst.close()
        dst = None
        src.close()
        src = None
        # Backups are standalone files; readers must not create WAL sidecars.
        with sqlite3.connect(temp) as plain:
            plain.execute("PRAGMA journal_mode=DELETE")
        plain.close()
        os.chmod(temp, 0o600)
        with open(temp, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp, destination)
    finally:
        if src is not None:
            src.close()
        if dst is not None:
            dst.close()
        if os.path.exists(temp):
            os.unlink(temp)


def restore(source, destination):
    """Caller MUST stop all writers first. Validate before touching the destination."""
    validate(source)
    # Stage the replacement before removing stale journals from the stopped DB.
    staged = str(destination) + ".restore"
    snapshot(source, staged)
    for suffix in ("-wal", "-shm", "-journal"):
        Path(str(destination) + suffix).unlink(missing_ok=True)
    os.replace(staged, destination)


def configure(path, config, check=False):
    validate(path)
    db = connect(path, readonly=False)
    changed = False
    try:
        db.execute("BEGIN IMMEDIATE")
        values = {"webListen": config["listen"], "webPort": str(config["port"])}
        if config.get("panel_path"):
            values["webBasePath"] = config["panel_path"]
        sub = config.get("subscription")
        if sub is not None:
            values["subEnable"] = "true" if sub["enabled"] else "false"
            if sub["enabled"]:
                if not sub.get("cert") or not sub.get("key"):
                    raise ValueError(
                        "Public subscription requires a TLS certificate and key"
                    )
                values.update(
                    subPort=str(sub["port"]),
                    subListen=sub.get("listen", ""),
                    subCertFile=sub["cert"],
                    subKeyFile=sub["key"],
                    subEncrypt="true",
                    subShowInfo="false",
                )
                for option, key in [("path", "subPath"), ("uri", "subURI")]:
                    if sub.get(option):
                        values[key] = sub[option]
            else:
                values.update(
                    subListen="127.0.0.1", subJsonEnable="false", subClashEnable="false"
                )
        for key, value in values.items():
            row = db.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            if row is None:
                db.execute("INSERT INTO settings(key,value) VALUES(?,?)", (key, value))
                changed = True
            elif row[0] != value:
                db.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
                changed = True
        for override in config.get("reality_overrides", []):
            if "id" in override:
                rows = db.execute(
                    "SELECT id,stream_settings,sniffing FROM inbounds WHERE id=?",
                    (override["id"],),
                ).fetchall()
            elif "tag" in override:
                rows = db.execute(
                    "SELECT id,stream_settings,sniffing FROM inbounds WHERE tag=?",
                    (override["tag"],),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT id,stream_settings,sniffing FROM inbounds WHERE port=?",
                    (override["port"],),
                ).fetchall()
            if len(rows) != 1:
                raise ValueError(
                    "REALITY selector must identify exactly one inbound; use id or tag"
                )
            identity, old_stream, old_sniffing = rows[0]
            stream, sniffing = json.loads(old_stream or "{}"), json.loads(
                old_sniffing or "{}"
            )
            if stream.get("security") != "reality":
                raise ValueError("Selected inbound does not use REALITY")
            original = json.dumps([stream, sniffing], sort_keys=True)
            reality = stream.setdefault("realitySettings", {})
            if override.get("target"):
                reality["target"] = override["target"]
                reality.pop("dest", None)
            if override.get("server_names"):
                reality["serverNames"] = override["server_names"]
            if override.get("fingerprint"):
                reality.setdefault("settings", {})["fingerprint"] = override[
                    "fingerprint"
                ]
            for source, target in [
                ("sniffing_enabled", "enabled"),
                ("sniffing_dest_override", "destOverride"),
                ("sniffing_metadata_only", "metadataOnly"),
                ("sniffing_route_only", "routeOnly"),
            ]:
                if source in override:
                    sniffing[target] = override[source]
            if json.dumps([stream, sniffing], sort_keys=True) != original:
                db.execute(
                    "UPDATE inbounds SET stream_settings=?,sniffing=? WHERE id=?",
                    (
                        json.dumps(stream, separators=(",", ":")),
                        json.dumps(sniffing, separators=(",", ":")),
                        identity,
                    ),
                )
                changed = True
        if check:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {"changed": changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["validate", "inspect", "snapshot", "restore", "configure"]
    )
    parser.add_argument("path")
    parser.add_argument("destination", nargs="?")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "configure":
            result = configure(args.path, json.load(sys.stdin), args.check)
        elif args.action == "inspect":
            result = inspect(args.path)
        elif args.action == "validate":
            validate(args.path)
            result = {"valid": True}
        else:
            if not args.destination:
                parser.error("destination is required")
            (snapshot if args.action == "snapshot" else restore)(
                args.path, args.destination
            )
            result = {"changed": True}
        print(json.dumps(result))
    except (ValueError, KeyError, sqlite3.Error, OSError, TimeoutError):
        # Neither SQL contents, credentials, paths nor supplied JSON are logged.
        print(
            "Database operation refused; check schema, selectors, TLS settings, permissions and available space.",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
