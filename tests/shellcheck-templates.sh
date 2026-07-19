#!/usr/bin/env bash
#
# Static analysis for the shell scripts that ship as Jinja templates.
#
# The linter skips roles/*/templates/*.j2: the extension is unknown to it and
# the Jinja tags are not valid bash. That left the scripts deciding whether the
# VPN is healthy as the only unchecked code in the repository - a typo there
# does not fail any lint and surfaces as monitoring quietly lying.
#
# So render them first, then check the result. Normally invoked through the
# pre-commit hook, which is what CI runs too; that hook also supplies a pinned
# linter, so its version never drifts from the one used for plain .sh files.
# Standalone runs need shellcheck on PATH: pip install shellcheck-py.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v shellcheck >/dev/null 2>&1; then
  printf 'shellcheck-templates: shellcheck not found - run me via pre-commit, or pip install shellcheck-py\n' >&2
  exit 1
fi

render_dir="$(mktemp -d)"
trap 'rm -rf "$render_dir"' EXIT

# Ansible is chatty and has nothing to say when it works, but every word of it
# matters when the render breaks.
if ! render_log="$(ansible-playbook "${repo_root}/tests/render-shell-templates.yml" \
  -e "render_dir=${render_dir}" 2>&1)"; then
  printf '%s\n' "$render_log" >&2
  exit 1
fi

# Relative paths so the report names the script, not a temporary directory.
cd "$render_dir"
shellcheck ./*
