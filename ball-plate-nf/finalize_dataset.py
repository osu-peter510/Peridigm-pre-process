#!/usr/bin/env python3
"""Validate a completed ball/plate dataset and write one final manifest."""

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from generate_scene import (
    MAX_IMPACT_TIME_S,
    MIN_IMPACT_TIME_S,
    XML_VARIANTS,
    validate_exodus,
)


def _parameter_value(root: ET.Element, name: str) -> str:
    matches = [
        parameter.get("value")
        for parameter in root.iter("Parameter")
        if parameter.get("name") == name
    ]
    if len(matches) != 1 or matches[0] is None:
        raise RuntimeError(f"Expected one XML parameter named {name!r}; got {matches}")
    return matches[0]


def _validate_xml(
    xml_path: Path,
    expected_mesh_path: str,
    expected_critical_stretch: float,
) -> float:
    root = ET.parse(xml_path).getroot()
    mesh_path = _parameter_value(root, "Input Mesh File")
    critical_stretch = float(_parameter_value(root, "Critical Stretch"))
    final_time = float(_parameter_value(root, "Final Time"))

    if mesh_path != expected_mesh_path:
        raise RuntimeError(
            f"Unexpected mesh path in {xml_path}: {mesh_path!r}"
        )
    if not math.isclose(
        critical_stretch,
        expected_critical_stretch,
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise RuntimeError(
            f"Unexpected critical stretch in {xml_path}: {critical_stretch}"
        )
    return final_time


def finalize_dataset(
    dataset_dir: str | Path,
    expected_scenes: int = 1000,
    max_elements: int = 12000,
    max_contact_activation_time_s: float = 3.0e-4,
) -> dict:
    dataset_dir = Path(dataset_dir).resolve()
    manifest = []
    errors = []
    element_counts = []
    node_counts = []
    contact_activation_times = []
    geometric_impact_times = []
    final_times = set()

    for seed in range(expected_scenes):
        scene_id = f"{seed:04d}"
        tag = f"ball_plate_nf_{scene_id}"
        geometry_dir = dataset_dir / "geometry" / scene_id
        mesh_path = geometry_dir / f"{tag}.g"
        metadata_path = geometry_dir / f"{tag}.json"
        xml_paths = {
            variant_name: dataset_dir / folder_name / scene_id / f"{tag}.xml"
            for variant_name, (folder_name, _) in XML_VARIANTS.items()
        }

        try:
            required_paths = [mesh_path, metadata_path, *xml_paths.values()]
            missing = [str(path) for path in required_paths if not path.is_file()]
            if missing:
                raise RuntimeError(f"Missing required files: {missing}")

            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("seed") != seed:
                raise RuntimeError(f"Metadata seed is {metadata.get('seed')}, expected {seed}")
            if metadata.get("tag") != tag:
                raise RuntimeError(f"Metadata tag is {metadata.get('tag')!r}, expected {tag!r}")
            if metadata.get("mesh_backend") != "gmsh-meshio":
                raise RuntimeError(
                    f"Unexpected mesh backend: {metadata.get('mesh_backend')!r}"
                )

            element_count = int(metadata.get("element_count", 0))
            node_count = int(metadata.get("node_count", 0))
            contact_time = float(
                metadata.get("predicted_contact_activation_time_s", 0.0)
            )
            geometric_time = float(
                metadata.get("predicted_geometric_impact_time_s", 0.0)
            )
            if not 0 < element_count <= max_elements:
                raise RuntimeError(
                    f"element_count={element_count} exceeds limit {max_elements}"
                )
            if not (
                MIN_IMPACT_TIME_S
                <= contact_time
                <= min(MAX_IMPACT_TIME_S, max_contact_activation_time_s)
            ):
                raise RuntimeError(
                    f"contact activation time {contact_time} is outside the allowed range"
                )
            if not (
                float(metadata.get("initial_surface_gap_mm", 0.0))
                > float(metadata.get("search_radius_mm", float("inf")))
            ):
                raise RuntimeError("Initial surface gap is not outside the search radius")

            mesh_relative = f"geometry/{scene_id}/{tag}.g"
            expected_mesh_from_xml = f"../../{mesh_relative}"
            if metadata.get("mesh_file") != mesh_relative:
                raise RuntimeError(
                    f"Unexpected metadata mesh path: {metadata.get('mesh_file')!r}"
                )

            validation = validate_exodus(mesh_path)
            if validation["element_count"] != element_count:
                raise RuntimeError(
                    "Exodus element count does not match scene metadata"
                )
            if validation["node_count"] != node_count:
                raise RuntimeError("Exodus node count does not match scene metadata")

            xml_relatives = {}
            for variant_name, (folder_name, critical_stretch) in XML_VARIANTS.items():
                xml_path = xml_paths[variant_name]
                final_times.add(
                    _validate_xml(
                        xml_path,
                        expected_mesh_from_xml,
                        critical_stretch,
                    )
                )
                xml_relatives[variant_name] = (
                    f"{folder_name}/{scene_id}/{tag}.xml"
                )
                if metadata.get("xml_files", {}).get(variant_name) != xml_relatives[variant_name]:
                    raise RuntimeError(
                        f"Unexpected metadata XML path for {variant_name}"
                    )

            manifest.append(
                {
                    "index": seed,
                    "tag": tag,
                    "seed": seed,
                    "status": "success",
                    "mesh_file": str(mesh_path),
                    "xml_file": str(xml_paths["critical_stretch_0p0005"]),
                    "xml_files": {
                        name: str(path) for name, path in xml_paths.items()
                    },
                    "metadata_file": str(metadata_path),
                    "parameters": metadata,
                }
            )
            element_counts.append(element_count)
            node_counts.append(node_count)
            contact_activation_times.append(contact_time)
            geometric_impact_times.append(geometric_time)
        except Exception as exc:
            errors.append(f"seed {seed}: {exc}")

        if (seed + 1) % 50 == 0:
            print(
                f"Validated {seed + 1}/{expected_scenes}; errors={len(errors)}",
                flush=True,
            )

    actual_counts = {
        "geometry": len(list(dataset_dir.glob("geometry/*/*.g"))),
        "metadata": len(list(dataset_dir.glob("geometry/*/*.json"))),
        "xml_critical_stretch_0p0005": len(
            list(dataset_dir.glob("critical-stretch-0p0005/*/*.xml"))
        ),
        "xml_critical_stretch_0p0015": len(
            list(dataset_dir.glob("critical-stretch-0p0015/*/*.xml"))
        ),
        "root_scene_metadata": len(list(dataset_dir.glob("ball_plate_nf_*.json"))),
    }
    expected_counts = {
        "geometry": expected_scenes,
        "metadata": expected_scenes,
        "xml_critical_stretch_0p0005": expected_scenes,
        "xml_critical_stretch_0p0015": expected_scenes,
        "root_scene_metadata": 0,
    }
    if actual_counts != expected_counts:
        errors.append(
            f"Dataset file counts differ: actual={actual_counts}, expected={expected_counts}"
        )

    if errors:
        for error in errors[:100]:
            print(error, file=sys.stderr)
        raise RuntimeError(f"Dataset validation failed with {len(errors)} errors")

    summary = {
        "status": "success",
        "scene_count": len(manifest),
        "geometry_count": actual_counts["geometry"],
        "metadata_count": actual_counts["metadata"],
        "xml_count": (
            actual_counts["xml_critical_stretch_0p0005"]
            + actual_counts["xml_critical_stretch_0p0015"]
        ),
        "element_count_min": min(element_counts),
        "element_count_max": max(element_counts),
        "node_count_min": min(node_counts),
        "node_count_max": max(node_counts),
        "contact_activation_time_s_min": min(contact_activation_times),
        "contact_activation_time_s_max": max(contact_activation_times),
        "geometric_impact_time_s_min": min(geometric_impact_times),
        "geometric_impact_time_s_max": max(geometric_impact_times),
        "geometric_impact_time_over_3e_4_count": sum(
            value > 3.0e-4 for value in geometric_impact_times
        ),
        "xml_final_times_s": sorted(final_times),
    }

    manifest_path = dataset_dir / "manifest.json"
    manifest_tmp = dataset_dir / "manifest.json.tmp"
    manifest_tmp.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_tmp.replace(manifest_path)

    logs_dir = dataset_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    summary_path = logs_dir / "validation_summary.json"
    summary_tmp = logs_dir / "validation_summary.json.tmp"
    summary_tmp.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary_tmp.replace(summary_path)

    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Validate a completed ball/plate dataset and write manifest.json."
    )
    parser.add_argument("dataset_dir", help="Ball/plate dataset root")
    parser.add_argument("--expected-scenes", type=int, default=1000)
    parser.add_argument("--max-elements", type=int, default=12000)
    parser.add_argument(
        "--max-contact-activation-time",
        type=float,
        default=3.0e-4,
    )
    args = parser.parse_args()
    finalize_dataset(
        args.dataset_dir,
        expected_scenes=args.expected_scenes,
        max_elements=args.max_elements,
        max_contact_activation_time_s=args.max_contact_activation_time,
    )
