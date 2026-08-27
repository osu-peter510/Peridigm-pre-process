from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from peridigm_preprocess.quality import (
    _safe_report_destination,
    _scan_transient,
    _strict_json_value,
    _validate_result_grouping,
    _validate_run_provenance,
)


class FakeVariable:
    def __init__(self, values, dimensions):
        self.values = np.asarray(values)
        self.dimensions = dimensions
        self.shape = self.values.shape

    def __getitem__(self, key):
        return self.values[key]

    def set_auto_mask(self, value):
        return None


def character_rows(*names: str):
    width = max(map(len, names)) + 1
    result = np.zeros((len(names), width), dtype="S1")
    for row, name in enumerate(names):
        result[row, : len(name)] = np.frombuffer(name.encode("ascii"), dtype="S1")
    return result


class QualityFieldTests(unittest.TestCase):
    def test_damage_is_resolved_through_exodus_name_table(self) -> None:
        class Dataset:
            variables = {
                "name_elem_var": FakeVariable(
                    character_rows("Damage"), ("num_elem_var", "len_name")
                ),
                "vals_elem_var1eb1": FakeVariable(
                    [[0.0, 0.1], [0.05, 0.2], [0.04, 0.3]],
                    ("time_step", "num_el_in_blk1"),
                ),
            }

        scan = _scan_transient(Dataset(), np)
        self.assertIsNotNone(scan["damage"])
        self.assertEqual(
            scan["damage"]["variables"][0]["storage"],
            "vals_elem_var1eb1",
        )
        self.assertEqual(scan["damage"]["variables"][0]["field"], "Damage")
        self.assertTrue(scan["damage"]["within_unit_interval"])
        self.assertFalse(scan["damage"]["nondecreasing"])

    def test_all_nonfinite_damage_is_not_reported_as_valid_json_infinity(self) -> None:
        class Dataset:
            variables = {
                "name_elem_var": FakeVariable(
                    character_rows("Damage"), ("num_elem_var", "len_name")
                ),
                "vals_elem_var1eb1": FakeVariable(
                    [[math.nan, math.nan]],
                    ("time_step", "num_el_in_blk1"),
                ),
            }

        scan = _scan_transient(Dataset(), np)
        self.assertIsNone(scan["damage"]["minimum"])
        self.assertIsNone(scan["damage"]["maximum"])
        self.assertFalse(scan["damage"]["within_unit_interval"])
        self.assertFalse(scan["damage"]["nondecreasing"])
        json.dumps(_strict_json_value(scan), allow_nan=False)

    def test_numpy_scalars_are_converted_to_strict_json_values(self) -> None:
        converted = _strict_json_value(
            {"count": np.int64(3), "value": np.float64(1.5), "missing": np.nan}
        )

        self.assertEqual(converted, {"count": 3, "value": 1.5, "missing": None})
        json.dumps(converted, allow_nan=False)


class RunProvenanceTests(unittest.TestCase):
    def test_report_destination_cannot_overwrite_case_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            (case / "metadata.json").write_text(
                json.dumps(
                    {
                        "files": {
                            "mesh": "case.g",
                            "xml": "case.xml",
                            "results": "results",
                        },
                        "config": {"runtime": {"mpi_ranks": 2}},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Refusing"):
                _safe_report_destination(
                    case, case / "metadata.json", "quality_report.json"
                )
            with self.assertRaisesRegex(ValueError, "Refusing"):
                _safe_report_destination(
                    case,
                    case / "case.g.decomp.2.json",
                    "quality_report.json",
                )

    def test_different_exodus_bases_cannot_form_one_rank_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary)
            candidates = [results / "old.e.2.0", results / "new.e.2.1"]
            errors: list[str] = []
            _validate_result_grouping(candidates, results, errors)
            self.assertTrue(any("Multiple Exodus" in error for error in errors))

    def test_sidecars_are_bound_to_current_mesh_xml_and_ranks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            results = case / "results"
            results.mkdir()
            metadata = {
                "files": {"mesh": "case.g", "xml": "case.xml"},
                "sha256": {"mesh": "a" * 64, "xml": "b" * 64},
                "config": {"runtime": {"mpi_ranks": 2}},
            }
            run = {
                "status": "complete",
                "exit_code": 0,
                "mpi_ranks": 2,
                "slurm_ntasks": 2,
                "mesh": "case.g",
                "mesh_sha256": "a" * 64,
                "xml": "case.xml",
                "xml_sha256": "b" * 64,
                "decomposition_manifest": "case.g.decomp.2.json",
                "result_files": [],
                "result_file_sizes": {},
            }
            (results / "run_metadata.json").write_text(
                json.dumps(run), encoding="utf-8"
            )
            (case / "case.g.decomp.2.json").write_text(
                json.dumps(
                    {
                        "base_mesh": "case.g",
                        "base_mesh_sha256": "a" * 64,
                        "mpi_ranks": 2,
                        "shards": ["case.g.2.0", "case.g.2.1"],
                    }
                ),
                encoding="utf-8",
            )
            errors: list[str] = []
            _validate_run_provenance(case, results, metadata, [], errors)
            self.assertEqual(errors, [])

            run["xml_sha256"] = "c" * 64
            (results / "run_metadata.json").write_text(
                json.dumps(run), encoding="utf-8"
            )
            errors = []
            _validate_run_provenance(case, results, metadata, [], errors)
            self.assertTrue(any("xml_sha256" in error for error in errors))

    def test_missing_run_sidecar_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            results = case / "results"
            results.mkdir()
            errors: list[str] = []
            _validate_run_provenance(case, results, {}, [], errors)
            self.assertTrue(any("Missing" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
