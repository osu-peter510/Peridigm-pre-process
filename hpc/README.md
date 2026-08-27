# Slurm workflow

All scripts consume generated metadata or `manifest.json`; none contains a user
home, Peridigm path, mesh name, or XML name.

Generate on a compute node:

```bash
mkdir -p logs
sbatch hpc/generate.slurm configs/mug_fall.yaml run.count=20
```

Preview an array submission (safe default), then submit it:

```bash
python hpc/submit_dataset.py output/mug_fall/manifest.json \
  --partition=preempt --concurrency=5

PERIDIGM_BIN=/path/to/Peridigm \
python hpc/submit_dataset.py output/mug_fall/manifest.json \
  --partition=preempt --concurrency=5 --submit
```

`PERIDIGM_BIN`, `PERIDIGM_ENV`, `DECOMP_BIN`, and `MPI_LAUNCHER` are optional
environment variables. The runner reuses a complete mesh decomposition, rejects
a partial or incorrectly suffixed shard set, and refuses to overwrite a nonempty
result directory. `submit_dataset.py` infers ranks from
`config.runtime.mpi_ranks` in every runnable case's metadata. An explicit
`--ranks` value must match it, and cases with different declared rank counts must
be submitted as separate manifests.

For each decomposition, the runner writes `<mesh>.decomp.<ranks>.json`. This
sidecar binds the exact shard suffix list and rank count to the base mesh SHA256.
Existing shards are reused only when the complete suffix set, sidecar, current
mesh hash, and case metadata all agree. Partial, untracked, or stale shards are
never deleted automatically: move or remove the whole set after inspecting the
diagnostic, then rerun. If no shards exist, decomposition is regenerated safely
and the sidecar is replaced atomically.

Each launch also writes `results/run_metadata.json`, including the actual MPI
rank count, Slurm allocation/job identifiers, mesh and XML hashes, launcher,
status, exit code, and the exact completed Exodus paths/sizes. A failed run
updates the sidecar through an exit trap.
The quality gate requires this successful sidecar and verifies it against the
current case metadata and decomposition provenance. There is no environment
variable that bypasses the nonempty-results guard; inspect and move the entire
old result directory before an intentional rerun.

After the run, submit `validate_results.slurm` with an `afterany` dependency and
the same array indices. It checks launch provenance, completion time, shard
consistency, NaN/Inf, and damage bounds/irreversibility even when a simulation
job failed.
