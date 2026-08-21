#!/usr/bin/env python3
"""Generate randomized no-floor ball/plate Peridigm scenes with Gmsh.

This backend is fully open source: Gmsh creates first-order tetrahedral meshes,
meshio writes ExodusII, and netCDF4 adds the Exodus block metadata that
Peridigm expects.

Randomized parameters (deterministic for a given seed):
  * plate diameter:       100-150 mm
  * plate thickness:      2-4 mm
  * ball diameter:        10-15 mm
  * shooting direction:   0-30 degrees from vertical, random azimuth
  * contact position:     uniformly sampled in the usable central 60% area
  * initial speed:        20-100 m/s

The plate top face is at z=0.  The ball position is chosen so it starts outside
the contact search radius and the short-range contact model activates 1e-4 to
2e-4 seconds after the simulation starts. Geometry is constructed directly in
meters, matching Peridigm's SI units.

Usage:
  conda env create -f environment-gmsh.yml
  conda activate peridigm-gmsh
  python ball-plate-nf/generate_scene.py --n-scenes 1000 --seed 0 \
      --output-dir E:/Peridigm/ball-plate-nf
"""

import argparse
import json
import math
import random
import sys
from pathlib import Path

try:
    import gmsh
    import meshio
    import netCDF4
    import numpy as np
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing open-source meshing dependencies. Create and activate the "
        "Conda environment with: conda env create -f environment-gmsh.yml"
    ) from exc


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DYNAMIC_IMPACT_DIR = REPO_ROOT / "dynamic-impact-generation"
sys.path.insert(0, str(DYNAMIC_IMPACT_DIR))

from peridigm_xml import generate_ball_plate_peridigm_xml


PLATE_BLOCK = "block_1"
BALL_BLOCK = "block_2"
PLATE_NODESET = "nodelist_1"
BALL_NODESET = "nodelist_2"
DEFAULT_MAX_ELEMENTS = 12000
MIN_IMPACT_TIME_S = 1.0e-4
MAX_IMPACT_TIME_S = 2.0e-4
CONTACT_RADIUS_FACTOR = 1.1
SEARCH_RADIUS_FACTOR = 1.5
XML_VARIANTS = {
    "critical_stretch_0p0005": ("critical-stretch-0p0005", 0.0005),
    "critical_stretch_0p0015": ("critical-stretch-0p0015", 0.0015),
}


def echo(message: str) -> None:
    sys.__stdout__.write(str(message) + "\n")
    sys.__stdout__.flush()


