"""Small Exodus/Gmsh helpers shared by the open-source scene backends."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


def require_mesh_dependencies():
    """Import optional meshing dependencies only when geometry is requested."""
    try:
        import gmsh
        import meshio
        import netCDF4
        import numpy as np
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "Missing Gmsh/Exodus dependencies. Create the environment with "
            "`conda env create -f environment.yml` and activate it."
        ) from exc
    return gmsh, meshio, netCDF4, np


def configure_gmsh(mesh_size_m: float, seed: int, verbose: bool = False) -> None:
    gmsh, _, _, _ = require_mesh_dependencies()
    gmsh.option.setNumber("General.Terminal", 1 if verbose else 0)
    gmsh.option.setNumber("General.NumThreads", 1)
    gmsh.option.setNumber("Mesh.MaxNumThreads1D", 1)
    gmsh.option.setNumber("Mesh.MaxNumThreads2D", 1)
    gmsh.option.setNumber("Mesh.MaxNumThreads3D", 1)
    gmsh.option.setNumber("Mesh.ElementOrder", 1)
    gmsh.option.setNumber("Mesh.Algorithm3D", 1)
    gmsh.option.setNumber("Mesh.RandomSeed", int(seed))
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeMin", float(mesh_size_m))
    gmsh.option.setNumber("Mesh.MeshSizeMax", float(mesh_size_m))


def _tetra_connectivity_for_entities(
    entity_tags: Iterable[int],
) -> tuple[Any, Any]:
    gmsh, _, _, np = require_mesh_dependencies()
    connectivity = []
    element_tags = []
    for entity_tag in entity_tags:
        types, tags_by_type, nodes_by_type = gmsh.model.mesh.getElements(3, entity_tag)
        for element_type, tags, flat_nodes in zip(
            types, tags_by_type, nodes_by_type
        ):
            _, dimension, order, node_count, _, primary_count = (
                gmsh.model.mesh.getElementProperties(element_type)
            )
            if dimension != 3:
                continue
            if order != 1 or node_count != 4 or primary_count != 4:
                raise RuntimeError(
                    f"Expected first-order TET4, got Gmsh element type {element_type}"
                )
            connectivity.append(
                np.asarray(flat_nodes, dtype=np.int64).reshape(-1, 4)
            )
            element_tags.append(np.asarray(tags, dtype=np.int64))
    if not connectivity:
        raise RuntimeError("Gmsh generated no tetrahedra for an Exodus block")
    return np.vstack(connectivity), np.concatenate(element_tags)


def extract_gmsh_tetra_blocks(
    block_entities: Sequence[Mapping[str, Any]],
) -> tuple[Any, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Extract TET4 blocks, compact node tags, node sets, and quality statistics."""
    gmsh, _, _, np = require_mesh_dependencies()
    raw_blocks: list[tuple[Mapping[str, Any], Any, Any]] = []
    all_used = []
    all_element_tags = []
    for block in block_entities:
        connectivity, element_tags = _tetra_connectivity_for_entities(
            block["entities"]
        )
        raw_blocks.append((block, connectivity, element_tags))
        all_used.append(connectivity.reshape(-1))
        all_element_tags.append(element_tags)

    node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
    node_tags = np.asarray(node_tags, dtype=np.int64)
    coordinates = np.asarray(coordinates, dtype=np.float64).reshape(-1, 3)
    order = np.argsort(node_tags)
    sorted_tags = node_tags[order]
    sorted_coordinates = coordinates[order]
    used_tags = np.unique(np.concatenate(all_used))
    positions = np.searchsorted(sorted_tags, used_tags)
    if (
        np.any(positions >= len(sorted_tags))
        or not np.array_equal(sorted_tags[positions], used_tags)
    ):
        raise RuntimeError("Gmsh connectivity references unknown node tags")
    points = sorted_coordinates[positions]

    cell_blocks: list[dict[str, Any]] = []
    node_sets: dict[str, Any] = {}
    for block, tag_connectivity, _ in raw_blocks:
        connectivity = np.searchsorted(used_tags, tag_connectivity).astype(np.int32)
        name = str(block["name"])
        nodeset = str(block.get("nodeset", name.replace("block_", "nodelist_")))
        cell_blocks.append(
            {"name": name, "cell_type": "tetra", "connectivity": connectivity}
        )
        node_sets[nodeset] = np.unique(connectivity.reshape(-1)).astype(np.int32)

    quality_values = np.asarray(
        gmsh.model.mesh.getElementQualities(
            np.concatenate(all_element_tags), "minSICN"
        ),
        dtype=np.float64,
    )
    quality = {
        "metric": "minSICN",
        "minimum": float(np.min(quality_values)),
        "p01": float(np.quantile(quality_values, 0.01)),
        "median": float(np.median(quality_values)),
        "p99": float(np.quantile(quality_values, 0.99)),
    }
    return points, cell_blocks, node_sets, quality


def characteristic_cell_size(
    points: Any,
    connectivity: Any,
    cell_type: str,
) -> float:
    """Return the median physical edge length of a cell block."""
    _, _, _, np = require_mesh_dependencies()
    cells = np.asarray(connectivity, dtype=np.int64)
    if cell_type == "tetra":
        edge_pairs = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
    elif cell_type in {"hexahedron", "hex8"}:
        edge_pairs = (
            (0, 1), (1, 2), (2, 3), (3, 0),
            (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7),
        )
    else:
        raise ValueError(f"Unsupported cell type for characteristic size: {cell_type}")
    lengths = [
        np.linalg.norm(points[cells[:, first]] - points[cells[:, second]], axis=1)
        for first, second in edge_pairs
    ]
    value = float(np.median(np.concatenate(lengths)))
    if not np.isfinite(value) or value <= 0.0:
        raise RuntimeError(f"Invalid characteristic mesh size: {value}")
    return value


