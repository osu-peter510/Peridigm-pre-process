"""Open-source Gmsh Kalthoff--Winkler fracture benchmark scene.

The geometry is expressed entirely in SI units.  A cylindrical projectile is
placed above a rectangular plate containing two open, round-ended notches and
is assigned a velocity in the negative y direction.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

from peridigm_preprocess.exodus import (
    block_mesh_sizes,
    configure_gmsh,
    extract_gmsh_tetra_blocks,
    require_mesh_dependencies,
    write_exodus,
)


_GEOMETRY_DEFAULTS = {
    "board_length_m": 0.200,
    "board_height_m": 0.100,
    "board_thickness_m": 0.009,
    "notch_gap_m": 0.0515,
    "notch_width_m": 0.0015,
    "notch_depth_m": 0.050,
    "projectile_radius_m": 0.025,
    "projectile_length_m": 0.060,
    "projectile_gap_m": 0.020,
    "projectile_speed_m_s": 100.0,
}


def _geometry_parameters(geometry: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve and validate the deterministic SI geometry configuration."""
    parameters = {
        name: float(geometry.get(name, default))
        for name, default in _GEOMETRY_DEFAULTS.items()
    }
    if any(not math.isfinite(value) for value in parameters.values()):
        raise ValueError("KW geometry parameters must be finite")
    if any(value <= 0.0 for value in parameters.values()):
        raise ValueError("KW dimensions, gaps, and projectile speed must be > 0")

    board_length = parameters["board_length_m"]
    board_height = parameters["board_height_m"]
    notch_gap = parameters["notch_gap_m"]
    notch_width = parameters["notch_width_m"]
    notch_depth = parameters["notch_depth_m"]
    notch_radius = 0.5 * notch_width

    if notch_depth <= notch_radius:
        raise ValueError("notch_depth_m must exceed notch_width_m / 2")
    if notch_depth >= board_height:
        raise ValueError("notch_depth_m must be smaller than board_height_m")
    if notch_gap <= notch_width:
        raise ValueError("notch_gap_m must exceed notch_width_m")
    if 0.5 * (notch_gap + notch_width) >= 0.5 * board_length:
        raise ValueError("The two notches must fit strictly inside the board width")

    parameters.update(
        {
            "notch_radius_m": notch_radius,
            "notch_center_x_m": [-0.5 * notch_gap, 0.5 * notch_gap],
            "projectile_axis": [0.0, 1.0, 0.0],
            "projectile_center_m": [
                0.0,
                0.5 * board_height
                + parameters["projectile_gap_m"]
                + 0.5 * parameters["projectile_length_m"],
                0.0,
            ],
        }
    )
    return parameters


def _build_geometry(gmsh: Any, parameters: Mapping[str, Any]) -> tuple[int, list[int]]:
    """Create the projectile and the round-ended, double-notched board."""
    board_length = float(parameters["board_length_m"])
    board_height = float(parameters["board_height_m"])
    board_thickness = float(parameters["board_thickness_m"])
    notch_width = float(parameters["notch_width_m"])
    notch_depth = float(parameters["notch_depth_m"])
    notch_radius = float(parameters["notch_radius_m"])
    projectile_length = float(parameters["projectile_length_m"])
    projectile_radius = float(parameters["projectile_radius_m"])
    projectile_gap = float(parameters["projectile_gap_m"])

    board_bottom_y = -0.5 * board_height
    board_top_y = 0.5 * board_height
    board_bottom_z = -0.5 * board_thickness
    board_volume = gmsh.model.occ.addBox(
        -0.5 * board_length,
        board_bottom_y,
        board_bottom_z,
        board_length,
        board_height,
        board_thickness,
    )

    # Each cutter is a rectangle open to the top edge plus a circular lower
    # cap.  notch_depth_m is therefore the total depth to the cap's lowest
    # point, rather than the depth to its centre.
    cutter_padding = max(0.1 * board_thickness, 0.25 * notch_width, 1.0e-6)
    cutter_entities: list[tuple[int, int]] = []
    notch_tip_center_y = board_top_y - notch_depth + notch_radius
    for center_x in parameters["notch_center_x_m"]:
        rectangle = gmsh.model.occ.addBox(
            float(center_x) - notch_radius,
            notch_tip_center_y,
            board_bottom_z - cutter_padding,
            notch_width,
            board_top_y - notch_tip_center_y + cutter_padding,
            board_thickness + 2.0 * cutter_padding,
        )
        round_tip = gmsh.model.occ.addCylinder(
            float(center_x),
            notch_tip_center_y,
            board_bottom_z - cutter_padding,
            0.0,
            0.0,
            board_thickness + 2.0 * cutter_padding,
            notch_radius,
        )
        cutter_entities.extend(((3, rectangle), (3, round_tip)))

    cut_entities, _ = gmsh.model.occ.cut(
        [(3, board_volume)],
        cutter_entities,
        removeObject=True,
        removeTool=True,
    )
    board_volumes = [tag for dimension, tag in cut_entities if dimension == 3]
    if not board_volumes:
        raise RuntimeError("Gmsh removed the entire KW board while cutting notches")

    projectile_bottom_y = board_top_y + projectile_gap
    projectile_volume = gmsh.model.occ.addCylinder(
        0.0,
        projectile_bottom_y,
        0.0,
        0.0,
        projectile_length,
        0.0,
        projectile_radius,
    )
    gmsh.model.occ.synchronize()
    return projectile_volume, board_volumes