def sample_parameters(seed: int) -> dict:
    """Sample one reproducible set of geometry and impact parameters."""
    rng = random.Random(seed)

    plate_diameter_mm = rng.uniform(100.0, 150.0)
    plate_thickness_mm = rng.uniform(2.0, 4.0)
    ball_diameter_mm = rng.uniform(10.0, 15.0)
    plate_radius_mm = plate_diameter_mm / 2.0
    ball_radius_mm = ball_diameter_mm / 2.0

    shooting_angle_deg = rng.uniform(0.0, 30.0)
    shooting_azimuth_deg = rng.uniform(0.0, 360.0)
    ball_speed = rng.uniform(20.0, 100.0)

    elevation = math.radians(shooting_angle_deg)
    azimuth = math.radians(shooting_azimuth_deg)
    dx = math.sin(elevation) * math.cos(azimuth)
    dy = math.sin(elevation) * math.sin(azimuth)
    dz = -math.cos(elevation)

    max_contact_position_radius_mm = (
        max(0.0, plate_radius_mm - ball_radius_mm) * 0.6
    )
    contact_position_radius_mm = (
        max_contact_position_radius_mm * math.sqrt(rng.random())
    )
    contact_azimuth_deg = rng.uniform(0.0, 360.0)
    contact_azimuth = math.radians(contact_azimuth_deg)
    contact_x_mm = contact_position_radius_mm * math.cos(contact_azimuth)
    contact_y_mm = contact_position_radius_mm * math.sin(contact_azimuth)

    requested_mesh_size_mm = ball_radius_mm / 3.0
    contact_radius_mm = CONTACT_RADIUS_FACTOR * requested_mesh_size_mm
    search_radius_mm = SEARCH_RADIUS_FACTOR * requested_mesh_size_mm
    target_contact_activation_time_s = rng.uniform(
        MIN_IMPACT_TIME_S, MAX_IMPACT_TIME_S
    )
    normal_speed_mm_s = -dz * ball_speed * 1000.0

    # Short-range contact starts when the surface gap reaches Contact Radius.
    # Add the distance closed during the requested activation time, then move
    # the ball center backward along its trajectory.  SEARCH_RADIUS_FACTOR is
    # deliberately small enough that every sampled scene starts outside the
    # search radius, including the slowest and most oblique impact.
    initial_surface_gap_mm = (
        contact_radius_mm
        + normal_speed_mm_s * target_contact_activation_time_s
    )
    if initial_surface_gap_mm <= search_radius_mm:
        raise RuntimeError(
            "Initial ball/plate gap is not outside the contact search radius"
        )
    standoff_mm = initial_surface_gap_mm / -dz
    initial_x_mm = contact_x_mm - dx * standoff_mm
    initial_y_mm = contact_y_mm - dy * standoff_mm
    initial_z_mm = ball_radius_mm - dz * standoff_mm

    return {
        "seed": seed,
        "plate_diameter_mm": plate_diameter_mm,
        "plate_radius_mm": plate_radius_mm,
        "plate_thickness_mm": plate_thickness_mm,
        "ball_diameter_mm": ball_diameter_mm,
        "ball_radius_mm": ball_radius_mm,
        "ball_speed": ball_speed,
        "shooting_angle_deg": shooting_angle_deg,
        "shooting_azimuth_deg": shooting_azimuth_deg,
        "elevation_deg": shooting_angle_deg,
        "azimuth_deg": shooting_azimuth_deg,
        "dx": dx,
        "dy": dy,
        "dz": dz,
        "contact_position_mm": [contact_x_mm, contact_y_mm, 0.0],
        "contact_position_radius_mm": contact_position_radius_mm,
        "contact_azimuth_deg": contact_azimuth_deg,
        "initial_position_mm": [initial_x_mm, initial_y_mm, initial_z_mm],
        "initial_velocity_m_s": [dx * ball_speed, dy * ball_speed, dz * ball_speed],
        "standoff_mm": standoff_mm,
        "initial_surface_gap_mm": initial_surface_gap_mm,
        "contact_radius_mm": contact_radius_mm,
        "search_radius_mm": search_radius_mm,
        "target_contact_activation_time_s": target_contact_activation_time_s,
        "predicted_search_entry_time_s": (
            (initial_surface_gap_mm - search_radius_mm) / normal_speed_mm_s
        ),
        "predicted_contact_activation_time_s": (
            (initial_surface_gap_mm - contact_radius_mm) / normal_speed_mm_s
        ),
        "predicted_geometric_impact_time_s": (
            initial_surface_gap_mm / normal_speed_mm_s
        ),
        # Six nominal elements across the ball diameter.
        "requested_mesh_size_mm": requested_mesh_size_mm,
    }


def _configure_gmsh(mesh_size_m: float, seed: int, verbose: bool) -> None:
    gmsh.option.setNumber("General.Terminal", 1 if verbose else 0)
    gmsh.option.setNumber("General.NumThreads", 1)
    gmsh.option.setNumber("Mesh.MaxNumThreads1D", 1)
    gmsh.option.setNumber("Mesh.MaxNumThreads2D", 1)
    gmsh.option.setNumber("Mesh.MaxNumThreads3D", 1)
    gmsh.option.setNumber("Mesh.ElementOrder", 1)
    gmsh.option.setNumber("Mesh.Algorithm3D", 1)
    gmsh.option.setNumber("Mesh.RandomSeed", int(seed) % 2147483647)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size_m)
    gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_m)


def _volume_element_count(volume_tags: tuple[int, int]) -> int:
    total = 0
    for volume_tag in volume_tags:
        _, element_tags, _ = gmsh.model.mesh.getElements(3, volume_tag)
        total += sum(len(tags) for tags in element_tags)
    return total


def _tetra_node_tags(volume_tag: int) -> np.ndarray:
    element_types, _, element_node_tags = gmsh.model.mesh.getElements(3, volume_tag)
    blocks = []

    for element_type, flat_node_tags in zip(element_types, element_node_tags):
        _, dimension, order, num_nodes, _, num_primary_nodes = (
            gmsh.model.mesh.getElementProperties(element_type)
        )
        if dimension != 3:
            continue
        if order != 1 or num_nodes != 4 or num_primary_nodes != 4:
            raise RuntimeError(
                f"Expected first-order tetrahedra, got Gmsh element type {element_type}"
            )
        blocks.append(np.asarray(flat_node_tags, dtype=np.int64).reshape(-1, 4))

    if not blocks:
        raise RuntimeError(f"No tetrahedra generated for volume {volume_tag}")
    return np.vstack(blocks)


