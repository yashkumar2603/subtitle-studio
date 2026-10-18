#!/usr/bin/env bash
# Start Subtitle Studio on Linux / macOS. First run installs everything it needs.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (one time only)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

EXTRA=()
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  EXTRA=(--extra gpu)
fi

echo "Preparing Subtitle Studio (first run downloads about 1-2 GB)..."
uv sync "${EXTRA[@]}" --quiet
exec uv run --no-sync python -m app "$@"
