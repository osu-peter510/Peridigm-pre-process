"""Open-source Gmsh generator for the cloth-fall scene.

All geometry in this module is expressed directly in SI metres.  The six
canonical Exodus blocks are the floor, cloth, three boxes, and sphere, in that
order.  Random geometry is sampled once before meshing so feedback iterations
can change the nominal element size without changing the scene.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

from peridigm_preprocess.exodus import (
    block_mesh_sizes,
    configure_gmsh,
    extract_gmsh_tetra_blocks,
    require_mesh_dependencies,
    write_exodus,
)


_DEFAULT_FLOOR_SIZE_M = (0.150, 0.150, 0.002)
_DEFAULT_CLOTH_SIZE_M = (0.140, 0.140, 0.002)
_DEFAULT_BRICK_SIZE_RANGES_M = (
    (0.012, 0.018),
    (0.012, 0.018),
    (0.026, 0.060),
)
_DEFAULT_SPHERE_RADIUS_M = (0.008, 0.012)


def _positive_vector3(raw: Any, name: str) -> tuple[float, float, float]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError(f"{name} must contain three positive lengths")
    if len(raw) != 3:
        raise ValueError(f"{name} must contain three positive lengths")
    values = tuple(float(value) for value in raw)
    if any(not math.isfinite(value) or value <= 0.0 for value in values):
        raise ValueError(f"Every {name} length must be finite and > 0")
    return values  # type: ignore[return-value]


def _nonnegative(raw: Any, name: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be finite and >= 0")
    return value


def _range(raw: Any, name: str, *, positive: bool = True) -> tuple[float, float]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError(f"{name} must contain [minimum, maximum]")
    if len(raw) != 2:
        raise ValueError(f"{name} must contain [minimum, maximum]")
    low, high = (float(raw[0]), float(raw[1]))
    if not math.isfinite(low) or not math.isfinite(high) or low > high:
        raise ValueError(f"{name} must be a finite ordered range")
    if positive and low <= 0.0:
        raise ValueError(f"{name} values must be > 0")
    return low, high


def _brick_ranges(raw: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("brick_size_ranges_m must contain x, y, and z ranges")
    if len(raw) != 3:
        raise ValueError("brick_size_ranges_m must contain x, y, and z ranges")
    return tuple(
        _range(axis_range, f"brick_size_ranges_m[{axis}]")
        for axis, axis_range in enumerate(raw)
    )


def _footprints_overlap(
    first: Mapping[str, Any], second: Mapping[str, Any], clearance: float
) -> bool:
    """Conservatively test horizontal AABBs, including requested clearance."""
    first_center = first["center_m"]
    second_center = second["center_m"]
    first_half = first["half_extents_m"]
    second_half = second["half_extents_m"]
    return (
        abs(float(first_center[0]) - float(second_center[0]))
        < float(first_half[0]) + float(second_half[0]) + clearance
        and abs(float(first_center[1]) - float(second_center[1]))
        < float(first_half[1]) + float(second_half[1]) + clearance
    )


def _place_obstacle(
    rng: random.Random,
    floor_size: tuple[float, float, float],
    half_extents: tuple[float, float, float],
    center_z: float,
    existing: list[dict[str, Any]],
    clearance: float,
    maximum_attempts: int,
    label: str,
) -> tuple[list[float], int]:
    x_limit = 0.5 * floor_size[0] - half_extents[0] - clearance
    y_limit = 0.5 * floor_size[1] - half_extents[1] - clearance
    if x_limit < 0.0 or y_limit < 0.0:
        raise ValueError(f"{label} does not fit on the floor with clearance_m")

    for attempt in range(1, maximum_attempts + 1):
        candidate = {
            "center_m": [
                rng.uniform(-x_limit, x_limit),
                rng.uniform(-y_limit, y_limit),
                center_z,
            ],
            "half_extents_m": list(half_extents),
        }
        if not any(
            _footprints_overlap(candidate, placed, clearance)
            for placed in existing
        ):
            return candidate["center_m"], attempt
    raise RuntimeError(
        f"Could not place {label} without overlap after {maximum_attempts} attempts"
    )


def _geometry_parameters(seed: int, geometry: Mapping[str, Any]) -> dict[str, Any]:
    rng = random.Random(int(seed))
    floor_size = _positive_vector3(
        geometry.get("floor_size_m", _DEFAULT_FLOOR_SIZE_M), "floor_size_m"
    )
    cloth_size = _positive_vector3(
        geometry.get("cloth_size_m", _DEFAULT_CLOTH_SIZE_M), "cloth_size_m"
    )
    cloth_height = float(geometry.get("cloth_height_m", 0.080))
    if not math.isfinite(cloth_height) or cloth_height <= 0.0:
        raise ValueError("cloth_height_m must be finite and > 0")
    if cloth_height - 0.5 * cloth_size[2] <= 0.0:
        raise ValueError("The cloth must start entirely above the floor")

    clearance = _nonnegative(geometry.get("clearance_m", 0.005), "clearance_m")
    brick_ranges = _brick_ranges(
        geometry.get("brick_size_ranges_m", _DEFAULT_BRICK_SIZE_RANGES_M)
    )
    sphere_radius_range = _range(
        geometry.get("sphere_radius_m", _DEFAULT_SPHERE_RADIUS_M),
        "sphere_radius_m",
    )
    placement_max_attempts = int(geometry.get("placement_max_attempts", 500))
    if placement_max_attempts <= 0:
        raise ValueError("placement_max_attempts must be > 0")

    brick_sizes = [
        [rng.uniform(*axis_range) for axis_range in brick_ranges]
        for _ in range(3)
    ]
    sphere_radius = rng.uniform(*sphere_radius_range)

    placed: list[dict[str, Any]] = []
    bricks: list[dict[str, Any]] = []
    placement_attempts: dict[str, int] = {}
    for index, size in enumerate(brick_sizes, start=1):
        half_extents = tuple(0.5 * value for value in size)
        center, attempts = _place_obstacle(
            rng,
            floor_size,
            half_extents,  # type: ignore[arg-type]
            0.5 * size[2],
            placed,
            clearance,
            placement_max_attempts,
            f"brick {index}",
        )
        obstacle = {
            "size_m": list(size),
            "center_m": center,
            "half_extents_m": list(half_extents),
        }
        placed.append(obstacle)
        bricks.append({"size_m": list(size), "center_m": list(center)})
        placement_attempts[f"brick_{index}"] = attempts

    sphere_half_extents = (sphere_radius, sphere_radius, sphere_radius)
    sphere_center, attempts = _place_obstacle(
        rng,
        floor_size,
        sphere_half_extents,
        sphere_radius,
        placed,
        clearance,
        placement_max_attempts,
        "sphere",
    )
    placement_attempts["sphere"] = attempts

    highest_obstacle = max(
        [brick["center_m"][2] + 0.5 * brick["size_m"][2] for brick in bricks]
        + [sphere_center[2] + sphere_radius]
    )
    cloth_bottom = cloth_height - 0.5 * cloth_size[2]
    if cloth_bottom <= highest_obstacle:
        raise ValueError(
            "cloth_height_m must put the cloth above every sampled obstacle"
        )

    return {
        "seed": int(seed),
        "units": "m",
        "floor_size_m": list(floor_size),
        "floor_center_m": [0.0, 0.0, -0.5 * floor_size[2]],
        "cloth_size_m": list(cloth_size),
        "cloth_height_m": cloth_height,
        "cloth_center_m": [0.0, 0.0, cloth_height],
        "clearance_m": clearance,
        "brick_size_ranges_m": [list(value) for value in brick_ranges],
        "brick_sizes_m": [brick["size_m"] for brick in bricks],
        "brick_centers_m": [brick["center_m"] for brick in bricks],
        "bricks": bricks,
        "sphere_radius_range_m": list(sphere_radius_range),
        "sphere_radius_m": sphere_radius,
        "sphere_center_m": list(sphere_center),
        "sphere": {
            "radius_m": sphere_radius,
            "center_m": list(sphere_center),
        },
        "placement_attempts": placement_attempts,
        "obstacles_non_overlapping": True,
    }


def _create_geometry(gmsh: Any, parameters: Mapping[str, Any]) -> list[int]:
    floor_size = parameters["floor_size_m"]
    floor = gmsh.model.occ.addBox(
        -0.5 * floor_size[0],
        -0.5 * floor_size[1],
        -floor_size[2],
        floor_size[0],
        floor_size[1],
        floor_size[2],
    )

    cloth_size = parameters["cloth_size_m"]
    cloth_center = parameters["cloth_center_m"]
    cloth = gmsh.model.occ.addBox(
        cloth_center[0] - 0.5 * cloth_size[0],
        cloth_center[1] - 0.5 * cloth_size[1],
        cloth_center[2] - 0.5 * cloth_size[2],
        cloth_size[0],
        cloth_size[1],
        cloth_size[2],
    )

    volumes = [floor, cloth]
    for brick in parameters["bricks"]:
        size = brick["size_m"]
        center = brick["center_m"]
        volumes.append(
            gmsh.model.occ.addBox(
                center[0] - 0.5 * size[0],
                center[1] - 0.5 * size[1],
                center[2] - 0.5 * size[2],
                size[0],
                size[1],
                size[2],
            )
        )

    sphere = parameters["sphere"]
    volumes.append(
        gmsh.model.occ.addSphere(*sphere["center_m"], sphere["radius_m"])
    )
    gmsh.model.occ.synchronize()

    # Physical-volume IDs mirror the Exodus IDs and are helpful when inspecting
    # a mesh in Gmsh, although Exodus extraction below is deliberately explicit.
    for identifier, volume in enumerate(volumes, start=1):
        physical = gmsh.model.addPhysicalGroup(3, [volume], identifier)
        gmsh.model.setPhysicalName(3, physical, f"block_{identifier}")
    return volumes


def _adapted_mesh_size(
    mesh_size: float,
    element_count: int,
    target: int,
    maximum: int,
) -> float:
    """Estimate the next TET4 edge length from three-dimensional feedback."""
    if element_count <= 0:
        return 0.67 * mesh_size
    scale = (element_count / target) ** (1.0 / 3.0)
    if element_count > maximum:
        # Bias just inside the hard limit when target and maximum are close.
        budget_scale = (element_count / (0.98 * maximum)) ** (1.0 / 3.0)
        scale = max(scale, budget_scale)
    scale = min(1.50, max(0.67, scale))
    updated = mesh_size * scale
    if math.isclose(updated, mesh_size, rel_tol=1.0e-12, abs_tol=0.0):
        updated = mesh_size * (1.05 if element_count > target else 0.95)
    return updated


def _configure_thin_box_mesh(gmsh: Any, volume: int, mesh_size: float) -> None:
    """Give a box a regular TET4 subdivision, avoiding thin-layer slivers."""
    surfaces = [
        tag
        for dimension, tag in gmsh.model.getBoundary(
            [(3, volume)], combined=False, oriented=False, recursive=False
        )
        if dimension == 2
    ]
    curves: set[int] = set()
    for surface in surfaces:
        curves.update(
            tag
            for dimension, tag in gmsh.model.getBoundary(
                [(2, surface)], combined=False, oriented=False, recursive=False
            )
            if dimension == 1
        )
    for curve in curves:
        bounds = gmsh.model.getBoundingBox(1, curve)
        length = math.sqrt(
            sum((bounds[axis + 3] - bounds[axis]) ** 2 for axis in range(3))
        )
        point_count = max(2, int(round(length / mesh_size)) + 1)
        gmsh.model.mesh.setTransfiniteCurve(curve, point_count)
    for surface in surfaces:
        gmsh.model.mesh.setTransfiniteSurface(surface)
    gmsh.model.mesh.setTransfiniteVolume(volume)


def _block_definitions(volumes: Sequence[int]) -> list[dict[str, Any]]:
    return [
        {
            "name": f"block_{identifier}",
            "nodeset": f"nodelist_{identifier}",
            "entities": [volume],
        }
        for identifier, volume in enumerate(volumes, start=1)
    ]


def generate(
    mesh_path: Path,
    seed: int,
    mesh_config: Mapping[str, Any],
    geometry_config: Mapping[str, Any],
    verbose: bool = False,
) -> dict[str, Any]:
    """Generate one deterministic cloth-fall scene as a TET4 Exodus mesh."""
    target = int(mesh_config["target_elements"])
    maximum = int(mesh_config["max_elements"])
    mesh_size = float(mesh_config["initial_size_m"])
    max_attempts = int(mesh_config.get("max_attempts", 10))
    tolerance = float(mesh_config.get("tolerance", 0.10))
    minimum_sicn = float(mesh_config.get("minimum_sicn", 0.05))
    if target <= 0 or maximum <= 0 or target > maximum:
        raise ValueError(
            "target_elements and max_elements must be positive, with target <= max"
        )
    if not math.isfinite(mesh_size) or mesh_size <= 0.0:
        raise ValueError("initial_size_m must be finite and > 0")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be > 0")
    if not 0.0 < tolerance < 1.0:
        raise ValueError("tolerance must be in (0, 1)")
    if not math.isfinite(minimum_sicn) or not 0.0 <= minimum_sicn <= 1.0:
        raise ValueError("minimum_sicn must be finite and in [0, 1]")

    parameters = _geometry_parameters(seed, geometry_config)
    gmsh, _, _, _ = require_mesh_dependencies()
    lower = max(1, int(round(target * (1.0 - tolerance))))
    upper = min(maximum, int(round(target * (1.0 + tolerance))))
    history: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None

    gmsh.initialize(["gmsh", "-nopopup"])
    try:
        gmsh.model.add(f"cloth_fall_{int(seed):04d}")
        volumes = _create_geometry(gmsh, parameters)
        for attempt in range(1, max_attempts + 1):
            configure_gmsh(mesh_size, int(seed), verbose)
            _configure_thin_box_mesh(gmsh, volumes[0], mesh_size)
            _configure_thin_box_mesh(gmsh, volumes[1], mesh_size)
            gmsh.model.mesh.generate(3)
            # Thin floor/cloth layers can leave isolated Delaunay slivers even
            # when the bulk quality distribution is good.  Netgen's local
            # optimization removes those without changing the sampled geometry.
            gmsh.model.mesh.optimize("Netgen")
            points, cell_blocks, node_sets, quality = extract_gmsh_tetra_blocks(
                _block_definitions(volumes)
            )
            element_count = sum(
                int(len(block["connectivity"])) for block in cell_blocks
            )
            within_maximum = element_count <= maximum
            within_target = lower <= element_count <= upper
            history.append(
                {
                    "attempt": attempt,
                    "mesh_size_m": mesh_size,
                    "elements": element_count,
                    "within_target_tolerance": within_target,
                    "within_max_elements": within_maximum,
                    "minimum_sicn": quality["minimum"],
                    "quality_constraint_met": quality["minimum"] >= minimum_sicn,
                }
            )
            if within_maximum and quality["minimum"] >= minimum_sicn:
                candidate = {
                    "points": points,
                    "cell_blocks": cell_blocks,
                    "node_sets": node_sets,
                    "quality": quality,
                    "mesh_size_m": mesh_size,
                    "element_count": element_count,
                }
                if best is None or abs(element_count - target) < abs(
                    int(best["element_count"]) - target
                ):
                    best = candidate
                if within_target:
                    break

            if attempt < max_attempts:
                # Count scales approximately with h^-3.  Bounded updates avoid
                # violent oscillation as thin slabs change layer count.
                mesh_size = _adapted_mesh_size(
                    mesh_size, element_count, target, maximum
                )
                gmsh.model.mesh.clear()

        gmsh_version = gmsh.__version__
    finally:
        gmsh.finalize()

    if best is None:
        final_count = history[-1]["elements"] if history else "unknown"
        raise RuntimeError(
            "Could not satisfy max_elements and mesh-quality constraints "
            f"in {max_attempts} attempts; max={maximum}, last count={final_count}"
        )

    points = best["points"]
    cell_blocks = best["cell_blocks"]
    node_sets = best["node_sets"]
    quality = best["quality"]
    selected_mesh_size = float(best["mesh_size_m"])
    actual_sizes = block_mesh_sizes(points, cell_blocks)

    if quality["minimum"] < minimum_sicn:
        raise RuntimeError(
            f"Cloth-fall mesh minimum SICN {quality['minimum']:.6g} "
            f"is below {minimum_sicn}"
        )

    validation = write_exodus(
        mesh_path,
        points,
        cell_blocks,
        node_sets,
        title=f"Peridigm cloth-fall scene seed {int(seed)}",
    )
    if validation["element_count"] > maximum:
        raise RuntimeError("Written cloth-fall mesh exceeds max_elements")

    by_block = dict(
        zip(validation["block_names"], validation["block_element_counts"])
    )
    return {
        "backend": "gmsh",
        "gmsh_version": gmsh_version,
        "seed": int(seed),
        "node_count": validation["node_count"],
        "element_count": validation["element_count"],
        "counts": {
            "nodes": validation["node_count"],
            "elements": validation["element_count"],
            "by_block": by_block,
        },
        "block_element_counts": by_block,
        "gmsh_nominal_mesh_size_m": selected_mesh_size,
        "mesh_size_method": "median_physical_cell_edge",
        "block_mesh_sizes_m": actual_sizes,
        "mesh_adaptation_history": history,
        "mesh_control": {
            "target_elements": target,
            "max_elements": maximum,
            "target_tolerance": tolerance,
            "target_range": [lower, upper],
            "target_met": lower <= validation["element_count"] <= upper,
            "max_constraint_met": validation["element_count"] <= maximum,
            "attempt_count": len(history),
        },
        "quality": quality,
        "validation": validation,
        "geometry_parameters": parameters,
    }
