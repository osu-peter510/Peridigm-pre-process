"""Portable analysis of Peridigm impact results with qualified weighting.

The functions in this module understand both a single Exodus result file and
rank-local Exodus/Nemesis files.  Exodus global element IDs establish shard
ownership; ambiguous duplicate element ownership is rejected before a report
can pass.  Nodal quantities are
first mapped through each selected element block's connectivity.  Valid
material-point ``Volume`` is used for physical reductions; otherwise only
explicitly named count-weighted quantities are returned and dimensional
integrals are suppressed.  This is important for Peridigm ``SPHERE`` output,
where local node and element ordering must not be assumed to match.

NumPy and netCDF4 are optional at package-import time.  They are imported only
when result data are actually read or convergence reports are compared.
Returned reports contain only JSON-compatible Python values and case-relative
file names; no machine-specific path is embedded in a report.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


__all__ = [
    "analyze_impact_case",
    "compare_convergence_reports",
    "read_volume_weighted_timeseries",
]


_RESULT_NAME = re.compile(
    r"(?:\.e|\.exo|\.exodus)(?:\.\d+\.\d+)?$", re.IGNORECASE
)
_SHARD_NAME = re.compile(
    r"^(?P<base>.*(?:\.e|\.exo|\.exodus))\.(?P<count>\d+)\.(?P<rank>\d+)$",
    re.IGNORECASE,
)
_AXES = ("X", "Y", "Z")
_DEFAULT_DAMAGE_THRESHOLDS = (0.01, 0.1, 0.5)


def _dependencies() -> tuple[Any, Any]:
    """Return netCDF4 and NumPy, importing them only on first use."""
    try:
        import netCDF4
        import numpy as np
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "Peridigm result analysis requires netCDF4 and NumPy"
        ) from exc
    return netCDF4, np


def _warn(warnings: list[str], message: str) -> None:
    """Append a warning once while preserving discovery order."""
    if message not in warnings:
        warnings.append(message)


def _normal_name(value: Any) -> str:
    """Normalize Exodus/configuration names for tolerant matching."""
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _json_value(value: Any, np: Any) -> Any:
    """Convert arrays/scalars to strict JSON-compatible Python values."""
    if isinstance(value, Mapping):
        return {str(key): _json_value(item, np) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item, np) for item in value]
    if isinstance(value, np.ndarray):
        return _json_value(value.tolist(), np)
    if isinstance(value, np.generic):
        return _json_value(value.item(), np)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _decode_names(variable: Any, np: Any) -> list[str]:
    """Decode either byte or unicode Exodus character-name tables."""
    try:
        variable.set_auto_mask(False)
    except AttributeError:
        pass
    values = np.asarray(variable[:])
    result: list[str] = []
    for row in values:
        row_array = np.asarray(row)
        if row_array.dtype.kind == "U":
            text = "".join(str(part) for part in row_array.reshape(-1))
        elif row_array.dtype.kind == "S":
            text = row_array.tobytes().decode("ascii", errors="replace")
        else:
            text = bytes(int(part) for part in row_array.reshape(-1)).decode(
                "ascii", errors="replace"
            )
        result.append(text.split("\x00", 1)[0].rstrip())
    return result


def _dimension_length(dataset: Any, name: str) -> int:
    """Read a netCDF3 integer dimension or a netCDF4 Dimension object."""
    dimension = dataset.dimensions.get(name)
    if dimension is None:
        return 0
    try:
        return len(dimension)
    except TypeError:
        return int(dimension)


def _load_metadata(directory: Path, warnings: list[str]) -> dict[str, Any]:
    path = directory / "metadata.json"
    if not path.is_file():
        _warn(warnings, "metadata.json was not found; configuration-derived values are unavailable")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _warn(warnings, f"metadata.json could not be read: {type(exc).__name__}: {exc}")
        return {}
    if not isinstance(value, dict):
        _warn(warnings, "metadata.json is not a JSON object")
        return {}
    return value


def _result_files(
    directory: Path,
    metadata: Mapping[str, Any],
    warnings: list[str],
    errors: list[str],
) -> list[Path]:
    """Find one coherent serial or Nemesis Exodus result-file group."""
    configured = metadata.get("files", {})
    configured_result = configured.get("results") if isinstance(configured, Mapping) else None
    if configured_result:
        result_root = (directory / str(configured_result)).resolve()
        try:
            result_root.relative_to(directory.resolve())
        except ValueError:
            errors.append(
                "Configured results path escapes the case directory; no files were read"
            )
            return []
    elif (directory / "results").is_dir():
        result_root = directory / "results"
    else:
        result_root = directory
    if not result_root.is_dir():
        _warn(warnings, f"configured results directory does not exist: {result_root.name}")
        return []

    candidates = sorted(
        (
            path
            for path in result_root.rglob("*")
            if path.is_file() and _RESULT_NAME.search(path.name)
        ),
        key=lambda path: path.as_posix(),
    )
    if not candidates:
        return []

    groups: dict[tuple[str, str], list[Path]] = {}
    for path in candidates:
        # Remove the conventional ``.<ranks>.<rank>`` suffix only.  A serial
        # ``case.e`` and a decomposed ``case.e.N.R`` therefore form one group.
        match = _SHARD_NAME.match(path.name)
        base = match.group("base") if match else path.name
        parent = path.parent.relative_to(result_root).as_posix()
        groups.setdefault((parent, base), []).append(path)
    if len(groups) != 1:
        errors.append(
            "Multiple Exodus result groups were found; move stale/restart outputs "
            "or analyze one unambiguous results directory"
        )
        return []
    selected = next(iter(groups.values()))
    parsed = [(path, _SHARD_NAME.match(path.name)) for path in selected]
    sharded = [(path, match) for path, match in parsed if match is not None]
    if sharded and len(sharded) != len(selected):
        errors.append("Serial and decomposed Exodus outputs are mixed")
        return []
    if sharded:
        declared_counts = {int(match.group("count")) for _, match in sharded}
        if len(declared_counts) != 1:
            errors.append("Nemesis shard filenames declare inconsistent rank counts")
            return []
        declared = next(iter(declared_counts))
        ranks = [int(match.group("rank")) for _, match in sharded]
        if len(ranks) != declared or set(ranks) != set(range(declared)):
            errors.append(
                f"Incomplete Nemesis result set: expected exact ranks 0..{declared - 1}"
            )
            return []
        configured = metadata.get("config", {})
        runtime = configured.get("runtime", {}) if isinstance(configured, Mapping) else {}
        expected_ranks = runtime.get("mpi_ranks") if isinstance(runtime, Mapping) else None
        if expected_ranks is not None:
            try:
                configured_ranks = int(expected_ranks)
            except (TypeError, ValueError):
                errors.append("metadata config.runtime.mpi_ranks is not an integer")
                return []
            if configured_ranks != declared:
                errors.append(
                    f"Nemesis filenames declare {declared} ranks but metadata declares "
                    f"{configured_ranks}"
                )
                return []
    return sorted(selected, key=lambda path: path.as_posix())


def _time_axes(
    files: Sequence[Path],
    netcdf4: Any,
    np: Any,
    warnings: list[str],
    errors: list[str],
) -> tuple[Any | None, dict[Path, Any]]:
    """Read shard time axes and choose their common saved frames."""
    axes: dict[Path, Any] = {}
    for path in files:
        try:
            with netcdf4.Dataset(path, "r") as dataset:
                if "time_whole" not in dataset.variables:
                    errors.append(f"{path.name}: missing time_whole")
                    continue
                times = _float_array(
                    dataset.variables["time_whole"][:], np
                ).reshape(-1)
        except OSError as exc:
            errors.append(f"{path.name}: cannot open Exodus result ({exc})")
            continue
        if not len(times) or not np.all(np.isfinite(times)):
            errors.append(f"{path.name}: empty or non-finite time axis")
            continue
        if len(times) > 1 and np.any(np.diff(times) <= 0.0):
            errors.append(f"{path.name}: non-increasing time axis")
            continue
        axes[path] = times
    if not axes:
        return None, {}

    first = next(iter(axes.values()))
    common = first.copy()
    for times in axes.values():
        if len(times) == len(common) and np.allclose(times, common, rtol=0.0, atol=1.0e-12):
            continue
        common = np.asarray(
            [
                value
                for value in common
                if np.any(np.isclose(times, value, rtol=0.0, atol=1.0e-12))
            ],
            dtype=float,
        )
    if not len(common):
        errors.append("Exodus shards have no common saved time frames")
        return None, axes
    if any(len(times) != len(common) for times in axes.values()):
        errors.append(
            "Exodus shard time axes differ; a partial common-frame merge is not accepted"
        )
    return common, axes


def _time_indices(source: Any, common: Any, np: Any) -> Any | None:
    indices = []
    for value in common:
        matches = np.flatnonzero(np.isclose(source, value, rtol=0.0, atol=1.0e-12))
        if not len(matches):
            return None
        indices.append(int(matches[0]))
    return np.asarray(indices, dtype=np.int64)


def _validate_result_completion(
    directory: Path,
    metadata: Mapping[str, Any],
    files: Sequence[Path],
    common_times: Any,
    warnings: list[str],
    errors: list[str],
) -> None:
    """Validate configured final time and an optional Slurm run sidecar."""
    config = metadata.get("config", {})
    if not isinstance(config, Mapping):
        config = {}
    solver = config.get("solver", {})
    output = config.get("output", {})
    if isinstance(solver, Mapping) and solver.get("final_time") is not None:
        try:
            expected_final = float(solver["final_time"])
            interval = (
                float(output.get("interval_s", 0.0))
                if isinstance(output, Mapping)
                else 0.0
            )
        except (TypeError, ValueError):
            errors.append("metadata solver final time or output interval is not numeric")
        else:
            tolerance = max(abs(interval), abs(expected_final) * 1.0e-6, 1.0e-12)
            actual_final = float(common_times[-1])
            if not math.isfinite(expected_final) or abs(actual_final - expected_final) > tolerance:
                errors.append(
                    f"Results end at {actual_final:.12g}s; expected configured final "
                    f"time {expected_final:.12g}s"
                )

    configured_files = metadata.get("files", {})
    configured_results = (
        configured_files.get("results")
        if isinstance(configured_files, Mapping)
        else None
    )
    if configured_results:
        results_root = (directory / str(configured_results)).resolve()
    elif (directory / "results").is_dir():
        results_root = (directory / "results").resolve()
    else:
        # A metadata-free case may place Exodus and run_metadata.json together
        # in the case root (or in one nested result group).  Validate the
        # sidecar beside the files that were actually selected.
        results_root = files[0].parent.resolve()
    try:
        results_root.relative_to(directory)
    except ValueError:
        return
    sidecar = results_root / "run_metadata.json"
    if not sidecar.is_file():
        return
    try:
        run = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"run_metadata.json could not be read: {type(exc).__name__}: {exc}")
        return
    if not isinstance(run, Mapping):
        errors.append("run_metadata.json is not a JSON object")
        return
    if run.get("status") != "complete" or run.get("exit_code") != 0:
        errors.append("run_metadata.json does not record a successful completed run")
    actual_result_files = sorted(
        path.relative_to(results_root).as_posix() for path in files
    )
    if run.get("result_files") != actual_result_files:
        errors.append(
            "run_metadata.json result_files differs from the exact Exodus input set"
        )
    actual_result_sizes = {
        path.relative_to(results_root).as_posix(): path.stat().st_size
        for path in files
    }
    if run.get("result_file_sizes") != actual_result_sizes:
        errors.append(
            "run_metadata.json result_file_sizes differs from current Exodus files"
        )

    if isinstance(configured_files, Mapping):
        digests = metadata.get("sha256", {})
        expected_values = (
            ("mesh", Path(str(configured_files.get("mesh", ""))).name),
            ("xml", Path(str(configured_files.get("xml", ""))).name),
            (
                "mesh_sha256",
                str(digests.get("mesh", "")).casefold()
                if isinstance(digests, Mapping)
                else "",
            ),
            (
                "xml_sha256",
                str(digests.get("xml", "")).casefold()
                if isinstance(digests, Mapping)
                else "",
            ),
        )
        for key, expected in expected_values:
            if not expected:
                continue
            actual = run.get(key)
            if key.endswith("sha256"):
                actual = str(actual or "").casefold()
            if actual != expected:
                errors.append(
                    f"run_metadata.json {key} differs from current metadata.json"
                )
    configured_runtime = config.get("runtime", {})
    configured_ranks = (
        configured_runtime.get("mpi_ranks")
        if isinstance(configured_runtime, Mapping)
        else None
    )
    declared_ranks = None
    if files:
        match = _SHARD_NAME.match(files[0].name)
        if match is not None:
            declared_ranks = int(match.group("count"))
    sidecar_ranks = run.get("mpi_ranks")
    try:
        sidecar_ranks_int = int(sidecar_ranks)
    except (TypeError, ValueError):
        errors.append("run_metadata.json mpi_ranks is not an integer")
        return
    if configured_ranks is not None:
        try:
            configured_ranks_int = int(configured_ranks)
        except (TypeError, ValueError):
            errors.append("metadata config.runtime.mpi_ranks is not an integer")
        else:
            if sidecar_ranks_int != configured_ranks_int:
                errors.append(
                    "run_metadata.json mpi_ranks differs from config.runtime.mpi_ranks"
                )
    if declared_ranks is not None and sidecar_ranks_int != declared_ranks:
        errors.append("run_metadata.json mpi_ranks differs from Nemesis filenames")


def _field_table(dataset: Any, table_name: str, np: Any) -> dict[str, int]:
    """Map normalized Exodus output names to one-based variable indices."""
    if table_name not in dataset.variables:
        return {}
    return {
        _normal_name(name): index
        for index, name in enumerate(_decode_names(dataset.variables[table_name], np), start=1)
        if name
    }


def _float_array(values: Any, np: Any) -> Any:
    """Convert netCDF values to float while preserving masked/fill values as NaN."""
    if np.ma.isMaskedArray(values):
        values = np.ma.filled(values, np.nan)
    return np.asarray(values, dtype=float)


def _history_shape(values: Any, frames: int, items: int, np: Any) -> Any | None:
    """Normalize a scalar history to ``(time, item)`` without guessing wildly."""
    array = _float_array(values, np)
    if array.ndim == 0:
        return np.full((frames, items), float(array), dtype=float)
    if array.ndim == 1:
        if array.size == items:
            return np.broadcast_to(array[None, :], (frames, items)).copy()
        if items == 1 and array.size == frames:
            return array[:, None]
        return None
    if array.ndim == 2:
        if array.shape == (frames, items):
            return array
        if array.shape == (items, frames):
            return array.T
    array = np.squeeze(array)
    if array.ndim != 2:
        return None
    if array.shape[1] == items:
        return array
    if array.shape[0] == items:
        return array.T
    return None


def _nodal_scalar(
    dataset: Any,
    table: Mapping[str, int],
    aliases: Iterable[str],
    frames: int,
    nodes: int,
    np: Any,
) -> Any | None:
    for alias in aliases:
        index = table.get(_normal_name(alias))
        if index is None:
            continue
        name = f"vals_nod_var{index}"
        if name in dataset.variables:
            return _history_shape(dataset.variables[name][:], frames, nodes, np)
    return None


def _element_scalar(
    dataset: Any,
    table: Mapping[str, int],
    aliases: Iterable[str],
    block_index: int,
    frames: int,
    elements: int,
    np: Any,
) -> Any | None:
    for alias in aliases:
        variable_index = table.get(_normal_name(alias))
        if variable_index is None:
            continue
        name = f"vals_elem_var{variable_index}eb{block_index}"
        if name in dataset.variables:
            return _history_shape(dataset.variables[name][:], frames, elements, np)
    return None


def _static_coordinates(dataset: Any, nodes: int, np: Any) -> Any | None:
    """Read conventional Exodus model coordinates as ``(node, 3)``."""
    result = np.full((nodes, 3), np.nan, dtype=float)
    found = False
    if "coord" in dataset.variables:
        values = _float_array(dataset.variables["coord"][:], np)
        if values.ndim == 2:
            if values.shape[1] == nodes:
                values = values.T
            if values.shape[0] == nodes:
                width = min(3, values.shape[1])
                result[:, :width] = values[:, :width]
                found = True
    for axis, variable_name in enumerate(("coordx", "coordy", "coordz")):
        if variable_name in dataset.variables:
            values = _float_array(dataset.variables[variable_name][:], np).reshape(-1)
            if len(values) == nodes:
                result[:, axis] = values
                found = True
    return result if found else None


def _nodal_vector(
    dataset: Any,
    table: Mapping[str, int],
    stem: str,
    frames: int,
    nodes: int,
    np: Any,
) -> Any | None:
    components = [
        _nodal_scalar(dataset, table, (f"{stem}{axis}",), frames, nodes, np)
        for axis in _AXES
    ]
    if not any(component is not None for component in components):
        return None
    # A partially emitted vector is still useful in its available directions;
    # NaNs prevent a missing component from silently becoming a physical zero.
    return np.stack(
        [
            component
            if component is not None
            else np.full((frames, nodes), np.nan, dtype=float)
            for component in components
        ],
        axis=2,
    )


def _element_vector(
    dataset: Any,
    table: Mapping[str, int],
    stem: str,
    block_index: int,
    frames: int,
    elements: int,
    np: Any,
) -> Any | None:
    components = [
        _element_scalar(
            dataset,
            table,
            (f"{stem}{axis}",),
            block_index,
            frames,
            elements,
            np,
        )
        for axis in _AXES
    ]
    if not any(component is not None for component in components):
        return None
    return np.stack(
        [
            component
            if component is not None
            else np.full((frames, elements), np.nan, dtype=float)
            for component in components
        ],
        axis=2,
    )


def _element_centroids(nodal_values: Any, connectivity: Any, np: Any) -> Any:
    """Map a nodal scalar/vector history to element centers."""
    local = np.asarray(connectivity, dtype=np.int64) - 1
    return np.nanmean(nodal_values[:, local, :], axis=2)


def _element_scalar_from_nodes(values: Any, connectivity: Any, np: Any) -> Any:
    local = np.asarray(connectivity, dtype=np.int64) - 1
    return np.nanmean(values[:, local], axis=2)


def _geometric_volumes(dataset: Any, connectivity: Any, nodes: int, np: Any) -> Any | None:
    """Compute reference TET4 volumes when no output ``Volume`` exists."""
    cells = np.asarray(connectivity, dtype=np.int64) - 1
    if cells.ndim != 2 or cells.shape[1] != 4:
        return None
    coordinates = _static_coordinates(dataset, nodes, np)
    if coordinates is None or not np.all(np.isfinite(coordinates[cells])):
        return None
    points = coordinates[cells]
    triple = np.einsum(
        "ij,ij->i",
        points[:, 1] - points[:, 0],
        np.cross(points[:, 2] - points[:, 0], points[:, 3] - points[:, 0]),
    )
    return np.abs(triple) / 6.0


def _block_catalog(dataset: Any, block_count: int, np: Any) -> list[dict[str, Any]]:
    names = [""] * block_count
    if "eb_names" in dataset.variables:
        decoded = _decode_names(dataset.variables["eb_names"], np)
        names[: min(len(decoded), block_count)] = decoded[:block_count]
    ids = list(range(1, block_count + 1))
    if "eb_prop1" in dataset.variables:
        raw = np.asarray(dataset.variables["eb_prop1"][:], dtype=np.int64).reshape(-1)
        ids[: min(len(raw), block_count)] = [int(value) for value in raw[:block_count]]
    return [
        {
            "index": index,
            "id": ids[index - 1],
            "name": names[index - 1] or f"block_{ids[index - 1]}",
        }
        for index in range(1, block_count + 1)
    ]


def _config_block_info(metadata: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Index configured blocks by every useful Exodus/configuration alias."""
    config = metadata.get("config", {})
    blocks = config.get("blocks", {}) if isinstance(config, Mapping) else {}
    materials = config.get("materials", {}) if isinstance(config, Mapping) else {}
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(blocks, Mapping):
        return result
    for logical_id, raw_block in blocks.items():
        if not isinstance(raw_block, Mapping):
            continue
        material_id = raw_block.get("material")
        raw_material = materials.get(material_id, {}) if isinstance(materials, Mapping) else {}
        parameters = raw_material.get("parameters", {}) if isinstance(raw_material, Mapping) else {}
        density = parameters.get("Density") if isinstance(parameters, Mapping) else None
        try:
            density_value = float(density) if density is not None else None
            if density_value is not None and (not math.isfinite(density_value) or density_value <= 0.0):
                density_value = None
        except (TypeError, ValueError):
            density_value = None
        entry = {
            "logical_id": str(logical_id),
            "exodus_name": str(raw_block.get("block_names", logical_id)),
            "display_name": str(raw_block.get("name", logical_id)),
            "material": str(material_id) if material_id is not None else None,
            "density_kg_m3": density_value,
            "damage_model": raw_block.get("damage_model"),
        }
        for alias in (
            logical_id,
            entry["exodus_name"],
            entry["display_name"],
        ):
            result[_normal_name(alias)] = entry
    return result


