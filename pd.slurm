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
cd /homes/malikas2/peter_zhang/PD-fracture-dataset/tryout/vase-fall

decomp -p 36 vase_drop_test.g
mpirun -np "${SLURM_NTASKS}" Peridigm vase_drop_test.xml |& tee peridigm.log
