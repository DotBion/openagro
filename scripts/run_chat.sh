#!/usr/bin/env bash
# Start the local OpenAgronomy chat UI. Does not send messages to anyone.
set -euo pipefail
ROOT=/workspace/openagronomy
cd "$ROOT"
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"
if [[ ! -f "$ROOT/kb/index/embeddings_v2.npy" ]]; then
  echo "Embeddings missing. Run: $ROOT/.venv/bin/python $ROOT/scripts/embed_chunks.py" >&2
  exit 2
fi
exec "$ROOT/.venv/bin/python" -m app.server "$@"