def _requested_blocks(
    metadata: Mapping[str, Any], blocks: Sequence[str | int] | str | int | None
) -> list[str | int] | None:
    if blocks is not None:
        if isinstance(blocks, (str, int)):
            return [blocks]
        return list(blocks)
    config = metadata.get("config", {})
    analysis = config.get("analysis", {}) if isinstance(config, Mapping) else {}
    if not isinstance(analysis, Mapping):
        analysis = {}
    configured = analysis.get("blocks", analysis.get("selected_blocks"))
    if configured is None:
        return None
    if isinstance(configured, (str, int)):
        return [configured]
    return list(configured)


def _block_selected(
    block: Mapping[str, Any],
    selectors: Sequence[str | int] | None,
    config_info: Mapping[str, Mapping[str, Any]],
) -> bool:
    if selectors is None:
        return True
    aliases = {
        _normal_name(block["name"]),
        _normal_name(block["id"]),
        _normal_name(f"block_{block['id']}"),
    }
    info = config_info.get(_normal_name(block["name"]))
    if info:
        aliases.update(
            _normal_name(info.get(key, ""))
            for key in ("logical_id", "exodus_name", "display_name")
        )
    return any(_normal_name(selector) in aliases for selector in selectors)


def _density_for_block(
    block: Mapping[str, Any], config_info: Mapping[str, Mapping[str, Any]]
) -> tuple[float | None, Mapping[str, Any] | None]:
    for alias in (block["name"], block["id"], f"block_{block['id']}"):
        info = config_info.get(_normal_name(alias))
        if info:
            return info.get("density_kg_m3"), info
    return None, None


