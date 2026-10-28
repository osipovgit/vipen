# Contributing

Use issues for reproducible bugs or focused proposals and pull requests for changes. See [SECURITY.md](SECURITY.md) for vulnerabilities. Discuss changes to defaults, host access, firewall ownership and data formats before a release.

## Development

Use a disposable container or VM and synthetic data. Install `requirements-dev.txt`, then run:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
pre-commit run --all-files
ansible-playbook -i inventories/production/hosts.example.yml site.yml --syntax-check
```

Tests must exercise behavior and failure paths. For database/deployment work include restoration tests; for firewall/SSH changes demonstrate that independent access survives. Report exactly which integration environments were exercised. Passing mocks or lint is not a production compatibility claim.

Do not run privileged Molecule tests on a workstation or an existing server. Use a disposable VM/ephemeral hosted runner; see [docs/testing.md](docs/testing.md). Do not pass a personal Docker socket, real inventory or secrets into test containers.

Keep real configuration in ignored inventory/secrets files. Commit only synthetic fixtures. Preserve ownership of existing host configuration and unrelated services. Avoid destructive resets, `--remove-orphans`, unbounded service commands and credentials in process arguments/logs.

## Pull requests and dependencies

Explain the concrete defect, changed behavior, migration impact, rollback and relevant verification. Update Russian and English entry-point documentation together. Dependency bumps should update pins consistently, review official release notes and rerun the affected regressions. Pin container images by digest and GitHub Actions by full commit SHA. Dependabot proposals still require review.

The project is MIT licensed; by contributing you agree your contribution is offered under the repository's license. Use respectful, factual discussion and keep reviews focused on code and evidence.
