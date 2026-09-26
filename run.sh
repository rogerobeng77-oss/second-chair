#!/usr/bin/env bash
# Second Chair. Bedrock is optional: open /settings in the running app and set the
# model tier to "off" (or just unplug the network), and every page still renders
# from the written fallbacks.
set -euo pipefail
cd "$(dirname "$0")"
exec ./venv/bin/python -m uvicorn second_chair.web:app --host 127.0.0.1 --port "${PORT:-8412}" "$@"
