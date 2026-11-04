#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/runtime.sh"
mkdir -p "$1/rig-work"
echo "$$" > "$1/rig-work/runtime.pid"
# Started by setsid; this group belongs only to this library job.
trap 'trap - TERM EXIT; kill -TERM -- -$$ 2>/dev/null || true' TERM EXIT
python "$UNIRIG_ROOT/run-model.py" "$1"
trap - TERM EXIT
rm -f -- "$1/rig-work/runtime.pid"