def _write_char_names(variable: Any, names: Sequence[str], np: Any) -> None:
    variable.set_auto_mask(False)
    variable[:] = np.zeros(variable.shape, dtype="S1")
    for row, name in enumerate(names):
        encoded = name.encode("ascii")
        if len(encoded) >= variable.shape[1]:
            raise ValueError(f"Exodus name is too long: {name}")
        for column, value in enumerate(encoded):
            variable[row, column] = bytes([value])


def patch_exodus_metadata(
    mesh_path: str | Path,
    block_names: Sequence[str],
    nodeset_names: Sequence[str],
    title: str,
) -> None:
    """Set stable Peridigm block/node-set IDs and names omitted by meshio."""
    _, _, netCDF4, np = require_mesh_dependencies()
    with netCDF4.Dataset(mesh_path, "a") as dataset:
        if len(dataset.dimensions["num_el_blk"]) != len(block_names):
            raise RuntimeError("Exodus element-block count differs from requested blocks")
        if len(dataset.dimensions["num_node_sets"]) != len(nodeset_names):
            raise RuntimeError("Exodus node-set count differs from requested node sets")

        dataset.variables["eb_prop1"][:] = np.arange(
            1, len(block_names) + 1, dtype=np.int32
        )
        dataset.variables["eb_prop1"].setncattr("name", "ID")
        if "eb_status" not in dataset.variables:
            dataset.createVariable("eb_status", "i4", ("num_el_blk",))
        dataset.variables["eb_status"][:] = 1
        if "eb_names" not in dataset.variables:
            dataset.createVariable("eb_names", "S1", ("num_el_blk", "len_string"))
        _write_char_names(dataset.variables["eb_names"], block_names, np)

        dataset.variables["ns_prop1"][:] = np.arange(
            1, len(nodeset_names) + 1, dtype=np.int32
        )
        dataset.variables["ns_prop1"].setncattr("name", "ID")
        if "ns_status" not in dataset.variables:
            dataset.createVariable("ns_status", "i4", ("num_node_sets",))
        dataset.variables["ns_status"][:] = 1
        if "ns_names" not in dataset.variables:
            dataset.createVariable("ns_names", "S1", ("num_node_sets", "len_string"))
        _write_char_names(dataset.variables["ns_names"], nodeset_names, np)
        dataset.title = title


def _decode_names(variable: Any) -> list[str]:
    variable.set_auto_mask(False)
    return [
        b"".join(row).split(b"\x00", 1)[0].decode("ascii").rstrip()
        for row in variable[:]
    ]


def inspect_exodus(mesh_path: str | Path) -> dict[str, Any]:
    """Read topology/count metadata without loading the whole mesh."""
    _, _, netCDF4, _ = require_mesh_dependencies()
    with netCDF4.Dataset(mesh_path, "r") as dataset:
        block_names = _decode_names(dataset.variables["eb_names"])
        nodeset_names = _decode_names(dataset.variables["ns_names"])
        result = {
            "node_count": len(dataset.dimensions["num_nodes"]),
            "element_count": len(dataset.dimensions["num_elem"]),
            "block_names": block_names,
            "nodeset_names": nodeset_names,
            "block_ids": dataset.variables["eb_prop1"][:].astype(int).tolist(),
            "nodeset_ids": dataset.variables["ns_prop1"][:].astype(int).tolist(),
            "block_element_counts": [
                len(dataset.dimensions[f"num_el_in_blk{index}"])
                for index in range(1, len(block_names) + 1)
            ],
            "element_types": [
                str(dataset.variables[f"connect{index}"].elem_type).upper()
                for index in range(1, len(block_names) + 1)
            ],
        }
    return result


def write_exodus(
    mesh_path: str | Path,
    points: Any,
    cell_blocks: Sequence[Mapping[str, Any]],
    node_sets: Mapping[str, Any],
    *,
    title: str,
) -> dict[str, Any]:
    """Atomically write a multi-block meshio Exodus file and validate metadata."""
    _, meshio, _, _ = require_mesh_dependencies()
    destination = Path(mesh_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=destination.suffix, dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    block_names = [str(block["name"]) for block in cell_blocks]
    nodeset_names = list(node_sets)
    try:
        mesh = meshio.Mesh(
            points=points,
            cells=[
                (str(block["cell_type"]), block["connectivity"])
                for block in cell_blocks
            ],
            point_sets=dict(node_sets),
        )
        meshio.write(temporary, mesh, file_format="exodus")
        patch_exodus_metadata(temporary, block_names, nodeset_names, title)
        validation = inspect_exodus(temporary)
        if validation["block_names"] != block_names:
            raise RuntimeError("Written Exodus block names failed validation")
        if validation["nodeset_names"] != nodeset_names:
            raise RuntimeError("Written Exodus node-set names failed validation")
        os.replace(temporary, destination)
        return validation
    finally:
        temporary.unlink(missing_ok=True)


def block_mesh_sizes(points: Any, cell_blocks: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Measure a characteristic length separately for every Exodus block."""
    return {
        str(block["name"]): characteristic_cell_size(
            points, block["connectivity"], str(block["cell_type"])
        )
        for block in cell_blocks
    }