def _extract_mesh(plate_volume: int, ball_volume: int) -> tuple:
    plate_tag_connectivity = _tetra_node_tags(plate_volume)
    ball_tag_connectivity = _tetra_node_tags(ball_volume)

    node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
    node_tags = np.asarray(node_tags, dtype=np.int64)
    coordinates = np.asarray(coordinates, dtype=np.float64).reshape(-1, 3)

    order = np.argsort(node_tags)
    sorted_node_tags = node_tags[order]
    sorted_coordinates = coordinates[order]
    used_node_tags = np.unique(
        np.concatenate((plate_tag_connectivity.ravel(), ball_tag_connectivity.ravel()))
    )

    positions = np.searchsorted(sorted_node_tags, used_node_tags)
    if (
        np.any(positions >= len(sorted_node_tags))
        or not np.array_equal(sorted_node_tags[positions], used_node_tags)
    ):
        raise RuntimeError("Gmsh connectivity references an unknown node tag")

    points = sorted_coordinates[positions]
    plate_connectivity = np.searchsorted(
        used_node_tags, plate_tag_connectivity
    ).astype(np.int32)
    ball_connectivity = np.searchsorted(
        used_node_tags, ball_tag_connectivity
    ).astype(np.int32)
    plate_nodes = np.unique(plate_connectivity).astype(np.int32)
    ball_nodes = np.unique(ball_connectivity).astype(np.int32)

    if np.intersect1d(plate_nodes, ball_nodes).size:
        raise RuntimeError("Plate and ball unexpectedly share mesh nodes")

    return points, plate_connectivity, ball_connectivity, plate_nodes, ball_nodes


def _write_char_names(variable, names: list[str]) -> None:
    variable.set_auto_mask(False)
    variable[:] = np.zeros(variable.shape, dtype="S1")
    for row, name in enumerate(names):
        encoded = name.encode("ascii")
        if len(encoded) >= variable.shape[1]:
            raise ValueError(f"Exodus name is too long: {name}")
        for column, value in enumerate(encoded):
            variable[row, column] = bytes([value])


def _patch_exodus_metadata(mesh_path: Path) -> None:
    """Add block names and standard 1-based IDs omitted by meshio 5.3.5."""
    with netCDF4.Dataset(mesh_path, "a") as dataset:
        num_blocks = len(dataset.dimensions["num_el_blk"])
        num_node_sets = len(dataset.dimensions["num_node_sets"])
        if num_blocks != 2 or num_node_sets != 2:
            raise RuntimeError(
                f"Expected 2 blocks and 2 node sets, got {num_blocks} and {num_node_sets}"
            )

        block_ids = dataset.variables["eb_prop1"]
        block_ids[:] = np.arange(1, num_blocks + 1, dtype=np.int32)
        block_ids.setncattr("name", "ID")

        if "eb_status" not in dataset.variables:
            block_status = dataset.createVariable("eb_status", "i4", ("num_el_blk",))
        else:
            block_status = dataset.variables["eb_status"]
        block_status[:] = 1

        if "eb_names" not in dataset.variables:
            block_names = dataset.createVariable(
                "eb_names", "S1", ("num_el_blk", "len_string")
            )
        else:
            block_names = dataset.variables["eb_names"]
        _write_char_names(block_names, [PLATE_BLOCK, BALL_BLOCK])

        node_set_ids = dataset.variables["ns_prop1"]
        node_set_ids[:] = np.arange(1, num_node_sets + 1, dtype=np.int32)
        node_set_ids.setncattr("name", "ID")

        if "ns_status" not in dataset.variables:
            node_set_status = dataset.createVariable(
                "ns_status", "i4", ("num_node_sets",)
            )
        else:
            node_set_status = dataset.variables["ns_status"]
        node_set_status[:] = 1

        _write_char_names(
            dataset.variables["ns_names"], [PLATE_NODESET, BALL_NODESET]
        )
        dataset.title = "Gmsh/meshio ball-plate impact mesh"


def _decode_names(variable) -> list[str]:
    variable.set_auto_mask(False)
    return [
        b"".join(row).split(b"\x00", 1)[0].decode("ascii").rstrip()
        for row in variable[:]
    ]


