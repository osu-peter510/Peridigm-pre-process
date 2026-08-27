from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from peridigm_preprocess.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def test_all_release_configs_validate(self) -> None:
        expected = {
            "ball_plate.yaml": "ball_plate",
            "kw_fracture.yaml": "kw_fracture",
            "mug_fall.yaml": "mug_fall",
            "mug_fall_progressive.yaml": "mug_fall",
            "cloth_fall.yaml": "cloth_fall",
        }
        for filename, scenario in expected.items():
            with self.subTest(filename=filename):
                config = load_config(ROOT / "configs" / filename)
                self.assertEqual(config["scenario"], scenario)
                self.assertLessEqual(
                    config["mesh"]["target_elements"],
                    config["mesh"]["max_elements"],
                )
                self.assertGreater(config["mesh"]["horizon_multiplier"], 0.0)

    def test_alias_and_dotted_override(self) -> None:
        config = load_config(
            ROOT / "configs" / "ball_plate.yaml",
            [
                "scenario=ball_plate_nf",
                "run.count=3",
                "mesh.horizon_multiplier=3.2",
                "damage_models.plate_fracture.parameters.Critical Stretch=0.001",
            ],
        )
        self.assertEqual(config["scenario"], "ball_plate")
        self.assertEqual(config["run"]["count"], 3)
        self.assertEqual(config["mesh"]["horizon_multiplier"], 3.2)
        self.assertEqual(
            config["damage_models"]["plate_fracture"]["parameters"][
                "Critical Stretch"
            ],
            0.001,
        )

    def test_rejects_target_above_hard_cap(self) -> None:
        with self.assertRaisesRegex(ConfigError, "cannot exceed"):
            load_config(
                ROOT / "configs" / "mug_fall.yaml",
                ["mesh.target_elements=12001", "mesh.max_elements=12000"],
            )

    def test_json_config_is_supported(self) -> None:
        yaml_config = load_config(ROOT / "configs" / "ball_plate.yaml")
        import json

        yaml_config.pop("_config_file")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scene.json"
            path.write_text(json.dumps(yaml_config), encoding="utf-8")
            loaded = load_config(path)
        self.assertEqual(loaded["scenario"], "ball_plate")

    def test_rejects_nonfinite_and_inexact_values(self) -> None:
        with self.assertRaisesRegex(ConfigError, "finite"):
            load_config(
                ROOT / "configs" / "ball_plate.yaml",
                ["mesh.horizon_multiplier=.nan"],
            )
        with self.assertRaisesRegex(ConfigError, "integer"):
            load_config(
                ROOT / "configs" / "ball_plate.yaml",
                ["run.count=1.5"],
            )
        with self.assertRaisesRegex(ConfigError, "boolean"):
            load_config(
                ROOT / "configs" / "ball_plate.yaml",
                ["run.resume='false'"],
            )

    def test_rejects_misspelled_viscoelastic_parameter(self) -> None:
        with self.assertRaisesRegex(ConfigError, "lambda_i"):
            load_config(
                ROOT / "configs" / "cloth_fall.yaml",
                ["materials.cloth.parameters.lambda i=0.8"],
            )


if __name__ == "__main__":
    unittest.main()
