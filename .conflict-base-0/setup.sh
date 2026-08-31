#!/usr/bin/env bash
# setup.sh — convenience alias for start.sh.
#
# Guaardvark has a single all-in-one entrypoint: start.sh detects a fresh
# install (no venv), creates the environment, runs database migrations, then
# launches the platform. "Setup" and "start" are the same step — there is no
# separate setup phase.
#
# This wrapper exists only because people (and some older install docs) reach
# for `setup.sh` out of habit. It forwards every argument straight to start.sh,
# so `./setup.sh`, `./setup.sh --fast`, etc. all behave exactly like start.sh.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/start.sh" "$@"