def validate_exodus(mesh_path: str | Path) -> dict:
    """Validate the Exodus structure required by the Peridigm XML."""
    mesh_path = Path(mesh_path)
    with netCDF4.Dataset(mesh_path, "r") as dataset:
        block_names = _decode_names(dataset.variables["eb_names"])
        node_set_names = _decode_names(dataset.variables["ns_names"])
        block_ids = dataset.variables["eb_prop1"][:].astype(int).tolist()
        node_set_ids = dataset.variables["ns_prop1"][:].astype(int).tolist()
        node_count = len(dataset.dimensions["num_nodes"])
        element_count = len(dataset.dimensions["num_elem"])

        if block_names != [PLATE_BLOCK, BALL_BLOCK]:
            raise RuntimeError(f"Unexpected Exodus block names: {block_names}")
        if node_set_names != [PLATE_NODESET, BALL_NODESET]:
            raise RuntimeError(f"Unexpected Exodus node-set names: {node_set_names}")
        if block_ids != [1, 2] or node_set_ids != [1, 2]:
            raise RuntimeError(
                f"Unexpected Exodus IDs: blocks={block_ids}, node_sets={node_set_ids}"
            )

        for index in (1, 2):
            connectivity = dataset.variables[f"connect{index}"]
            if str(connectivity.elem_type).upper() not in {"TETRA", "TETRA4"}:
                raise RuntimeError(
                    f"Block {index} is not tetrahedral: {connectivity.elem_type}"
                )
            if np.any(connectivity[:] < 1) or np.any(connectivity[:] > node_count):
                raise RuntimeError(f"Block {index} has invalid node connectivity")

    # A read-back catches writer-level incompatibilities independently of netCDF.
    mesh = meshio.read(mesh_path, file_format="exodus")
    if len(mesh.cells) != 2 or any(cell.type != "tetra" for cell in mesh.cells):
        raise RuntimeError("meshio read-back did not preserve two tetrahedral blocks")

    return {
        "node_count": node_count,
        "element_count": element_count,
        "block_names": block_names,
        "node_set_names": node_set_names,
    }


def _build_gmsh_mesh(
    parameters: dict,
    max_elements: int,
    verbose: bool,
) -> tuple:
    gmsh.clear()
    gmsh.model.add(f"ball_plate_nf_{parameters['seed']:04d}")

    plate_radius_m = parameters["plate_radius_mm"] * 0.001
    plate_thickness_m = parameters["plate_thickness_mm"] * 0.001
    ball_radius_m = parameters["ball_radius_mm"] * 0.001
    initial_x_m, initial_y_m, initial_z_m = [
        value * 0.001 for value in parameters["initial_position_mm"]
    ]

    plate_volume = gmsh.model.occ.addCylinder(
        0.0,
        0.0,
        -plate_thickness_m,
        0.0,
        0.0,
        plate_thickness_m,
        plate_radius_m,
    )
    ball_volume = gmsh.model.occ.addSphere(
        initial_x_m,
        initial_y_m,
        initial_z_m,
        ball_radius_m,
    )
    gmsh.model.occ.synchronize()

    gmsh.model.addPhysicalGroup(3, [plate_volume], 1, PLATE_BLOCK)
    gmsh.model.addPhysicalGroup(3, [ball_volume], 2, BALL_BLOCK)

    mesh_size_mm = parameters["requested_mesh_size_mm"]
    element_count = 0
    for attempt in range(20):
        _configure_gmsh(mesh_size_mm * 0.001, parameters["seed"], verbose)
        gmsh.model.mesh.generate(3)
        element_count = _volume_element_count((plate_volume, ball_volume))
        if element_count <= max_elements:
            break
        if attempt == 19:
            raise RuntimeError(
                f"Could not satisfy max_elements={max_elements}; got {element_count}"
            )

        # Tetrahedron count scales approximately with h^-3.  The extra margin
        # avoids landing just above the hard cap because of topology changes.
        scale = max(1.12, (element_count / max_elements) ** (1.0 / 3.0) * 1.06)
        mesh_size_mm *= scale
        gmsh.model.mesh.clear()

    extracted = _extract_mesh(plate_volume, ball_volume)
    return (*extracted, mesh_size_mm, element_count)


