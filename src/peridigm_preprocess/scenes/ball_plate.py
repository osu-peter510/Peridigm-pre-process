"""Open-source Gmsh ball/plate impact scene."""

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


def _sample(raw: Any, rng: random.Random, name: str) -> float:
    if isinstance(raw, (list, tuple)):
        if len(raw) != 2:
            raise ValueError(f"{name} range must contain [minimum, maximum]")
        low, high = map(float, raw)
        if not low <= high:
            raise ValueError(f"{name} range minimum exceeds maximum")
        return rng.uniform(low, high)
    return float(raw)


def _geometry_parameters(seed: int, geometry: Mapping[str, Any]) -> dict[str, Any]:
    rng = random.Random(seed)
    plate_diameter = _sample(
        geometry.get("plate_diameter_m", [0.100, 0.150]), rng, "plate_diameter_m"
    )
    plate_thickness = _sample(
        geometry.get("plate_thickness_m", [0.002, 0.004]),
        rng,
        "plate_thickness_m",
    )
    ball_diameter = _sample(
        geometry.get("ball_diameter_m", [0.010, 0.015]), rng, "ball_diameter_m"
    )
    angle_deg = _sample(
        geometry.get("impact_angle_deg", [0.0, 30.0]), rng, "impact_angle_deg"
    )
    azimuth_deg = _sample(
        geometry.get("impact_azimuth_deg", [0.0, 360.0]),
        rng,
        "impact_azimuth_deg",
    )
    speed = _sample(
        geometry.get("ball_speed_m_s", [20.0, 100.0]), rng, "ball_speed_m_s"
    )
    activation_time = _sample(
        geometry.get("contact_activation_time_s", [1.0e-4, 2.0e-4]),
        rng,
        "contact_activation_time_s",
    )
    if min(plate_diameter, plate_thickness, ball_diameter, speed, activation_time) <= 0:
        raise ValueError("Ball/plate dimensions, speed, and activation time must be > 0")
    if not 0.0 <= angle_deg < 90.0:
        raise ValueError("impact_angle_deg must be in [0, 90)")

    elevation = math.radians(angle_deg)
    azimuth = math.radians(azimuth_deg)
    direction = [
        math.sin(elevation) * math.cos(azimuth),
        math.sin(elevation) * math.sin(azimuth),
        -math.cos(elevation),
    ]
    plate_radius = 0.5 * plate_diameter
    ball_radius = 0.5 * ball_diameter
    usable_fraction = float(geometry.get("contact_position_fraction", 0.6))
    if not 0.0 <= usable_fraction <= 1.0:
        raise ValueError("contact_position_fraction must be in [0, 1]")
    maximum_offset = max(0.0, plate_radius - ball_radius) * usable_fraction
    offset = maximum_offset * math.sqrt(rng.random())
    contact_azimuth_deg = rng.uniform(0.0, 360.0)
    contact_azimuth = math.radians(contact_azimuth_deg)
    contact_position = [
        offset * math.cos(contact_azimuth),
        offset * math.sin(contact_azimuth),
        0.0,
    ]
    return {
        "seed": seed,
        "plate_diameter_m": plate_diameter,
        "plate_radius_m": plate_radius,
        "plate_thickness_m": plate_thickness,
        "ball_diameter_m": ball_diameter,
        "ball_radius_m": ball_radius,
        "ball_speed_m_s": speed,
        "impact_angle_deg": angle_deg,
        "impact_azimuth_deg": azimuth_deg,
        "direction": direction,
        "initial_velocity_m_s": [component * speed for component in direction],
        "contact_position_m": contact_position,
        "contact_position_radius_m": offset,
        "contact_position_azimuth_deg": contact_azimuth_deg,
        "target_contact_activation_time_s": activation_time,
        "contact_radius_multiplier": float(
            geometry.get("contact_radius_multiplier", 1.1)
        ),
        "search_radius_multiplier": float(
            geometry.get("search_radius_multiplier", 1.5)
        ),
    }


def _place_ball(parameters: Mapping[str, Any], mesh_size_m: float) -> dict[str, Any]:
    dx, dy, dz = parameters["direction"]
    speed = float(parameters["ball_speed_m_s"])
    normal_speed = -dz * speed
    contact_radius = float(parameters["contact_radius_multiplier"]) * mesh_size_m
    search_radius = float(parameters["search_radius_multiplier"]) * mesh_size_m
    surface_gap = contact_radius + normal_speed * float(
        parameters["target_contact_activation_time_s"]
    )
    if surface_gap <= search_radius:
        surface_gap = search_radius + max(0.25 * mesh_size_m, normal_speed * 1.0e-5)
    standoff = surface_gap / -dz
    cx, cy, _ = parameters["contact_position_m"]
    center = [
        cx - dx * standoff,
        cy - dy * standoff,
        float(parameters["ball_radius_m"]) - dz * standoff,
    ]
    return {
        "center": center,
        "surface_gap_m": surface_gap,
        "standoff_m": standoff,
        "contact_radius_m": contact_radius,
        "search_radius_m": search_radius,
        "predicted_search_entry_time_s": (
            (surface_gap - search_radius) / normal_speed
        ),
        "predicted_contact_activation_time_s": (
            (surface_gap - contact_radius) / normal_speed
        ),
        "predicted_geometric_impact_time_s": surface_gap / normal_speed,
    }


