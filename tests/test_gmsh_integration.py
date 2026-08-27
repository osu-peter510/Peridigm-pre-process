from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("RUN_GMSH_INTEGRATION") == "1",
    "set RUN_GMSH_INTEGRATION=1 to generate real Exodus meshes",
)
class GmshIntegrationTests(unittest.TestCase):
    def test_primary_scenarios_generate_and_validate(self) -> None:
        for scenario in ("ball_plate", "kw_fracture", "mug_fall", "cloth_fall"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as output:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "generate.py",
                        "generate",
                        f"configs/{scenario}.yaml",
                        "--set",
                        f"run.output_root={output}",
                    ],
                    cwd=ROOT,
                    check=False,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                validated = subprocess.run(
                    [sys.executable, "generate.py", "validate", f"{output}/{scenario}"],
                    cwd=ROOT,
                    check=False,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(validated.returncode, 0, validated.stderr)


if __name__ == "__main__":
    unittest.main()
