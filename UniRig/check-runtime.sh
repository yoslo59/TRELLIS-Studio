#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/runtime.sh"
exec python "$UNIRIG_ROOT/check-runtime.py"