def _body_force(
    metadata: Mapping[str, Any],
    selected_blocks: Mapping[str, Mapping[str, Any]],
    np: Any,
    warnings: list[str],
) -> Any | None:
    """Resolve constant selected-body forces from configured Body Force entries."""
    config = metadata.get("config", {})
    conditions = config.get("boundary_conditions", []) if isinstance(config, Mapping) else []
    if not isinstance(conditions, Sequence):
        return None
    total = np.zeros(3, dtype=float)
    found = False
    for condition in conditions:
        if not isinstance(condition, Mapping):
            continue
        if _normal_name(condition.get("type", "")) != "bodyforce":
            continue
        coordinate = str(condition.get("coordinate", "")).casefold()
        if coordinate not in {"x", "y", "z"}:
            _warn(warnings, "a configured Body Force has no supported x/y/z coordinate")
            continue
        material = condition.get("material")
        matching = [
            entry
            for entry in selected_blocks.values()
            if material is None or entry.get("material") == str(material)
        ]
        if not matching:
            continue
        axis = {"x": 0, "y": 1, "z": 2}[coordinate]
        if "acceleration" in condition:
            try:
                acceleration = float(condition["acceleration"])
            except (TypeError, ValueError):
                _warn(warnings, "a configured Body Force acceleration is not numeric")
                continue
            for entry in matching:
                density = entry.get("density_kg_m3")
                volume = entry.get("initial_volume_m3")
                if density is None or volume is None:
                    _warn(warnings, "Body Force could not be resolved because block density/volume is missing")
                    continue
                total[axis] += float(density) * float(volume) * acceleration
                found = True
        elif "value" in condition:
            try:
                force_density = float(condition["value"])
            except (TypeError, ValueError):
                _warn(warnings, "a configured Body Force value is not numeric")
                continue
            for entry in matching:
                volume = entry.get("initial_volume_m3")
                if volume is not None:
                    total[axis] += force_density * float(volume)
                    found = True
        else:
            _warn(warnings, "a configured Body Force has neither acceleration nor value")
    return total if found else None


def _empty_accumulators(frames: int, thresholds: Sequence[float], np: Any) -> dict[str, Any]:
    zeros = lambda width=None: np.zeros((frames,) if width is None else (frames, width), dtype=float)
    return {
        "volume": zeros(),
        "volume_weight": {"coordinates": zeros(), "velocity": zeros(), "damage": zeros()},
        "volume_moment": {"coordinates": zeros(3), "velocity": zeros(3)},
        "count": zeros(),
        "count_weight": {"coordinates": zeros(), "velocity": zeros(), "damage": zeros()},
        "count_moment": {"coordinates": zeros(3), "velocity": zeros(3)},
        "mass": zeros(),
        "mass_weight": {"coordinates": zeros(), "velocity": zeros()},
        "mass_moment": {"coordinates": zeros(3), "velocity": zeros(3)},
        "force": zeros(3),
        "force_coverage": zeros(),
        "damage_moment": zeros(),
        "damage_positive_volume": zeros(),
        "damage_threshold_volume": {float(value): zeros() for value in thresholds},
        "damage_count_moment": zeros(),
        "damage_positive_count": zeros(),
        "damage_threshold_count": {float(value): zeros() for value in thresholds},
    }


def _finite_weighted_add(moment: Any, denominator: Any, values: Any, weights: Any, np: Any) -> None:
    """Add a field while excluding elements with non-finite saved values."""
    finite = np.all(np.isfinite(values), axis=2) if values.ndim == 3 else np.isfinite(values)
    valid_weight = np.where(finite, weights, 0.0)
    clean = np.where(np.expand_dims(finite, 2), values, 0.0) if values.ndim == 3 else np.where(finite, values, 0.0)
    if values.ndim == 3:
        moment += np.sum(clean * valid_weight[:, :, None], axis=1)
    else:
        moment += np.sum(clean * valid_weight, axis=1)
    denominator += np.sum(valid_weight, axis=1)


def _safe_divide(numerator: Any, denominator: Any, np: Any) -> Any:
    if numerator.ndim == 2:
        return np.divide(
            numerator,
            denominator[:, None],
            out=np.full_like(numerator, np.nan, dtype=float),
            where=denominator[:, None] > 0.0,
        )
    return np.divide(
        numerator,
        denominator,
        out=np.full_like(numerator, np.nan, dtype=float),
        where=denominator > 0.0,
    )


