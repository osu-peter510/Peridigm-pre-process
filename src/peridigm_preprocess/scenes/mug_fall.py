"""Open-source Gmsh mug-drop scene.

The mug is an analytic OpenCASCADE solid (a hollow cylinder fused to a
trimmed torus), so this backend has no dependency on the historical Cubit
mesh.  The floor is built directly as a one-element-thick structured HEX8
grid and the breakable mug is meshed with first-order tetrahedra.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any, Mapping

from peridigm_preprocess.exodus import (
    block_mesh_sizes,
    configure_gmsh,
    extract_gmsh_tetra_blocks,
    require_mesh_dependencies,
    write_exodus,
)


_MUG_DEFAULTS: dict[str, tuple[float, float]] = {
    "body_radius_m": (0.030, 0.050),
    "body_height_m": (0.070, 0.115),
    "wall_thickness_m": (0.0025, 0.0050),
    "handle_width_m": (0.007, 0.014),
    "handle_height_m": (0.035, 0.060),
    "handle_protrusion_m": (0.018, 0.035),
}


def _sample(raw: Any, rng: random.Random, name: str) -> float:
    """Resolve either a scalar or an inclusive ``[minimum, maximum]`` range."""
    if isinstance(raw, bool):
        raise ValueError(f"{name} must be a number or [minimum, maximum]")
    if isinstance(raw, (list, tuple)):
        if len(raw) != 2:
            raise ValueError(f"{name} range must contain [minimum, maximum]")
        low, high = map(float, raw)
        if not (math.isfinite(low) and math.isfinite(high)):
            raise ValueError(f"{name} range must be finite")
        if low > high:
            raise ValueError(f"{name} range minimum exceeds maximum")
        return rng.uniform(low, high)
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _vector3(raw: Any, name: str) -> list[float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError(f"{name} must contain exactly three components")
    result = [float(component) for component in raw]
    if not all(math.isfinite(component) for component in result):
        raise ValueError(f"{name} components must be finite")
    return result


def _random_rotation_degrees(raw: Any, rng: random.Random) -> list[float]:
    """Resolve optional independent x/y/z Euler-angle ranges.

    The preferred spelling is ``{x: [min, max], y: ..., z: ...}``.  A
    three-item sequence supplies one scalar/range per axis, while a plain
    two-number range is sampled independently for all three axes.
    """
    if raw is None:
        return [0.0, 0.0, 0.0]
    axes = ("x", "y", "z")
    if isinstance(raw, Mapping):
        return [
            _sample(raw.get(axis, 0.0), rng, f"random_rotation_deg.{axis}")
            for axis in axes
        ]
    if isinstance(raw, (list, tuple)):
        if len(raw) == 3:
            return [
                _sample(value, rng, f"random_rotation_deg.{axis}")
                for axis, value in zip(axes, raw)
            ]
        if len(raw) == 2:
            return [
                _sample(raw, rng, f"random_rotation_deg.{axis}") for axis in axes
            ]
        raise ValueError(
            "random_rotation_deg must be [minimum, maximum], three axis "
            "values/ranges, or an {x, y, z} mapping"
        )
    magnitude = abs(float(raw))
    if not math.isfinite(magnitude):
        raise ValueError("random_rotation_deg must be finite")
    return [rng.uniform(-magnitude, magnitude) for _ in axes]


def _geometry_parameters(seed: int, geometry: Mapping[str, Any]) -> dict[str, Any]:
    rng = random.Random(seed)
    mug_config = geometry.get("mug", {})
    floor_config = geometry.get("floor", {})
    if not isinstance(mug_config, Mapping):
        raise ValueError("geometry.mug must be a mapping")
    if not isinstance(floor_config, Mapping):
        raise ValueError("geometry.floor must be a mapping")

    mug = {
        name: _sample(mug_config.get(name, default), rng, f"geometry.mug.{name}")
        for name, default in _MUG_DEFAULTS.items()
    }
    if any(value <= 0.0 for value in mug.values()):
        raise ValueError("All mug dimensions must be > 0")
    if mug["wall_thickness_m"] >= mug["body_radius_m"]:
        raise ValueError("mug wall_thickness_m must be smaller than body_radius_m")
    if mug["wall_thickness_m"] >= mug["body_height_m"]:
        raise ValueError("mug wall_thickness_m must be smaller than body_height_m")
    if mug["handle_width_m"] >= mug["handle_height_m"]:
        raise ValueError(
            "mug handle_width_m must be smaller than handle_height_m for a torus"
        )

    side_length = float(floor_config.get("side_length_m", 0.200))
    thickness = float(floor_config.get("thickness_m", 0.002))
    raw_elements_per_side = floor_config.get("elements_per_side", 44)
    if isinstance(raw_elements_per_side, bool):
        raise ValueError("geometry.floor.elements_per_side must be an integer")
    elements_per_side = int(raw_elements_per_side)
    if float(raw_elements_per_side) != elements_per_side:
        raise ValueError("geometry.floor.elements_per_side must be an integer")
    if not (math.isfinite(side_length) and side_length > 0.0):
        raise ValueError("geometry.floor.side_length_m must be finite and > 0")
    if not (math.isfinite(thickness) and thickness > 0.0):
        raise ValueError("geometry.floor.thickness_m must be finite and > 0")
    if elements_per_side <= 0:
        raise ValueError("geometry.floor.elements_per_side must be > 0")

    clearance = float(geometry.get("clearance_m", 0.075))
    if not math.isfinite(clearance) or clearance < 0.0:
        raise ValueError("geometry.clearance_m must be finite and >= 0")
    velocity = _vector3(
        geometry.get("initial_velocity_m_s", [0.0, 0.0, -5.0]),
        "geometry.initial_velocity_m_s",
    )
    rotation = _random_rotation_degrees(
        geometry.get("random_rotation_deg"), rng
    )
    return {
        "seed": int(seed),
        "mug": mug,
        "floor": {
            "side_length_m": side_length,
            "thickness_m": thickness,
            "elements_per_side": elements_per_side,
            "in_plane_spacing_m": side_length / elements_per_side,
        },
        "clearance_m": clearance,
        "rotation_deg": rotation,
        "initial_velocity_m_s": velocity,
    }


def _build_mug_cad(gmsh: Any, parameters: Mapping[str, float]) -> int:
    """Create and return the single fused OCC volume for the mug."""
    radius = float(parameters["body_radius_m"])
    height = float(parameters["body_height_m"])
    wall = float(parameters["wall_thickness_m"])
    handle_width = float(parameters["handle_width_m"])
    handle_height = float(parameters["handle_height_m"])
    protrusion = float(parameters["handle_protrusion_m"])

    outer = gmsh.model.occ.addCylinder(
        0.0, 0.0, 0.0, 0.0, 0.0, height, radius
    )
    inner = gmsh.model.occ.addCylinder(
        0.0,
        0.0,
        wall,
        0.0,
        0.0,
        height - wall,
        radius - wall,
    )
    body_parts, _ = gmsh.model.occ.cut([(3, outer)], [(3, inner)])
    if len(body_parts) != 1:
        raise RuntimeError(f"Expected one hollow mug body, got {body_parts}")

    center_x = radius + 0.5 * protrusion
    center_z = 0.55 * height
    torus = gmsh.model.occ.addTorus(
        center_x,
        0.0,
        center_z,
        0.5 * handle_height,
        0.5 * handle_width,
    )
    # OCC creates a torus around z.  Rotate it into the x-z plane so the
    # retained arc becomes the familiar side handle.
    gmsh.model.occ.rotate(
        [(3, torus)],
        center_x,
        0.0,
        center_z,
        1.0,
        0.0,
        0.0,
        0.5 * math.pi,
    )

    cut_size = 2.0 * max(height, 4.0 * radius)
    cut_box = gmsh.model.occ.addBox(
        radius - 0.5 * wall - cut_size,
        -0.5 * cut_size,
        center_z - 0.5 * cut_size,
        cut_size,
        cut_size,
        cut_size,
    )
    handle_parts, _ = gmsh.model.occ.cut([(3, torus)], [(3, cut_box)])
    if not handle_parts:
        raise RuntimeError("Handle trim removed the entire torus")
    fused_parts, _ = gmsh.model.occ.fuse(body_parts, handle_parts)
    gmsh.model.occ.synchronize()

    volumes = gmsh.model.getEntities(3)
    if len(fused_parts) != 1 or len(volumes) != 1:
        raise RuntimeError(
            f"Expected one fused mug volume, got fuse={fused_parts}, model={volumes}"
        )
    return int(volumes[0][1])


def _build_floor(np: Any, parameters: Mapping[str, Any]) -> tuple[Any, Any]:
    """Build a centered, one-element-through-thickness structured HEX8 floor."""
    element_count = int(parameters["elements_per_side"])
    node_count = element_count + 1
    coordinates = np.linspace(
        -0.5 * float(parameters["side_length_m"]),
        0.5 * float(parameters["side_length_m"]),
        node_count,
        dtype=np.float64,
    )
    z_coordinates = (-float(parameters["thickness_m"]), 0.0)
    points = np.asarray(
        [
            (x, y, z)
            for z in z_coordinates
            for y in coordinates
            for x in coordinates
        ],
        dtype=np.float64,
    )
    layer_size = node_count * node_count

    def node(layer: int, iy: int, ix: int) -> int:
        return layer * layer_size + iy * node_count + ix

    cells = np.asarray(
        [
            (
                node(0, iy, ix),
                node(0, iy, ix + 1),
                node(0, iy + 1, ix + 1),
                node(0, iy + 1, ix),
                node(1, iy, ix),
                node(1, iy, ix + 1),
                node(1, iy + 1, ix + 1),
                node(1, iy + 1, ix),
            )
            for iy in range(element_count)
            for ix in range(element_count)
        ],
        dtype=np.int32,
    )
    return points, cells


def _rotation_matrix(np: Any, degrees: list[float]) -> Any:
    x_angle, y_angle, z_angle = map(math.radians, degrees)
    cx, sx = math.cos(x_angle), math.sin(x_angle)
    cy, sy = math.cos(y_angle), math.sin(y_angle)
    cz, sz = math.cos(z_angle), math.sin(z_angle)
    rotate_x = np.asarray(
        ((1.0, 0.0, 0.0), (0.0, cx, -sx), (0.0, sx, cx)),
        dtype=np.float64,
    )
    rotate_y = np.asarray(
        ((cy, 0.0, sy), (0.0, 1.0, 0.0), (-sy, 0.0, cy)),
        dtype=np.float64,
    )
    rotate_z = np.asarray(
        ((cz, -sz, 0.0), (sz, cz, 0.0), (0.0, 0.0, 1.0)),
        dtype=np.float64,
    )
    return rotate_z @ rotate_y @ rotate_x


def _transform_mug(
    points: Any,
    np: Any,
    rotation_deg: list[float],
    clearance_m: float,
    contact_patch_depth_m: float,
) -> tuple[Any, dict[str, Any]]:
    """Rotate the mug, center its first-contact patch, and impose its clearance."""
    transformed = np.asarray(points, dtype=np.float64).copy()
    source_bbox = [
        transformed.min(axis=0).tolist(),
        transformed.max(axis=0).tolist(),
    ]
    center = 0.5 * (transformed.min(axis=0) + transformed.max(axis=0))
    rotation = _rotation_matrix(np, rotation_deg)
    transformed = (transformed - center) @ rotation.T + center
    rotated_bbox = [
        transformed.min(axis=0).tolist(),
        transformed.max(axis=0).tolist(),
    ]

    minimum_z = float(transformed[:, 2].min())
    contact_patch = transformed[:, 2] <= minimum_z + contact_patch_depth_m
    if not np.any(contact_patch):
        raise RuntimeError("Mug mesh has no lowest-node contact patch")
    contact_xy = transformed[contact_patch, :2].mean(axis=0)
    transformed[:, 0] -= contact_xy[0]
    transformed[:, 1] -= contact_xy[1]
    transformed[:, 2] += clearance_m - minimum_z
    actual_clearance = float(transformed[:, 2].min())
    absolute_tolerance = max(1.0e-14, abs(clearance_m) * 1.0e-12)
    if not math.isclose(
        actual_clearance, clearance_m, rel_tol=0.0, abs_tol=absolute_tolerance
    ):
        raise RuntimeError(
            f"Failed to impose mug clearance {clearance_m}; got {actual_clearance}"
        )
    return transformed, {
        "source_bbox_m": source_bbox,
        "rotated_bbox_m": rotated_bbox,
        "contact_patch_node_count": int(np.count_nonzero(contact_patch)),
        "contact_patch_source_centroid_xy_m": contact_xy.tolist(),
        "final_bbox_m": [
            transformed.min(axis=0).tolist(),
            transformed.max(axis=0).tolist(),
        ],
        "actual_minimum_clearance_m": actual_clearance,
    }


def _orient_tetrahedra(points: Any, cells: Any, np: Any) -> tuple[Any, Any]:
    oriented = np.asarray(cells, dtype=np.int32).copy()

    def signed_volumes(connectivity: Any) -> Any:
        a = points[connectivity[:, 0]]
        b = points[connectivity[:, 1]]
        c = points[connectivity[:, 2]]
        d = points[connectivity[:, 3]]
        return np.einsum(
            "ij,ij->i", np.cross(b - a, c - a), d - a
        ) / 6.0

    signed = signed_volumes(oriented)
    negative = signed < 0.0
    if np.any(negative):
        temporary = oriented[negative, 1].copy()
        oriented[negative, 1] = oriented[negative, 2]
        oriented[negative, 2] = temporary
    volumes = signed_volumes(oriented)
    if np.any(~np.isfinite(volumes)) or np.any(volumes <= 0.0):
        raise RuntimeError("Generated mug has zero, inverted, or invalid tetrahedra")
    return oriented, volumes


def generate(
    mesh_path: Path,
    seed: int,
    mesh_config: Mapping[str, Any],
    geometry_config: Mapping[str, Any],
    verbose: bool = False,
) -> dict[str, Any]:
    """Generate one reproducible mug/floor Exodus mesh.

    ``target_elements`` and ``max_elements`` refer to the complete scene.  The
    structured floor count is subtracted before adapting the nominal Gmsh size,
    because only the mug tetrahedra respond to that size.
    """
    gmsh, _, _, np = require_mesh_dependencies()
    parameters = _geometry_parameters(seed, geometry_config)
    floor_parameters = parameters["floor"]
    floor_element_count = int(floor_parameters["elements_per_side"]) ** 2

    target = int(mesh_config["target_elements"])
    maximum = int(mesh_config["max_elements"])
    tolerance = float(mesh_config.get("tolerance", 0.03))
    max_attempts = int(mesh_config.get("max_attempts", 10))
    mesh_size = float(mesh_config["initial_size_m"])
    minimum_sicn = float(mesh_config.get("minimum_sicn", 0.10))
    if target <= floor_element_count:
        raise ValueError(
            "mesh.target_elements must exceed the fixed structured-floor count "
            f"({floor_element_count})"
        )
    if maximum < target:
        raise ValueError("mesh.max_elements must be >= mesh.target_elements")
    if maximum <= floor_element_count:
        raise ValueError("mesh.max_elements leaves no elements for the mug")
    if not 0.0 < tolerance < 1.0:
        raise ValueError("mesh.tolerance must be in (0, 1)")
    if max_attempts <= 0:
        raise ValueError("mesh.max_attempts must be > 0")
    if not math.isfinite(mesh_size) or mesh_size <= 0.0:
        raise ValueError("mesh.initial_size_m must be finite and > 0")
    if not math.isfinite(minimum_sicn) or not 0.0 <= minimum_sicn <= 1.0:
        raise ValueError("mesh.minimum_sicn must be finite and in [0, 1]")

    target_mug_elements = target - floor_element_count
    lower = max(
        floor_element_count + 1, int(round(target * (1.0 - tolerance)))
    )
    upper = min(maximum, int(round(target * (1.0 + tolerance))))
    if lower > upper:
        lower = upper

    gmsh.initialize(["gmsh", "-nopopup"])
    try:
        # Suppress OCC Boolean progress as well as meshing output when requested;
        # configure_gmsh() is called only after the CAD model already exists.
        gmsh.option.setNumber("General.Terminal", 1 if verbose else 0)
        gmsh.model.add(f"mug_fall_{seed:04d}")
        mug_volume = _build_mug_cad(gmsh, parameters["mug"])
        cad_volume = float(gmsh.model.occ.getMass(3, mug_volume))

        history: list[dict[str, Any]] = []
        best: dict[str, Any] | None = None
        mug_element_count = 0
        total_element_count = floor_element_count
        for attempt in range(max_attempts):
            configure_gmsh(mesh_size, seed, verbose)
            gmsh.model.mesh.generate(3)
            mug_points, mug_blocks, _, quality = extract_gmsh_tetra_blocks(
                [
                    {
                        "name": "block_2",
                        "nodeset": "nodelist_2",
                        "entities": [mug_volume],
                    }
                ]
            )
            netgen_optimized = False
            if quality["minimum"] < minimum_sicn:
                # A Boolean seam between the body and handle can occasionally
                # leave a single sliver despite otherwise good statistics.
                # Netgen's volume optimizer removes it, but can change the TET
                # count, so optimization must happen inside the feedback loop.
                gmsh.model.mesh.optimize("Netgen")
                netgen_optimized = True
                mug_points, mug_blocks, _, quality = extract_gmsh_tetra_blocks(
                    [
                        {
                            "name": "block_2",
                            "nodeset": "nodelist_2",
                            "entities": [mug_volume],
                        }
                    ]
                )
            if quality["minimum"] < minimum_sicn:
                raise RuntimeError(
                    f"Mug mesh minimum SICN {quality['minimum']:.6g} remains below "
                    f"mesh.minimum_sicn={minimum_sicn:.6g} after optimization"
                )
            if len(mug_blocks) != 1:
                raise RuntimeError("Expected exactly one extracted mug element block")
            mug_cells = mug_blocks[0]["connectivity"]
            mug_element_count = len(mug_cells)
            total_element_count = floor_element_count + mug_element_count
            history.append(
                {
                    "attempt": attempt + 1,
                    "mesh_size_m": mesh_size,
                    "floor_hex8_elements": floor_element_count,
                    "mug_tet4_elements": mug_element_count,
                    "total_elements": total_element_count,
                    "minimum_sicn": quality["minimum"],
                    "netgen_optimized": netgen_optimized,
                    "within_target_tolerance": lower <= total_element_count <= upper,
                    "within_max_elements": total_element_count <= maximum,
                }
            )
            if total_element_count <= maximum:
                candidate = {
                    "points": mug_points,
                    "blocks": mug_blocks,
                    "cells": mug_cells,
                    "quality": quality,
                    "mesh_size_m": mesh_size,
                    "mug_element_count": mug_element_count,
                    "total_element_count": total_element_count,
                }
                if best is None or abs(total_element_count - target) < abs(
                    int(best["total_element_count"]) - target
                ):
                    best = candidate
                if lower <= total_element_count <= upper:
                    break
            if attempt == max_attempts - 1:
                break

            # Mug count scales approximately with h^-3.  Subtracting the fixed
            # floor before this feedback step avoids systematically under-meshing
            # the mug as the floor density changes.
            scale = (mug_element_count / target_mug_elements) ** (1.0 / 3.0)
            if total_element_count > upper:
                scale = max(1.002, scale)
            else:
                scale = min(0.998, scale)
            if total_element_count > maximum:
                maximum_mug = maximum - floor_element_count
                cap_scale = (mug_element_count / maximum_mug) ** (1.0 / 3.0)
                scale = max(scale, 1.01 * cap_scale)
            mesh_size *= min(1.5, max(0.67, scale))
            gmsh.model.mesh.clear()

        if best is None:
            raise RuntimeError(
                "Could not satisfy the hard max_elements constraint "
                f"({maximum}) in {max_attempts} attempts; last total was "
                f"{total_element_count}"
            )
        mug_points = best["points"]
        mug_blocks = best["blocks"]
        mug_cells = best["cells"]
        quality = best["quality"]
        selected_mesh_size = float(best["mesh_size_m"])
        mug_element_count = int(best["mug_element_count"])
        total_element_count = int(best["total_element_count"])
        measured_mug_size = block_mesh_sizes(mug_points, mug_blocks)["block_2"]
        mug_points, placement = _transform_mug(
            mug_points,
            np,
            parameters["rotation_deg"],
            float(parameters["clearance_m"]),
            0.25 * measured_mug_size,
        )
        mug_cells, mug_tetra_volumes = _orient_tetrahedra(
            mug_points, mug_cells, np
        )
        gmsh_version = gmsh.__version__
    finally:
        gmsh.finalize()

    if quality["minimum"] < minimum_sicn:
        raise RuntimeError(
            f"Mug mesh minimum SICN {quality['minimum']:.6g} is below "
            f"mesh.minimum_sicn={minimum_sicn:.6g}"
        )

    floor_points, floor_cells = _build_floor(np, floor_parameters)
    mug_offset = len(floor_points)
    points = np.vstack((floor_points, mug_points))
    mug_cells = mug_cells + mug_offset
    cell_blocks = [
        {
            "name": "block_1",
            "cell_type": "hexahedron",
            "connectivity": floor_cells,
        },
        {
            "name": "block_2",
            "cell_type": "tetra",
            "connectivity": mug_cells,
        },
    ]
    node_sets = {
        "nodelist_1": np.arange(len(floor_points), dtype=np.int32),
        "nodelist_2": np.arange(mug_offset, len(points), dtype=np.int32),
    }
    actual_sizes = block_mesh_sizes(points, cell_blocks)
    validation = write_exodus(
        mesh_path,
        points,
        cell_blocks,
        node_sets,
        title=f"Peridigm mug-fall scene seed {seed}",
    )
    if validation["element_count"] > maximum:
        raise RuntimeError(
            f"Written mug/floor mesh has {validation['element_count']} elements, "
            f"above hard cap {maximum}"
        )
    block_counts = dict(
        zip(validation["block_names"], validation["block_element_counts"])
    )
    expected_block_counts = {
        "block_1": floor_element_count,
        "block_2": mug_element_count,
    }
    if block_counts != expected_block_counts:
        raise RuntimeError(
            f"Written Exodus block counts differ from memory: {block_counts} != "
            f"{expected_block_counts}"
        )

    counts = {
        "nodes": validation["node_count"],
        "elements": validation["element_count"],
        "floor_hex8_elements": floor_element_count,
        "mug_tet4_elements": mug_element_count,
        "by_block": block_counts,
    }
    tetra_volume = float(np.sum(mug_tetra_volumes))
    return {
        "backend": "gmsh",
        "gmsh_version": gmsh_version,
        "seed": int(seed),
        "node_count": validation["node_count"],
        "element_count": validation["element_count"],
        "counts": counts,
        "block_element_counts": block_counts,
        "gmsh_nominal_mesh_size_m": selected_mesh_size,
        "mesh_size_method": "median_physical_cell_edge",
        "block_mesh_sizes_m": actual_sizes,
        "mesh_adaptation_history": history,
        "target_element_interval": [lower, upper],
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
        "minimum_sicn_required": minimum_sicn,
        "validation": validation,
        "geometry_parameters": parameters,
        "initial_velocity_m_s": parameters["initial_velocity_m_s"],
        "cad_mug_volume_m3": cad_volume,
        "tetrahedral_mug_volume_m3": tetra_volume,
        "tetrahedral_to_cad_volume_relative_error": tetra_volume / cad_volume - 1.0,
        **placement,
    }
