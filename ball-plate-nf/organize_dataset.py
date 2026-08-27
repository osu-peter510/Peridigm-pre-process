#!/usr/bin/env python3
"""Organize a ball-plate dataset into shared geometry and XML variants."""

import argparse
import json
import sys
from pathlib import Path, PurePosixPath


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "dynamic-impact-generation"))

from peridigm_xml import generate_ball_plate_peridigm_xml


VARIANTS = {
    "critical_stretch_0p0005": ("critical-stretch-0p0005", 0.0005),
    "critical_stretch_0p0015": ("critical-stretch-0p0015", 0.0015),
}


def organize_dataset(dataset_dir: str | Path) -> dict:
    """Move meshes and metadata, then generate both XML variants."""
    dataset_dir = Path(dataset_dir).resolve()
    metadata_files = sorted(dataset_dir.glob("ball_plate_nf_*.json"))
    if not metadata_files:
        raise RuntimeError(f"No scene metadata found in {dataset_dir}")

    metadata_by_seed = {}
    for metadata_path in metadata_files:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        seed = int(data["seed"])
        scene_id = f"{seed:04d}"
        tag = f"ball_plate_nf_{scene_id}"

        geometry_dir = dataset_dir / "geometry" / scene_id
        geometry_dir.mkdir(parents=True, exist_ok=True)
        mesh_path = geometry_dir / f"{tag}.g"
        organized_metadata_path = geometry_dir / f"{tag}.json"
        flat_mesh_path = dataset_dir / f"{tag}.g"
        if flat_mesh_path.exists():
            if mesh_path.exists():
                raise RuntimeError(
                    f"Both flat and organized meshes exist for scene {scene_id}"
                )
            flat_mesh_path.replace(mesh_path)
        if not mesh_path.is_file():
            raise RuntimeError(f"Missing mesh for scene {scene_id}: {mesh_path}")

        old_xml_path = dataset_dir / f"{tag}.xml"
        default_xml_dir = dataset_dir / VARIANTS["critical_stretch_0p0005"][0] / scene_id
        default_xml_dir.mkdir(parents=True, exist_ok=True)
        default_xml_path = default_xml_dir / f"{tag}.xml"
        if old_xml_path.exists():
            if default_xml_path.exists():
                raise RuntimeError(
                    f"Both flat and organized XML files exist for scene {scene_id}"
                )
            old_xml_path.replace(default_xml_path)

        mesh_relative_to_xml = f"../../geometry/{scene_id}/{tag}.g"
        xml_files = {}
        for variant_name, (folder_name, critical_stretch) in VARIANTS.items():
            xml_dir = dataset_dir / folder_name / scene_id
            xml_dir.mkdir(parents=True, exist_ok=True)
            xml_path = xml_dir / f"{tag}.xml"
            generate_ball_plate_peridigm_xml(
                mesh_relative_to_xml,
                data,
                str(xml_path),
                critical_stretch=critical_stretch,
            )
            xml_files[variant_name] = xml_path

        mesh_relative_to_root = PurePosixPath("geometry") / scene_id / mesh_path.name
        data["mesh_file"] = str(mesh_relative_to_root)
        data["xml_file"] = str(
            PurePosixPath(VARIANTS["critical_stretch_0p0005"][0])
            / scene_id
            / default_xml_path.name
        )
        data["xml_files"] = {
            variant_name: str(
                PurePosixPath(VARIANTS[variant_name][0])
                / scene_id
                / xml_path.name
            )
            for variant_name, xml_path in xml_files.items()
        }
        metadata_path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        metadata_path.replace(organized_metadata_path)
        metadata_by_seed[seed] = data

    manifest_path = dataset_dir / "manifest.json"
    manifest = []
    for index, seed in enumerate(sorted(metadata_by_seed)):
        data = metadata_by_seed[seed]
        manifest.append(
            {
                "index": index,
                "tag": data["tag"],
                "seed": seed,
                "status": "success",
                "mesh_file": str(
                    dataset_dir / PurePosixPath(data["mesh_file"])
                ),
                "xml_file": str(dataset_dir / PurePosixPath(data["xml_file"])),
                "xml_files": {
                name: str(dataset_dir / PurePosixPath(path))
                for name, path in data["xml_files"].items()
                },
                "metadata_file": str(
                    dataset_dir
                    / "geometry"
                    / f"{seed:04d}"
                    / f"ball_plate_nf_{seed:04d}.json"
                ),
                "parameters": data,
            }
        )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {
        "scene_count": len(metadata_files),
        "xml_count": len(metadata_files) * len(VARIANTS),
        "dataset_dir": str(dataset_dir),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Organize shared geometry and critical-stretch XML variants."
    )
    parser.add_argument("dataset_dir", help="Existing ball-plate dataset directory")
    args = parser.parse_args()
    result = organize_dataset(args.dataset_dir)
    print(
        f"Organized {result['scene_count']} scenes and "
        f"generated {result['xml_count']} XML files under {result['dataset_dir']}"
    )
