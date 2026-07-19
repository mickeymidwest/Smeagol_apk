#!/usr/bin/env bash
# Checks origin/main for new commits and, if there are any, pulls them
# and restarts gremlin.service so the update actually takes effect.
# Run periodically by gremlin-update.timer -- see install-all.sh or the
# README's "Auto-updating from GitHub" section for how it gets installed.
#
# Never touches anything if the working tree has local changes that
# would conflict: `git pull --ff-only` fails cleanly (does nothing) in
# that case instead of overwriting them, same "don't destroy state"
# rule the rest of this project already follows (see snapshots.py,
# root_exec.py). This is read-only against GitHub -- pulling from a
# public repo needs no token/auth at all, so this keeps working
# indefinitely regardless of any push-side credential's lifetime.

set -e
cd "$(dirname "$0")/.."   # this script lives in deploy/, repo root is one level up

git fetch origin main --quiet

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "[$(date -Iseconds)] Up to date ($LOCAL)."
    exit 0
fi

echo "[$(date -Iseconds)] New commits on origin/main -- pulling..."
if ! git pull --ff-only origin main; then
    echo "[$(date -Iseconds)] Pull failed (local changes conflict?) -- not touching anything further."
    exit 1
fi

echo "[$(date -Iseconds)] Pulled $LOCAL -> $(git rev-parse HEAD). Restarting gremlin.service..."
systemctl --user restart gremlin.service 2>&1 || echo "[$(date -Iseconds)] Couldn't restart gremlin.service (not installed as a service?)"
