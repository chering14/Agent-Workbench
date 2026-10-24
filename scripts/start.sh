#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install --require-hashes -r requirements.lock
if [ ! -f .env ]; then cp .env.example .env; fi
exec .venv/bin/python -m agent_workbench serve
