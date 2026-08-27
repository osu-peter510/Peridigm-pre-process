from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "generate.py", *arguments],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )

    def test_help_does_not_require_mesher(self) -> None:
        completed = self.run_cli("--help")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("validate-config", completed.stdout)

    def test_list(self) -> None:
        completed = self.run_cli("list")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.splitlines(),
            ["ball_plate", "kw_fracture", "mug_fall", "cloth_fall"],
        )

    def test_validate_config(self) -> None:
        completed = self.run_cli("validate-config", "configs/mug_fall.yaml")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('"scenario": "mug_fall"', completed.stdout)


if __name__ == "__main__":
    unittest.main()
