#!/usr/bin/env bash
set -euo pipefail

uv run fastapi dev main.py --host 0.0.0.0
