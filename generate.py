#!/usr/bin/env python3
"""
generate.py — Unified Peridigm preprocessing script.

Generates Cubit mesh (.g) + Peridigm XML (.xml) pairs for all scenario types.
Output is always written to: output/<scenario>/<tag>.g + <tag>.xml

Scenarios
---------
  Dynamic impact (brittle fragmentation):
    freefall_vase   — vase dropped from ~1.5-2.5 m onto a steel floor
    freefall_mug    — mug  dropped from ~1.5-2.5 m onto a steel floor
    bullet_vase     — 400 m/s bullet impacts a vase sitting on a floor
    bullet_mug      — 400 m/s bullet impacts a mug  sitting on a floor
    bullet_vase_nf  — 400 m/s bullet impacts a floating vase (no floor)
    bullet_mug_nf   — 400 m/s bullet impacts a floating mug  (no floor)

    ball_plate_nf   - random steel ball impacts a floating ceramic plate

  Kalthoff-Winkler fracture benchmark:
    kw_fracture     — notched steel board + cylindrical projectile (deterministic)

  Cloth fall (soft-body):
    cloth_fall      — randomised cloth falling over 3 bricks + 1 sphere

Usage
-----
  python generate.py <scenario> <n_scenes> [options]

Examples
--------
  python generate.py freefall_vase  50
  python generate.py bullet_mug     20 --base-seed 100 --max-nodes 30000
  python generate.py kw_fracture     1
  python generate.py cloth_fall    100 --max-nodes 20000
"""

import sys
import json
import re
import importlib.util
from pathlib import Path

# ------------------------------------------------------------------ #
#  EDIT THIS to match your Coreform Cubit installation                #
# ------------------------------------------------------------------ #
CUBIT_PATH = r"E:\Program Files\Coreform Cubit 2025.12\bin"

REPO_ROOT   = Path(__file__).parent.resolve()
OUTPUT_ROOT = REPO_ROOT / "output"

