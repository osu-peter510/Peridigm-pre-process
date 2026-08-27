from __future__ import annotations

import importlib.util
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from peridigm_preprocess.result_analysis import (
    analyze_impact_case,
    compare_convergence_reports,
)


HAS_NETCDF4 = importlib.util.find_spec("netCDF4") is not None


@unittest.skipUnless(HAS_NETCDF4, "netCDF4 is required for result integration tests")
class ResultAnalysisTests(unittest.TestCase):
    def _write_names(self, dataset, variable_name, dimension_name, names):
        import numpy as np

        if dimension_name not in dataset.dimensions:
            dataset.createDimension(dimension_name, len(names))
        variable = dataset.createVariable(
            variable_name, "S1", (dimension_name, "len_name")
        )
        characters = np.zeros((len(names), 33), dtype="S1")
        for row, name in enumerate(names):
            encoded = name.encode("ascii")
            characters[row, : len(encoded)] = np.frombuffer(encoded, dtype="S1")
        variable[:] = characters

    def _write_case(
        self,
        directory: Path,
        *,
        include_volume: bool = True,
        include_direct_force: bool = False,
        include_element_map: bool = True,
        split_analysis_roles: bool = False,
        times: list[float] | None = None,
    ) -> None:
        import netCDF4
        import numpy as np

        saved_times = times if times is not None else [0.0, 1.0, 2.0]
        frame_count = len(saved_times)
        if not frame_count:
            raise ValueError("at least one saved time is required")
        item_count = 4 if split_analysis_roles else 2
        block_count = 2 if split_analysis_roles else 1
        results = directory / "results"
        results.mkdir()
        with netCDF4.Dataset(results / "case.e", "w") as dataset:
            dataset.createDimension("len_name", 33)
            dataset.createDimension("time_step", frame_count)
            dataset.createDimension("num_nodes", item_count)
            dataset.createDimension("num_elem", item_count)
            dataset.createDimension("num_el_blk", block_count)
            dataset.createDimension("num_el_in_blk1", 2)
            dataset.createDimension("num_nod_per_el1", 1)
            if split_analysis_roles:
                dataset.createDimension("num_el_in_blk2", 2)
                dataset.createDimension("num_nod_per_el2", 1)

            dataset.createVariable("time_whole", "f8", ("time_step",))[:] = saved_times
            block_ids = [1, 2] if split_analysis_roles else [2]
            block_names = ["block_1", "block_2"] if split_analysis_roles else ["block_2"]
            dataset.createVariable("eb_prop1", "i4", ("num_el_blk",))[:] = block_ids
            self._write_names(dataset, "eb_names", "num_el_blk", block_names)
            dataset.createVariable("node_num_map", "i4", ("num_nodes",))[:] = range(
                1, item_count + 1
            )
            if include_element_map:
                dataset.createVariable("elem_num_map", "i4", ("num_elem",))[:] = range(
                    1, item_count + 1
                )
            connectivity = dataset.createVariable(
                "connect1", "i4", ("num_el_in_blk1", "num_nod_per_el1")
            )
            connectivity[:] = [[1], [2]]
            connectivity.elem_type = "SPHERE"
            if split_analysis_roles:
                connectivity = dataset.createVariable(
                    "connect2", "i4", ("num_el_in_blk2", "num_nod_per_el2")
                )
                connectivity[:] = [[3], [4]]
                connectivity.elem_type = "SPHERE"

            nodal_names = [
                "CoordinatesX",
                "CoordinatesY",
                "CoordinatesZ",
                "VelocityX",
                "VelocityY",
                "VelocityZ",
                "Contact_Force_DensityX",
                "Contact_Force_DensityY",
                "Contact_Force_DensityZ",
            ]
            if include_direct_force:
                nodal_names.extend(
                    ["Contact_ForceX", "Contact_ForceY", "Contact_ForceZ"]
                )
            self._write_names(dataset, "name_nod_var", "num_nod_var", nodal_names)
            zeros = np.zeros((frame_count, item_count), dtype=float)
            if frame_count == 3:
                impact_velocity_z = np.asarray(
                    [[-2.0, -2.0], [0.0, 0.0], [1.0, 1.0]]
                )
                impact_force_z = np.asarray(
                    [[0.0, 0.0], [10.0, 10.0], [0.0, 0.0]]
                )
                damage = np.asarray(
                    [[0.0, 0.0], [0.1, 0.5], [0.2, 0.8]]
                )
            else:
                impact_velocity_z = np.asarray([[-2.0, -2.0]] * frame_count)
                impact_force_z = np.asarray([[10.0, 10.0]] * frame_count)
                damage = np.asarray([[0.2, 0.8]] * frame_count)
            velocity_z = np.zeros((frame_count, item_count), dtype=float)
            velocity_z[:, :2] = impact_velocity_z
            force_z = np.zeros((frame_count, item_count), dtype=float)
            force_z[:, :2] = impact_force_z
            nodal_values = [
                zeros,
                zeros,
                np.asarray([[1.0, 2.0] + ([0.0, 0.0] if split_analysis_roles else [])] * frame_count),
                zeros,
                zeros,
                velocity_z,
                zeros,
                zeros,
                force_z,
            ]
            if include_direct_force:
                nodal_values.extend([zeros, zeros, force_z])
            for index, values in enumerate(nodal_values, 1):
                dataset.createVariable(
                    f"vals_nod_var{index}", "f8", ("time_step", "num_nodes")
                )[:] = values

            element_names = ["Volume", "Damage"] if include_volume else ["Damage"]
            self._write_names(dataset, "name_elem_var", "num_elem_var", element_names)
            if include_volume:
                dataset.createVariable(
                    "vals_elem_var1eb1", "f8", ("time_step", "num_el_in_blk1")
                )[:] = [[1.0, 3.0]] * frame_count
                if split_analysis_roles:
                    dataset.createVariable(
                        "vals_elem_var1eb2",
                        "f8",
                        ("time_step", "num_el_in_blk2"),
                    )[:] = [[1.0, 3.0]] * frame_count
                damage_index = 2
            else:
                damage_index = 1
            dataset.createVariable(
                f"vals_elem_var{damage_index}eb1",
                "f8",
                ("time_step", "num_el_in_blk1"),
            )[:] = np.zeros_like(damage) if split_analysis_roles else damage
            if split_analysis_roles:
                dataset.createVariable(
                    f"vals_elem_var{damage_index}eb2",
                    "f8",
                    ("time_step", "num_el_in_blk2"),
                )[:] = damage

        blocks = (
            {
                "projectile": {
                    "name": "Projectile Block",
                    "block_names": "block_1",
                    "material": "mug",
                },
                "target": {
                    "name": "Target Block",
                    "block_names": "block_2",
                    "material": "target",
                },
            }
            if split_analysis_roles
            else {
                "mug": {
                    "name": "Mug Block",
                    "block_names": "block_2",
                    "material": "mug",
                }
            }
        )
        analysis = (
            {"blocks": ["projectile"], "damage_blocks": ["target"]}
            if split_analysis_roles
            else {"blocks": ["mug"]}
        )
        metadata = {
            "tag": "case",
            "scenario": "mug_fall",
            "files": {"results": "results"},
            "config": {
                "materials": {
                    "mug": {"parameters": {"Density": 2.0}},
                    "target": {"parameters": {"Density": 4.0}},
                },
                "blocks": blocks,
                "analysis": analysis,
            },
        }
        (directory / "metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )

    def _write_shared_tet_direct_force_case(self, directory: Path) -> None:
        """Write two TET4s sharing one force-bearing global node."""
        import netCDF4
        import numpy as np

        results = directory / "results"
        results.mkdir()
        result_path = results / "case.e"
        with netCDF4.Dataset(result_path, "w") as dataset:
            dataset.createDimension("len_name", 33)
            dataset.createDimension("time_step", 2)
            dataset.createDimension("num_nodes", 7)
            dataset.createDimension("num_elem", 2)
            dataset.createDimension("num_el_blk", 1)
            dataset.createDimension("num_el_in_blk1", 2)
            dataset.createDimension("num_nod_per_el1", 4)
            dataset.createVariable("time_whole", "f8", ("time_step",))[:] = [
                0.0,
                1.0,
            ]
            dataset.createVariable("eb_prop1", "i4", ("num_el_blk",))[:] = [2]
            self._write_names(dataset, "eb_names", "num_el_blk", ["block_2"])
            dataset.createVariable("node_num_map", "i4", ("num_nodes",))[:] = range(
                1, 8
            )
            dataset.createVariable("elem_num_map", "i4", ("num_elem",))[:] = [
                1,
                2,
            ]
            connectivity = dataset.createVariable(
                "connect1", "i4", ("num_el_in_blk1", "num_nod_per_el1")
            )
            connectivity[:] = [[1, 2, 3, 4], [1, 5, 6, 7]]
            connectivity.elem_type = "TET4"
            coordinates = np.asarray(
                [
                    [0.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.0, 1.0],
                    [-1.0, 0.0, 0.0],
                    [0.0, -1.0, 0.0],
                    [0.0, 0.0, -1.0],
                ]
            )
            for axis, name in enumerate(("coordx", "coordy", "coordz")):
                dataset.createVariable(name, "f8", ("num_nodes",))[:] = coordinates[
                    :, axis
                ]
            self._write_names(
                dataset,
                "name_nod_var",
                "num_nod_var",
                ["Contact_ForceX", "Contact_ForceY", "Contact_ForceZ"],
            )
            force_x = np.zeros((2, 7), dtype=float)
            force_x[:, 0] = 10.0
            for index, values in enumerate(
                (force_x, np.zeros_like(force_x), np.zeros_like(force_x)), 1
            ):
                dataset.createVariable(
                    f"vals_nod_var{index}", "f8", ("time_step", "num_nodes")
                )[:] = values

        metadata = {
            "tag": "shared-node-force",
            "files": {"results": "results"},
            "config": {
                "materials": {"mug": {"parameters": {"Density": 2.0}}},
                "blocks": {
                    "mug": {
                        "name": "Mug Block",
                        "block_names": "block_2",
                        "material": "mug",
                    }
                },
                "analysis": {"blocks": ["mug"]},
            },
        }
        (directory / "metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )

    def test_volume_weighted_impact_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            self._write_case(case)
            report = analyze_impact_case(case)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["merge"]["unique_element_count"], 2)
        metrics = report["metrics"]
        self.assertTrue(math.isclose(metrics["mass_kg"], 8.0))
        self.assertTrue(math.isclose(metrics["peak_contact_force_n"], 40.0))
        self.assertTrue(
            math.isclose(metrics["contact_impulse_magnitude_n_s"], 40.0)
        )
        self.assertTrue(math.isclose(metrics["coefficient_of_restitution"], 0.5))
        self.assertTrue(math.isclose(metrics["damage_mean_final"], 0.65))
        self.assertTrue(
            math.isclose(metrics["damage_fraction_gt_0p5_final"], 0.75)
        )

    def test_partial_nemesis_shard_set_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            self._write_case(case)
            result = case / "results" / "case.e"
            result.rename(result.with_name("case.e.2.0"))
            report = analyze_impact_case(case)

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("Incomplete Nemesis" in message for message in report["errors"]),
            report,
        )

    def test_mixed_serial_and_nemesis_outputs_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            self._write_case(case)
            serial = case / "results" / "case.e"
            shutil.copy2(serial, serial.with_name("case.e.2.0"))
            report = analyze_impact_case(case)

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("mixed" in message.casefold() for message in report["errors"]),
            report,
        )

    def test_nemesis_without_global_element_map_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            self._write_case(case, include_element_map=False)
            serial = case / "results" / "case.e"
            first = serial.with_name("case.e.2.0")
            serial.rename(first)
            shutil.copy2(first, first.with_name("case.e.2.1"))
            report = analyze_impact_case(case)

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("elem_num_map" in message for message in report["errors"]),
            report,
        )

    def test_duplicate_global_elements_across_nemesis_shards_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            self._write_case(case)
            serial = case / "results" / "case.e"
            first = serial.with_name("case.e.2.0")
            serial.rename(first)
            shutil.copy2(first, first.with_name("case.e.2.1"))
            report = analyze_impact_case(case)

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("duplicate global element" in message for message in report["errors"]),
            report,
        )

    def test_single_saved_frame_preserves_time_item_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            self._write_case(case, times=[0.0])
            report = analyze_impact_case(case)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["metrics"]["frame_count"], 1)
        self.assertTrue(math.isclose(report["metrics"]["mass_kg"], 8.0))
        self.assertTrue(math.isclose(report["metrics"]["peak_contact_force_n"], 40.0))
        self.assertTrue(math.isclose(report["metrics"]["damage_mean_final"], 0.65))

    def test_missing_volume_does_not_report_dimensional_integrals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            self._write_case(case, include_volume=False)
            report = analyze_impact_case(case)

        self.assertEqual(report["status"], "pass")
        metrics = report["metrics"]
        for name in (
            "initial_volume_m3",
            "final_volume_m3",
            "mass_kg",
            "peak_contact_force_n",
            "contact_impulse_magnitude_n_s",
            "momentum_inferred_contact_impulse_magnitude_n_s",
            "maximum_momentum_residual_n_s",
        ):
            self.assertIsNone(metrics.get(name), name)
        series = report["series"]
        for name in (
            "total_volume_m3",
            "center_of_volume_m",
            "volume_weighted_velocity_m_s",
            "force_n",
            "impulse_n_s",
            "linear_momentum_kg_m_s",
            "momentum_inferred_contact_impulse_n_s",
            "momentum_residual_n_s",
        ):
            self.assertIsNone(series.get(name), name)
        self.assertIn("unit", report["merge"]["weighting"].casefold())

    def test_missing_volume_falls_back_to_complete_direct_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            self._write_case(
                case, include_volume=False, include_direct_force=True
            )
            report = analyze_impact_case(case)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["metrics"]["force_source"], "Contact_Force")
        self.assertTrue(
            math.isclose(report["metrics"]["peak_contact_force_n"], 20.0)
        )
        self.assertTrue(
            math.isclose(
                report["metrics"]["contact_impulse_magnitude_n_s"], 20.0
            )
        )
        self.assertIsNone(report["metrics"]["initial_volume_m3"])

    def test_nodal_direct_force_sums_unique_nodes_not_element_averages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            self._write_shared_tet_direct_force_case(case)
            report = analyze_impact_case(case)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["merge"]["force_aggregation_basis"], "nodes")
        self.assertEqual(
            report["merge"]["force_item_coverage"],
            {"observed": 7, "expected": 7},
        )
        self.assertTrue(math.isclose(report["metrics"]["peak_contact_force_n"], 10.0))

    def test_bare_case_validates_adjacent_run_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            self._write_case(case)
            (case / "metadata.json").unlink()
            result = case / "results" / "case.e"
            bare_result = case / "case.e"
            result.rename(bare_result)
            (case / "results").rmdir()
            (case / "run_metadata.json").write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "exit_code": 0,
                        "mpi_ranks": 1,
                        "result_files": ["case.e"],
                        "result_file_sizes": {"case.e": bare_result.stat().st_size + 1},
                    }
                ),
                encoding="utf-8",
            )
            report = analyze_impact_case(case)

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("result_file_sizes" in message for message in report["errors"]),
            report,
        )

    def test_metadata_damage_thresholds_are_used_when_api_omits_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            self._write_case(case)
            metadata_path = case / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["config"]["analysis"]["damage_thresholds"] = [0.25]
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            report = analyze_impact_case(case)

        self.assertIn("damage_fraction_gt_0p25_final", report["metrics"])
        self.assertNotIn("damage_fraction_gt_0p5_final", report["metrics"])

    def test_impact_and_damage_roles_are_analyzed_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            self._write_case(case, split_analysis_roles=True)
            report = analyze_impact_case(case)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["analysis_roles"]["impact_blocks"], ["projectile"])
        self.assertEqual(report["analysis_roles"]["damage_blocks"], ["target"])
        self.assertTrue(math.isclose(report["metrics"]["mass_kg"], 8.0))
        self.assertTrue(math.isclose(report["metrics"]["peak_contact_force_n"], 40.0))
        self.assertTrue(math.isclose(report["metrics"]["coefficient_of_restitution"], 0.5))
        self.assertTrue(math.isclose(report["metrics"]["damage_mean_final"], 0.0))

        damage = report["damage_analysis"]
        self.assertEqual(damage["status"], "pass")
        self.assertEqual(damage["selected_blocks"][0]["logical_id"], "target")
        self.assertTrue(math.isclose(damage["metrics"]["damage_mean_final"], 0.65))
        self.assertTrue(
            math.isclose(damage["metrics"]["damage_fraction_gt_0p5_final"], 0.75)
        )

    def test_convergence_gate(self) -> None:
        coarse = {"metrics": {"coefficient_of_restitution": 0.52}}
        fine = {"metrics": {"coefficient_of_restitution": 0.50}}
        report = compare_convergence_reports(
            coarse, fine, {"relative": {"restitution": 0.05}}
        )
        self.assertEqual(report["status"], "pass")

    def test_convergence_fails_when_a_source_report_failed(self) -> None:
        metrics = {"coefficient_of_restitution": 0.5}
        coarse = {"status": "fail", "metrics": metrics}
        fine = {"status": "pass", "metrics": metrics}
        report = compare_convergence_reports(
            coarse, fine, {"relative": {"restitution": 0.01}}
        )

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("coarse" in reason.casefold() for reason in report["block_reasons"]),
            report,
        )

    def test_convergence_fails_when_any_requested_metric_is_missing(self) -> None:
        coarse = {"status": "pass", "metrics": {"coefficient_of_restitution": 0.5}}
        fine = {"status": "pass", "metrics": {"coefficient_of_restitution": 0.5}}
        report = compare_convergence_reports(
            coarse,
            fine,
            {
                "relative": {
                    "restitution": 0.01,
                    "contact_impulse": 0.10,
                }
            },
        )

        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["comparisons"]["contact_impulse:relative"]["available"])
        self.assertTrue(
            any("contact_impulse" in reason for reason in report["block_reasons"]),
            report,
        )

    def test_convergence_relative_error_uses_fine_reference(self) -> None:
        coarse = {"status": "pass", "metrics": {"coefficient_of_restitution": 2.0}}
        fine = {"status": "pass", "metrics": {"coefficient_of_restitution": 1.0}}
        report = compare_convergence_reports(
            coarse, fine, {"relative": {"restitution": 0.75}}
        )

        self.assertEqual(report["status"], "fail")
        comparison = report["comparisons"]["restitution:relative"]
        self.assertTrue(math.isclose(comparison["relative_difference"], 1.0))

    def test_convergence_with_zero_fine_reference_is_strict_json(self) -> None:
        coarse = {"status": "pass", "metrics": {"coefficient_of_restitution": 1.0}}
        fine = {"status": "pass", "metrics": {"coefficient_of_restitution": 0.0}}
        report = compare_convergence_reports(
            coarse, fine, {"relative": {"restitution": 0.5}}
        )

        comparison = report["comparisons"]["restitution:relative"]
        self.assertTrue(math.isfinite(comparison["relative_difference"]))
        json.dumps(report, allow_nan=False)

    def test_convergence_damage_alias_prefers_nested_target_analysis(self) -> None:
        coarse = {
            "status": "pass",
            "metrics": {"damage_mean_final": 0.0},
            "damage_analysis": {
                "status": "pass",
                "metrics": {"damage_mean_final": 0.65},
            },
        }
        fine = {
            "status": "pass",
            "metrics": {"damage_mean_final": 0.0},
            "damage_analysis": {
                "status": "pass",
                "metrics": {"damage_mean_final": 0.50},
            },
        }
        report = compare_convergence_reports(
            coarse, fine, {"absolute": {"damage_mean": 0.10}}
        )

        self.assertEqual(report["status"], "fail")
        comparison = report["comparisons"]["damage_mean:absolute"]
        self.assertEqual(
            comparison["resolved_coarse_metric"],
            "damage_analysis.metrics.damage_mean_final",
        )
        self.assertEqual(
            comparison["resolved_fine_metric"],
            "damage_analysis.metrics.damage_mean_final",
        )
        self.assertTrue(math.isclose(comparison["absolute_difference"], 0.15))

    def test_convergence_rejects_mixed_volume_and_count_damage_weighting(self) -> None:
        coarse = {
            "status": "pass",
            "damage_analysis": {
                "metrics": {"damage_mean_final": 0.5}
            },
        }
        fine = {
            "status": "pass",
            "damage_analysis": {
                "metrics": {"damage_count_weighted_mean_final": 0.5}
            },
        }
        report = compare_convergence_reports(
            coarse, fine, {"absolute": {"damage_mean": 0.01}}
        )

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("weight" in reason for reason in report["block_reasons"]),
            report,
        )

    def test_convergence_resolves_dynamic_count_weighted_damage_fraction(self) -> None:
        coarse = {
            "status": "pass",
            "damage_analysis": {
                "metrics": {"damage_count_fraction_gt_0p2_final": 0.25}
            },
        }
        fine = {
            "status": "pass",
            "damage_analysis": {
                "metrics": {"damage_count_fraction_gt_0p2_final": 0.25}
            },
        }
        report = compare_convergence_reports(
            coarse,
            fine,
            {"absolute": {"damage_fraction_gt_0p2": 0.0}},
        )

        self.assertEqual(report["status"], "pass")
        comparison = report["comparisons"]["damage_fraction_gt_0p2:absolute"]
        self.assertEqual(
            comparison["resolved_coarse_metric"],
            "damage_analysis.metrics.damage_count_fraction_gt_0p2_final",
        )


if __name__ == "__main__":
    unittest.main()
