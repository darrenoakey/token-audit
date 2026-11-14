#!/usr/bin/env bash
# Historical CLI delegates to the venv-backed, bounded hotspot audit.
# --skip ID --count N remain supported; --api replaces environment configuration.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/run" "$@"
