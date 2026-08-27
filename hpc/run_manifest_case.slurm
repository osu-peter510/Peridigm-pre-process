#!/bin/bash
set -euo pipefail

if (( $# != 1 )); then
  echo "Usage: sbatch --array=... hpc/run_manifest_case.slurm MANIFEST" >&2
  exit 2
fi
if [[ -z "${SLURM_ARRAY_TASK_ID:-}" ]]; then
  echo "SLURM_ARRAY_TASK_ID is not set" >&2
  exit 3
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MANIFEST=$(cd -- "$(dirname -- "$1")" && pwd)/$(basename -- "$1")
CASE_DIR=$(python - "$MANIFEST" "$SLURM_ARRAY_TASK_ID" <<'PY'
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1]).resolve()
data = json.loads(manifest.read_text(encoding="utf-8"))
entry = data["cases"][int(sys.argv[2])]
if entry.get("status") not in {"success", "skipped_complete"}:
    raise SystemExit("Array task does not select a runnable manifest case")
dataset = (manifest.parent / data.get("dataset_dir", ".")).resolve()
case = (dataset / entry["case_dir"]).resolve()
try:
    case.relative_to(dataset)
except ValueError:
    raise SystemExit("Manifest case_dir escapes the dataset directory")
print(case)
PY
)

exec "$SCRIPT_DIR/run_case.slurm" "$CASE_DIR"
