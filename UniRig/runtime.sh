#!/usr/bin/env bash
export UNIRIG_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export UNIRIG_ENV="${TRELLIS_UNIRIG_ENV:-$HOME/.local/share/trellis-unirig}"
export PATH="$UNIRIG_ENV/venv/bin:$UNIRIG_ENV/tools:$PATH"
export LD_LIBRARY_PATH="$UNIRIG_ENV/runtime/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
export HF_HOME="$UNIRIG_ROOT/models/huggingface"
export WANDB_MODE=disabled
export HF_HUB_DISABLE_TELEMETRY=1
export PYTHONUNBUFFERED=1
