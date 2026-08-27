"""Configuration loading, overrides, normalization, and validation."""

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping, MutableMapping


SCENARIO_ALIASES = {
    "ball_plate_nf": "ball_plate",
    "freefall_mug": "mug_fall",
    "mug_freefall": "mug_fall",
    "kw": "kw_fracture",
    "kalthoff_winkler": "kw_fracture",
    "cloth": "cloth_fall",
}
PRIMARY_SCENARIOS = ("ball_plate", "kw_fracture", "mug_fall", "cloth_fall")


class ConfigError(ValueError):
    """Raised when a scene configuration is malformed or inconsistent."""


def _load_yaml_or_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
            raise ConfigError(
                "YAML configuration requires PyYAML; install the project dependencies "
                "or use a .json configuration file."
            ) from exc
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ConfigError(f"Configuration root must be a mapping: {path}")
    return data


def _parse_override_value(raw: str) -> Any:
    try:
        import yaml
    except ModuleNotFoundError:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return yaml.safe_load(raw)


def _set_dotted(config: MutableMapping[str, Any], dotted_key: str, value: Any) -> None:
    parts = [part for part in dotted_key.split(".") if part]
    if not parts:
        raise ConfigError("Override key cannot be empty")
    current: MutableMapping[str, Any] = config
    for part in parts[:-1]:
        existing = current.get(part)
        if existing is None:
            existing = {}
            current[part] = existing
        if not isinstance(existing, MutableMapping):
            raise ConfigError(
                f"Cannot set {dotted_key!r}: {part!r} is not a mapping"
            )
        current = existing
    current[parts[-1]] = value


