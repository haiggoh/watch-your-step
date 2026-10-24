#!/usr/bin/env bash
# The version is stated in two places (plugin.json, the top CHANGELOG entry); they must agree.
# Exits 1 and names the stale surface on any mismatch. --help prints this text.
set -u
case "${1:-}" in -h|--help) sed -n '2,3p' "$0" | sed 's/^# //'; exit 0 ;; "") ;; *) echo "usage: $0 [--help]" >&2; exit 2 ;; esac
root=$(cd "$(dirname "$0")/.." && pwd)
manifest=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$root/.claude-plugin/plugin.json")
changelog=$(sed -n -E 's/^## \[([0-9]+\.[0-9]+\.[0-9]+)\].*/\1/p' "$root/CHANGELOG.md" | head -1)
printf 'plugin.json  %s\nCHANGELOG    %s\n' "${manifest:-<none>}" "${changelog:-<none>}"
if [ -z "$manifest" ] || [ -z "$changelog" ] || [ "$manifest" != "$changelog" ]; then
  echo "✖ version surfaces disagree" >&2
  exit 1
fi
echo "✔ versions agree: $manifest"
