#!/bin/bash
# Generic Peridigm case runner. Resource settings are normally supplied by
# submit_dataset.py or sbatch flags rather than hard-coded for one cluster.
#SBATCH --job-name=peridigm_case
#SBATCH --nodes=1
#SBATCH --ntasks=36
#SBATCH --cpus-per-task=1
#SBATCH --time=24:00:00

set -euo pipefail
ulimit -c 0

if (( $# != 1 )); then
  echo "Usage: sbatch hpc/run_case.slurm CASE_DIR" >&2
  exit 2
fi

CASE_DIR=$(cd -- "$1" && pwd)
METADATA="$CASE_DIR/metadata.json"
if [[ ! -s "$METADATA" ]]; then
  echo "Missing metadata: $METADATA" >&2
  exit 3
fi

if ! CASE_FILE_TEXT=$(python - "$METADATA" "$CASE_DIR" <<'PY'
import json
import sys
from pathlib import Path

metadata_path = Path(sys.argv[1])
case = Path(sys.argv[2]).resolve()
try:
    metadata_path.resolve().relative_to(case)
except ValueError:
    raise SystemExit("metadata.json resolves outside the case directory")
data = json.loads(metadata_path.read_text(encoding="utf-8"))
files = data.get("files", {})
resolved = []
for key in ("mesh", "xml", "results"):
    value = files.get(key) if isinstance(files, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"metadata files.{key} must be a non-empty relative path")
    candidate = (case / value).resolve()
    try:
        candidate.relative_to(case)
    except ValueError:
        raise SystemExit(f"metadata files.{key} escapes the case directory: {value}")
    resolved.append(candidate)
if resolved[2] == case:
    raise SystemExit("metadata files.results cannot be the case directory")
for path in resolved:
    print(path)
print(data.get("config", {}).get("runtime", {}).get("mpi_ranks", ""))
print(data.get("sha256", {}).get("mesh", ""))
print(data.get("sha256", {}).get("xml", ""))
PY
); then
  echo "Cannot resolve safe case paths from $METADATA" >&2
  exit 3
fi
readarray -t CASE_FILES <<< "$CASE_FILE_TEXT"
if (( ${#CASE_FILES[@]} != 6 )); then
  echo "metadata.json did not resolve the expected six case values" >&2
  exit 3
fi
MESH=${CASE_FILES[0]}
XML=${CASE_FILES[1]}
RESULTS=${CASE_FILES[2]}
METADATA_RANKS=${CASE_FILES[3]}
METADATA_MESH_SHA256=${CASE_FILES[4]}
METADATA_XML_SHA256=${CASE_FILES[5]}
MPI_RANKS=${MPI_RANKS:-${SLURM_NTASKS:-36}}
PERIDIGM_BIN=${PERIDIGM_BIN:-Peridigm}
DECOMP_BIN=${DECOMP_BIN:-decomp}
MPI_LAUNCHER=${MPI_LAUNCHER:-srun}

if [[ ! "$MPI_RANKS" =~ ^[1-9][0-9]*$ ]]; then
  echo "MPI_RANKS must be a positive integer, got: $MPI_RANKS" >&2
  exit 4
fi
if [[ -n "$METADATA_RANKS" && "$MPI_RANKS" != "$METADATA_RANKS" ]]; then
  echo "Rank mismatch: runner requested $MPI_RANKS but metadata config.runtime.mpi_ranks is $METADATA_RANKS. Regenerate the case with the intended rank count or submit with matching ranks." >&2
  exit 4
fi
if [[ -n "${SLURM_NTASKS:-}" && "$MPI_RANKS" != "$SLURM_NTASKS" ]]; then
  echo "Rank mismatch: MPI_RANKS=$MPI_RANKS but Slurm allocated SLURM_NTASKS=$SLURM_NTASKS." >&2
  exit 4
fi

if [[ -n "${PERIDIGM_ENV:-}" ]]; then
  if [[ ! -s "$PERIDIGM_ENV" ]]; then
    echo "PERIDIGM_ENV does not exist: $PERIDIGM_ENV" >&2
    exit 4
  fi
  # shellcheck disable=SC1090
  source "$PERIDIGM_ENV"
fi
for path in "$MESH" "$XML"; do
  if [[ ! -s "$path" ]]; then
    echo "Missing input: $path" >&2
    exit 5
  fi
done
if [[ ! "$METADATA_MESH_SHA256" =~ ^[0-9a-fA-F]{64}$ ]]; then
  echo "metadata.json does not contain a valid sha256.mesh for $MESH" >&2
  exit 5
fi
if [[ ! "$METADATA_XML_SHA256" =~ ^[0-9a-fA-F]{64}$ ]]; then
  echo "metadata.json does not contain a valid sha256.xml for $XML" >&2
  exit 5
fi
MESH_SHA256=$(python -c 'import hashlib,sys; h=hashlib.sha256(); f=open(sys.argv[1], "rb"); [h.update(chunk) for chunk in iter(lambda: f.read(1024 * 1024), b"")]; print(h.hexdigest())' "$MESH")
if [[ "${MESH_SHA256,,}" != "${METADATA_MESH_SHA256,,}" ]]; then
  echo "Base mesh SHA256 differs from metadata.json: $MESH. Regenerate the case metadata before decomposing or running it." >&2
  exit 5
fi
XML_SHA256=$(python -c 'import hashlib,sys; h=hashlib.sha256(); f=open(sys.argv[1], "rb"); [h.update(chunk) for chunk in iter(lambda: f.read(1024 * 1024), b"")]; print(h.hexdigest())' "$XML")
if [[ "${XML_SHA256,,}" != "${METADATA_XML_SHA256,,}" ]]; then
  echo "XML SHA256 differs from metadata.json: $XML. Regenerate the case metadata before running it." >&2
  exit 5
fi
if ! command -v "$PERIDIGM_BIN" >/dev/null 2>&1; then
  echo "Peridigm executable not found: $PERIDIGM_BIN" >&2
  exit 6
fi

mkdir -p "$RESULTS"
shopt -s nullglob dotglob
existing_results=("$RESULTS"/*)
shopt -u dotglob
if (( ${#existing_results[@]} != 0 )); then
  echo "Refusing to overwrite nonempty results directory: $RESULTS" >&2
  exit 7
fi

DECOMP_MANIFEST="$MESH.decomp.$MPI_RANKS.json"

validate_shard_set() {
  python - "$MESH" "$MPI_RANKS" <<'PY'
import sys
from pathlib import Path

mesh = Path(sys.argv[1])
ranks = int(sys.argv[2])
width = len(str(ranks))
prefix = f"{mesh.name}.{ranks}."
expected = {
    mesh.parent / f"{prefix}{rank:0{width}d}" for rank in range(ranks)
}
actual = {
    path for path in mesh.parent.iterdir() if path.name.startswith(prefix)
}
missing = sorted(path.name for path in expected - actual)
unexpected = sorted(path.name for path in actual - expected)
empty = sorted(path.name for path in expected & actual if not path.is_file() or path.stat().st_size == 0)
if missing or unexpected or empty:
    details = []
    if missing:
        details.append("missing=" + ",".join(missing[:8]))
    if unexpected:
        details.append("unexpected=" + ",".join(unexpected[:8]))
    if empty:
        details.append("empty=" + ",".join(empty[:8]))
    raise SystemExit(
        f"Invalid {ranks}-rank decomposition for {mesh}: " + "; ".join(details)
        + ". No shards were deleted. Move or remove the entire stale/partial shard set after review, then rerun."
    )
PY
}

validate_decomp_manifest() {
  python - "$DECOMP_MANIFEST" "$MESH" "$MESH_SHA256" "$MPI_RANKS" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
mesh = Path(sys.argv[2])
mesh_sha256 = sys.argv[3].lower()
ranks = int(sys.argv[4])
width = len(str(ranks))
expected_shards = [f"{mesh.name}.{ranks}.{rank:0{width}d}" for rank in range(ranks)]
try:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
except (OSError, ValueError) as exc:
    raise SystemExit(f"Cannot read decomposition manifest {manifest_path}: {exc}")
problems = []
if data.get("base_mesh") != mesh.name:
    problems.append("base mesh name")
if str(data.get("base_mesh_sha256", "")).lower() != mesh_sha256:
    problems.append("base mesh SHA256")
if data.get("mpi_ranks") != ranks:
    problems.append("rank count")
if data.get("shards") != expected_shards:
    problems.append("exact shard suffix list")
if problems:
    raise SystemExit(
        f"Decomposition manifest {manifest_path} does not match the current mesh/decomposition ("
        + ", ".join(problems)
        + "). No shards were deleted. Move or remove the stale shard set and its sidecar after review, then rerun."
    )
PY
}

write_decomp_manifest() {
  python - "$DECOMP_MANIFEST" "$MESH" "$MESH_SHA256" "$MPI_RANKS" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

manifest_path = Path(sys.argv[1])
mesh = Path(sys.argv[2])
mesh_sha256 = sys.argv[3].lower()
ranks = int(sys.argv[4])
width = len(str(ranks))
shards = [f"{mesh.name}.{ranks}.{rank:0{width}d}" for rank in range(ranks)]
data = {
    "schema_version": 1,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "base_mesh": mesh.name,
    "base_mesh_sha256": mesh_sha256,
    "mpi_ranks": ranks,
    "shards": shards,
}
temporary = manifest_path.with_name(f".{manifest_path.name}.tmp.{os.getpid()}")
try:
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, manifest_path)
finally:
    temporary.unlink(missing_ok=True)
PY
}

shards=("$MESH.$MPI_RANKS."*)
if (( ${#shards[@]} == 0 )); then
  if ! command -v "$DECOMP_BIN" >/dev/null 2>&1; then
    echo "Decomposition executable not found: $DECOMP_BIN" >&2
    exit 8
  fi
  MESH_DIR=$(dirname -- "$MESH")
  MESH_NAME=$(basename -- "$MESH")
  (
    cd "$MESH_DIR"
    "$DECOMP_BIN" -p "$MPI_RANKS" "$MESH_NAME"
  )
  validate_shard_set
  write_decomp_manifest
else
  validate_shard_set
  if [[ ! -s "$DECOMP_MANIFEST" ]]; then
    echo "Decomposition shards exist without provenance sidecar: $DECOMP_MANIFEST. No shards were deleted. Move or remove the entire shard set after review, then rerun to create a verified decomposition." >&2
    exit 8
  fi
  validate_decomp_manifest
fi

RUN_SIDECAR="$RESULTS/run_metadata.json"
RUN_STARTED_AT=$(python -c 'from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())')
RUN_STATUS=running
RUN_EXIT_CODE=""

write_run_sidecar() {
  python - "$RUN_SIDECAR" "$RUN_STATUS" "$RUN_STARTED_AT" "$RUN_EXIT_CODE" "$MPI_RANKS" "${SLURM_NTASKS:-}" "$MESH" "$MESH_SHA256" "$XML" "$XML_SHA256" "$DECOMP_MANIFEST" "$MPI_LAUNCHER" "${SLURM_JOB_ID:-}" "${SLURM_ARRAY_JOB_ID:-}" "${SLURM_ARRAY_TASK_ID:-}" <<'PY'
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    sidecar_name, status, started_at, exit_code, ranks, slurm_ntasks,
    mesh_name, mesh_sha256, xml_name, xml_sha256, decomp_name, launcher,
    job_id, array_job_id, array_task_id,
) = sys.argv[1:]
sidecar = Path(sidecar_name)
data = {
    "schema_version": 1,
    "status": status,
    "started_at": started_at,
    "mpi_ranks": int(ranks),
    "slurm_ntasks": int(slurm_ntasks) if slurm_ntasks else None,
    "mesh": Path(mesh_name).name,
    "mesh_sha256": mesh_sha256,
    "xml": Path(xml_name).name,
    "xml_sha256": xml_sha256,
    "decomposition_manifest": Path(decomp_name).name,
    "mpi_launcher": launcher,
    "slurm_job_id": job_id or None,
    "slurm_array_job_id": array_job_id or None,
    "slurm_array_task_id": int(array_task_id) if array_task_id else None,
}
if status != "running":
    data["completed_at"] = datetime.now(timezone.utc).isoformat()
    data["exit_code"] = int(exit_code)
    result_pattern = re.compile(
        r"(?:\.e|\.exo|\.exodus)(?:\.\d+\.\d+)?$", re.IGNORECASE
    )
    result_files = sorted(
        path.relative_to(sidecar.parent).as_posix()
        for path in sidecar.parent.rglob("*")
        if path.is_file() and result_pattern.search(path.name)
    )
    data["result_files"] = result_files
    data["result_file_sizes"] = {
        name: (sidecar.parent / name).stat().st_size for name in result_files
    }
temporary = sidecar.with_name(f".{sidecar.name}.tmp.{os.getpid()}")
try:
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, sidecar)
finally:
    temporary.unlink(missing_ok=True)
PY
}

record_failed_run() {
  local exit_code=$?
  if [[ "$RUN_STATUS" == running ]]; then
    RUN_STATUS=failed
    RUN_EXIT_CODE=$exit_code
    write_run_sidecar || true
  fi
}

write_run_sidecar
trap record_failed_run EXIT

export OMP_NUM_THREADS=${OMP_NUM_THREADS:-1}
export OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS:-1}
export MKL_NUM_THREADS=${MKL_NUM_THREADS:-1}
cd "$CASE_DIR"

echo "[$(date --iso-8601=seconds)] START case=$(basename -- "$CASE_DIR") ranks=$MPI_RANKS"
if [[ "$MPI_LAUNCHER" == "srun" ]]; then
  srun --ntasks="$MPI_RANKS" "$PERIDIGM_BIN" "$(basename -- "$XML")" \
    |& tee "$RESULTS/peridigm.log"
else
  "$MPI_LAUNCHER" -np "$MPI_RANKS" "$PERIDIGM_BIN" "$(basename -- "$XML")" \
    |& tee "$RESULTS/peridigm.log"
fi
RUN_STATUS=complete
RUN_EXIT_CODE=0
write_run_sidecar
trap - EXIT
echo "[$(date --iso-8601=seconds)] COMPLETE case=$(basename -- "$CASE_DIR")"
