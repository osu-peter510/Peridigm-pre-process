#!/bin/bash
#SBATCH --job-name=Peridigm
#SBATCH --partition=xeon-gen3
#SBATCH --nodes=1
#SBATCH --ntasks=36
#SBATCH --time=48:00:00
#SBATCH --output=output_%j.log
#SBATCH --error=error_%j.log

set -euo pipefail

# Prevent "unbound variable" under 'set -u' when peridigm.env appends to these vars
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
export PKG_CONFIG_PATH="${PKG_CONFIG_PATH:-}"
export PATH="${PATH:-/usr/bin:/bin}"
export PYTHONPATH="${PYTHONPATH:-}"
export CPATH="${CPATH:-}"
export LIBRARY_PATH="${LIBRARY_PATH:-}"
export CMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH:-}"

source /homes/malikas2/peter_zhang/peridigm.env
cd /homes/malikas2/peter_zhang/remodel_cloth_fall/final-tryout

decomp -p 36 scene.g
mpirun -np "${SLURM_NTASKS}" Peridigm scene.xml |& tee peridigm.log
