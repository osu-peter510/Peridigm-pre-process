#!/bin/bash
# Legacy cloth runner name retained as a generic metadata-driven wrapper.
#SBATCH --job-name=peridigm_case
#SBATCH --nodes=1
#SBATCH --ntasks=36
#SBATCH --time=24:00:00

set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec "$SCRIPT_DIR/hpc/run_case.slurm" "${1:?Usage: sbatch runpd.slurm CASE_DIR}"