def _load_module(name: str, path: Path):
    """Import a Python file by absolute path, registering it under a unique name."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Cubit must be on sys.path and initialised before sub-module imports.
sys.path.insert(0, CUBIT_PATH)
import cubit
cubit.init(['cubit', '-nojournal'])

# Sub-module directories
sys.path.insert(0, str(REPO_ROOT / "dynamic-impact-generation"))
sys.path.insert(0, str(REPO_ROOT / "cloth-fall-generation"))
sys.path.insert(0, str(REPO_ROOT / "kw-board-impact"))

# Dynamic-impact imports (geometry primitives + XML generators + scene builders)
from geometry_builder import (
    make_vase_parametric, make_mug_parametric, make_bullet, make_floor,
)
from peridigm_xml import (
    generate_vase_drop_peridigm_xml,
    generate_mug_drop_peridigm_xml,
    generate_bullet_vase_peridigm_xml,
    generate_bullet_mug_peridigm_xml,
    generate_bullet_vase_nf_peridigm_xml,
    generate_bullet_mug_nf_peridigm_xml,
    generate_ball_plate_peridigm_xml,
)
_di = _load_module(
    "di_generate_scenes",
    REPO_ROOT / "dynamic-impact-generation" / "generate_scenes.py",
)

# Cloth-fall import (generator class only; no naming conflict with _di)
from cloth_scene_generator import ClothFallSceneGenerator

# KW-fracture import
from block_generation import build_kw_scene


def echo(msg: str) -> None:
    sys.__stdout__.write(str(msg) + "\n")
    sys.__stdout__.flush()


# ------------------------------------------------------------------ #
#  Dynamic impact                                                     #
# ------------------------------------------------------------------ #

_DI_SCENARIOS = {
    "freefall_vase":  (_di.build_vase_drop_scene,      generate_vase_drop_peridigm_xml),
    "freefall_mug":   (_di.build_mug_drop_scene,       generate_mug_drop_peridigm_xml),
    "bullet_vase":    (_di.build_bullet_vase_scene,    generate_bullet_vase_peridigm_xml),
    "bullet_mug":     (_di.build_bullet_mug_scene,     generate_bullet_mug_peridigm_xml),
    "bullet_vase_nf": (_di.build_bullet_vase_nf_scene, generate_bullet_vase_nf_peridigm_xml),
    "bullet_mug_nf":  (_di.build_bullet_mug_nf_scene,  generate_bullet_mug_nf_peridigm_xml),
    "ball_plate":     (_di.build_ball_plate_scene,     generate_ball_plate_peridigm_xml),
    "ball_plate_nf":  (_di.build_ball_plate_scene,     generate_ball_plate_peridigm_xml),
}


def run_dynamic_impact(scenario: str, n_scenes: int, base_seed: int, max_nodes: int) -> None:
    build_fn, xml_fn = _DI_SCENARIOS[scenario]
    out_dir = OUTPUT_ROOT / scenario
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for i in range(n_scenes):
        seed = base_seed + i
        tag  = f"{scenario}_{seed:04d}"
        echo(f"\n=== [{i+1}/{n_scenes}] {tag} ===")
        try:
            info      = build_fn(seed=seed, max_nodes=max_nodes)
            mesh_path = out_dir / f"{tag}.g"
            xml_path  = out_dir / f"{tag}.xml"
            cubit.cmd(f'export mesh "{mesh_path}" overwrite')
            xml_fn(str(mesh_path.name), info, str(xml_path))
            manifest_entry = {
                "index": i, "tag": tag, "scenario": scenario, "seed": seed,
                "status": "success",
                "mesh_file": str(mesh_path), "xml_file": str(xml_path),
            }
            if scenario in {"ball_plate", "ball_plate_nf"}:
                manifest_entry["parameters"] = {
                    key: value
                    for key, value in info.items()
                    if not key.endswith("_vol")
                }
            manifest.append(manifest_entry)
            echo(f"  -> {mesh_path.name}  {xml_path.name}")
        except Exception as exc:
            echo(f"  ERROR: {exc}")
            manifest.append({
                "index": i, "tag": tag, "scenario": scenario, "seed": seed,
                "status": "error", "error": str(exc),
            })

    _write_manifest(out_dir, manifest, n_scenes)


# ------------------------------------------------------------------ #
#  Kalthoff-Winkler fracture                                          #
# ------------------------------------------------------------------ #

def run_kw_fracture(scenario: str, n_scenes: int, base_seed: int, max_nodes: int) -> None:
    """KW board is deterministic; each scene is geometrically identical."""
    out_dir = OUTPUT_ROOT / "kw_fracture"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for i in range(n_scenes):
        seed = base_seed + i
        tag  = f"kw_fracture_{seed:04d}"
        echo(f"\n=== [{i+1}/{n_scenes}] {tag} ===")
        try:
            result = build_kw_scene(out_dir, tag)
            manifest.append({
                "index": i, "tag": tag, "scenario": "kw_fracture", "seed": seed,
                "status": "success",
                "mesh_file": result["mesh_file"], "xml_file": result["xml_file"],
            })
        except Exception as exc:
            echo(f"  ERROR: {exc}")
            manifest.append({
                "index": i, "tag": tag, "scenario": "kw_fracture", "seed": seed,
                "status": "error", "error": str(exc),
            })

    _write_manifest(out_dir, manifest, n_scenes)


# ------------------------------------------------------------------ #
#  Cloth fall                                                         #
# ------------------------------------------------------------------ #

def run_cloth_fall(scenario: str, n_scenes: int, base_seed: int, max_nodes: int) -> None:
    out_dir = OUTPUT_ROOT / "cloth_fall"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for i in range(1, n_scenes + 1):
        seed = base_seed + i
        tag  = f"cloth_fall_{seed:04d}"
        echo(f"\n=== [{i}/{n_scenes}] {tag} ===")
        try:
            g_path   = out_dir / f"{tag}.g"
            xml_path = out_dir / f"{tag}.xml"

            gen = ClothFallSceneGenerator(
                max_nodes=max_nodes,
                random_seed=seed,
                mesh_size_multiplier=1.1,
                clearance=5.0,
            )
            gen.generate_scene(str(g_path), str(xml_path))

            # Patch mesh filename and output basename in the generated XML
            txt = xml_path.read_text(encoding="utf-8")
            txt = re.sub(
                r'(<Parameter\s+name="Input Mesh File"\s+type="string"\s+value=")[^"]+(")',
                rf'\1{tag}.g\2', txt, count=1,
            )
            txt = re.sub(
                r'(<Parameter\s+name="Output Filename"\s+type="string"\s+value=")[^"]+(")',
                rf'\1{tag}\2', txt, count=1,
            )
            xml_path.write_text(txt, encoding="utf-8")

            manifest.append({
                "index": i, "tag": tag, "scenario": "cloth_fall", "seed": seed,
                "status": "success",
                "mesh_file": str(g_path), "xml_file": str(xml_path),
            })
            echo(f"  -> {tag}.g  {tag}.xml")
        except Exception as exc:
            echo(f"  ERROR: {exc}")
            manifest.append({
                "index": i, "tag": tag, "scenario": "cloth_fall", "seed": seed,
                "status": "error", "error": str(exc),
            })

    _write_manifest(out_dir, manifest, n_scenes)


# ------------------------------------------------------------------ #
#  Shared helper                                                      #
# ------------------------------------------------------------------ #

def _write_manifest(out_dir: Path, manifest: list, n_scenes: int) -> None:
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    ok = sum(1 for e in manifest if e["status"] == "success")
    echo(f"\nDone. {ok}/{n_scenes} succeeded. Manifest: {manifest_path}")


# ------------------------------------------------------------------ #
#  Dispatch table                                                     #
# ------------------------------------------------------------------ #

ALL_SCENARIOS = {
    "freefall_vase":  run_dynamic_impact,
    "freefall_mug":   run_dynamic_impact,
    "bullet_vase":    run_dynamic_impact,
    "bullet_mug":     run_dynamic_impact,
    "bullet_vase_nf": run_dynamic_impact,
    "bullet_mug_nf":  run_dynamic_impact,
    "ball_plate":     run_dynamic_impact,
    "ball_plate_nf":  run_dynamic_impact,
    "kw_fracture":    run_kw_fracture,
    "cloth_fall":     run_cloth_fall,
}


# ------------------------------------------------------------------ #
#  CLI                                                                #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        prog="generate.py",
        description="Unified Peridigm mesh+XML generator for all scenarios.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
scenarios:
  freefall_vase   — vase dropped from ~1.5-2.5 m onto a steel floor
  freefall_mug    — mug  dropped from ~1.5-2.5 m onto a steel floor
  bullet_vase     — 400 m/s bullet impacts a vase sitting on a floor
  bullet_mug      — 400 m/s bullet impacts a mug  sitting on a floor
  bullet_vase_nf  — 400 m/s bullet impacts a floating vase (no floor)
  bullet_mug_nf   — 400 m/s bullet impacts a floating mug  (no floor)
  ball_plate_nf   — random steel ball impacts a floating ceramic plate
  ball_plate      — backward-compatible alias for ball_plate_nf
  kw_fracture     — Kalthoff-Winkler notched board + cylinder (deterministic)
  cloth_fall      — randomised cloth falling over 3 bricks + 1 sphere

output:
  All files are written to: output/<scenario>/<tag>.g + <tag>.xml

examples:
  python generate.py freefall_vase  50
  python generate.py freefall_mug   50 --base-seed 100
  python generate.py bullet_vase    20 --max-nodes 30000
  python generate.py bullet_mug     20 --base-seed 100 --max-nodes 30000
  python generate.py bullet_vase_nf 30
  python generate.py bullet_mug_nf  30
  python generate.py ball_plate_nf  50 --max-nodes 30000
  python generate.py kw_fracture     1
  python generate.py cloth_fall    100 --max-nodes 20000
""",
    )
    parser.add_argument(
        "scenario",
        choices=list(ALL_SCENARIOS),
        help="Which scenario to generate.",
    )
    parser.add_argument(
        "n_scenes",
        type=int,
        help="Number of scenes to generate.",
    )
    parser.add_argument(
        "--base-seed",
        type=int,
        default=0,
        metavar="N",
        help="Starting random seed (scene i uses seed base_seed+i). Default: 0.",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=50000,
        metavar="N",
        help="Element budget; mesh is coarsened if exceeded. Default: 50000.",
    )

    args = parser.parse_args()
    ALL_SCENARIOS[args.scenario](
        scenario=args.scenario,
        n_scenes=args.n_scenes,
        base_seed=args.base_seed,
        max_nodes=args.max_nodes,
    )