def _adapted_mesh_size(
    mesh_size: float,
    element_count: int,
    target: int,
    maximum: int,
) -> float:
    """Return the next nominal TET4 size using cubic count feedback."""
    if element_count <= 0:
        raise RuntimeError("Gmsh generated no tetrahedral elements")
    scale = (element_count / target) ** (1.0 / 3.0)
    if element_count > maximum:
        # Aim slightly inside the hard budget when the requested target lies
        # directly on the maximum and tetrahedral count quantisation matters.
        budget_scale = (element_count / (0.98 * maximum)) ** (1.0 / 3.0)
        scale = max(scale, budget_scale)
    scale = min(2.0, max(0.5, scale))
    updated = mesh_size * scale
    if math.isclose(updated, mesh_size, rel_tol=1.0e-12, abs_tol=0.0):
        updated = mesh_size * (1.05 if element_count > target else 0.95)
    return updated


def generate(
    mesh_path: Path,
    seed: int,
    mesh_config: Mapping[str, Any],
    geometry_config: Mapping[str, Any],
    verbose: bool = False,
) -> dict[str, Any]:
    """Generate a deterministic Kalthoff--Winkler Exodus mesh.

    ``target_elements`` is a soft target.  ``max_elements`` is a hard output
    constraint: if no in-budget candidate is found within ``max_attempts``, no
    Exodus file is written and a :class:`RuntimeError` is raised.
    """
    gmsh, _, _, _ = require_mesh_dependencies()
    parameters = _geometry_parameters(geometry_config)

    target = int(mesh_config["target_elements"])
    maximum = int(mesh_config["max_elements"])
    mesh_size = float(mesh_config["initial_size_m"])
    max_attempts = int(mesh_config.get("max_attempts", 10))
    tolerance = float(mesh_config.get("tolerance", 0.08))
    minimum_sicn = float(mesh_config.get("minimum_sicn", 0.05))
    if target <= 0 or maximum <= 0 or target > maximum:
        raise ValueError("Require 0 < target_elements <= max_elements")
    if not math.isfinite(mesh_size) or mesh_size <= 0.0:
        raise ValueError("initial_size_m must be finite and > 0")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be > 0")
    if not 0.0 < tolerance < 1.0:
        raise ValueError("mesh tolerance must be in (0, 1)")
    if not math.isfinite(minimum_sicn) or not 0.0 <= minimum_sicn <= 1.0:
        raise ValueError("minimum_sicn must be finite and in [0, 1]")

    lower = max(1, int(round(target * (1.0 - tolerance))))
    upper = min(maximum, int(round(target * (1.0 + tolerance))))
    if lower > upper:
        lower = upper

    history: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    gmsh.initialize(["gmsh", "-nopopup"])
    try:
        configure_gmsh(mesh_size, int(seed), verbose)
        gmsh.model.add(f"kw_fracture_{int(seed):04d}")
        projectile_volume, board_volumes = _build_geometry(gmsh, parameters)
        block_entities = [
            {
                "name": "block_1",
                "nodeset": "nodelist_1",
                "entities": [projectile_volume],
            },
            {
                "name": "block_2",
                "nodeset": "nodelist_2",
                "entities": board_volumes,
            },
        ]

        for attempt in range(1, max_attempts + 1):
            configure_gmsh(mesh_size, int(seed), verbose)
            gmsh.model.mesh.generate(3)
            points, cell_blocks, node_sets, quality = extract_gmsh_tetra_blocks(
                block_entities
            )
            element_count = sum(
                int(len(block["connectivity"])) for block in cell_blocks
            )
            history.append(
                {
                    "attempt": attempt,
                    "mesh_size_m": mesh_size,
                    "elements": element_count,
                    "within_max_elements": element_count <= maximum,
                    "minimum_sicn": quality["minimum"],
                    "quality_constraint_met": quality["minimum"] >= minimum_sicn,
                }
            )

            if element_count <= maximum and history[-1]["quality_constraint_met"]:
                candidate = {
                    "points": points,
                    "cell_blocks": cell_blocks,
                    "node_sets": node_sets,
                    "quality": quality,
                    "mesh_size": mesh_size,
                    "element_count": element_count,
                }
                if best is None or abs(element_count - target) < abs(
                    int(best["element_count"]) - target
                ):
                    best = candidate
                if lower <= element_count <= upper:
                    break

            if attempt < max_attempts:
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
    selected_mesh_size = float(best["mesh_size"])
    actual_sizes = block_mesh_sizes(points, cell_blocks)

    if quality["minimum"] < minimum_sicn:
        raise RuntimeError(
            f"KW mesh minimum SICN {quality['minimum']:.6g} is below "
            f"the configured threshold {minimum_sicn}"
        )

    validation = write_exodus(
        mesh_path,
        points,
        cell_blocks,
        node_sets,
        title=f"Peridigm Kalthoff-Winkler fracture scene seed {int(seed)}",
    )
    if validation["element_count"] > maximum:
        raise RuntimeError("Written KW mesh exceeds max_elements")

    block_counts = dict(
        zip(validation["block_names"], validation["block_element_counts"])
    )
    initial_velocity = [
        0.0,
        -float(parameters["projectile_speed_m_s"]),
        0.0,
    ]
    return {
        "backend": "gmsh",
        "gmsh_version": gmsh_version,
        "seed": int(seed),
        "node_count": validation["node_count"],
        "element_count": validation["element_count"],
        "block_element_counts": block_counts,
        "counts": {
            "nodes": validation["node_count"],
            "elements": validation["element_count"],
            "by_block": block_counts,
        },
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
        "initial_velocity_m_s": initial_velocity,
    }
