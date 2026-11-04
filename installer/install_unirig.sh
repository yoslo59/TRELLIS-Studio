#!/usr/bin/env bash
set -euo pipefail
STUDIO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$STUDIO_ROOT/UniRig/runtime.sh"
mkdir -p "$UNIRIG_ENV/tools" "$UNIRIG_ENV/runtime/packages"
export UV_PYTHON_INSTALL_DIR="$UNIRIG_ENV/python"
export UV_CACHE_DIR="$UNIRIG_ENV/cache"
if [ ! -x "$UNIRIG_ENV/tools/uv" ]; then
    readarray -t PIN < <(python3 - "$STUDIO_ROOT/installer/manifest.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))['sources']['uv_linux'];print(p['url']);print(p['sha256'])
PY
)
    archive="$UNIRIG_ENV/tools/uv.tar.gz"
    curl -fL --retry 3 "${PIN[0]}" -o "$archive"
    printf '%s  %s\n' "${PIN[1]}" "$archive" | sha256sum --check -
    tar -xzf "$archive" --strip-components=1 -C "$UNIRIG_ENV/tools" uv-x86_64-unknown-linux-gnu/uv
fi
UV="$UNIRIG_ENV/tools/uv"
if [ ! -x "$UNIRIG_ENV/venv/bin/python" ]; then "$UV" venv --python 3.11 "$UNIRIG_ENV/venv";fi
PY="$UNIRIG_ENV/venv/bin/python"
"$UV" pip install --python "$PY" -r "$STUDIO_ROOT/installer/requirements-unirig.txt" \
    --extra-index-url https://download.pytorch.org/whl/cu124 \
    --find-links https://data.pyg.org/whl/torch-2.5.0+cu124.html --index-strategy unsafe-best-match
# User-local runtime libraries: does not install a Linux NVIDIA driver or require sudo.
cd "$UNIRIG_ENV/runtime/packages"
apt-get download libsm6 libice6 libgomp1
for pkg in ./*.deb;do dpkg-deb -x "$pkg" "$UNIRIG_ENV/runtime";done
python "$STUDIO_ROOT/UniRig/check-runtime.py"