def _read_arrays(
    case_dir: str | Path,
    *,
    blocks: Sequence[str | int] | str | int | None,
    damage_thresholds: Sequence[float],
) -> dict[str, Any]:
    """Internal reader returning NumPy arrays plus portable metadata."""
    netcdf4, np = _dependencies()
    directory = Path(case_dir).expanduser().resolve()
    warnings: list[str] = []
    errors: list[str] = []
    metadata = _load_metadata(directory, warnings)
    files = _result_files(directory, metadata, warnings, errors)
    if not files:
        if not errors:
            errors.append("No serial or Nemesis Exodus result files were found")
        return {
            "np": np,
            "metadata": metadata,
            "warnings": warnings,
            "errors": errors,
            "files": [],
            "times": None,
        }
    common_times, axes = _time_axes(files, netcdf4, np, warnings, errors)
    if common_times is None:
        errors.append("No usable common Exodus time axis was found")
        return {
            "np": np,
            "metadata": metadata,
            "warnings": warnings,
            "errors": errors,
            "files": files,
            "times": None,
        }
    _validate_result_completion(
        directory, metadata, files, common_times, warnings, errors
    )

    thresholds = tuple(sorted({float(value) for value in damage_thresholds}))
    if any(not math.isfinite(value) or value < 0.0 for value in thresholds):
        raise ValueError("damage_thresholds must contain finite nonnegative values")
    selectors = _requested_blocks(metadata, blocks)
    config_info = _config_block_info(metadata)
    accum = _empty_accumulators(len(common_times), thresholds, np)
    seen_elements: set[Any] = set()
    seen_nodes: set[Any] = set()
    force_seen_nodes: set[Any] = set()
    selected_catalog: dict[str, dict[str, Any]] = {}
    block_identities: dict[int, str] = {}
    selected_aliases: set[str] = set()
    duplicate_elements = 0
    synthetic_ids = False
    volume_fallback = False
    physical_volume_complete = True
    force_source: str | None = None
    force_sources_seen: set[str] = set()
    force_bases_seen: set[str] = set()
    field_element_counts = {"coordinates": 0, "velocity": 0, "damage": 0, "force": 0}
    total_elements = 0
    density_missing = False
    volume_changed = False

    usable_files = [path for path in files if path in axes]
    for file_number, path in enumerate(usable_files):
        source_times = axes[path]
        indices = _time_indices(source_times, common_times, np)
        if indices is None:
            errors.append(
                f"{path.name}: common time indices could not be resolved"
            )
            continue
        try:
            dataset_context = netcdf4.Dataset(path, "r")
        except OSError as exc:
            errors.append(f"{path.name}: cannot reopen Exodus result ({exc})")
            continue
        with dataset_context as dataset:
            nodal_table = _field_table(dataset, "name_nod_var", np)
            element_table = _field_table(dataset, "name_elem_var", np)
            nodes = _dimension_length(dataset, "num_nodes") or _dimension_length(dataset, "num_node")
            block_count = _dimension_length(dataset, "num_el_blk")
            if not nodes or not block_count:
                errors.append(
                    f"{path.name}: missing node or element-block dimensions"
                )
                continue
            node_map = None
            if "node_num_map" in dataset.variables:
                node_map = np.asarray(dataset.variables["node_num_map"][:], dtype=np.int64).reshape(-1)
            element_map = None
            if "elem_num_map" in dataset.variables:
                element_map = np.asarray(dataset.variables["elem_num_map"][:], dtype=np.int64).reshape(-1)
            declared_elements = (
                _dimension_length(dataset, "num_elem")
                or _dimension_length(dataset, "num_elements")
            )
            block_dimension_total = sum(
                _dimension_length(dataset, f"num_el_in_blk{index}")
                for index in range(1, block_count + 1)
            )
            structural_problem = False
            if node_map is not None and len(node_map) != nodes:
                errors.append(
                    f"{path.name}: node_num_map length {len(node_map)} does not "
                    f"match num_nodes={nodes}"
                )
                structural_problem = True
            if element_map is not None and declared_elements and len(element_map) != declared_elements:
                errors.append(
                    f"{path.name}: elem_num_map length {len(element_map)} does not "
                    f"match num_elem={declared_elements}"
                )
                structural_problem = True
            if declared_elements and block_dimension_total != declared_elements:
                errors.append(
                    f"{path.name}: element-block dimensions total {block_dimension_total} "
                    f"but num_elem={declared_elements}"
                )
                structural_problem = True
            if len(usable_files) > 1:
                if element_map is None:
                    synthetic_ids = True
                    errors.append(
                        f"{path.name}: Nemesis shard lacks elem_num_map"
                    )
                    structural_problem = True
                if node_map is None:
                    errors.append(
                        f"{path.name}: Nemesis shard lacks node_num_map"
                    )
                    structural_problem = True
            if structural_problem:
                continue

            frames = len(source_times)
            coordinates_nodal = _nodal_vector(dataset, nodal_table, "Coordinates", frames, nodes, np)
            if coordinates_nodal is None:
                displacement = _nodal_vector(dataset, nodal_table, "Displacement", frames, nodes, np)
                reference = _static_coordinates(dataset, nodes, np)
                if displacement is not None and reference is not None:
                    coordinates_nodal = displacement + reference[None, :, :]
                elif reference is not None:
                    coordinates_nodal = np.broadcast_to(reference[None, :, :], (frames, nodes, 3)).copy()
                    _warn(warnings, "transient Coordinates/Displacement are absent; COM position uses static mesh coordinates")
            velocity_nodal = _nodal_vector(dataset, nodal_table, "Velocity", frames, nodes, np)

            contact_density_nodal = _nodal_vector(
                dataset, nodal_table, "Contact_Force_Density", frames, nodes, np
            )
            contact_direct_nodal = _nodal_vector(
                dataset, nodal_table, "Contact_Force", frames, nodes, np
            )
            resultant_density_nodal = _nodal_vector(
                dataset, nodal_table, "Force_Density", frames, nodes, np
            )
            resultant_direct_nodal = _nodal_vector(dataset, nodal_table, "Force", frames, nodes, np)

            element_offset = 0
            for block in _block_catalog(dataset, block_count, np):
                block_index = int(block["index"])
                connect_name = f"connect{block_index}"
                if connect_name not in dataset.variables:
                    count = _dimension_length(dataset, f"num_el_in_blk{block_index}")
                    if count:
                        errors.append(
                            f"{path.name}: missing connectivity variable {connect_name}"
                        )
                    element_offset += count
                    continue
                connectivity = np.asarray(dataset.variables[connect_name][:], dtype=np.int64)
                if connectivity.ndim == 1:
                    connectivity = connectivity[:, None]
                element_count = int(connectivity.shape[0])
                expected_block_elements = _dimension_length(
                    dataset, f"num_el_in_blk{block_index}"
                )
                if expected_block_elements != element_count:
                    errors.append(
                        f"{path.name}/{connect_name}: connectivity has {element_count} "
                        f"rows but block dimension declares {expected_block_elements}"
                    )
                    element_offset += expected_block_elements
                    continue
                block_ids = (
                    element_map[element_offset : element_offset + element_count]
                    if element_map is not None
                    else np.arange(element_offset + 1, element_offset + element_count + 1, dtype=np.int64)
                )
                element_offset += element_count
                if len(block_ids) != element_count:
                    errors.append(
                        f"{path.name}: element map is shorter than connectivity"
                    )
                    continue
                block_id = int(block["id"])
                block_name_normal = _normal_name(block["name"])
                previous_name = block_identities.setdefault(
                    block_id, block_name_normal
                )
                if previous_name != block_name_normal:
                    errors.append(
                        f"{path.name}: Exodus block ID {block_id} has inconsistent names"
                    )
                    continue
                if not _block_selected(block, selectors, config_info):
                    continue
                selected_aliases.add(_normal_name(block["name"]))

                density, configured = _density_for_block(block, config_info)
                if density is None:
                    density_missing = True
                catalog_key = _normal_name(block["name"])
                catalog = selected_catalog.setdefault(
                    catalog_key,
                    {
                        "name": str(block["name"]),
                        "id": int(block["id"]),
                        "logical_id": configured.get("logical_id") if configured else None,
                        "material": configured.get("material") if configured else None,
                        "density_kg_m3": density,
                        "element_count": 0,
                        "initial_volume_m3": 0.0,
                        "physical_volume_complete": True,
                    },
                )
                if catalog["id"] != block_id:
                    errors.append(
                        f"{path.name}: Exodus block {block['name']} has inconsistent IDs"
                    )
                    continue

                keys = [
                    int(global_id) if element_map is not None else (file_number, int(global_id))
                    for global_id in block_ids
                ]
                current_keys: set[Any] = set()
                keep_values = []
                for key in keys:
                    retain = key not in seen_elements and key not in current_keys
                    keep_values.append(retain)
                    if retain:
                        current_keys.add(key)
                keep = np.asarray(keep_values, dtype=bool)
                duplicate_elements += int(np.count_nonzero(~keep))
                if not np.any(keep):
                    continue
                retained = np.flatnonzero(keep)

                local_nodes = connectivity[retained] - 1
                if np.any(local_nodes < 0) or np.any(local_nodes >= nodes):
                    errors.append(
                        f"{path.name}/{block['name']}: connectivity is out of node range"
                    )
                    continue
                seen_elements.update(current_keys)
                total_elements += int(len(retained))
                catalog["element_count"] += int(len(retained))
                if node_map is not None:
                    for node_id in node_map[local_nodes].reshape(-1):
                        seen_nodes.add(int(node_id))
                else:
                    for node_id in np.unique(local_nodes):
                        seen_nodes.add((file_number, int(node_id)))

                volume_is_physical = True
                volume = _element_scalar(
                    dataset, element_table, ("Volume",), block_index, frames, element_count, np
                )
                if volume is None:
                    nodal_volume = _nodal_scalar(
                        dataset, nodal_table, ("Volume",), frames, nodes, np
                    )
                    if nodal_volume is not None:
                        volume = _element_scalar_from_nodes(nodal_volume, connectivity, np)
                        _warn(warnings, "Volume was nodal; element weights use connectivity averages")
                if volume is None:
                    geometric = _geometric_volumes(dataset, connectivity, nodes, np)
                    if geometric is not None:
                        volume = np.broadcast_to(geometric[None, :], (frames, element_count)).copy()
                        _warn(warnings, "Volume output is absent; TET4 reference volumes were computed from the mesh")
                    else:
                        volume = np.ones((frames, element_count), dtype=float)
                        volume_is_physical = False
                        volume_fallback = True
                        physical_volume_complete = False
                        _warn(
                            warnings,
                            "Volume output is absent; dimensionless unit element "
                            "weights were used for count-weighted fields only",
                        )
                volume = volume[indices][:, retained]
                invalid_volume = ~np.isfinite(volume) | (volume <= 0.0)
                if np.any(invalid_volume):
                    _warn(warnings, f"{path.name}/{block['name']}: nonpositive/non-finite Volume values were excluded")
                    physical_volume_complete = False
                    volume_is_physical = False
                    catalog["physical_volume_complete"] = False
                    catalog["initial_volume_m3"] = None
                    volume = np.where(invalid_volume, 0.0, volume)
                if len(common_times) > 1 and not np.allclose(
                    volume, volume[0:1], rtol=1.0e-10, atol=1.0e-18
                ):
                    volume_changed = True
                accum["volume"] += np.sum(volume, axis=1)
                unit_weight = np.ones_like(volume, dtype=float)
                accum["count"] += np.sum(unit_weight, axis=1)
                if volume_is_physical and catalog["initial_volume_m3"] is not None:
                    initial_volume = float(np.sum(volume[0]))
                    catalog["initial_volume_m3"] += initial_volume
                else:
                    catalog["physical_volume_complete"] = False
                    catalog["initial_volume_m3"] = None
                if volume_is_physical and density is not None:
                    accum["mass"] += np.sum(volume * float(density), axis=1)

                coordinates = None
                if coordinates_nodal is not None:
                    coordinates = _element_centroids(coordinates_nodal, connectivity, np)[indices][:, retained, :]
                    _finite_weighted_add(
                        accum["volume_moment"]["coordinates"],
                        accum["volume_weight"]["coordinates"],
                        coordinates,
                        volume,
                        np,
                    )
                    _finite_weighted_add(
                        accum["count_moment"]["coordinates"],
                        accum["count_weight"]["coordinates"],
                        coordinates,
                        unit_weight,
                        np,
                    )
                    if density is not None:
                        _finite_weighted_add(
                            accum["mass_moment"]["coordinates"],
                            accum["mass_weight"]["coordinates"],
                            coordinates,
                            volume * float(density),
                            np,
                        )
                    field_element_counts["coordinates"] += int(len(retained))

                velocity = None
                if velocity_nodal is not None:
                    velocity = _element_centroids(velocity_nodal, connectivity, np)[indices][:, retained, :]
                    _finite_weighted_add(
                        accum["volume_moment"]["velocity"],
                        accum["volume_weight"]["velocity"],
                        velocity,
                        volume,
                        np,
                    )
                    _finite_weighted_add(
                        accum["count_moment"]["velocity"],
                        accum["count_weight"]["velocity"],
                        velocity,
                        unit_weight,
                        np,
                    )
                    if density is not None:
                        _finite_weighted_add(
                            accum["mass_moment"]["velocity"],
                            accum["mass_weight"]["velocity"],
                            velocity,
                            volume * float(density),
                            np,
                        )
                    field_element_counts["velocity"] += int(len(retained))

                damage = _element_scalar(
                    dataset, element_table, ("Damage",), block_index, frames, element_count, np
                )
                if damage is None:
                    nodal_damage = _nodal_scalar(dataset, nodal_table, ("Damage",), frames, nodes, np)
                    if nodal_damage is not None:
                        damage = _element_scalar_from_nodes(nodal_damage, connectivity, np)
                        _warn(warnings, "Damage was nodal; element values use connectivity averages")
                if damage is not None:
                    damage = damage[indices][:, retained]
                    finite = np.isfinite(damage)
                    valid_weight = np.where(finite, volume, 0.0)
                    accum["damage_moment"] += np.sum(np.where(finite, damage, 0.0) * valid_weight, axis=1)
                    accum["volume_weight"]["damage"] += np.sum(valid_weight, axis=1)
                    accum["damage_positive_volume"] += np.sum(
                        np.where(finite & (damage > 1.0e-8), volume, 0.0), axis=1
                    )
                    accum["damage_count_moment"] += np.sum(
                        np.where(finite, damage, 0.0), axis=1
                    )
                    accum["count_weight"]["damage"] += np.sum(finite, axis=1)
                    accum["damage_positive_count"] += np.sum(
                        finite & (damage > 1.0e-8), axis=1
                    )
                    for threshold in thresholds:
                        accum["damage_threshold_volume"][threshold] += np.sum(
                            np.where(finite & (damage > threshold), volume, 0.0), axis=1
                        )
                        accum["damage_threshold_count"][threshold] += np.sum(
                            finite & (damage > threshold), axis=1
                        )
                    field_element_counts["damage"] += int(len(retained))

                force_values = None
                local_force_source = None
                force_is_density = False
                force_is_nodal_direct = False
                skipped_density_sources: list[str] = []
                for candidate, name, is_density in (
                    (contact_density_nodal, "Contact_Force_Density", True),
                    (contact_direct_nodal, "Contact_Force", False),
                    (resultant_density_nodal, "Force_Density", True),
                    (resultant_direct_nodal, "Force", False),
                ):
                    if candidate is not None:
                        if is_density and not volume_is_physical:
                            skipped_density_sources.append(name)
                            continue
                        force_values = (
                            _element_centroids(candidate, connectivity, np)[indices][
                                :, retained, :
                            ]
                            if is_density
                            else candidate[indices]
                        )
                        local_force_source = name
                        force_is_density = is_density
                        force_is_nodal_direct = not is_density
                        break
                if force_values is None:
                    for name, is_density in (
                        ("Contact_Force_Density", True),
                        ("Contact_Force", False),
                        ("Force_Density", True),
                        ("Force", False),
                    ):
                        candidate = _element_vector(
                            dataset, element_table, name, block_index, frames, element_count, np
                        )
                        if candidate is not None:
                            if is_density and not volume_is_physical:
                                skipped_density_sources.append(name)
                                continue
                            force_values = candidate[indices][:, retained, :]
                            local_force_source = name
                            force_is_density = is_density
                            force_is_nodal_direct = False
                            break
                if skipped_density_sources:
                    replacement = (
                        f"; complete {local_force_source} was used instead"
                        if force_values is not None and not force_is_density
                        else ""
                    )
                    _warn(
                        warnings,
                        f"{path.name}/{block['name']}: density force field(s) "
                        + ", ".join(sorted(set(skipped_density_sources)))
                        + " were not integrated because physical Volume is unavailable"
                        + replacement,
                    )
                if force_values is not None and local_force_source is not None:
                    if force_is_nodal_direct:
                        local_force_nodes = np.unique(local_nodes.reshape(-1))
                        node_keys = [
                            int(node_map[node])
                            if node_map is not None
                            else (file_number, int(node))
                            for node in local_force_nodes
                        ]
                        new_node_mask = np.asarray(
                            [key not in force_seen_nodes for key in node_keys],
                            dtype=bool,
                        )
                        selected_force_nodes = local_force_nodes[new_node_mask]
                        selected_node_keys = [
                            key
                            for key, retain in zip(node_keys, new_node_mask)
                            if retain
                        ]
                        direct_values = force_values[:, selected_force_nodes, :]
                        finite = np.all(np.isfinite(direct_values), axis=2)
                        accum["force"] += np.sum(
                            np.where(
                                finite[:, :, None], direct_values, 0.0
                            ),
                            axis=1,
                        )
                        accum["force_coverage"] += np.sum(finite, axis=1)
                        force_seen_nodes.update(selected_node_keys)
                        field_element_counts["force"] += int(
                            len(selected_force_nodes)
                        )
                        force_bases_seen.add("nodes")
                    else:
                        finite = np.all(np.isfinite(force_values), axis=2)
                    if force_is_density:
                        weights = np.where(finite, volume, 0.0)
                        accum["force"] += np.sum(
                            np.where(finite[:, :, None], force_values, 0.0) * weights[:, :, None],
                            axis=1,
                        )
                        accum["force_coverage"] += np.sum(finite, axis=1)
                        field_element_counts["force"] += int(len(retained))
                        force_bases_seen.add("elements")
                    elif not force_is_nodal_direct:
                        accum["force"] += np.sum(
                            np.where(finite[:, :, None], force_values, 0.0), axis=1
                        )
                        accum["force_coverage"] += np.sum(finite, axis=1)
                        field_element_counts["force"] += int(len(retained))
                        force_bases_seen.add("elements")
                    force_sources_seen.add(local_force_source)
                    if force_source is None:
                        force_source = local_force_source

    if selectors is not None:
        matched = {
            _normal_name(value)
            for entry in selected_catalog.values()
            for value in (entry.get("name"), entry.get("id"), entry.get("logical_id"))
            if value is not None
        }
        missing = [str(selector) for selector in selectors if _normal_name(selector) not in matched]
        if missing:
            errors.append(
                "Requested block selector(s) were not found: " + ", ".join(missing)
            )
    if not total_elements:
        errors.append("No elements matched the requested block selection")
    if synthetic_ids:
        errors.append(
            "Nemesis shards lack elem_num_map; global element ownership "
            "cannot be verified"
        )
    if duplicate_elements:
        errors.append(
            f"Found {duplicate_elements} duplicate global element occurrence(s); "
            "ambiguous first-wins shard reductions are not accepted"
        )
    if volume_changed:
        _warn(warnings, "Volume changes across saved frames; each frame uses its saved Volume weights")
    if len(force_sources_seen) > 1:
        _warn(
            warnings,
            "force field availability differed across selected blocks; mixed sources "
            "cannot be interpreted as one resultant",
        )
    resolved_force_source = (
        next(iter(force_sources_seen)) if len(force_sources_seen) == 1 else None
    )
    if resolved_force_source in {"Force", "Force_Density"}:
        _warn(warnings, "Contact_Force was unavailable; resultant Force was used and is not contact-specific")
    if density_missing and total_elements:
        _warn(
            warnings,
            "one or more selected blocks have no material Density; center of mass "
            "and momentum closure are unavailable, while qualified weighted "
            "kinematics may still be reported",
        )
    resolved_force_basis = (
        next(iter(force_bases_seen)) if len(force_bases_seen) == 1 else None
    )
    force_expected_count = (
        len(seen_nodes)
        if resolved_force_basis == "nodes"
        else total_elements
        if resolved_force_basis == "elements"
        else 0
    )
    if len(force_bases_seen) > 1:
        _warn(
            warnings,
            "force aggregation basis differed across selected blocks/shards; "
            "node- and element-based values cannot be combined",
        )
    for field, count in field_element_counts.items():
        expected = force_expected_count if field == "force" else total_elements
        if count == 0:
            _warn(warnings, f"{field} field was not found for selected blocks")
        elif count < expected:
            item = "nodes" if field == "force" and resolved_force_basis == "nodes" else "elements"
            _warn(
                warnings,
                f"{field} field covers only {count}/{expected} selected {item}",
            )

    return {
        "np": np,
        "metadata": metadata,
        "warnings": warnings,
        "errors": errors,
        "files": files,
        "case_name": directory.name,
        "times": common_times,
        "accum": accum,
        "selected_blocks": selected_catalog,
        "element_count": total_elements,
        "node_count": len(seen_nodes),
        "duplicate_elements": duplicate_elements,
        "global_ids_available": not synthetic_ids,
        "force_source": resolved_force_source,
        "force_sources": tuple(sorted(force_sources_seen)),
        "force_sources_uniform": len(force_sources_seen) <= 1,
        "force_basis": resolved_force_basis,
        "force_bases_uniform": len(force_bases_seen) <= 1,
        "force_expected_count": force_expected_count,
        "volume_fallback": volume_fallback,
        "physical_volume_complete": physical_volume_complete,
        "density_complete": not density_missing and physical_volume_complete,
        "field_element_counts": field_element_counts,
        "force_frame_coverage": accum["force_coverage"],
        "selectors_explicit": selectors is not None,
        "damage_thresholds": thresholds,
    }


