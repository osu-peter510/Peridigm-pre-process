"""Render a complete Peridigm XML file from resolved scene configuration."""

from __future__ import annotations

import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping


class XmlConfigError(ValueError):
    """Raised when an XML setting cannot be resolved safely."""


def _plist(parent: ET.Element, name: str) -> ET.Element:
    return ET.SubElement(parent, "ParameterList", {"name": str(name)})


def _format_value(value: Any, parameter_type: str) -> str:
    if parameter_type == "bool":
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower()
        raise XmlConfigError(
            f"Boolean XML parameters require true/false, got {value!r}"
        )
    if parameter_type == "double":
        try:
            converted = float(value)
        except (TypeError, ValueError) as exc:
            raise XmlConfigError(f"Invalid double XML value: {value!r}") from exc
        if not math.isfinite(converted):
            raise XmlConfigError(f"Double XML values must be finite: {value!r}")
        return f"{converted:.12g}"
    if parameter_type == "int":
        if isinstance(value, bool):
            raise XmlConfigError(f"Invalid integer XML value: {value!r}")
        try:
            converted = int(value)
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise XmlConfigError(f"Invalid integer XML value: {value!r}") from exc
        if not math.isfinite(numeric) or numeric != converted:
            raise XmlConfigError(f"Integer XML values must be exact: {value!r}")
        return str(converted)
    if parameter_type == "string":
        return str(value)
    raise XmlConfigError(
        f"Unsupported XML parameter type {parameter_type!r}; expected bool, double, int, or string"
    )


def _typed_value(raw: Any) -> tuple[str, Any]:
    if isinstance(raw, Mapping) and "value" in raw:
        value = raw["value"]
        parameter_type = str(raw.get("type") or _infer_type(value))
        return parameter_type, value
    return _infer_type(raw), raw


def _numeric_scalar(raw: Any, name: str) -> float:
    """Resolve a plain or explicitly typed parameter for derived arithmetic."""
    _, value = _typed_value(raw)
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise XmlConfigError(f"{name} must be numeric") from exc
    if not math.isfinite(converted):
        raise XmlConfigError(f"{name} must be finite")
    return converted


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "double"
    if isinstance(value, str):
        # PyYAML 1.1 treats values such as ``1.490e10`` (without an explicit
        # exponent sign) as strings. Peridigm still needs these physical
        # constants emitted as numeric parameters. An explicit
        # ``{type: string, value: ...}`` remains available for numeric-looking
        # values that are intentionally strings.
        if re.fullmatch(r"[+-]?\d+", value.strip()):
            return "int"
        if re.fullmatch(
            r"[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)(?:[eE][+-]?\d+)?",
            value.strip(),
        ):
            return "double"
    return "string"


def _parameter(parent: ET.Element, name: str, raw: Any) -> ET.Element:
    parameter_type, value = _typed_value(raw)
    return ET.SubElement(
        parent,
        "Parameter",
        {
            "name": str(name),
            "type": parameter_type,
            "value": _format_value(value, parameter_type),
        },
    )


def _nice_dt(raw: float) -> float:
    if raw <= 0.0:
        raise XmlConfigError("Computed time step must be positive")
    exponent = math.floor(math.log10(raw))
    base = 10.0**exponent
    return math.floor(raw / base) * base


