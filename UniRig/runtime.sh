#!/usr/bin/env bash
export UNIRIG_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
_studio_unirig="$HOME/.local/share/trellis-unirig"
if [ -f "$UNIRIG_ROOT/../bundled-web.json" ]; then
    _studio_unirig="$HOME/.local/share/trellis-unirig-setup"
fi
export UNIRIG_ENV="${TRELLIS_UNIRIG_ENV:-$_studio_unirig}"
unset _studio_unirig
export PATH="$UNIRIG_ENV/venv/bin:$UNIRIG_ENV/tools:$PATH"
export LD_LIBRARY_PATH="$UNIRIG_ENV/runtime/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
export HF_HOME="$UNIRIG_ROOT/models/huggingface"
export WANDB_MODE=disabled
export HF_HUB_DISABLE_TELEMETRY=1
export PYTHONUNBUFFERED=1