def _element_count(gmsh: Any, volume_tags: tuple[int, int]) -> int:
    total = 0
    for volume_tag in volume_tags:
        _, element_tags, _ = gmsh.model.mesh.getElements(3, volume_tag)
        total += sum(len(tags) for tags in element_tags)
    return total


def generate(
    mesh_path: Path,
    seed: int,
    mesh_config: Mapping[str, Any],
    geometry_config: Mapping[str, Any],
    verbose: bool = False,
) -> dict[str, Any]:
    """Generate one randomized, reproducible ball/plate Exodus mesh."""
    gmsh, _, _, np = require_mesh_dependencies()
    parameters = _geometry_parameters(seed, geometry_config)
    target = int(mesh_config["target_elements"])
    maximum = int(mesh_config["max_elements"])
    tolerance = float(mesh_config.get("tolerance", 0.08))
    max_attempts = int(mesh_config.get("max_attempts", 10))
    mesh_size = float(mesh_config["initial_size_m"])
    minimum_sicn = float(mesh_config.get("minimum_sicn", 0.05))
    if not 0.0 < tolerance < 1.0:
        raise ValueError("mesh.tolerance must be in (0, 1)")

    gmsh.initialize(["gmsh", "-nopopup"])
    try:
        gmsh.model.add(f"ball_plate_{seed:04d}")
        initial = _place_ball(parameters, mesh_size)
        plate_volume = gmsh.model.occ.addCylinder(
            0.0,
            0.0,
            -parameters["plate_thickness_m"],
            0.0,
            0.0,
            parameters["plate_thickness_m"],
            parameters["plate_radius_m"],
        )
        ball_volume = gmsh.model.occ.addSphere(
            *initial["center"], parameters["ball_radius_m"]
        )
        gmsh.model.occ.synchronize()

        history = []
        best: dict[str, Any] | None = None
        lower = max(1, int(round(target * (1.0 - tolerance))))
        upper = min(maximum, int(round(target * (1.0 + tolerance))))
        if lower > upper:
            lower = upper
        for attempt in range(max_attempts):
            configure_gmsh(mesh_size, seed, verbose)
            gmsh.model.mesh.generate(3)
            count = _element_count(gmsh, (plate_volume, ball_volume))
            within_maximum = count <= maximum
            within_target = lower <= count <= upper
            history.append(
                {
                    "attempt": attempt + 1,
                    "mesh_size_m": mesh_size,
                    "elements": count,
                    "within_target_tolerance": within_target,
                    "within_max_elements": within_maximum,
                }
            )
            if within_maximum:
                points, cell_blocks, node_sets, quality = extract_gmsh_tetra_blocks(
                    [
                        {
                            "name": "block_1",
                            "nodeset": "nodelist_1",
                            "entities": [plate_volume],
                        },
                        {
                            "name": "block_2",
                            "nodeset": "nodelist_2",
                            "entities": [ball_volume],
                        },
                    ]
                )
                candidate = {
                    "points": points,
                    "cell_blocks": cell_blocks,
                    "node_sets": node_sets,
                    "quality": quality,
                    "mesh_size_m": mesh_size,
                    "element_count": count,
                }
                history[-1]["minimum_sicn"] = quality["minimum"]
                history[-1]["quality_constraint_met"] = (
                    quality["minimum"] >= minimum_sicn
                )
                if quality["minimum"] >= minimum_sicn:
                    if best is None or abs(count - target) < abs(
                        int(best["element_count"]) - target
                    ):
                        best = candidate
                    if within_target:
                        break
            if attempt == max_attempts - 1:
                break
            scale = (count / target) ** (1.0 / 3.0)
            mesh_size *= min(1.5, max(0.67, scale))
            gmsh.model.mesh.clear()

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

        # Reposition the already-meshed ball using the measured contact scale.
        # Translation preserves mesh quality while making impact timing independent
        # of the initial Gmsh-size guess.
        contact_scale = max(actual_sizes.values())
        final_placement = _place_ball(parameters, contact_scale)
        shift = np.asarray(final_placement["center"]) - np.asarray(initial["center"])
        points[node_sets["nodelist_2"]] += shift
        actual_sizes = block_mesh_sizes(points, cell_blocks)
        gmsh_version = gmsh.__version__
    finally:
        gmsh.finalize()

    if quality["minimum"] < minimum_sicn:
        raise RuntimeError(
            f"Ball/plate mesh minimum SICN {quality['minimum']:.6g} is below {minimum_sicn}"
        )
    validation = write_exodus(
        mesh_path,
        points,
        cell_blocks,
        node_sets,
        title=f"Peridigm ball/plate scene seed {seed}",
    )
    if validation["element_count"] > maximum:
        raise RuntimeError("Written ball/plate mesh exceeds max_elements")
    return {
        "backend": "gmsh",
        "gmsh_version": gmsh_version,
        "seed": seed,
        "node_count": validation["node_count"],
        "element_count": validation["element_count"],
        "block_element_counts": dict(
            zip(validation["block_names"], validation["block_element_counts"])
        ),
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
        "initial_velocity_m_s": parameters["initial_velocity_m_s"],
        **final_placement,
    }
