#!/usr/bin/env python3
"""Build (or execute) one Slurm array submission from a dataset manifest."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _metadata_rank_count(
    manifest: Path, data: dict, entries: list[tuple[int, dict]]
) -> tuple[int | None, list[Path]]:
    """Return the common declared rank count and metadata files without one."""
    dataset_dir = (manifest.parent / data.get("dataset_dir", ".")).resolve()
    declared: dict[int, list[Path]] = {}
    missing: list[Path] = []
    for index, entry in entries:
        case_relative = entry.get("case_dir")
        if not isinstance(case_relative, str) or not case_relative:
            raise SystemExit(f"Runnable manifest case {index} has no case_dir")
        case_path = (dataset_dir / case_relative).resolve()
        try:
            case_path.relative_to(dataset_dir)
        except ValueError:
            raise SystemExit(
                f"Case {index} path escapes the dataset directory: {case_relative}"
            ) from None
        relative = entry.get("metadata_file")
        if not relative:
            raise SystemExit(f"Runnable manifest case {index} has no metadata_file")
        metadata_path = (dataset_dir / relative).resolve()
        try:
            metadata_path.relative_to(dataset_dir)
        except ValueError:
            raise SystemExit(
                f"Case {index} metadata escapes the dataset directory: {relative}"
            ) from None
        if metadata_path.parent != case_path:
            raise SystemExit(
                f"Case {index} metadata is not inside its declared case_dir: {relative}"
            )
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Cannot read case {index} metadata {metadata_path}: {exc}")
        value = metadata.get("config", {}).get("runtime", {}).get("mpi_ranks")
        if value is None:
            missing.append(metadata_path)
            continue
        if isinstance(value, bool):
            raise SystemExit(f"Invalid config.runtime.mpi_ranks in {metadata_path}: {value}")
        try:
            ranks = int(value)
        except (TypeError, ValueError):
            raise SystemExit(
                f"Invalid config.runtime.mpi_ranks in {metadata_path}: {value}"
            ) from None
        if ranks <= 0 or str(ranks) != str(value):
            raise SystemExit(f"Invalid config.runtime.mpi_ranks in {metadata_path}: {value}")
        declared.setdefault(ranks, []).append(metadata_path)
    if len(declared) > 1:
        summary = ", ".join(
            f"{ranks} ({len(paths)} cases)" for ranks, paths in sorted(declared.items())
        )
        raise SystemExit(
            "Runnable cases declare inconsistent config.runtime.mpi_ranks values: "
            + summary
            + ". Generate separate manifests for different rank counts."
        )
    return (next(iter(declared)) if declared else None), missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--partition")
    parser.add_argument("--time", default="24:00:00")
    parser.add_argument(
        "--ranks",
        type=int,
        help="MPI ranks; inferred from case metadata when omitted.",
    )
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--submit", action="store_true", help="Run sbatch; default is dry-run.")
    args = parser.parse_args()

    manifest = Path(args.manifest).resolve()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entries = [
        (index, entry)
        for index, entry in enumerate(data.get("cases", []))
        if entry.get("status") in {"success", "skipped_complete"}
    ]
    if not entries:
        raise SystemExit("Manifest contains no runnable cases")
    if args.concurrency <= 0 or (args.ranks is not None and args.ranks <= 0):
        raise SystemExit("--ranks and --concurrency must be positive")

    metadata_ranks, missing_rank_metadata = _metadata_rank_count(manifest, data, entries)
    if args.ranks is None:
        if missing_rank_metadata:
            examples = ", ".join(str(path) for path in missing_rank_metadata[:3])
            raise SystemExit(
                "Cannot infer ranks because some runnable case metadata does not declare "
                f"config.runtime.mpi_ranks (for example: {examples}). Pass --ranks explicitly."
            )
        if metadata_ranks is None:
            raise SystemExit("Cannot infer ranks from an empty metadata rank set")
        ranks = metadata_ranks
    else:
        ranks = args.ranks
    if metadata_ranks is not None and ranks != metadata_ranks:
        raise SystemExit(
            f"--ranks={ranks} conflicts with case metadata config.runtime.mpi_ranks="
            f"{metadata_ranks}. Update the configuration and regenerate the cases, or "
            "submit with the metadata rank count."
        )

    indices = [str(index) for index, _ in entries]

    script = Path(__file__).resolve().parent / "run_manifest_case.slurm"
    logs = manifest.parent / "logs"
    logs.mkdir(exist_ok=True)
    array = ",".join(indices) + f"%{args.concurrency}"
    command = [
        "sbatch",
        f"--array={array}",
        f"--ntasks={ranks}",
        f"--time={args.time}",
        f"--output={logs}/run_%A_%a.out",
        f"--error={logs}/run_%A_%a.err",
        f"--export=ALL,MPI_RANKS={ranks}",
    ]
    if args.partition:
        command.append(f"--partition={args.partition}")
    command.extend([str(script), str(manifest)])
    print(shlex.join(command))
    if not args.submit:
        return 0

    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    print(completed.stdout.strip())
    record = {
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest),
        "mpi_ranks": ranks,
        "command": command,
        "sbatch_stdout": completed.stdout.strip(),
    }
    record_path = manifest.parent / "submission.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
