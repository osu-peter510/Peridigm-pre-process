#!/bin/bash
# Run this with an afterany dependency so failed/incomplete jobs are audited too.
#SBATCH --job-name=peridigm_validate
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=02:00:00

set -euo pipefail

if (( $# != 1 )); then
  echo "Usage: sbatch --array=... hpc/validate_results.slurm MANIFEST" >&2
  exit 2
fi
if [[ -z "${SLURM_ARRAY_TASK_ID:-}" ]]; then
  echo "SLURM_ARRAY_TASK_ID is not set" >&2
  exit 3
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
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

cd "$REPO_ROOT"
python generate.py quality "$CASE_DIR"