def _metadata_value(metadata: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = metadata
    for part in dotted_path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                raise XmlConfigError(
                    f"Metadata value {dotted_path!r} does not exist (missing {part!r})"
                )
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise XmlConfigError(
                    f"Invalid list index in metadata path {dotted_path!r}"
                ) from exc
        else:
            raise XmlConfigError(f"Cannot descend into metadata path {dotted_path!r}")
    return current


def _block_mesh_sizes(metadata: Mapping[str, Any]) -> dict[str, float]:
    raw = metadata.get("block_mesh_sizes_m")
    if not isinstance(raw, Mapping) or not raw:
        raise XmlConfigError(
            "Geometry metadata must define measured block_mesh_sizes_m"
        )
    result = {str(name): float(value) for name, value in raw.items()}
    if any(not math.isfinite(value) or value <= 0.0 for value in result.values()):
        raise XmlConfigError("Every measured block mesh size must be finite and > 0")
    return result


def _select_mesh_size(
    selector: str | None,
    sizes: Mapping[str, float],
) -> float:
    selector = str(selector or "max")
    if selector == "max":
        return max(sizes.values())
    if selector == "min":
        return min(sizes.values())
    if selector not in sizes:
        raise XmlConfigError(
            f"Unknown mesh length selector {selector!r}; expected max, min, or a block name"
        )
    return sizes[selector]


def _material_wave_speed(material: Mapping[str, Any]) -> float:
    parameters = material.get("parameters", {})
    density = float(parameters["Density"])
    bulk = float(parameters["Bulk Modulus"])
    shear = float(parameters["Shear Modulus"])
    value = math.sqrt((bulk + 4.0 * shear / 3.0) / density)
    if not math.isfinite(value) or value <= 0.0:
        raise XmlConfigError(f"Invalid material P-wave speed for {material.get('name')}")
    return value


def _resolve_solver(
    config: Mapping[str, Any],
    sizes: Mapping[str, float],
) -> dict[str, Any]:
    solver = config["solver"]
    time_step = solver["time_step"]
    mode = time_step["mode"]
    resolved: dict[str, Any] = {
        "method": str(solver.get("method", "Verlet")),
        "initial_time": float(solver.get("initial_time", 0.0)),
        "final_time": float(solver["final_time"]),
        "time_step_mode": mode,
    }
    if mode == "fixed":
        resolved["fixed_dt"] = float(time_step["value"])
    elif mode == "auto":
        selector = time_step.get("mesh_scale", "min")
        mesh_size = _select_mesh_size(selector, sizes)
        wave_speed = max(
            _material_wave_speed(material)
            for material in config["materials"].values()
        )
        safety_factor = float(time_step["safety_factor"])
        dt = mesh_size / wave_speed * safety_factor
        if time_step.get("round_down_significant", True):
            dt = _nice_dt(dt)
        resolved.update(
            {
                "fixed_dt": dt,
                "stability_mesh_size_m": mesh_size,
                "maximum_p_wave_speed_m_s": wave_speed,
                "safety_factor": safety_factor,
            }
        )
    elif mode == "safety_factor":
        resolved["safety_factor"] = float(time_step["safety_factor"])
    else:  # guarded by config validation; defensive for direct API callers
        raise XmlConfigError(f"Unsupported time-step mode: {mode}")
    return resolved


def _damage_horizons(
    config: Mapping[str, Any],
    block_horizons: Mapping[str, float],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for block_id, block in config["blocks"].items():
        damage_id = block.get("damage_model")
        if damage_id is None:
            continue
        horizon = block_horizons[block_id]
        previous = result.get(damage_id)
        if previous is not None and not math.isclose(previous, horizon, rel_tol=1e-12):
            raise XmlConfigError(
                f"Damage model {damage_id!r} is shared by blocks with different horizons; "
                "define separate damage model entries so Characteristic Length is unambiguous"
            )
        result[damage_id] = horizon
    return result


def _resolve_damage_parameters(
    damage_id: str,
    damage: Mapping[str, Any],
    horizon: float | None,
) -> dict[str, Any]:
    raw_parameters = damage.get("parameters", {})
    resolved: dict[str, Any] = {}

    # Resolve direct values and dynamic characteristic length first so derived
    # fracture energy can reference the final, mesh-dependent horizon.
    for name, raw in raw_parameters.items():
        if isinstance(raw, Mapping) and raw.get("source") == "horizon":
            if horizon is None:
                raise XmlConfigError(
                    f"Damage model {damage_id!r} requests horizon but no block uses it"
                )
            resolved[name] = float(horizon)
        elif not (isinstance(raw, Mapping) and "derive" in raw):
            resolved[name] = raw

    for name, raw in raw_parameters.items():
        if not isinstance(raw, Mapping) or "derive" not in raw:
            continue
        if raw["derive"] != "progressive_fracture_energy":
            raise XmlConfigError(
                f"Unsupported derived damage parameter: {raw['derive']!r}"
            )
        initiation_name = str(
            raw.get("initiation_parameter", "Damage Initiation Stretch")
        )
        if initiation_name not in resolved:
            raise XmlConfigError(
                f"Derived Fracture Energy requires {initiation_name!r}"
            )
        if horizon is None:
            raise XmlConfigError("Derived Fracture Energy requires a block horizon")
        initiation = _numeric_scalar(resolved[initiation_name], initiation_name)
        ratio = _numeric_scalar(
            raw["failure_stretch_ratio"], "failure_stretch_ratio"
        )
        tensile_parameter = str(
            raw.get("tensile_modulus_parameter", "Tensile Modulus")
        )
        if tensile_parameter in resolved:
            tensile_modulus = _numeric_scalar(
                resolved[tensile_parameter], tensile_parameter
            )
        elif "tensile_modulus" in raw:
            # Backward-compatible spelling from the research scripts.  The
            # Progressive Bond Energy model also consumes this parameter, so
            # make it explicit in the emitted XML rather than using it only in
            # the derived-energy calculation.
            tensile_modulus = _numeric_scalar(
                raw["tensile_modulus"], "tensile_modulus"
            )
            resolved[tensile_parameter] = tensile_modulus
        else:
            raise XmlConfigError(
                f"Derived Fracture Energy requires {tensile_parameter!r}"
            )
        if initiation <= 0.0 or ratio <= 1.0 or tensile_modulus <= 0.0:
            raise XmlConfigError(
                "Progressive fracture energy requires positive initiation/tensile "
                "modulus and failure_stretch_ratio > 1"
            )
        # sf = s0*r and Gc = 0.5*Et*s0*sf*characteristic_length.
        resolved[name] = 0.5 * tensile_modulus * initiation**2 * ratio * horizon
    return resolved


def _resolve_boundary_value(
    condition: Mapping[str, Any],
    config: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> Any:
    if "value" in condition:
        return condition["value"]
    if "value_from_metadata" in condition:
        return _metadata_value(metadata, str(condition["value_from_metadata"]))
    if "acceleration" in condition:
        material_id = condition.get("material")
        try:
            density = float(config["materials"][material_id]["parameters"]["Density"])
        except KeyError as exc:
            raise XmlConfigError(
                f"Body force references unknown material {material_id!r}"
            ) from exc
        return density * float(condition["acceleration"])
    raise XmlConfigError(
        f"Boundary condition {condition.get('name')!r} needs value, "
        "value_from_metadata, or acceleration+material"
    )


def build_peridigm_tree(
    config: Mapping[str, Any],
    geometry_metadata: Mapping[str, Any],
    mesh_reference: str,
    output_basename: str,
) -> tuple[ET.Element, dict[str, Any]]:
    """Build an ElementTree root and return all mesh-derived XML values."""
    sizes = _block_mesh_sizes(geometry_metadata)
    global_multiplier = float(config["mesh"]["horizon_multiplier"])
    block_horizons: dict[str, float] = {}
    for block_id, block in config["blocks"].items():
        exodus_name = str(block["block_names"])
        if exodus_name not in sizes:
            raise XmlConfigError(
                f"Configured block {exodus_name!r} is absent from geometry metadata"
            )
        multiplier = float(block.get("horizon_multiplier", global_multiplier))
        block_horizons[block_id] = sizes[exodus_name] * multiplier

    solver_values = _resolve_solver(config, sizes)
    damage_horizons = _damage_horizons(config, block_horizons)
    damage_parameters = {
        damage_id: _resolve_damage_parameters(
            damage_id, damage, damage_horizons.get(damage_id)
        )
        for damage_id, damage in config.get("damage_models", {}).items()
    }

    root = ET.Element(
        "ParameterList", {"name": str(config.get("xml_name", "Peridigm"))}
    )
    _parameter(root, "Verbose", bool(config.get("verbose", False)))
    discretization = _plist(root, "Discretization")
    _parameter(discretization, "Type", "Exodus")
    _parameter(discretization, "Input Mesh File", mesh_reference)

    materials_element = _plist(root, "Materials")
    for material in config["materials"].values():
        material_element = _plist(materials_element, material["name"])
        _parameter(material_element, "Material Model", material["model"])
        for name, value in material.get("parameters", {}).items():
            _parameter(material_element, name, value)

    if config.get("damage_models"):
        damage_models_element = _plist(root, "Damage Models")
        for damage_id, damage in config["damage_models"].items():
            damage_element = _plist(damage_models_element, damage["name"])
            _parameter(damage_element, "Damage Model", damage["model"])
            for name, value in damage_parameters[damage_id].items():
                _parameter(damage_element, name, value)

    blocks_element = _plist(root, "Blocks")
    for block_id, block in config["blocks"].items():
        block_element = _plist(blocks_element, block["name"])
        _parameter(block_element, "Block Names", block["block_names"])
        _parameter(
            block_element,
            "Material",
            config["materials"][block["material"]]["name"],
        )
        if block.get("damage_model") is not None:
            _parameter(
                block_element,
                "Damage Model",
                config["damage_models"][block["damage_model"]]["name"],
            )
        _parameter(block_element, "Horizon", block_horizons[block_id])
        for name, value in block.get("parameters", {}).items():
            _parameter(block_element, name, value)

    contact = config.get("contact", {})
    if contact:
        contact_element = _plist(root, "Contact")
        _parameter(contact_element, "Verbose", bool(contact.get("verbose", False)))
        contact_scale = _select_mesh_size(contact.get("mesh_scale", "max"), sizes)
        _parameter(
            contact_element,
            "Search Radius",
            float(contact["search_radius_multiplier"]) * contact_scale,
        )
        _parameter(contact_element, "Search Frequency", int(contact["search_frequency"]))
        models_element = _plist(contact_element, "Models")
        for model in contact.get("models", {}).values():
            model_element = _plist(models_element, model["name"])
            _parameter(model_element, "Contact Model", model["model"])
            model_scale = _select_mesh_size(model.get("mesh_scale", contact.get("mesh_scale", "max")), sizes)
            _parameter(
                model_element,
                "Contact Radius",
                float(model["contact_radius_multiplier"]) * model_scale,
            )
            for name, value in model.get("parameters", {}).items():
                _parameter(model_element, name, value)
        interactions_element = _plist(contact_element, "Interactions")
        for interaction in contact.get("interactions", []):
            interaction_element = _plist(interactions_element, interaction["name"])
            _parameter(
                interaction_element,
                "Contact Model",
                contact["models"][interaction["model"]]["name"],
            )
            _parameter(interaction_element, "First Block", interaction["first_block"])
            _parameter(interaction_element, "Second Block", interaction["second_block"])

    boundary_conditions = config.get("boundary_conditions", [])
    if boundary_conditions:
        boundary_element = _plist(root, "Boundary Conditions")
        for condition in boundary_conditions:
            condition_element = _plist(boundary_element, condition["name"])
            _parameter(condition_element, "Type", condition["type"])
            _parameter(condition_element, "Node Set", condition["node_set"])
            if "coordinate" in condition:
                _parameter(condition_element, "Coordinate", condition["coordinate"])
            _parameter(
                condition_element,
                "Value",
                {
                    "type": "string",
                    "value": _resolve_boundary_value(condition, config, geometry_metadata),
                },
            )

    solver_element = _plist(root, "Solver")
    for name, value in config["solver"].get("parameters", {}).items():
        _parameter(solver_element, name, value)
    _parameter(solver_element, "Initial Time", solver_values["initial_time"])
    _parameter(solver_element, "Final Time", solver_values["final_time"])
    method_element = _plist(solver_element, solver_values["method"])
    if "fixed_dt" in solver_values:
        _parameter(method_element, "Fixed dt", solver_values["fixed_dt"])
    else:
        _parameter(method_element, "Safety Factor", solver_values["safety_factor"])

    output = config["output"]
    if "frequency" in output:
        output_frequency = int(output["frequency"])
    else:
        if "fixed_dt" not in solver_values:
            raise XmlConfigError(
                "output.interval_s requires a fixed or automatically resolved dt"
            )
        output_frequency = max(
            1, int(round(float(output["interval_s"]) / solver_values["fixed_dt"]))
        )
    output_element = _plist(root, "Output")
    _parameter(output_element, "Output File Type", output.get("file_type", "ExodusII"))
    _parameter(output_element, "Output Filename", output_basename)
    _parameter(output_element, "Output Frequency", output_frequency)
    variables_element = _plist(output_element, "Output Variables")
    for variable in output["variables"]:
        _parameter(variables_element, variable, True)

    resolved = {
        "mesh_size_method": geometry_metadata.get(
            "mesh_size_method", "median_physical_cell_edge"
        ),
        "block_mesh_sizes_m": dict(sizes),
        "horizon_multiplier": global_multiplier,
        "block_horizons_m": dict(block_horizons),
        "damage_parameters": damage_parameters,
        "solver": solver_values,
        "output_frequency": output_frequency,
        "contact_mesh_size_m": (
            _select_mesh_size(contact.get("mesh_scale", "max"), sizes)
            if contact
            else None
        ),
    }
    return root, resolved


def write_peridigm_xml(
    output_path: str | Path,
    config: Mapping[str, Any],
    geometry_metadata: Mapping[str, Any],
    mesh_reference: str,
    output_basename: str,
) -> dict[str, Any]:
    """Build and atomically write a configured, mesh-resolved XML file."""
    root, resolved = build_peridigm_tree(
        config, geometry_metadata, mesh_reference, output_basename
    )
    ET.indent(root, space="  ")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=destination.suffix, dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        ET.ElementTree(root).write(
            temporary, encoding="utf-8", xml_declaration=True
        )
        ET.parse(temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return resolved
