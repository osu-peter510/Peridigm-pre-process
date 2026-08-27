from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
RUN_CASE = REPOSITORY / "hpc" / "run_case.slurm"
SUBMIT_DATASET = REPOSITORY / "hpc" / "submit_dataset.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SubmitDatasetRankTests(unittest.TestCase):
    def _dataset(self, root: Path, ranks: list[int | None]) -> Path:
        cases = []
        for index, rank_count in enumerate(ranks):
            case = root / f"case_{index}"
            case.mkdir()
            runtime = {} if rank_count is None else {"mpi_ranks": rank_count}
            (case / "metadata.json").write_text(
                json.dumps({"config": {"runtime": runtime}}), encoding="utf-8"
            )
            cases.append(
                {
                    "status": "success",
                    "case_dir": case.name,
                    "metadata_file": f"{case.name}/metadata.json",
                }
            )
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps({"dataset_dir": ".", "cases": cases}), encoding="utf-8"
        )
        return manifest

    def _run(self, manifest: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                os.fspath(SUBMIT_DATASET),
                os.fspath(manifest),
                *arguments,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_infers_common_metadata_rank_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._dataset(Path(directory), [4, 4])
            completed = self._run(manifest)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("--ntasks=4", completed.stdout)
            self.assertIn("MPI_RANKS=4", completed.stdout)

    def test_rejects_explicit_or_cross_case_rank_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._dataset(root, [4])
            completed = self._run(manifest, "--ranks=3")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("conflicts with case metadata", completed.stderr)

            mixed = root / "mixed"
            mixed.mkdir()
            mixed_manifest = self._dataset(mixed, [4, 8])
            completed = self._run(mixed_manifest)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("inconsistent", completed.stderr)

    def test_missing_metadata_rank_requires_explicit_rank(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = self._dataset(Path(directory), [4, None])
            completed = self._run(manifest)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Cannot infer ranks", completed.stderr)
            completed = self._run(manifest, "--ranks=4")
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rejects_case_path_outside_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._dataset(root, [4])
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["cases"][0]["case_dir"] = "../outside"
            manifest.write_text(json.dumps(data), encoding="utf-8")

            completed = self._run(manifest)

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("escapes the dataset", completed.stderr)


class RunCaseDecompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.case = self.root / "case"
        self.case.mkdir()
        self.mesh = self.case / "case.g"
        self.mesh.write_bytes(b"base mesh\n")
        (self.case / "case.xml").write_text("<ParameterList/>\n", encoding="utf-8")
        (self.case / "results").mkdir()
        self._write_metadata()

        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.decomp_log = self.root / "decomp_calls.log"
        self._executable(
            "decomp",
            """#!/bin/bash
set -euo pipefail
[[ "$1" == "-p" ]]
ranks=$2
mesh=$3
width=${#ranks}
echo "$mesh:$ranks" >> "$DECOMP_CALL_LOG"
for ((rank=0; rank<ranks; rank++)); do
  suffix=$(printf "%0${width}d" "$rank")
  cp -- "$mesh" "$mesh.$ranks.$suffix"
done
""",
        )
        self._executable(
            "Peridigm",
            """#!/bin/bash
if [[ "${FAIL_PERIDIGM:-0}" == 1 ]]; then
  exit 17
fi
echo "fake Peridigm $1"
""",
        )
        self._executable(
            "mpiexec-fake",
            """#!/bin/bash
set -euo pipefail
[[ "$1" == "-np" ]]
shift 2
exec "$@"
""",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _executable(self, name: str, content: str) -> None:
        path = self.bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _write_metadata(self) -> None:
        xml = self.case / "case.xml"
        metadata = {
            "files": {"mesh": "case.g", "xml": "case.xml", "results": "results"},
            "sha256": {"mesh": _sha256(self.mesh), "xml": _sha256(xml)},
            "config": {"runtime": {"mpi_ranks": 2}},
        }
        (self.case / "metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )

    def _run(self, **extra_environment: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": os.fspath(self.bin) + os.pathsep + environment.get("PATH", ""),
                "MPI_RANKS": "2",
                "SLURM_NTASKS": "2",
                "PERIDIGM_BIN": os.fspath(self.bin / "Peridigm"),
                "DECOMP_BIN": os.fspath(self.bin / "decomp"),
                "MPI_LAUNCHER": os.fspath(self.bin / "mpiexec-fake"),
                "DECOMP_CALL_LOG": os.fspath(self.decomp_log),
            }
        )
        environment.update(extra_environment)
        return subprocess.run(
            ["bash", os.fspath(RUN_CASE), os.fspath(self.case)],
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

    def _clear_results(self) -> None:
        shutil.rmtree(self.case / "results")
        (self.case / "results").mkdir()

    def test_writes_bound_decomposition_and_run_sidecars(self) -> None:
        completed = self._run()
        self.assertEqual(completed.returncode, 0, completed.stderr)

        decomp = json.loads(
            (self.case / "case.g.decomp.2.json").read_text(encoding="utf-8")
        )
        self.assertEqual(decomp["base_mesh_sha256"], _sha256(self.mesh))
        self.assertEqual(decomp["mpi_ranks"], 2)
        self.assertEqual(decomp["shards"], ["case.g.2.0", "case.g.2.1"])

        run = json.loads(
            (self.case / "results" / "run_metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual(run["status"], "complete")
        self.assertEqual(run["mpi_ranks"], 2)
        self.assertEqual(run["slurm_ntasks"], 2)
        self.assertEqual(run["exit_code"], 0)
        self.assertEqual(run["result_files"], [])
        self.assertEqual(run["result_file_sizes"], {})
        self.assertEqual(run["mesh_sha256"], _sha256(self.mesh))
        self.assertEqual(
            run["xml_sha256"], _sha256(self.case / "case.xml")
        )

    def test_hidden_result_file_cannot_be_bypassed_by_environment(self) -> None:
        hidden = self.case / "results" / ".partial-output"
        hidden.write_text("stale\n", encoding="utf-8")

        completed = self._run(ALLOW_RESTART="1")

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Refusing to overwrite nonempty results", completed.stderr)
        self.assertEqual(hidden.read_text(encoding="utf-8"), "stale\n")
        self.assertFalse(self.decomp_log.exists())

    def test_rejects_xml_that_differs_from_metadata_hash(self) -> None:
        (self.case / "case.xml").write_text(
            "<ParameterList name=\"changed\"/>\n", encoding="utf-8"
        )

        completed = self._run()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("XML SHA256 differs", completed.stderr)
        self.assertFalse(self.decomp_log.exists())

    def test_rejects_metadata_result_path_outside_case(self) -> None:
        metadata_path = self.case / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["files"]["results"] = "../outside-results"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        completed = self._run()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("escapes the case directory", completed.stderr)
        self.assertFalse((self.root / "outside-results").exists())
        self.assertFalse(self.decomp_log.exists())

    def test_rejects_partial_shards_without_deleting_or_regenerating(self) -> None:
        first = self._run()
        self.assertEqual(first.returncode, 0, first.stderr)
        self._clear_results()
        (self.case / "case.g.2.1").unlink()

        completed = self._run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Invalid 2-rank decomposition", completed.stderr)
        self.assertIn("No shards were deleted", completed.stderr)
        self.assertTrue((self.case / "case.g.2.0").is_file())
        self.assertFalse((self.case / "case.g.2.1").exists())
        self.assertEqual(self.decomp_log.read_text(encoding="utf-8").count("\n"), 1)

    def test_rejects_shards_bound_to_previous_mesh_hash(self) -> None:
        first = self._run()
        self.assertEqual(first.returncode, 0, first.stderr)
        self._clear_results()
        self.mesh.write_bytes(b"changed base mesh\n")
        self._write_metadata()

        completed = self._run()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("base mesh SHA256", completed.stderr)
        self.assertIn("No shards were deleted", completed.stderr)
        self.assertTrue((self.case / "case.g.2.0").is_file())
        self.assertTrue((self.case / "case.g.2.1").is_file())

    def test_failed_launch_records_actual_rank_count_and_exit_code(self) -> None:
        completed = self._run(FAIL_PERIDIGM="1")
        self.assertEqual(completed.returncode, 17, completed.stderr)
        run = json.loads(
            (self.case / "results" / "run_metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["mpi_ranks"], 2)
        self.assertEqual(run["exit_code"], 17)


if __name__ == "__main__":
    unittest.main()
