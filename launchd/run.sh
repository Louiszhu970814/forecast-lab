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
    resolve) target=src/resolve.py ;; # Pending later M3 work; confirm its CLI before installing.
    report) target=src/report.py ;;   # Pending later M3 work; confirm its CLI before installing.
    *) exit 64 ;;
esac
exec >>"data/logs/$job.stdout.log" 2>>"data/logs/$job.stderr.log"
if [ ! -f "$target" ]; then
    echo "$target is pending later M3 work" >&2
    exit 69
fi
if [ "$job" = cycle ]; then
    if .venv/bin/python "$target" --campaign config/cycle_campaign.json; then
        # The cycle worker has exited and released its locks. Observation is bounded
        # and cannot invoke models or approve a baseline (D124).
        .venv/bin/python src/auto_reaccept.py tick || true
        exit 0
    else
        exit "$?"
    fi
fi
exec .venv/bin/python "$target"
