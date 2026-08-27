from __future__ import annotations

import copy
import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from peridigm_preprocess.config import load_config
from peridigm_preprocess.xml_writer import XmlConfigError, build_peridigm_tree


def named_list(root, name):
    matches = [
        element
        for element in root.iter("ParameterList")
        if element.get("name") == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"Expected one list {name!r}; got {len(matches)}")
    return matches[0]


def direct_value(element, name):
    matches = [
        child.get("value")
        for child in element.findall("Parameter")
        if child.get("name") == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"Expected one parameter {name!r}; got {matches}")
    return matches[0]


class XmlWriterTests(unittest.TestCase):
    def test_horizon_is_measured_per_block(self) -> None:
        config = load_config(ROOT / "configs" / "ball_plate.yaml")
        config["blocks"]["ball"]["horizon_multiplier"] = 3.2
        metadata = {
            "block_mesh_sizes_m": {"block_1": 0.002, "block_2": 0.003},
            "mesh_size_method": "median_physical_cell_edge",
            "initial_velocity_m_s": [1.0, 2.0, -3.0],
        }
        root, resolved = build_peridigm_tree(config, metadata, "mesh.g", "results/case")
        plate = named_list(root, "Plate Block")
        ball = named_list(root, "Ball Block")
        self.assertTrue(
            math.isclose(float(direct_value(plate, "Horizon")), 0.002 * 3.015)
        )
        self.assertTrue(
            math.isclose(float(direct_value(ball, "Horizon")), 0.003 * 3.2)
        )
        self.assertEqual(resolved["mesh_size_method"], "median_physical_cell_edge")
        self.assertEqual(direct_value(named_list(root, "Ball Initial Velocity Z"), "Value"), "-3.0")

    def test_material_damage_and_solver_are_explicit(self) -> None:
        config = load_config(ROOT / "configs" / "ball_plate.yaml")
        metadata = {
            "block_mesh_sizes_m": {"block_1": 0.002, "block_2": 0.002},
            "initial_velocity_m_s": [0.0, 0.0, -20.0],
        }
        root, resolved = build_peridigm_tree(config, metadata, "mesh.g", "results/case")
        plate_material = named_list(root, "Plate Material")
        plate_damage = named_list(root, "Plate Damage")
        solver = named_list(root, "Solver")
        self.assertEqual(direct_value(plate_material, "Density"), "2200")
        self.assertEqual(direct_value(plate_material, "Bulk Modulus"), "14900000000")
        self.assertEqual(direct_value(plate_damage, "Damage Model"), "Critical Stretch")
        self.assertEqual(direct_value(plate_damage, "Critical Stretch"), "0.0005")
        self.assertEqual(direct_value(named_list(solver, "Verlet"), "Fixed dt"), "2e-07")
        self.assertEqual(resolved["output_frequency"], 25)

    def test_progressive_energy_tracks_final_horizon(self) -> None:
        config = load_config(ROOT / "configs" / "mug_fall_progressive.yaml")
        metadata = {
            "block_mesh_sizes_m": {"block_1": 0.0045, "block_2": 0.006},
            "initial_velocity_m_s": [0.0, 0.0, -5.0],
        }
        root, resolved = build_peridigm_tree(config, metadata, "mesh.g", "results/case")
        horizon = 0.006 * 3.015
        parameters = resolved["damage_parameters"]["mug_fracture"]
        expected_gc = 0.5 * 2.235e10 * (5.0e-4) ** 2 * 4.0 * horizon
        self.assertTrue(math.isclose(parameters["Characteristic Length"], horizon))
        self.assertTrue(math.isclose(parameters["Fracture Energy"], expected_gc))
        mug_material = named_list(root, "Mug Material")
        mug_damage = named_list(root, "Mug Damage")
        self.assertEqual(direct_value(mug_material, "lambda_i"), "0.2")
        self.assertFalse(
            any(
                child.get("name") == "lambda i"
                for child in mug_material.findall("Parameter")
            )
        )
        self.assertEqual(direct_value(mug_damage, "Tensile Modulus"), "22350000000")

    def test_explicit_xml_types_are_strict(self) -> None:
        config = load_config(ROOT / "configs" / "kw_fracture.yaml")
        config["solver"]["parameters"] = {
            "Flag": {"type": "bool", "value": "false"},
        }
        metadata = {
            "block_mesh_sizes_m": {"block_1": 0.006, "block_2": 0.004},
            "initial_velocity_m_s": [0.0, -100.0, 0.0],
        }
        root, _ = build_peridigm_tree(config, metadata, "mesh.g", "results/case")
        self.assertEqual(direct_value(named_list(root, "Solver"), "Flag"), "false")

        config["solver"]["parameters"]["Flag"] = {
            "type": "bool",
            "value": "not-a-boolean",
        }
        with self.assertRaises(XmlConfigError):
            build_peridigm_tree(config, metadata, "mesh.g", "results/case")

        config["solver"]["parameters"]["Flag"] = {
            "type": "int",
            "value": 1.9,
        }
        with self.assertRaises(XmlConfigError):
            build_peridigm_tree(config, metadata, "mesh.g", "results/case")

    def test_auto_dt_uses_fastest_material_and_selected_mesh_scale(self) -> None:
        config = load_config(ROOT / "configs" / "kw_fracture.yaml")
        metadata = {
            "block_mesh_sizes_m": {"block_1": 0.006, "block_2": 0.004},
            "initial_velocity_m_s": [0.0, -100.0, 0.0],
        }
        _, resolved = build_peridigm_tree(config, metadata, "mesh.g", "results/case")
        solver = resolved["solver"]
        self.assertEqual(solver["stability_mesh_size_m"], 0.004)
        self.assertGreater(solver["maximum_p_wave_speed_m_s"], 5000.0)
        self.assertGreater(solver["fixed_dt"], 0.0)
        self.assertLess(solver["fixed_dt"], 1.0e-6)


if __name__ == "__main__":
    unittest.main()
