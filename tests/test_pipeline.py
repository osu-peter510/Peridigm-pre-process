from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from peridigm_preprocess.pipeline import (
    GenerationError,
    _planned_tags,
    _protect_existing_results,
)
from peridigm_preprocess.validation import ValidationError, validate_generated_case


class PipelineSafetyTests(unittest.TestCase):
    def test_rejects_parent_and_path_tags(self) -> None:
        for template in ("..", ".", "case/{seed}", "/tmp/case"):
            with self.subTest(template=template), self.assertRaises(GenerationError):
                _planned_tags("mug_fall", 1, 0, template)

    def test_rejects_duplicate_tags_before_generation(self) -> None:
        with self.assertRaisesRegex(GenerationError, "duplicate"):
            _planned_tags("mug_fall", 2, 0, "same")

    def test_accepts_default_style_tags(self) -> None:
        self.assertEqual(
            _planned_tags("mug_fall", 2, 7, "{scenario}_{seed:04d}"),
            [(0, 7, "mug_fall_0007"), (1, 8, "mug_fall_0008")],
        )

    def test_case_validation_rejects_artifact_paths_outside_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary) / "case"
            case.mkdir()
            (case / "metadata.json").write_text(
                json.dumps(
                    {
                        "files": {
                            "mesh": "../../outside.g",
                            "xml": "case.xml",
                            "results": "results",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValidationError, "escapes"):
                validate_generated_case(case)

    def test_force_protects_hidden_or_directory_result_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary) / "case"
            results = case / "results"
            (results / ".checkpoint").mkdir(parents=True)
            with self.assertRaisesRegex(GenerationError, "existing results"):
                _protect_existing_results(case)


if __name__ == "__main__":
    unittest.main()
