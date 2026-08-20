#!/usr/bin/env python3
"""Generate a randomized ball-plate impact scene with no floor.

The plate is a free-floating brittle ceramic disk whose top face is at z=0.
A steel ball starts 80 mm back along its randomized trajectory and is aimed at
a randomized point on the plate.  Cubit geometry is built in millimeters and
scaled to meters before the Exodus mesh is exported.

Randomized parameters (deterministic for a given seed):
  * plate diameter:       100-150 mm
  * plate thickness:      2-4 mm
  * ball diameter:        10-15 mm
  * shooting direction:   0-30 degrees from vertical, random azimuth
  * contact position:     uniformly sampled in the usable central 60% area
  * initial speed:        20-100 m/s

Outputs:
  <tag>.g       Cubit/Exodus mesh
  <tag>.xml     Peridigm input
  <tag>.json    sampled parameters and derived initial conditions

Usage:
  python generate_scene.py --n-scenes 1000 --seed 0 --output-dir output
"""

import argparse
import json
import os
import sys
from pathlib import Path


# ------------------------------------------------------------------ #
DEFAULT_CUBIT_PATH = r"D:\Program Files\Coreform Cubit 2025.8\bin"
# ------------------------------------------------------------------ #

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DYNAMIC_IMPACT_DIR = REPO_ROOT / "dynamic-impact-generation"

def echo(message: str) -> None:
    sys.__stdout__.write(str(message) + "\n")
    sys.__stdout__.flush()


def _load_dependencies(cubit_path: str | Path | None = None):
    """Load Cubit and the shared no-floor scene implementation lazily."""
    configured_path = cubit_path or os.environ.get("CUBIT_PATH") or DEFAULT_CUBIT_PATH
    sys.path.insert(0, str(configured_path))
    sys.path.insert(0, str(DYNAMIC_IMPACT_DIR))

    try:
        import cubit
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Could not import the Coreform Cubit Python module. "
            "Pass --cubit-path, set the CUBIT_PATH environment variable, "
            f"or update DEFAULT_CUBIT_PATH (currently {configured_path!s})."
        ) from exc

    from generate_scenes import build_ball_plate_scene
    from peridigm_xml import generate_ball_plate_peridigm_xml

    return cubit, build_ball_plate_scene, generate_ball_plate_peridigm_xml


def build_scene(
    out_dir: str | Path = ".",
    tag: str | None = None,
    seed: int = 42,
    max_nodes: int = 50000,
    cubit_path: str | Path | None = None,
    _dependencies=None,
) -> dict:
    """Build, mesh, and export one reproducible no-floor ball-plate scene."""
    dependencies = _dependencies or _load_dependencies(cubit_path)
    cubit, build_ball_plate_scene, generate_ball_plate_peridigm_xml = dependencies
    if _dependencies is None:
        cubit.init(["cubit", "-nojournal"])

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = tag or f"ball_plate_nf_{seed:04d}"

    info = build_ball_plate_scene(seed=seed, max_nodes=max_nodes)

    mesh_path = out_dir / f"{tag}.g"
    xml_path = out_dir / f"{tag}.xml"
    metadata_path = out_dir / f"{tag}.json"

    cubit.cmd(f'export mesh "{mesh_path}" overwrite')
    generate_ball_plate_peridigm_xml(mesh_path.name, info, str(xml_path))

    # Cubit volume IDs are run-local implementation details, not dataset
    # parameters, so omit them from the persistent metadata.
    metadata = {
        "scenario": "ball_plate_nf",
        "tag": tag,
        "mesh_file": mesh_path.name,
        "xml_file": xml_path.name,
        **{key: value for key, value in info.items() if not key.endswith("_vol")},
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    echo(f"  mesh     -> {mesh_path}")
    echo(f"  xml      -> {xml_path}")
    echo(f"  metadata -> {metadata_path}")

    return {
        "mesh_file": str(mesh_path),
        "xml_file": str(xml_path),
        "metadata_file": str(metadata_path),
        "parameters": metadata,
    }


def build_scenes(
    n_scenes: int,
    out_dir: str | Path,
    base_seed: int = 0,
    max_nodes: int = 50000,
    cubit_path: str | Path | None = None,
    tag: str | None = None,
) -> dict:
    """Generate a batch and persist progress to ``manifest.json``."""
    if n_scenes <= 0:
        raise ValueError("n_scenes must be positive")

    dependencies = _load_dependencies(cubit_path)
    cubit = dependencies[0]
    cubit.init(["cubit", "-nojournal"])

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest = []

    for index in range(n_scenes):
        seed = base_seed + index
        scene_tag = tag if n_scenes == 1 and tag else f"{tag or 'ball_plate_nf'}_{seed:04d}"
        echo(f"\n=== [{index + 1}/{n_scenes}] {scene_tag} ===")

        try:
            result = build_scene(
                out_dir=out_dir,
                tag=scene_tag,
                seed=seed,
                max_nodes=max_nodes,
                _dependencies=dependencies,
            )
            manifest.append({
                "index": index,
                "tag": scene_tag,
                "seed": seed,
                "status": "success",
                "mesh_file": result["mesh_file"],
                "xml_file": result["xml_file"],
                "metadata_file": result["metadata_file"],
                "parameters": result["parameters"],
            })
        except Exception as exc:
            echo(f"  ERROR: {exc}")
            manifest.append({
                "index": index,
                "tag": scene_tag,
                "seed": seed,
                "status": "error",
                "error": str(exc),
            })

        # Write after every scene so a long run retains completed progress.
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    succeeded = sum(entry["status"] == "success" for entry in manifest)
    echo(f"\nDone. {succeeded}/{n_scenes} succeeded. Manifest: {manifest_path}")
    return {
        "manifest_file": str(manifest_path),
        "succeeded": succeeded,
        "attempted": n_scenes,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate randomized ball-plate scenes with no floor.",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="One-scene tag or batch filename prefix (default: ball_plate_nf)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Base random seed; scene i uses seed + i (default: 0)",
    )
    parser.add_argument(
        "--n-scenes",
        type=int,
        default=1,
        help="Number of scenes to generate (default: 1)",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=50000,
        help="Element budget used for adaptive mesh coarsening (default: 50000)",
    )
    parser.add_argument(
        "--cubit-path",
        default=None,
        help="Cubit bin directory (overrides CUBIT_PATH and the built-in default)",
    )
    args = parser.parse_args()

    if args.max_nodes <= 0:
        parser.error("--max-nodes must be positive")
    if args.n_scenes <= 0:
        parser.error("--n-scenes must be positive")

    build_scenes(
        n_scenes=args.n_scenes,
        out_dir=args.output_dir,
        base_seed=args.seed,
        max_nodes=args.max_nodes,
        cubit_path=args.cubit_path,
        tag=args.tag,
    )