def build_scene(
    out_dir: str | Path = ".",
    tag: str | None = None,
    seed: int = 0,
    max_elements: int = DEFAULT_MAX_ELEMENTS,
    verbose_gmsh: bool = False,
) -> dict:
    """Build, export, and validate one reproducible no-floor scene."""
    if max_elements <= 0:
        raise ValueError("max_elements must be positive")

    owns_gmsh = not bool(gmsh.isInitialized())
    if owns_gmsh:
        gmsh.initialize(["gmsh", "-nopopup"])

    try:
        parameters = sample_parameters(seed)
        (
            points,
            plate_connectivity,
            ball_connectivity,
            plate_nodes,
            ball_nodes,
            mesh_size_mm,
            element_count,
        ) = _build_gmsh_mesh(parameters, max_elements, verbose_gmsh)

        out_dir = Path(out_dir)
        tag = tag or f"ball_plate_nf_{seed:04d}"
        scene_id = f"{seed:04d}"
        mesh_path = out_dir / "geometry" / scene_id / f"{tag}.g"
        metadata_path = out_dir / f"{tag}.json"
        mesh_path.parent.mkdir(parents=True, exist_ok=True)
        xml_paths = {
            variant_name: out_dir / folder_name / scene_id / f"{tag}.xml"
            for variant_name, (folder_name, _) in XML_VARIANTS.items()
        }
        for xml_path in xml_paths.values():
            xml_path.parent.mkdir(parents=True, exist_ok=True)

        mesh = meshio.Mesh(
            points=points,
            cells=[
                ("tetra", plate_connectivity),
                ("tetra", ball_connectivity),
            ],
            point_sets={
                PLATE_NODESET: plate_nodes,
                BALL_NODESET: ball_nodes,
            },
        )
        meshio.write(mesh_path, mesh, file_format="exodus")
        _patch_exodus_metadata(mesh_path)
        validation = validate_exodus(mesh_path)

        info = {
            **parameters,
            "mesh_backend": "gmsh-meshio",
            "gmsh_version": gmsh.__version__,
            "meshio_version": meshio.__version__,
            "mesh_size": mesh_size_mm,
            "node_count": validation["node_count"],
            "element_count": validation["element_count"],
            "plate_element_count": int(len(plate_connectivity)),
            "ball_element_count": int(len(ball_connectivity)),
        }
        if info["element_count"] != element_count:
            raise RuntimeError("Gmsh and Exodus element counts do not agree")

        mesh_relative_to_xml = f"../../geometry/{scene_id}/{mesh_path.name}"
        for variant_name, (_, critical_stretch) in XML_VARIANTS.items():
            generate_ball_plate_peridigm_xml(
                mesh_relative_to_xml,
                info,
                str(xml_paths[variant_name]),
                critical_stretch=critical_stretch,
            )

        mesh_relative = mesh_path.relative_to(out_dir).as_posix()
        xml_relatives = {
            name: path.relative_to(out_dir).as_posix()
            for name, path in xml_paths.items()
        }
        default_xml_relative = xml_relatives["critical_stretch_0p0005"]

        metadata = {
            "scenario": "ball_plate_nf",
            "tag": tag,
            "mesh_file": mesh_relative,
            "xml_file": default_xml_relative,
            "xml_files": xml_relatives,
            "validation": validation,
            **info,
        }
        metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        echo(f"  mesh     -> {mesh_path}")
        for variant_name, xml_path in xml_paths.items():
            echo(f"  xml ({variant_name}) -> {xml_path}")
        echo(f"  metadata -> {metadata_path}")
        echo(
            f"  nodes={info['node_count']} elements={info['element_count']} "
            f"mesh_size={mesh_size_mm:.4f} mm"
        )
        return {
            "mesh_file": str(mesh_path),
            "xml_file": str(xml_paths["critical_stretch_0p0005"]),
            "xml_files": {name: str(path) for name, path in xml_paths.items()},
            "metadata_file": str(metadata_path),
            "parameters": metadata,
        }
    finally:
        if owns_gmsh:
            gmsh.finalize()


