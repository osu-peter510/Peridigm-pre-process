#!/bin/bash
#SBATCH --job-name=pdprep_generate
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=02:00:00
#SBATCH --output=logs/generate_%j.out
#SBATCH --error=logs/generate_%j.err

set -euo pipefail

if (( $# < 1 )); then
  echo "Usage: sbatch hpc/generate.slurm CONFIG [KEY=VALUE ...]" >&2
  exit 2
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
CONFIG=$1
shift

cd "$REPO_ROOT"
mkdir -p logs

ARGS=(generate "$CONFIG")
for override in "$@"; do
  ARGS+=(--set "$override")
done

python generate.py "${ARGS[@]}"
