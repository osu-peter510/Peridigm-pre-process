#!/bin/bash
# Backward-compatible wrapper. Prefer hpc/submit_dataset.py for arrays.
#SBATCH --job-name=peridigm_case
#SBATCH --nodes=1
#SBATCH --ntasks=36
#SBATCH --time=24:00:00

set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec "$SCRIPT_DIR/hpc/run_case.slurm" "${1:?Usage: sbatch pd.slurm CASE_DIR}"