def _cumulative_trapezoid(values: Any, times: Any, np: Any) -> Any:
    result = np.zeros_like(values, dtype=float)
    if len(times) > 1:
        result[1:] = np.cumsum(
            0.5 * (values[1:] + values[:-1]) * np.diff(times)[:, None], axis=0
        )
    return result


def _first_time(times: Any, mask: Any, np: Any) -> float | None:
    indices = np.flatnonzero(mask)
    return float(times[int(indices[0])]) if len(indices) else None


def _threshold_label(value: float) -> str:
    text = f"{value:.12g}".replace("-", "m").replace(".", "p")
    return text


def _derive_report(arrays: Mapping[str, Any]) -> dict[str, Any]:
    """Turn merged accumulators into explicitly qualified impact metrics."""
    np = arrays["np"]
    metadata = arrays["metadata"]
    warnings = list(arrays["warnings"])
    errors = list(arrays["errors"])
    times = arrays.get("times")
    files = arrays.get("files", [])
    base: dict[str, Any] = {
        "schema_version": 1,
        "analysis": "Peridigm impact and damage history",
        "status": "fail" if errors else "pass",
        "case": str(metadata.get("tag", arrays.get("case_name", "case"))),
        "scenario": metadata.get("scenario"),
        "result_format": "serial" if len(files) == 1 else "nemesis_shards",
        "result_files": [path.name for path in files],
        "result_file_count": len(files),
        "warnings": warnings,
        "errors": errors,
    }
    if times is None or "accum" not in arrays:
        return _json_value(base, np)

    accum = arrays["accum"]
    total_elements = int(arrays["element_count"])
    field_counts = arrays["field_element_counts"]
    physical_volume = bool(arrays.get("physical_volume_complete", False))
    selected_blocks = sorted(
        arrays["selected_blocks"].values(),
        key=lambda entry: (entry["id"], entry["name"]),
    )
    config = metadata.get("config", {})
    analysis_config = config.get("analysis", {}) if isinstance(config, Mapping) else {}
    if not isinstance(analysis_config, Mapping):
        analysis_config = {}
    allow_multi_block = analysis_config.get("allow_multi_block_impact") is True
    impact_role_unambiguous = len(selected_blocks) <= 1 or allow_multi_block
    if not impact_role_unambiguous:
        _warn(
            warnings,
            "multiple blocks were selected for the impact role; force, momentum "
            "closure, and restitution were suppressed to avoid internal-force cancellation",
        )

    count_position = _safe_divide(
        accum["count_moment"]["coordinates"],
        accum["count_weight"]["coordinates"],
        np,
    )
    count_velocity = _safe_divide(
        accum["count_moment"]["velocity"],
        accum["count_weight"]["velocity"],
        np,
    )
    volume_position = None
    volume_velocity = None
    if physical_volume:
        volume_position = _safe_divide(
            accum["volume_moment"]["coordinates"],
            accum["volume_weight"]["coordinates"],
            np,
        )
        volume_velocity = _safe_divide(
            accum["volume_moment"]["velocity"],
            accum["volume_weight"]["velocity"],
            np,
        )
    else:
        _warn(
            warnings,
            "physical Volume is incomplete; dimensional volume/mass and "
            "Force_Density-derived quantities are unavailable",
        )

    coordinate_complete = field_counts.get("coordinates", 0) == total_elements
    velocity_complete = field_counts.get("velocity", 0) == total_elements
    coordinate_frames_complete = bool(
        coordinate_complete
        and np.all(accum["count_weight"]["coordinates"] == total_elements)
    )
    velocity_frames_complete = bool(
        velocity_complete
        and np.all(accum["count_weight"]["velocity"] == total_elements)
    )
    density_complete = bool(arrays["density_complete"])
    center_of_mass = None
    center_of_mass_velocity = None
    mass = None
    if density_complete:
        mass = accum["mass"]
        if coordinate_frames_complete:
            center_of_mass = _safe_divide(
                accum["mass_moment"]["coordinates"],
                accum["mass_weight"]["coordinates"],
                np,
            )
        if velocity_frames_complete:
            center_of_mass_velocity = _safe_divide(
                accum["mass_moment"]["velocity"],
                accum["mass_weight"]["velocity"],
                np,
            )

    if center_of_mass_velocity is not None:
        impact_velocity = center_of_mass_velocity
        impact_velocity_basis = "center_of_mass"
    elif velocity_frames_complete:
        impact_velocity = volume_velocity if physical_volume else count_velocity
        impact_velocity_basis = (
            "volume_weighted" if physical_volume else "count_weighted"
        )
    else:
        impact_velocity = None
        impact_velocity_basis = None

    if physical_volume:
        damage_mean = _safe_divide(
            accum["damage_moment"], accum["volume_weight"]["damage"], np
        )
        positive_damage_fraction = _safe_divide(
            accum["damage_positive_volume"],
            accum["volume_weight"]["damage"],
            np,
        )
        damage_fractions = {
            threshold: _safe_divide(
                accum["damage_threshold_volume"][threshold],
                accum["volume_weight"]["damage"],
                np,
            )
            for threshold in arrays["damage_thresholds"]
        }
        damage_weighting = "volume"
    else:
        damage_mean = _safe_divide(
            accum["damage_count_moment"], accum["count_weight"]["damage"], np
        )
        positive_damage_fraction = _safe_divide(
            accum["damage_positive_count"], accum["count_weight"]["damage"], np
        )
        damage_fractions = {
            threshold: _safe_divide(
                accum["damage_threshold_count"][threshold],
                accum["count_weight"]["damage"],
                np,
            )
            for threshold in arrays["damage_thresholds"]
        }
        damage_weighting = "count"

    source = arrays.get("force_source")
    force_coverage = arrays.get("force_frame_coverage")
    force_complete = bool(
        source is not None
        and arrays.get("force_sources_uniform", False)
        and arrays.get("force_bases_uniform", False)
        and arrays.get("force_expected_count", 0) > 0
        and field_counts.get("force", 0) == arrays.get("force_expected_count")
        and force_coverage is not None
        and np.all(force_coverage == arrays.get("force_expected_count"))
    )
    if source is not None and not force_complete:
        _warn(warnings, "force history was suppressed because its selected-element coverage is incomplete")
    force = accum["force"] if force_complete and impact_role_unambiguous else None
    reported_force_source = source if force is not None else None
    impulse = _cumulative_trapezoid(force, times, np) if force is not None else None

    body_force = None
    momentum = None
    inferred_contact_impulse = None
    momentum_residual = None
    if (
        impact_role_unambiguous
        and mass is not None
        and center_of_mass_velocity is not None
        and np.all(np.isfinite(center_of_mass_velocity))
    ):
        body_force = _body_force(metadata, arrays["selected_blocks"], np, warnings)
        momentum = center_of_mass_velocity * mass[:, None]
        delta_momentum = momentum - momentum[0]
        duration = times - times[0]
        inferred_contact_impulse = delta_momentum - (
            body_force[None, :] * duration[:, None]
            if body_force is not None
            else 0.0
        )
        if body_force is None and reported_force_source in {
            "Contact_Force",
            "Contact_Force_Density",
        }:
            _warn(
                warnings,
                "no configured Body Force was resolved; momentum-inferred contact "
                "impulse omits other external loads",
            )
        if impulse is not None:
            if reported_force_source in {"Contact_Force", "Contact_Force_Density"}:
                expected = impulse + (
                    body_force[None, :] * duration[:, None]
                    if body_force is not None
                    else 0.0
                )
                momentum_residual = delta_momentum - expected
            else:
                momentum_residual = delta_momentum - impulse
    elif field_counts.get("velocity", 0):
        _warn(
            warnings,
            "momentum closure was not computed because complete physical Volume, "
            "Density, and Velocity coverage are required",
        )

    metrics: dict[str, Any] = {
        "frame_count": int(len(times)),
        "initial_time_s": float(times[0]),
        "final_time_s": float(times[-1]),
        "element_count": total_elements,
        "node_count": int(arrays["node_count"]),
        "initial_weight_sum": (
            None if physical_volume else float(accum["count"][0])
        ),
        "final_weight_sum": (
            None if physical_volume else float(accum["count"][-1])
        ),
        "initial_volume_m3": (
            float(accum["volume"][0]) if physical_volume else None
        ),
        "final_volume_m3": (
            float(accum["volume"][-1]) if physical_volume else None
        ),
        "mass_kg": float(mass[0]) if mass is not None else None,
        "force_source": reported_force_source,
        "detected_force_sources": list(arrays.get("force_sources", ())),
        "impact_velocity_basis": impact_velocity_basis,
        "first_contact_time_s": None,
        "first_damage_time_s": None,
        "first_robust_damage_time_s": None,
        "peak_contact_force_n": None,
        "contact_impulse_magnitude_n_s": None,
        "momentum_inferred_contact_impulse_magnitude_n_s": None,
        "maximum_momentum_residual_n_s": None,
        "maximum_momentum_residual_ratio": None,
        "impact_approach_speed_m_s": None,
        "rebound_peak_velocity_m_s": None,
        "coefficient_of_restitution": None,
        "damage_mean_final": None,
        "damage_count_weighted_mean_final": None,
    }

    contact_index = None
    if force is not None and np.any(np.all(np.isfinite(force), axis=1)):
        force_norm = np.linalg.norm(np.nan_to_num(force, nan=0.0), axis=1)
        peak = float(np.max(force_norm))
        requested_threshold = analysis_config.get("contact_force_threshold_n")
        try:
            force_threshold = (
                float(requested_threshold)
                if requested_threshold is not None
                else max(1.0e-12, peak * 1.0e-8)
            )
        except (TypeError, ValueError):
            force_threshold = max(1.0e-12, peak * 1.0e-8)
            _warn(warnings, "analysis.contact_force_threshold_n is invalid; an adaptive threshold was used")
        contact_indices = np.flatnonzero(force_norm > force_threshold)
        contact_index = int(contact_indices[0]) if len(contact_indices) else None
        metrics["first_contact_time_s"] = (
            float(times[contact_index]) if contact_index is not None else None
        )
        metrics["peak_contact_force_n"] = peak
        metrics["contact_impulse_magnitude_n_s"] = float(np.linalg.norm(impulse[-1]))
    else:
        _warn(warnings, "first contact and sampled contact impulse require a complete force field")

    if np.any(np.isfinite(damage_mean)):
        metrics["first_damage_time_s"] = _first_time(
            times, positive_damage_fraction > 0.0, np
        )
        metrics["first_robust_damage_time_s"] = _first_time(
            times, positive_damage_fraction >= 1.0e-3, np
        )
        mean_key = (
            "damage_mean_final"
            if physical_volume
            else "damage_count_weighted_mean_final"
        )
        metrics[mean_key] = (
            float(damage_mean[-1]) if np.isfinite(damage_mean[-1]) else None
        )
        for threshold, fraction in damage_fractions.items():
            key = (
                f"damage_fraction_gt_{_threshold_label(threshold)}_final"
                if physical_volume
                else f"damage_count_fraction_gt_{_threshold_label(threshold)}_final"
            )
            metrics[key] = float(fraction[-1]) if np.isfinite(fraction[-1]) else None

    impact_direction = None
    if (
        impact_role_unambiguous
        and impact_velocity is not None
        and len(impact_velocity)
        and np.all(np.isfinite(impact_velocity))
    ):
        configured_axis = analysis_config.get("impact_axis")
        if configured_axis is not None:
            try:
                vector = np.asarray(configured_axis, dtype=float).reshape(-1)
                if vector.size == 3 and np.all(np.isfinite(vector)) and np.linalg.norm(vector) > 0.0:
                    impact_direction = vector / np.linalg.norm(vector)
            except (TypeError, ValueError):
                pass
            if impact_direction is None:
                _warn(warnings, "analysis.impact_axis is invalid; initial weighted velocity was used")
        if impact_direction is None:
            initial_speed = float(np.linalg.norm(impact_velocity[0]))
            if initial_speed > 1.0e-14:
                impact_direction = impact_velocity[0] / initial_speed
            elif impulse is not None and float(np.linalg.norm(impulse[-1])) > 1.0e-14:
                impact_direction = -impulse[-1] / np.linalg.norm(impulse[-1])
                _warn(warnings, "initial weighted velocity is zero; impact direction was inferred from impulse")
        if impact_direction is not None:
            along = impact_velocity @ impact_direction
            start = contact_index if contact_index is not None else 0
            approach_index = (
                max(start - 1, 0)
                if contact_index is not None
                else int(np.argmax(along))
            )
            approach_speed = max(float(along[approach_index]), 0.0)
            rebound_offset = int(np.argmax(-along[start:]))
            rebound_speed = max(float(-along[start + rebound_offset]), 0.0)
            metrics["impact_approach_speed_m_s"] = approach_speed
            metrics["rebound_peak_velocity_m_s"] = rebound_speed
            metrics["coefficient_of_restitution"] = (
                rebound_speed / approach_speed if approach_speed > 1.0e-14 else None
            )
            metrics["impact_axis_unit_vector"] = impact_direction
        else:
            _warn(warnings, "impact direction and restitution could not be inferred")

    if inferred_contact_impulse is not None:
        metrics["momentum_inferred_contact_impulse_magnitude_n_s"] = float(
            np.linalg.norm(inferred_contact_impulse[-1])
        )
    if momentum_residual is not None:
        residual_norm = np.linalg.norm(momentum_residual, axis=1)
        maximum = float(np.max(residual_norm))
        scales = [1.0e-30]
        if impulse is not None:
            scales.append(float(np.max(np.linalg.norm(impulse, axis=1))))
        if momentum is not None:
            scales.append(float(np.linalg.norm(momentum[0])))
        metrics["maximum_momentum_residual_n_s"] = maximum
        metrics["maximum_momentum_residual_ratio"] = maximum / max(scales)

    series = {
        "time_s": times,
        "weight_sum": accum["count"] if not physical_volume else None,
        "total_volume_m3": accum["volume"] if physical_volume else None,
        "count_weighted_position_m": count_position if not physical_volume else None,
        "count_weighted_velocity_m_s": count_velocity if not physical_volume else None,
        "center_of_volume_m": volume_position,
        "volume_weighted_velocity_m_s": volume_velocity,
        "center_of_mass_m": center_of_mass,
        "center_of_mass_velocity_m_s": center_of_mass_velocity,
        "force_n": force,
        "impulse_n_s": impulse,
        "linear_momentum_kg_m_s": momentum,
        "momentum_inferred_contact_impulse_n_s": inferred_contact_impulse,
        "momentum_residual_n_s": momentum_residual,
        "damage_volume_weighted_mean": damage_mean if physical_volume else None,
        "damage_volume_fraction_gt": (
            {
                f"{threshold:.12g}": values
                for threshold, values in damage_fractions.items()
            }
            if physical_volume
            else None
        ),
        "damage_count_weighted_mean": damage_mean if not physical_volume else None,
        "damage_count_fraction_gt": (
            {
                f"{threshold:.12g}": values
                for threshold, values in damage_fractions.items()
            }
            if not physical_volume
            else None
        ),
    }
    base.update(
        {
            "status": "fail" if errors else "pass",
            "warnings": warnings,
            "merge": {
                "global_element_ids_available": bool(arrays["global_ids_available"]),
                "duplicate_global_element_occurrences": int(arrays["duplicate_elements"]),
                "duplicate_global_element_ownership_rejected": bool(
                    arrays["duplicate_elements"]
                ),
                "unique_element_count": total_elements,
                "unique_connected_node_count": int(arrays["node_count"]),
                "physical_volume_available": physical_volume,
                "weighting": (
                    "saved or reference element Volume"
                    if physical_volume
                    else "unit element count (dimensionless)"
                ),
                "damage_weighting": damage_weighting,
                "field_element_coverage": dict(field_counts),
                "force_aggregation_basis": arrays.get("force_basis"),
                "force_item_coverage": {
                    "observed": field_counts.get("force", 0),
                    "expected": arrays.get("force_expected_count", 0),
                },
            },
            "selected_blocks": selected_blocks,
            "metrics": metrics,
            "series": series,
        }
    )
    return _json_value(base, np)


