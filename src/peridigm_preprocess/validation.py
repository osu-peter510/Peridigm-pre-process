"""Static validation for generated mesh/XML/metadata case bundles."""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from peridigm_preprocess.exodus import inspect_exodus


class ValidationError(RuntimeError):
    """Raised when a generated case is incomplete or internally inconsistent."""


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parameter_values(root: ET.Element, name: str) -> list[str]:
    return [
        str(element.attrib["value"])
        for element in root.iter("Parameter")
        if element.attrib.get("name") == name and "value" in element.attrib
    ]


def _named_list(root: ET.Element, name: str) -> ET.Element:
    matches = [
        element
        for element in root.iter("ParameterList")
        if element.attrib.get("name") == name
    ]
    if len(matches) != 1:
        raise ValidationError(
            f"Expected exactly one ParameterList {name!r}; found {len(matches)}"
        )
    return matches[0]


def _direct_parameter_element(element: ET.Element, name: str) -> ET.Element:
    matches = [
        child
        for child in element.findall("Parameter")
        if child.attrib.get("name") == name
    ]
    if len(matches) != 1 or "value" not in matches[0].attrib:
        raise ValidationError(
            f"Expected one direct Parameter {name!r} in "
            f"{element.attrib.get('name')!r}; got {matches}"
        )
    return matches[0]


def _direct_parameter(element: ET.Element, name: str) -> str:
    return str(_direct_parameter_element(element, name).attrib["value"])


def _require_direct_type(element: ET.Element, name: str, expected: str) -> None:
    parameter = _direct_parameter_element(element, name)
    actual = parameter.attrib.get("type")
    if actual != expected:
        raise ValidationError(
            f"Parameter {element.attrib.get('name')!r}/{name!r} has type "
            f"{actual!r}; expected {expected!r}"
        )


def validate_generated_case(case_dir: str | Path) -> dict[str, Any]:
    """Validate one standard case directory and return a concise report."""
    directory = Path(case_dir).resolve()
    metadata_path = directory / "metadata.json"
    if not metadata_path.is_file():
        raise ValidationError(f"Missing case metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    files = metadata.get("files", {})
    resolved_files: dict[str, Path] = {}
    for key in ("mesh", "xml", "results"):
        value = files.get(key) if isinstance(files, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"metadata files.{key} must be a non-empty path")
        candidate = (directory / value).resolve()
        try:
            candidate.relative_to(directory)
        except ValueError as exc:
            raise ValidationError(
                f"metadata files.{key} escapes the case directory: {value}"
            ) from exc
        resolved_files[key] = candidate
    if resolved_files["results"] == directory:
        raise ValidationError("metadata files.results cannot be the case directory")
    mesh_path = resolved_files["mesh"]
    xml_path = resolved_files["xml"]
    for path in (mesh_path, xml_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValidationError(f"Missing or empty generated file: {path}")

    mesh_digest = sha256(mesh_path)
    xml_digest = sha256(xml_path)
    recorded = metadata.get("sha256", {})
    if recorded.get("mesh") != mesh_digest or recorded.get("xml") != xml_digest:
        raise ValidationError("Generated artifact SHA256 differs from metadata")

    exodus = inspect_exodus(mesh_path)
    geometry = metadata["geometry"]
    if exodus["node_count"] != int(geometry["node_count"]):
        raise ValidationError("Exodus node count differs from geometry metadata")
    if exodus["element_count"] != int(geometry["element_count"]):
        raise ValidationError("Exodus element count differs from geometry metadata")
    if exodus["element_count"] > int(metadata["config"]["mesh"]["max_elements"]):
        raise ValidationError("Exodus element count exceeds configured hard cap")

    root = ET.parse(xml_path).getroot()
    input_mesh_values = _parameter_values(root, "Input Mesh File")
    if len(input_mesh_values) != 1:
        raise ValidationError("XML must contain exactly one Input Mesh File")
    referenced_mesh = (xml_path.parent / input_mesh_values[0]).resolve()
    if referenced_mesh != mesh_path.resolve():
        raise ValidationError(
            f"XML mesh reference resolves to {referenced_mesh}, expected {mesh_path}"
        )

    resolved = metadata["xml_resolved"]
    for material in metadata["config"]["materials"].values():
        material_list = _named_list(root, material["name"])
        _require_direct_type(material_list, "Material Model", "string")
        for parameter_name in ("Density", "Bulk Modulus", "Shear Modulus"):
            _require_direct_type(material_list, parameter_name, "double")
        if material["model"] == "Viscoelastic":
            _require_direct_type(material_list, "lambda_i", "double")
            _require_direct_type(material_list, "tau b", "double")

    config_blocks = metadata["config"]["blocks"]
    for block_id, expected_horizon in resolved["block_horizons_m"].items():
        block_list = _named_list(root, config_blocks[block_id]["name"])
        _require_direct_type(block_list, "Horizon", "double")
        actual_horizon = float(_direct_parameter(block_list, "Horizon"))
        if not math.isclose(
            actual_horizon,
            float(expected_horizon),
            rel_tol=2.0e-11,
            abs_tol=1.0e-15,
        ):
            raise ValidationError(
                f"Block {block_id} horizon {actual_horizon} != {expected_horizon}"
            )

    solver_list = _named_list(root, "Solver")
    _require_direct_type(solver_list, "Initial Time", "double")
    _require_direct_type(solver_list, "Final Time", "double")
    final_time = float(_direct_parameter(solver_list, "Final Time"))
    if not math.isclose(final_time, float(metadata["config"]["solver"]["final_time"])):
        raise ValidationError("XML Final Time differs from resolved configuration")
    if resolved["solver"].get("fixed_dt") is not None:
        method_list = _named_list(
            solver_list, metadata["config"]["solver"].get("method", "Verlet")
        )
        _require_direct_type(method_list, "Fixed dt", "double")
        fixed_dt = float(_direct_parameter(method_list, "Fixed dt"))
        if not math.isclose(fixed_dt, float(resolved["solver"]["fixed_dt"])):
            raise ValidationError("XML Fixed dt differs from resolved configuration")

    damage_values = resolved.get("damage_parameters", {})
    for damage_id, parameters in damage_values.items():
        damage_name = metadata["config"]["damage_models"][damage_id]["name"]
        damage_list = _named_list(root, damage_name)
        for parameter_name, expected in parameters.items():
            expected_value = expected.get("value") if isinstance(expected, dict) else expected
            if isinstance(expected_value, (int, float)) and not isinstance(
                expected_value, bool
            ):
                _require_direct_type(damage_list, parameter_name, "double")
            actual = _direct_parameter(damage_list, parameter_name)
            if isinstance(expected_value, (int, float)):
                if not math.isclose(float(actual), float(expected_value), rel_tol=2.0e-11):
                    raise ValidationError(
                        f"Damage parameter {damage_name}/{parameter_name} differs"
                    )
            elif actual != str(expected_value):
                raise ValidationError(
                    f"Damage parameter {damage_name}/{parameter_name} differs"
                )

    return {
        "status": "pass",
        "case": metadata["tag"],
        "scenario": metadata["scenario"],
        "node_count": exodus["node_count"],
        "element_count": exodus["element_count"],
        "block_names": exodus["block_names"],
        "horizons_m": resolved["block_horizons_m"],
        "mesh_sha256": mesh_digest,
        "xml_sha256": xml_digest,
    }
