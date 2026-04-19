#!/usr/bin/env bash
set -euo pipefail
ruff format --check .
ruff check .
pytest -xvs --cov=mopedzoomd --cov-fail-under=80