def build_scenes(
    n_scenes: int,
    out_dir: str | Path,
    base_seed: int = 0,
    max_elements: int = DEFAULT_MAX_ELEMENTS,
    tag: str | None = None,
    resume: bool = False,
    verbose_gmsh: bool = False,
) -> dict:
    """Generate a batch and persist progress after every scene."""
    if n_scenes <= 0:
        raise ValueError("n_scenes must be positive")
    if max_elements <= 0:
        raise ValueError("max_elements must be positive")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest = []

    gmsh.initialize(["gmsh", "-nopopup"])
    try:
        for index in range(n_scenes):
            seed = base_seed + index
            scene_tag = (
                tag if n_scenes == 1 and tag else f"{tag or 'ball_plate_nf'}_{seed:04d}"
            )
            echo(f"\n=== [{index + 1}/{n_scenes}] {scene_tag} ===")

            scene_id = f"{seed:04d}"
            mesh_path = out_dir / "geometry" / scene_id / f"{scene_tag}.g"
            xml_paths = {
                variant_name: out_dir / folder_name / scene_id / f"{scene_tag}.xml"
                for variant_name, (folder_name, _) in XML_VARIANTS.items()
            }
            metadata_path = out_dir / f"{scene_tag}.json"
            existing_complete = (
                mesh_path.exists()
                and all(path.exists() for path in xml_paths.values())
                and metadata_path.exists()
            )
            metadata = None
            if resume and existing_complete:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

            existing_within_limit = (
                metadata is not None
                and metadata.get("mesh_backend") == "gmsh-meshio"
                and 0 < metadata.get("element_count", 0) <= max_elements
                and MIN_IMPACT_TIME_S
                <= metadata.get("predicted_contact_activation_time_s", 0.0)
                <= MAX_IMPACT_TIME_S
                and metadata.get("initial_surface_gap_mm", 0.0)
                > metadata.get("search_radius_mm", float("inf"))
            )
            if resume and existing_within_limit:
                manifest.append({
                    "index": index,
                    "tag": scene_tag,
                    "seed": seed,
                    "status": "success",
                    "resumed": True,
                    "mesh_file": str(mesh_path),
                    "xml_file": str(xml_paths["critical_stretch_0p0005"]),
                    "xml_files": {
                        name: str(path) for name, path in xml_paths.items()
                    },
                    "metadata_file": str(metadata_path),
                    "parameters": metadata,
                })
                echo("  existing scene retained (--resume; limits verified)")
            else:
                if resume and existing_complete:
                    echo("  existing scene does not satisfy current limits; regenerating")
                try:
                    result = build_scene(
                        out_dir=out_dir,
                        tag=scene_tag,
                        seed=seed,
                        max_elements=max_elements,
                        verbose_gmsh=verbose_gmsh,
                    )
                    manifest.append({
                        "index": index,
                        "tag": scene_tag,
                        "seed": seed,
                        "status": "success",
                        "mesh_file": result["mesh_file"],
                        "xml_file": result["xml_file"],
                        "xml_files": result["xml_files"],
                        "metadata_file": result["metadata_file"],
                        "parameters": result["parameters"],
                    })
                except Exception as exc:
                    echo(f"  ERROR: {exc}")
                    manifest.append({
                        "index": index,
                        "tag": scene_tag,
                        "seed": seed,
                        "status": "error",
                        "error": str(exc),
                    })

            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    finally:
        gmsh.finalize()

    succeeded = sum(entry["status"] == "success" for entry in manifest)
    echo(f"\nDone. {succeeded}/{n_scenes} succeeded. Manifest: {manifest_path}")
    return {
        "manifest_file": str(manifest_path),
        "succeeded": succeeded,
        "attempted": n_scenes,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate randomized no-floor ball/plate scenes with Gmsh/meshio.",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="One-scene tag or batch filename prefix (default: ball_plate_nf)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Base random seed; scene i uses seed + i (default: 0)",
    )
    parser.add_argument(
        "--n-scenes",
        type=int,
        default=1,
        help="Number of scenes to generate (default: 1)",
    )
    parser.add_argument(
        "--max-elements",
        "--max-nodes",
        dest="max_elements",
        type=int,
        default=DEFAULT_MAX_ELEMENTS,
        help=f"Maximum total tetrahedra per scene (default: {DEFAULT_MAX_ELEMENTS})",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Retain complete existing scenes and continue the batch",
    )
    parser.add_argument(
        "--verbose-gmsh",
        action="store_true",
        help="Show Gmsh meshing output",
    )
    args = parser.parse_args()

    if args.n_scenes <= 0:
        parser.error("--n-scenes must be positive")
    if args.max_elements <= 0:
        parser.error("--max-elements must be positive")

    build_scenes(
        n_scenes=args.n_scenes,
        out_dir=args.output_dir,
        base_seed=args.seed,
        max_elements=args.max_elements,
        tag=args.tag,
        resume=args.resume,
        verbose_gmsh=args.verbose_gmsh,
    )
