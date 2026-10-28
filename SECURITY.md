# Security policy

## Scope and supported code

Security fixes are maintained on the current default branch. There is no promise of backports to older snapshots. Use the image and tooling versions pinned by the project, review upstream advisories and rehearse upgrades on a copy of the deployment.

Vipen manages privileged services and sensitive VPN databases. A local container using host networking, NET_ADMIN or a mounted Docker socket must be treated as privileged access to the host. Do not expose the administration panel publicly.

## Reporting a vulnerability

Do not put exploit details, real server addresses, databases, credentials or subscription URLs in a public issue. Use GitHub's **Security → Report a vulnerability** for [osipovgit/vipen](https://github.com/osipovgit/vipen/security) if private vulnerability reporting is enabled. Otherwise contact the maintainer to arrange a private channel; an initial public request must contain no sensitive details. Availability of private reporting and response times are not guaranteed.

Include the affected revision, a minimal synthetic reproduction, impact, platform/version information and any proposed mitigation. Keep the report limited to systems you own or are authorized to test. Issues in 3x-ui, Docker or Ansible should also be reported through the respective upstream security channels.

Maintainers should enable private vulnerability reporting, branch protection and required CI checks in repository settings before publishing a release. These hosting settings are outside the repository and are not configured by this playbook.

## Handling credentials and recovery data

Keep secrets in Ansible Vault and out of Git even when encrypted. Recovery archives, SQLite WAL/SHM files, logs and certificate exports are sensitive. Store copies encrypted on a separate system and test restoration. Never paste them into CI logs or issues. If an actual credential is exposed, rotate it; removing a file from the latest commit is insufficient.
