#!/bin/sh
set -eu
# Resolve the repository from this wrapper; no credentials in argv or launchd configuration.
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# launchd starts jobs with only /usr/bin:/bin:/usr/sbin:/sbin; the official model CLIs
# (claude, codex, agy) are installed per user in ~/.local/bin.
PATH="$project_dir/launchd/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
cd "$project_dir"
mkdir -p data/logs
job=${1:-cycle}
case "$job" in
    cycle) target=src/cycle.py ;;
    *) exit 64 ;;
esac
exec >>"data/logs/$job.stdout.log" 2>>"data/logs/$job.stderr.log"
if [ "$job" = cycle ]; then
    if .venv/bin/python "$target" --campaign config/cycle_campaign.json --heartbeat; then
        exit 0
    else
        code=$?
        if [ "$code" -eq 3 ]; then exit 0; fi
        exit "$code"
    fi
fi
exec .venv/bin/python "$target"
