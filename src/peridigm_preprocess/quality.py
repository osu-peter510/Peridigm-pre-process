"""Dataset input validation and lightweight Peridigm result-quality checks."""

from __future__ import annotations

import json
import math
import numbers
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from peridigm_preprocess.validation import ValidationError, validate_generated_case


_RESULT_NAME = re.compile(
    r"(?:\.e|\.exo|\.exodus)(?:\.\d+\.\d+)?$", re.IGNORECASE
)
_SHARD_NAME = re.compile(
    r"^(?P<base>.*(?:\.e|\.exo|\.exodus))\.(?P<count>\d+)\.(?P<rank>\d+)$",
    re.IGNORECASE,
)


def _strict_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _strict_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strict_json_value(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        converted = float(value)
        return converted if math.isfinite(converted) else None
    return value


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=path.suffix, dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(
                _strict_json_value(data),
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_report_destination(
    case_dir: str | Path,
    report_path: str | Path | None,
    default_name: str,
) -> Path:
    """Resolve a JSON report path without allowing generated inputs to be overwritten."""
    directory = Path(case_dir).expanduser().resolve()
    destination = (
        Path(report_path).expanduser().resolve()
        if report_path is not None
        else directory / default_name
    )
    if destination.suffix.casefold() != ".json":
        raise ValueError("Report destination must use a .json suffix")
    if destination.is_dir():
        raise ValueError(f"Report destination is a directory: {destination}")

    reserved = {
        directory / "metadata.json",
        directory / "manifest.json",
        directory / "results" / "run_metadata.json",
    }
    metadata_path = directory / "metadata.json"
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
        files = metadata.get("files", {}) if isinstance(metadata, dict) else {}
        if isinstance(files, dict):
            for value in files.values():
                if not isinstance(value, str):
                    continue
                candidate = (directory / value).resolve()
                reserved.add(candidate)
                if candidate.is_dir() or value == files.get("results"):
                    reserved.add(candidate / "run_metadata.json")
            mesh_value = files.get("mesh")
            config = metadata.get("config", {})
            runtime = config.get("runtime", {}) if isinstance(config, dict) else {}
            ranks = runtime.get("mpi_ranks") if isinstance(runtime, dict) else None
            if isinstance(mesh_value, str) and ranks is not None:
                reserved.add(
                    Path(f"{(directory / mesh_value).resolve()}.decomp.{ranks}.json")
                )
    if destination in reserved:
        raise ValueError(
            f"Refusing to overwrite a generated input or run sidecar: {destination}"
        )
    return destination


def validate_dataset(dataset_dir: str | Path) -> dict[str, Any]:
    """Validate every successful entry in a portable dataset manifest."""
    root = Path(dataset_dir).resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValidationError(f"Missing dataset manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    reports = []
    errors = []
    cases = manifest.get("cases", [])
    for entry in cases:
        if entry.get("status") not in {"success", "skipped_complete"}:
            errors.append(f"{entry.get('tag')}: manifest status={entry.get('status')}")
            continue
        case_dir = root / entry["case_dir"]
        try:
            reports.append(validate_generated_case(case_dir))
        except Exception as exc:
            errors.append(f"{entry.get('tag')}: {exc}")
    report = {
        "status": "pass" if not errors else "fail",
        "dataset": str(root),
        "manifest_cases": len(cases),
        "validated_cases": len(reports),
        "errors": errors,
        "cases": reports,
    }
    if errors:
        raise ValidationError(
            f"Dataset validation failed with {len(errors)} error(s): "
            + "; ".join(errors[:3])
        )
    return report


def _decode_names(variable: Any) -> list[str]:
    variable.set_auto_mask(False)
    return [
        b"".join(row).split(b"\x00", 1)[0].decode("ascii", errors="replace").rstrip()
        for row in variable[:]
    ]


def _transient_variable_names(dataset: Any) -> list[str]:
    return [
        name
        for name, variable in dataset.variables.items()
        if variable.dimensions and variable.dimensions[0] in {"time_step", "time"}
    ]


def _damage_storage(dataset: Any) -> dict[str, list[tuple[str, int | None]]]:
    """Map Exodus storage variables to logical Damage fields/columns."""
    result: dict[str, list[tuple[str, int | None]]] = {}

    def add(storage: str, label: str, column: int | None = None) -> None:
        if storage in dataset.variables:
            result.setdefault(storage, []).append((label, column))

    if "name_nod_var" in dataset.variables:
        for index, label in enumerate(_decode_names(dataset.variables["name_nod_var"]), 1):
            if "damage" in label.lower():
                add(f"vals_nod_var{index}", label)
    if "name_elem_var" in dataset.variables:
        for index, label in enumerate(_decode_names(dataset.variables["name_elem_var"]), 1):
            if "damage" not in label.lower():
                continue
            prefix = f"vals_elem_var{index}eb"
            for storage in dataset.variables:
                if storage.startswith(prefix):
                    add(storage, label)
    if "name_glo_var" in dataset.variables and "vals_glo_var" in dataset.variables:
        for column, label in enumerate(_decode_names(dataset.variables["name_glo_var"])):
            if "damage" in label.lower():
                add("vals_glo_var", label, column)
    for storage in _transient_variable_names(dataset):
        if "damage" in storage.lower() and storage not in result:
            add(storage, storage)
    return result


def _scan_transient(dataset: Any, np: Any) -> dict[str, Any]:
    nonfinite = []
    scanned = 0
    damage_min = math.inf
    damage_max = -math.inf
    damage_irreversible = True
    finite_damage_found = False
    damage_storage = _damage_storage(dataset)
    previous_damage: dict[tuple[str, str, int | None], Any] = {}
    for name in _transient_variable_names(dataset):
        variable = dataset.variables[name]
        for frame in range(variable.shape[0]):
            raw_values = variable[frame]
            if np.ma.isMaskedArray(raw_values):
                values = np.asarray(np.ma.filled(raw_values, np.nan), dtype=float)
            else:
                values = np.asarray(raw_values)
            scanned += int(values.size)
            if not np.all(np.isfinite(values)):
                nonfinite.append({"variable": name, "frame": frame})
                if len(nonfinite) >= 20:
                    break
            for label, column in damage_storage.get(name, []):
                damage_values = values if column is None else values[..., column]
                if not damage_values.size:
                    continue
                finite_damage = np.isfinite(damage_values)
                if np.any(finite_damage):
                    finite_values = damage_values[finite_damage]
                    damage_min = min(damage_min, float(np.min(finite_values)))
                    damage_max = max(damage_max, float(np.max(finite_values)))
                    finite_damage_found = True
                if not np.all(finite_damage):
                    damage_irreversible = False
                key = (name, label, column)
                previous = previous_damage.get(key)
                if previous is not None and damage_values.shape == previous.shape:
                    if np.any(damage_values < previous - 1.0e-10):
                        damage_irreversible = False
                previous_damage[key] = damage_values.copy()
        if len(nonfinite) >= 20:
            break
    damage = None
    if damage_storage:
        damage = {
            "variables": [
                {"storage": storage, "field": label, "column": column}
                for storage, entries in sorted(damage_storage.items())
                for label, column in entries
            ],
            "minimum": damage_min if finite_damage_found else None,
            "maximum": damage_max if finite_damage_found else None,
            "within_unit_interval": bool(
                finite_damage_found
                and damage_min >= -1.0e-10
                and damage_max <= 1.0 + 1.0e-10
            ),
            "nondecreasing": damage_irreversible,
        }
    return {
        "values_scanned": scanned,
        "nonfinite": nonfinite,
        "damage": damage,
    }


def _validate_run_provenance(
    directory: Path,
    results_dir: Path,
    metadata: dict[str, Any],
    candidates: list[Path],
    errors: list[str],
) -> dict[str, Any] | None:
    """Require the runner sidecar and bind it to current mesh/XML metadata."""
    sidecar_path = results_dir / "run_metadata.json"
    if not sidecar_path.is_file():
        errors.append(
            "Missing results/run_metadata.json; result provenance and launch "
            "completion cannot be verified"
        )
        return None
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"Cannot read run_metadata.json: {exc}")
        return None
    if not isinstance(sidecar, dict):
        errors.append("run_metadata.json is not a JSON object")
        return None
    if sidecar.get("status") != "complete" or sidecar.get("exit_code") != 0:
        errors.append("run_metadata.json does not record a successful completed launch")

    actual_result_files = sorted(
        path.relative_to(results_dir).as_posix() for path in candidates
    )
    recorded_result_files = sidecar.get("result_files")
    if recorded_result_files != actual_result_files:
        errors.append(
            "run_metadata.json result_files does not match the exact current Exodus set"
        )
    recorded_sizes = sidecar.get("result_file_sizes")
    actual_sizes = {
        path.relative_to(results_dir).as_posix(): path.stat().st_size
        for path in candidates
    }
    if recorded_sizes != actual_sizes:
        errors.append(
            "run_metadata.json result_file_sizes does not match current Exodus files"
        )

    files = metadata["files"]
    digests = metadata["sha256"]
    expected_mesh = Path(files["mesh"]).name
    expected_xml = Path(files["xml"]).name
    expected_mesh_sha = str(digests["mesh"]).casefold()
    expected_xml_sha = str(digests["xml"]).casefold()
    for key, actual, expected in (
        ("mesh", sidecar.get("mesh"), expected_mesh),
        ("xml", sidecar.get("xml"), expected_xml),
        ("mesh_sha256", str(sidecar.get("mesh_sha256", "")).casefold(), expected_mesh_sha),
        ("xml_sha256", str(sidecar.get("xml_sha256", "")).casefold(), expected_xml_sha),
    ):
        if actual != expected:
            errors.append(
                f"run_metadata.json {key} does not match current metadata.json"
            )

    configured_ranks = metadata["config"].get("runtime", {}).get("mpi_ranks")
    if configured_ranks is None:
        errors.append("config.runtime.mpi_ranks is required to verify run provenance")
        return sidecar
    expected_ranks = int(configured_ranks)
    if sidecar.get("mpi_ranks") != expected_ranks:
        errors.append(
            f"Run used {sidecar.get('mpi_ranks')} ranks; config declares {expected_ranks}"
        )
    slurm_ntasks = sidecar.get("slurm_ntasks")
    if slurm_ntasks is not None and slurm_ntasks != expected_ranks:
        errors.append(
            f"run_metadata.json slurm_ntasks={slurm_ntasks} differs from {expected_ranks} ranks"
        )

    expected_decomp = f"{expected_mesh}.decomp.{expected_ranks}.json"
    if sidecar.get("decomposition_manifest") != expected_decomp:
        errors.append(
            "run_metadata.json decomposition_manifest does not match the current mesh/ranks"
        )
        return sidecar
    decomp_path = directory / expected_decomp
    if not decomp_path.is_file():
        errors.append(f"Missing decomposition provenance sidecar: {expected_decomp}")
        return sidecar
    try:
        decomp = json.loads(decomp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"Cannot read decomposition provenance sidecar: {exc}")
        return sidecar
    width = len(str(expected_ranks))
    expected_shards = [
        f"{expected_mesh}.{expected_ranks}.{rank:0{width}d}"
        for rank in range(expected_ranks)
    ]
    if (
        not isinstance(decomp, dict)
        or decomp.get("base_mesh") != expected_mesh
        or str(decomp.get("base_mesh_sha256", "")).casefold() != expected_mesh_sha
        or decomp.get("mpi_ranks") != expected_ranks
        or decomp.get("shards") != expected_shards
    ):
        errors.append(
            "Decomposition provenance sidecar does not match current mesh SHA/ranks/shards"
        )
    return sidecar


def _validate_result_grouping(
    candidates: list[Path], results_dir: Path, errors: list[str]
) -> None:
    """Require all Exodus candidates to share one parent and one serial base."""
    groups: dict[tuple[str, str], list[Path]] = {}
    for path in candidates:
        match = _SHARD_NAME.match(path.name)
        base = match.group("base") if match else path.name
        parent = path.parent.relative_to(results_dir).as_posix()
        groups.setdefault((parent, base), []).append(path)
    if len(groups) > 1:
        errors.append(
            "Multiple Exodus result groups were found; stale/restart outputs "
            "must not be combined into one quality report"
        )


def analyze_results(
    case_dir: str | Path,
    *,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Check result shards, time coverage, finite fields, and damage invariants.

    This is intentionally a generic first gate. Contact impulse, energy closure,
    fragment graphs, and paired-dt convergence require scenario-specific postprocessing
    and are listed as follow-up gates in the returned report.
    """
    try:
        import netCDF4
        import numpy as np
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("Result quality checks require netCDF4 and NumPy") from exc

    directory = Path(case_dir).resolve()
    input_report = validate_generated_case(directory)
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    results_dir = (directory / metadata["files"]["results"]).resolve()
    candidates = sorted(
        path
        for path in results_dir.rglob("*")
        if path.is_file() and _RESULT_NAME.search(path.name)
    )
    errors: list[str] = []
    warnings: list[str] = []
    shards = []
    if not candidates:
        errors.append(f"No Exodus result files found under {results_dir}")
    _validate_result_grouping(candidates, results_dir, errors)

    expected_final = float(metadata["config"]["solver"]["final_time"])
    output_interval = metadata["config"]["output"].get("interval_s")
    final_tolerance = max(float(output_interval or 0.0), expected_final * 1.0e-6, 1.0e-12)
    reference_times = None
    for path in candidates:
        if path.stat().st_size == 0:
            errors.append(f"Empty result shard: {path}")
            continue
        try:
            with netCDF4.Dataset(path, "r") as dataset:
                if "time_whole" not in dataset.variables:
                    errors.append(f"Missing time_whole: {path.name}")
                    continue
                raw_times = dataset.variables["time_whole"][:]
                if np.ma.isMaskedArray(raw_times):
                    raw_times = np.ma.filled(raw_times, np.nan)
                times = np.asarray(raw_times, dtype=float)
                if not len(times):
                    errors.append(f"No output frames: {path.name}")
                    continue
                if not np.all(np.isfinite(times)) or np.any(np.diff(times) <= 0.0):
                    errors.append(f"Invalid/non-increasing time axis: {path.name}")
                if reference_times is None:
                    reference_times = times
                elif len(times) != len(reference_times) or not np.allclose(
                    times, reference_times, rtol=0.0, atol=1.0e-12
                ):
                    errors.append(f"Result shard time axes differ: {path.name}")
                if abs(float(times[-1]) - expected_final) > final_tolerance:
                    errors.append(
                        f"{path.name} ends at {times[-1]:.9g}s; expected {expected_final:.9g}s"
                    )
                scan = _scan_transient(dataset, np)
                if scan["nonfinite"]:
                    errors.append(f"Non-finite transient values in {path.name}")
                if scan["damage"] is not None:
                    if not scan["damage"]["within_unit_interval"]:
                        errors.append(f"Damage outside [0,1] in {path.name}")
                    if not scan["damage"]["nondecreasing"]:
                        errors.append(f"Damage decreases over time in {path.name}")
                name_groups = {}
                for variable_name in ("name_nod_var", "name_elem_var", "name_glo_var"):
                    if variable_name in dataset.variables:
                        name_groups[variable_name] = _decode_names(
                            dataset.variables[variable_name]
                        )
                shards.append(
                    {
                        "file": str(path.relative_to(directory)),
                        "size_bytes": path.stat().st_size,
                        "frames": int(len(times)),
                        "initial_time_s": float(times[0]),
                        "final_time_s": float(times[-1]),
                        "fields": name_groups,
                        "scan": scan,
                    }
                )
        except OSError as exc:
            errors.append(f"Cannot open result shard {path.name}: {exc}")

    runtime = metadata["config"].get("runtime", {})
    configured_ranks = runtime.get("mpi_ranks")
    run_sidecar = _validate_run_provenance(
        directory, results_dir, metadata, candidates, errors
    )

    shard_matches = [(path, _SHARD_NAME.match(path.name)) for path in candidates]
    sharded = [(path, match) for path, match in shard_matches if match is not None]
    if sharded:
        if len(sharded) != len(candidates):
            errors.append("Serial and decomposed Exodus results are mixed")
        declared_counts = {int(match.group("count")) for _, match in sharded}
        if len(declared_counts) != 1:
            errors.append("Result shard filenames declare different MPI rank counts")
        else:
            declared = next(iter(declared_counts))
            indices = [int(match.group("rank")) for _, match in sharded]
            if len(indices) != declared or set(indices) != set(range(declared)):
                errors.append(
                    f"Result shards do not contain the exact rank suffix set 0..{declared - 1}"
                )
            if configured_ranks and declared != int(configured_ranks):
                errors.append(
                    f"Result filenames declare {declared} ranks; config declares {int(configured_ranks)}"
                )
            if isinstance(run_sidecar, dict) and run_sidecar.get("mpi_ranks") != declared:
                errors.append(
                    "Result shard rank count differs from run_metadata.json"
                )
    elif len(candidates) > 1:
        errors.append("Multiple serial Exodus result files were found for one case")
    if not errors and not any(shard["scan"]["damage"] for shard in shards):
        warnings.append("No damage field was found; fracture quality was not assessed")

    report = {
        "schema_version": 1,
        "status": "pass" if not errors else "fail",
        "case": metadata["tag"],
        "scenario": metadata["scenario"],
        "input_validation": input_report,
        "result_file_count": len(candidates),
        "expected_final_time_s": expected_final,
        "errors": errors,
        "warnings": warnings,
        "shards": shards,
        "recommended_follow_up_gates": [
            "contact impulse versus center-of-mass momentum closure",
            "energy and global momentum balance",
            "paired-dt convergence",
            "mesh/horizon convergence",
            "volume-weighted damage and broken-bond fragment analysis",
        ],
    }
    report = _strict_json_value(report)
    destination = _safe_report_destination(
        directory, report_path, "quality_report.json"
    )
    _write_json_atomic(destination, report)
    return report
