#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077
cd "$(dirname "$0")/../.."
export PYTHONDONTWRITEBYTECODE=1
exec "${PYTHON:-python3}" experiments/unified/run.py "$@"