def read_volume_weighted_timeseries(
    case_dir: str | Path,
    *,
    blocks: Sequence[str | int] | str | int | None = None,
    damage_thresholds: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Read and merge a serial or decomposed Peridigm Exodus history.

    Parameters
    ----------
    case_dir:
        Generated case directory.  ``metadata.json`` is used when present, but
        a bare directory containing Exodus files is also accepted.
    blocks:
        Optional Exodus block names, integer block IDs, or logical block names
        from ``metadata.json``.  The default is every block unless
        ``config.analysis.blocks`` supplies a selection.
    damage_thresholds:
        Damage values for which fractions ``Damage > threshold`` are returned.
        When omitted, ``config.analysis.damage_thresholds`` is used, followed
        by the release defaults.

    Returns
    -------
    dict
        A JSON-compatible report containing merged time arrays and weighted
        fields.  Missing optional fields add warnings and yield ``None`` rather
        than aborting the read.
    """
    resolved_thresholds = _configured_damage_thresholds(
        case_dir, damage_thresholds
    )
    arrays = _read_arrays(
        case_dir, blocks=blocks, damage_thresholds=resolved_thresholds
    )
    return _derive_report(arrays)


def _configured_damage_thresholds(
    case_dir: str | Path, explicit: Sequence[float] | None
) -> tuple[float, ...]:
    if explicit is not None:
        return tuple(explicit)
    metadata_path = Path(case_dir).expanduser().resolve() / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _DEFAULT_DAMAGE_THRESHOLDS
    config = metadata.get("config", {}) if isinstance(metadata, Mapping) else {}
    analysis = config.get("analysis", {}) if isinstance(config, Mapping) else {}
    configured = (
        analysis.get("damage_thresholds")
        if isinstance(analysis, Mapping)
        else None
    )
    if isinstance(configured, Sequence) and not isinstance(configured, (str, bytes)):
        return tuple(configured)
    return _DEFAULT_DAMAGE_THRESHOLDS


def _role_selectors(
    case_dir: str | Path,
    explicit: Sequence[str | int] | str | int | None,
    key: str,
) -> list[str | int] | None:
    if explicit is not None:
        if isinstance(explicit, (str, int)):
            return [explicit]
        return list(explicit)
    metadata_path = Path(case_dir).expanduser().resolve() / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    config = metadata.get("config", {}) if isinstance(metadata, Mapping) else {}
    analysis = config.get("analysis", {}) if isinstance(config, Mapping) else {}
    if not isinstance(analysis, Mapping):
        return None
    value = analysis.get(key)
    if value is None and key == "blocks":
        value = analysis.get("selected_blocks")
    if value is None:
        return None
    if isinstance(value, (str, int)):
        return [value]
    return list(value) if isinstance(value, Sequence) else None


def _same_selectors(
    left: Sequence[str | int] | None, right: Sequence[str | int] | None
) -> bool:
    if left is None or right is None:
        return left is right
    return {_normal_name(value) for value in left} == {
        _normal_name(value) for value in right
    }


def analyze_impact_case(
    case_dir: str | Path,
    *,
    blocks: Sequence[str | int] | str | int | None = None,
    damage_blocks: Sequence[str | int] | str | int | None = None,
    damage_thresholds: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Analyze one impact case without making scenario-specific assumptions.

    With complete physical Volume and fields, the report includes weighted
    position/velocity, force, impulse, momentum closure, Damage statistics,
    first saved contact/damage times, rebound velocity, and restitution.
    Otherwise unavailable physical quantities remain ``None`` and explicitly
    named count-weighted fields may be returned.  Missing fields are warnings.

    ``blocks`` selects one moving/impact body.  ``damage_blocks`` independently
    selects the damaged target and defaults to ``config.analysis.damage_blocks``.
    A distinct target report is returned under ``damage_analysis`` so target
    damage cannot contaminate projectile momentum or cancel contact forces.
    """
    impact_selectors = _role_selectors(case_dir, blocks, "blocks")
    target_selectors = _role_selectors(case_dir, damage_blocks, "damage_blocks")
    resolved_thresholds = _configured_damage_thresholds(
        case_dir, damage_thresholds
    )
    report = read_volume_weighted_timeseries(
        case_dir, blocks=impact_selectors, damage_thresholds=resolved_thresholds
    )
    report["analysis_roles"] = {
        "impact_blocks": impact_selectors,
        "damage_blocks": target_selectors,
    }
    if target_selectors is None or _same_selectors(impact_selectors, target_selectors):
        return report

    target = read_volume_weighted_timeseries(
        case_dir, blocks=target_selectors, damage_thresholds=resolved_thresholds
    )
    target_metrics = target.get("metrics", {})
    target_series = target.get("series", {})
    damage_report = {
        "status": target.get("status", "fail"),
        "selected_blocks": target.get("selected_blocks", []),
        "weighting": target.get("merge", {}).get("damage_weighting"),
        "metrics": {
            key: value
            for key, value in target_metrics.items()
            if "damage" in key
        },
        "series": {
            key: value
            for key, value in target_series.items()
            if key == "time_s" or "damage" in key
        },
        "warnings": target.get("warnings", []),
        "errors": target.get("errors", []),
    }
    report["damage_analysis"] = damage_report
    if damage_report["status"] != "pass":
        report["status"] = "fail"
        report.setdefault("errors", []).extend(
            f"damage analysis: {message}"
            for message in damage_report.get("errors", [])
        )
    return report


def _flatten_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten scalar report values and give ``metrics`` entries short names."""
    result: dict[str, Any] = {}

    def visit(prefix: str, value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                visit(f"{prefix}.{key}" if prefix else str(key), item)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if math.isfinite(float(value)):
                result[prefix] = float(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            try:
                numeric = [float(item) for item in value]
            except (TypeError, ValueError):
                return
            if numeric and all(math.isfinite(item) for item in numeric):
                result[prefix] = numeric

    visit("", report)
    metrics = report.get("metrics", {})
    if isinstance(metrics, Mapping):
        for key in metrics:
            full = f"metrics.{key}"
            if full in result:
                result[str(key)] = result[full]
    return result


def _metric_aliases(name: str) -> tuple[str, ...]:
    aliases: dict[str, tuple[str, ...]] = {
        "contact_impulse": (
            "contact_impulse_magnitude_n_s",
            "sampled_contact_impulse_z_n_s",
            "contact_impulse_z_n_s",
        ),
        "momentum_inferred_contact_impulse": (
            "momentum_inferred_contact_impulse_magnitude_n_s",
            "momentum_inferred_contact_impulse_z_n_s",
        ),
        "peak_contact_force": ("peak_contact_force_n", "peak_sampled_contact_force_z_n"),
        "rebound_velocity": ("rebound_peak_velocity_m_s", "rebound_peak_vcm_z_m_s"),
        "restitution": ("coefficient_of_restitution", "whole_mug_coefficient_of_restitution"),
        "damage_mean": (
            "damage_mean_final",
            "damage_count_weighted_mean_final",
        ),
        "damage_fraction_gt_0p5": (
            "damage_fraction_gt_0p5_final",
            "damage_count_fraction_gt_0p5_final",
        ),
        "first_damage_time": ("first_damage_time_s", "first_robust_damage_time_s"),
        "momentum_residual": ("maximum_momentum_residual_ratio",),
    }
    normalized = _normal_name(name)
    for key, values in aliases.items():
        if normalized == _normal_name(key):
            return values
    dynamic_damage = re.match(
        r"^damage_(?P<count>count_)?fraction_gt_(?P<threshold>.+?)(?:_final)?$",
        str(name),
        flags=re.IGNORECASE,
    )
    if dynamic_damage is not None:
        threshold = dynamic_damage.group("threshold")
        volume_name = f"damage_fraction_gt_{threshold}_final"
        count_name = f"damage_count_fraction_gt_{threshold}_final"
        ordered = (
            (count_name, volume_name, str(name))
            if dynamic_damage.group("count")
            else (volume_name, count_name, str(name))
        )
        return tuple(dict.fromkeys(ordered))
    return (name,)


def _lookup_metric(
    flat: Mapping[str, Any],
    requested: str,
    report: Mapping[str, Any] | None = None,
) -> tuple[str, Any] | None:
    aliases = _metric_aliases(requested)
    damage_request = any("damage" in _normal_name(alias) for alias in aliases)
    damage_analysis = report.get("damage_analysis") if report is not None else None
    if damage_request and isinstance(damage_analysis, Mapping):
        target_metrics = damage_analysis.get("metrics", {})
        if isinstance(target_metrics, Mapping):
            for alias in aliases:
                if alias in target_metrics and target_metrics[alias] is not None:
                    return f"damage_analysis.metrics.{alias}", target_metrics[alias]
                normalized = _normal_name(alias)
                for key, value in target_metrics.items():
                    if _normal_name(key) == normalized and value is not None:
                        return f"damage_analysis.metrics.{key}", value
        # Once roles are split, target Damage is authoritative.  Falling back
        # to projectile Damage would make a convergence gate silently compare
        # the wrong physical body.
        return None

    for alias in aliases:
        if alias in flat:
            return alias, flat[alias]
        metric_name = f"metrics.{alias}"
        if metric_name in flat:
            return alias, flat[metric_name]
    requested_normal = _normal_name(requested)
    for key, value in flat.items():
        if _normal_name(key) == requested_normal or _normal_name(key.split(".")[-1]) == requested_normal:
            return key, value
    return None


def _threshold_specs(thresholds: Mapping[str, Any]) -> list[tuple[str, str, float]]:
    """Normalize supported threshold syntaxes to metric/mode/limit triples."""
    specs: list[tuple[str, str, float]] = []
    raw_metrics = thresholds.get("metrics", thresholds)
    if not isinstance(raw_metrics, Mapping):
        raise TypeError("thresholds must be a mapping")
    for group, mode in (("absolute", "absolute"), ("relative", "relative")):
        grouped = raw_metrics.get(group)
        if isinstance(grouped, Mapping):
            for metric, limit in grouped.items():
                specs.append((str(metric), mode, float(limit)))
    for raw_name, raw_limit in raw_metrics.items():
        if raw_name in {"absolute", "relative"}:
            continue
        name = str(raw_name)
        if isinstance(raw_limit, Mapping):
            for key, mode in (
                ("absolute", "absolute"),
                ("atol", "absolute"),
                ("relative", "relative"),
                ("rtol", "relative"),
            ):
                if key in raw_limit:
                    specs.append((name, mode, float(raw_limit[key])))
            continue
        mode = "relative"
        metric = name
        for suffix, candidate_mode in (
            ("_relative_tolerance", "relative"),
            ("_absolute_tolerance", "absolute"),
            ("_relative", "relative"),
            ("_absolute", "absolute"),
            ("_rtol", "relative"),
            ("_atol", "absolute"),
        ):
            if name.endswith(suffix):
                metric = name[: -len(suffix)]
                mode = candidate_mode
                break
        specs.append((metric, mode, float(raw_limit)))
    for metric, mode, limit in specs:
        if not math.isfinite(limit) or limit < 0.0:
            raise ValueError(f"invalid {mode} threshold for {metric!r}: {limit}")
    return specs


def compare_convergence_reports(
    coarse: Mapping[str, Any],
    fine: Mapping[str, Any],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare two impact reports and return a machine-readable pass/fail gate.

    ``thresholds`` accepts either grouped tolerances, for example
    ``{"relative": {"contact_impulse": 0.10}, "absolute":
    {"damage_mean_final": 0.03}}``, or per-metric specifications such as
    ``{"contact_impulse": {"relative": 0.10},
    "damage_mean_final_absolute": 0.03}``.  A bare numeric metric tolerance is
    relative.  Relative error uses the fine report as reference and a nonzero
    coarse-value fallback only when the fine value is zero.

    Missing/non-finite requested metrics produce warnings and fail the gate
    rather than being silently skipped.  Failed source reports also fail the
    comparison because they provide no valid evidence of convergence.
    """
    _, np = _dependencies()
    if not isinstance(coarse, Mapping) or not isinstance(fine, Mapping):
        raise TypeError("coarse and fine reports must be mappings")
    coarse_flat = _flatten_report(coarse)
    fine_flat = _flatten_report(fine)
    specs = _threshold_specs(thresholds)
    warnings: list[str] = []
    comparisons: dict[str, dict[str, Any]] = {}
    block_reasons: list[str] = []

    for label, report in (("coarse", coarse), ("fine", fine)):
        source_status = report.get("status")
        if source_status is not None and source_status != "pass":
            block_reasons.append(
                f"{label} source report status is {source_status!r}, not 'pass'"
            )

    for requested, mode, limit in specs:
        left_item = _lookup_metric(coarse_flat, requested, coarse)
        right_item = _lookup_metric(fine_flat, requested, fine)
        label = f"{requested}:{mode}"
        if left_item is None or right_item is None:
            missing = []
            if left_item is None:
                missing.append("coarse")
            if right_item is None:
                missing.append("fine")
            _warn(warnings, f"{requested}: metric missing from {' and '.join(missing)} report(s)")
            comparisons[label] = {
                "metric": requested,
                "mode": mode,
                "limit": limit,
                "available": False,
                "passed": None,
            }
            block_reasons.append(f"{requested} is missing from a source report")
            continue
        left = np.asarray(left_item[1], dtype=float)
        right = np.asarray(right_item[1], dtype=float)
        left_count_weighted = "count" in _normal_name(left_item[0])
        right_count_weighted = "count" in _normal_name(right_item[0])
        if left_count_weighted != right_count_weighted:
            _warn(
                warnings,
                f"{requested}: coarse/fine metrics use incompatible volume/count weighting",
            )
            comparisons[label] = {
                "metric": requested,
                "mode": mode,
                "limit": limit,
                "available": False,
                "passed": None,
            }
            block_reasons.append(
                f"{requested} coarse/fine weighting methods differ"
            )
            continue
        if left.shape != right.shape:
            _warn(warnings, f"{requested}: coarse/fine metric shapes differ")
            comparisons[label] = {
                "metric": requested,
                "mode": mode,
                "limit": limit,
                "available": False,
                "passed": None,
            }
            block_reasons.append(f"{requested} coarse/fine shapes differ")
            continue
        absolute = float(np.linalg.norm((left - right).reshape(-1)))
        reference = float(np.linalg.norm(right.reshape(-1)))
        relative_scale = (
            reference
            if reference > np.finfo(float).tiny
            else max(
                float(np.linalg.norm(left.reshape(-1))),
                np.finfo(float).tiny,
            )
        )
        relative = absolute / relative_scale
        observed = absolute if mode == "absolute" else relative
        passed = observed <= limit
        comparison = {
            "metric": requested,
            "resolved_coarse_metric": left_item[0],
            "resolved_fine_metric": right_item[0],
            "mode": mode,
            "limit": limit,
            "coarse": _json_value(left, np),
            "fine": _json_value(right, np),
            "absolute_difference": absolute,
            "relative_difference": relative,
            "observed": observed,
            "available": True,
            "passed": passed,
        }
        comparisons[label] = comparison
        if not passed:
            block_reasons.append(
                f"{requested} {mode} difference {observed:.6g} exceeds {limit:.6g}"
            )

    available = [item for item in comparisons.values() if item["available"]]
    if not specs:
        _warn(warnings, "no convergence thresholds were supplied")
    if not available:
        block_reasons.append("no requested convergence metric was comparable")
    passed = bool(available) and not block_reasons
    return _json_value({
        "schema_version": 1,
        "gate": "Peridigm impact convergence",
        "status": "pass" if passed else "fail",
        "passed": passed,
        "comparison_count": len(available),
        "requested_comparison_count": len(specs),
        "comparisons": comparisons,
        "warnings": warnings,
        "block_reasons": block_reasons,
    }, np)
