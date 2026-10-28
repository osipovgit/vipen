# Changelog

## Unreleased — 2026-10-26

### Changed

- Pin 3x-ui 3.11.0 by tag and OCI index digest; update controller, lint, Molecule, collection and GitHub Actions versions.
- Default to application-only management. Make OS upgrades, SSH/UFW/Docker host management and network tuning explicit decisions.
- Replace live database edits with isolated trial migration, staged configuration and recovery of the previous database/image pair.
- Preserve existing REALITY settings; require unambiguous selectors and TLS for enabled public subscriptions.
- Preserve Docker daemon settings, configure log rotation per service and reduce default container privileges.

### Fixed

- SSH first-value configuration, key parsing, port migration refusal and Linux ControlPath.
- Destructive UFW reset and stale owned rules; add an independent firewall rollback watchdog and preserve unrelated rules.
- SQLite backup consistency, schema checks, private permissions, deadlines and recovery archive contents.
- Monitor listener discovery, empty fresh installations, external-command timeouts and disabled workers/timers.
- Empty/unreadable journal false positives, private diagnostic reports and repeat daily digest delivery after successful sends.
- Debian deb822 dependency, BBR module loading order and unintended conntrack decreases.

### Added

- Offline behavioral regressions covering database, deployment, firewall, backup and rendered scripts.
- Migration, testing, security and contribution documentation; issue/PR templates and dependency update proposals.

### Migration notes

Read [docs/migration.md](docs/migration.md). Existing HTTP subscriptions, default administrator credentials and legacy generic UFW comments can block an upgrade until explicitly resolved. A full privileged integration run and an external client test on a server copy remain necessary before production rollout.