def load_config(
    path: str | Path,
    overrides: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Load one YAML/JSON config, apply ``key=value`` overrides, and validate it."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ConfigError(f"Configuration file does not exist: {source}")
    config = copy.deepcopy(_load_yaml_or_json(source))
    for override in overrides:
        if "=" not in override:
            raise ConfigError(
                f"Invalid override {override!r}; expected dotted.key=value"
            )
        key, raw_value = override.split("=", 1)
        _set_dotted(config, key.strip(), _parse_override_value(raw_value.strip()))
    config["_config_file"] = str(source)
    normalize_config(config)
    validate_config(config)
    return config


def normalize_config(config: MutableMapping[str, Any]) -> None:
    scenario = str(config.get("scenario", "")).strip().lower().replace("-", "_")
    config["scenario"] = SCENARIO_ALIASES.get(scenario, scenario)
    config.setdefault("version", 1)
    run = config.setdefault("run", {})
    if isinstance(run, MutableMapping):
        run.setdefault("count", 1)
        run.setdefault("base_seed", 0)
        run.setdefault("output_root", "output")
        run.setdefault("resume", False)
    mesh = config.setdefault("mesh", {})
    if isinstance(mesh, MutableMapping):
        mesh.setdefault("horizon_multiplier", 3.015)
        mesh.setdefault("max_attempts", 10)


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _parameter_value(value: Any) -> Any:
    if isinstance(value, Mapping) and "value" in value:
        return value["value"]
    return value


def _integer(value: Any, path: str, *, positive: bool = False) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{path} must be an integer")
    if isinstance(value, int):
        converted = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ConfigError(f"{path} must be an integer")
        converted = int(value)
    elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        converted = int(value)
    else:
        raise ConfigError(f"{path} must be an integer")
    if positive and converted <= 0:
        raise ConfigError(f"{path} must be > 0")
    return converted


def _number(value: Any, path: str, *, positive: bool = False) -> float:
    raw = _parameter_value(value)
    if isinstance(raw, bool):
        raise ConfigError(f"{path} must be a finite number")
    try:
        converted = float(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} must be a finite number") from exc
    if not math.isfinite(converted):
        raise ConfigError(f"{path} must be finite")
    if positive and converted <= 0.0:
        raise ConfigError(f"{path} must be > 0")
    return converted


def _positive(value: Any, path: str, *, integer: bool = False) -> float | int:
    if integer:
        return _integer(value, path, positive=True)
    return _number(value, path, positive=True)


def _validate_typed_parameter(raw: Any, path: str) -> None:
    if not isinstance(raw, Mapping) or not ({"type", "value"} & set(raw)):
        return
    if "value" not in raw:
        raise ConfigError(f"{path} typed parameter must define value")
    parameter_type = raw.get("type")
    if parameter_type is None:
        return
    if parameter_type not in {"bool", "double", "int", "string"}:
        raise ConfigError(
            f"{path}.type must be bool, double, int, or string"
        )
    value = raw["value"]
    if parameter_type == "bool":
        if not (
            isinstance(value, bool)
            or isinstance(value, str)
            and value.strip().lower() in {"true", "false"}
        ):
            raise ConfigError(f"{path}.value must be true or false")
    elif parameter_type == "double":
        _number(value, f"{path}.value")
    elif parameter_type == "int":
        _integer(value, f"{path}.value")


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate cross-references and the fields needed for deterministic output."""
    if type(config.get("version")) is not int or config.get("version") != 1:
        raise ConfigError("version must be 1")
    scenario = config.get("scenario")
    if scenario not in PRIMARY_SCENARIOS:
        choices = ", ".join(PRIMARY_SCENARIOS)
        raise ConfigError(f"scenario must be one of: {choices}; got {scenario!r}")

    run = _mapping(config.get("run"), "run")
    _positive(run.get("count"), "run.count", integer=True)
    _integer(run.get("base_seed"), "run.base_seed")
    if not isinstance(run.get("resume"), bool):
        raise ConfigError("run.resume must be a boolean")
    if not str(run.get("output_root", "")).strip():
        raise ConfigError("run.output_root cannot be empty")

    mesh = _mapping(config.get("mesh"), "mesh")
    target = _positive(mesh.get("target_elements"), "mesh.target_elements", integer=True)
    maximum = _positive(mesh.get("max_elements"), "mesh.max_elements", integer=True)
    if target > maximum:
        raise ConfigError("mesh.target_elements cannot exceed mesh.max_elements")
    _positive(mesh.get("initial_size_m"), "mesh.initial_size_m")
    _positive(mesh.get("horizon_multiplier"), "mesh.horizon_multiplier")
    _positive(mesh.get("max_attempts"), "mesh.max_attempts", integer=True)
    tolerance = _number(mesh.get("tolerance", 0.08), "mesh.tolerance")
    if not 0.0 < tolerance < 1.0:
        raise ConfigError("mesh.tolerance must be in (0, 1)")
    minimum_sicn = _number(mesh.get("minimum_sicn", 0.0), "mesh.minimum_sicn")
    if not 0.0 <= minimum_sicn <= 1.0:
        raise ConfigError("mesh.minimum_sicn must be in [0, 1]")

    geometry = _mapping(config.get("geometry", {}), "geometry")
    if not geometry:
        raise ConfigError("geometry must contain scenario parameters")

    materials = _mapping(config.get("materials"), "materials")
    if not materials:
        raise ConfigError("materials cannot be empty")
    for material_id, material in materials.items():
        material = _mapping(material, f"materials.{material_id}")
        if not str(material.get("name", "")).strip():
            raise ConfigError(f"materials.{material_id}.name cannot be empty")
        if not str(material.get("model", "")).strip():
            raise ConfigError(f"materials.{material_id}.model cannot be empty")
        parameters = _mapping(
            material.get("parameters", {}), f"materials.{material_id}.parameters"
        )
        for parameter_name, parameter_value in parameters.items():
            _validate_typed_parameter(
                parameter_value,
                f"materials.{material_id}.parameters.{parameter_name}",
            )
        for required in ("Density", "Bulk Modulus", "Shear Modulus"):
            if required not in parameters:
                raise ConfigError(
                    f"materials.{material_id}.parameters must define {required!r}"
                )
            _positive(parameters[required], f"materials.{material_id}.{required}")
        if material["model"] == "Viscoelastic":
            if "lambda i" in parameters:
                raise ConfigError(
                    f"materials.{material_id}.parameters uses 'lambda i'; "
                    "Peridigm requires the exact key 'lambda_i'"
                )
            for required in ("lambda_i", "tau b"):
                if required not in parameters:
                    raise ConfigError(
                        f"Viscoelastic materials.{material_id}.parameters must define "
                        f"{required!r}"
                    )
                _positive(
                    parameters[required],
                    f"materials.{material_id}.parameters.{required}",
                )

    damage_models = _mapping(config.get("damage_models", {}), "damage_models")
    for damage_id, damage in damage_models.items():
        damage = _mapping(damage, f"damage_models.{damage_id}")
        if not str(damage.get("name", "")).strip():
            raise ConfigError(f"damage_models.{damage_id}.name cannot be empty")
        if not str(damage.get("model", "")).strip():
            raise ConfigError(f"damage_models.{damage_id}.model cannot be empty")
        parameters = _mapping(
            damage.get("parameters", {}), f"damage_models.{damage_id}.parameters"
        )
        for parameter_name, parameter_value in parameters.items():
            if not (
                isinstance(parameter_value, Mapping)
                and ("source" in parameter_value or "derive" in parameter_value)
            ):
                _validate_typed_parameter(
                    parameter_value,
                    f"damage_models.{damage_id}.parameters.{parameter_name}",
                )
        if damage["model"] == "Critical Stretch":
            if "Critical Stretch" not in parameters:
                raise ConfigError(
                    f"damage_models.{damage_id}.parameters must define 'Critical Stretch'"
                )
            _positive(
                parameters["Critical Stretch"],
                f"damage_models.{damage_id}.parameters.Critical Stretch",
            )
        if damage["model"] == "Progressive Bond Energy":
            required_parameters = (
                "Tensile Modulus",
                "Damage Initiation Stretch",
                "Characteristic Length",
                "Fracture Energy",
            )
            missing = [name for name in required_parameters if name not in parameters]
            if missing:
                raise ConfigError(
                    f"Progressive Bond Energy damage_models.{damage_id}.parameters "
                    f"is missing: {', '.join(missing)}"
                )
            _positive(
                parameters["Tensile Modulus"],
                f"damage_models.{damage_id}.parameters.Tensile Modulus",
            )
            _positive(
                parameters["Damage Initiation Stretch"],
                f"damage_models.{damage_id}.parameters.Damage Initiation Stretch",
            )
            characteristic_length = parameters["Characteristic Length"]
            if not (
                isinstance(characteristic_length, Mapping)
                and characteristic_length.get("source") == "horizon"
            ):
                raise ConfigError(
                    "Progressive Bond Energy Characteristic Length must use "
                    "{source: horizon}"
                )
            fracture_energy = parameters["Fracture Energy"]
            if not (
                isinstance(fracture_energy, Mapping)
                and fracture_energy.get("derive") == "progressive_fracture_energy"
            ):
                raise ConfigError(
                    "Progressive Bond Energy Fracture Energy must use the "
                    "progressive_fracture_energy derivation"
                )
            ratio = _positive(
                fracture_energy.get("failure_stretch_ratio"),
                f"damage_models.{damage_id}.Fracture Energy.failure_stretch_ratio",
            )
            if ratio <= 1.0:
                raise ConfigError("failure_stretch_ratio must be > 1")
            tensile_parameter = str(
                fracture_energy.get("tensile_modulus_parameter", "Tensile Modulus")
            )
            if tensile_parameter not in parameters:
                raise ConfigError(
                    "Fracture Energy references an unknown tensile modulus parameter "
                    f"{tensile_parameter!r}"
                )

    blocks = _mapping(config.get("blocks"), "blocks")
    if not blocks:
        raise ConfigError("blocks cannot be empty")
    block_names: set[str] = set()
    for block_id, block in blocks.items():
        block = _mapping(block, f"blocks.{block_id}")
        exodus_name = str(block.get("block_names", "")).strip()
        if not exodus_name:
            raise ConfigError(f"blocks.{block_id}.block_names cannot be empty")
        if exodus_name in block_names:
            raise ConfigError(f"Duplicate Exodus block name: {exodus_name}")
        block_names.add(exodus_name)
        material = block.get("material")
        if material not in materials:
            raise ConfigError(
                f"blocks.{block_id}.material references unknown material {material!r}"
            )
        damage = block.get("damage_model")
        if damage is not None and damage not in damage_models:
            raise ConfigError(
                f"blocks.{block_id}.damage_model references unknown model {damage!r}"
            )
        if "horizon_multiplier" in block:
            _positive(
                block["horizon_multiplier"],
                f"blocks.{block_id}.horizon_multiplier",
            )
        for parameter_name, parameter_value in _mapping(
            block.get("parameters", {}), f"blocks.{block_id}.parameters"
        ).items():
            _validate_typed_parameter(
                parameter_value,
                f"blocks.{block_id}.parameters.{parameter_name}",
            )

    contact = _mapping(config.get("contact", {}), "contact")
    if contact:
        _positive(contact.get("search_radius_multiplier"), "contact.search_radius_multiplier")
        _positive(contact.get("search_frequency"), "contact.search_frequency", integer=True)
        models = _mapping(contact.get("models", {}), "contact.models")
        for model_id, model in models.items():
            model = _mapping(model, f"contact.models.{model_id}")
            _positive(
                model.get("contact_radius_multiplier"),
                f"contact.models.{model_id}.contact_radius_multiplier",
            )
            model_parameters = _mapping(
                model.get("parameters", {}),
                f"contact.models.{model_id}.parameters",
            )
            for parameter_name, parameter_value in model_parameters.items():
                _validate_typed_parameter(
                    parameter_value,
                    f"contact.models.{model_id}.parameters.{parameter_name}",
                )
            capability = str(
                _mapping(config.get("runtime", {}), "runtime").get(
                    "peridigm_capability", "stock"
                )
            )
            if model.get("model") == "Short Range Force" and capability == "stock" and {
                "Damping Coefficient",
                "Normal Damping Coefficient",
            } & set(model_parameters):
                raise ConfigError(
                    "Stock Short Range Force does not support contact damping; "
                    "remove it or declare a compatible custom Peridigm capability"
                )
        for index, interaction in enumerate(contact.get("interactions", [])):
            interaction = _mapping(interaction, f"contact.interactions[{index}]")
            if interaction.get("model") not in models:
                raise ConfigError(
                    f"contact.interactions[{index}] references unknown contact model"
                )
            for key in ("first_block", "second_block"):
                if interaction.get(key) not in block_names:
                    raise ConfigError(
                        f"contact.interactions[{index}].{key} references unknown block"
                    )

    solver = _mapping(config.get("solver"), "solver")
    for parameter_name, parameter_value in _mapping(
        solver.get("parameters", {}), "solver.parameters"
    ).items():
        _validate_typed_parameter(
            parameter_value, f"solver.parameters.{parameter_name}"
        )
    initial_time = _number(solver.get("initial_time", 0.0), "solver.initial_time")
    final_time = _positive(solver.get("final_time"), "solver.final_time")
    if initial_time < 0.0 or final_time <= initial_time:
        raise ConfigError(
            "solver requires 0 <= initial_time < final_time"
        )
    time_step = _mapping(solver.get("time_step"), "solver.time_step")
    mode = time_step.get("mode")
    if mode not in {"auto", "fixed", "safety_factor"}:
        raise ConfigError(
            "solver.time_step.mode must be auto, fixed, or safety_factor"
        )
    if mode == "fixed":
        _positive(time_step.get("value"), "solver.time_step.value")
    else:
        _positive(time_step.get("safety_factor"), "solver.time_step.safety_factor")

    output = _mapping(config.get("output"), "output")
    if "frequency" in output:
        _positive(output["frequency"], "output.frequency", integer=True)
    elif "interval_s" in output:
        _positive(output["interval_s"], "output.interval_s")
    else:
        raise ConfigError("output must define frequency or interval_s")
    variables = output.get("variables", [])
    if not isinstance(variables, list) or not variables:
        raise ConfigError("output.variables must be a non-empty list")
    if any(not isinstance(variable, str) or not variable.strip() for variable in variables):
        raise ConfigError("output.variables must contain non-empty strings")

    runtime = _mapping(config.get("runtime", {}), "runtime")
    if "mpi_ranks" in runtime:
        _positive(runtime["mpi_ranks"], "runtime.mpi_ranks", integer=True)

    analysis = _mapping(config.get("analysis", {}), "analysis")
    aliases = {
        re.sub(r"[^a-z0-9]+", "", str(alias).casefold())
        for block_id, block in blocks.items()
        for alias in (block_id, block["name"], block["block_names"])
    }
    for selector_key in ("blocks", "damage_blocks"):
        selectors = analysis.get(selector_key)
        if selectors is None and selector_key == "blocks":
            selectors = analysis.get("selected_blocks")
        if selectors is None:
            continue
        if isinstance(selectors, (str, int)):
            selectors = [selectors]
        if not isinstance(selectors, list) or not selectors:
            raise ConfigError(
                f"analysis.{selector_key} must be a non-empty list or selector"
            )
        unknown = [
            selector
            for selector in selectors
            if re.sub(r"[^a-z0-9]+", "", str(selector).casefold()) not in aliases
        ]
        if unknown:
            raise ConfigError(
                f"analysis.{selector_key} references unknown block selector(s): "
                + ", ".join(map(str, unknown))
            )
    if "allow_multi_block_impact" in analysis and not isinstance(
        analysis["allow_multi_block_impact"], bool
    ):
        raise ConfigError("analysis.allow_multi_block_impact must be a boolean")
    if "damage_thresholds" in analysis:
        thresholds = analysis["damage_thresholds"]
        if not isinstance(thresholds, list) or not thresholds:
            raise ConfigError("analysis.damage_thresholds must be a non-empty list")
        for index, threshold in enumerate(thresholds):
            value = _number(threshold, f"analysis.damage_thresholds[{index}]")
            if value < 0.0 or value > 1.0:
                raise ConfigError("analysis damage thresholds must be in [0, 1]")
    if "impact_axis" in analysis:
        axis = analysis["impact_axis"]
        if not isinstance(axis, list) or len(axis) != 3:
            raise ConfigError("analysis.impact_axis must contain three numbers")
        components = [
            _number(value, f"analysis.impact_axis[{index}]")
            for index, value in enumerate(axis)
        ]
        if math.sqrt(sum(value * value for value in components)) == 0.0:
            raise ConfigError("analysis.impact_axis cannot be the zero vector")
    if "contact_force_threshold_n" in analysis:
        _positive(
            analysis["contact_force_threshold_n"],
            "analysis.contact_force_threshold_n",
        )


def public_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep copy without internal loader bookkeeping keys."""
    return {
        key: copy.deepcopy(value)
        for key, value in config.items()
        if not str(key).startswith("_")
    }
