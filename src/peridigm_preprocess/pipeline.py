"""One-command scene generation orchestration."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from peridigm_preprocess import __version__
from peridigm_preprocess.config import public_config
from peridigm_preprocess.scenes import get_generator
from peridigm_preprocess.validation import sha256, validate_generated_case
from peridigm_preprocess.xml_writer import write_peridigm_xml


class GenerationError(RuntimeError):
    """Raised when one or more requested cases fail generation."""


_SAFE_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=path.suffix, dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _case_complete(case_dir: Path) -> bool:
    try:
        validate_generated_case(case_dir)
    except Exception:
        return False
    return True


def _protect_existing_results(case_dir: Path) -> None:
    results = case_dir / "results"
    if results.exists() and (
        not results.is_dir() or any(results.iterdir())
    ):
        raise GenerationError(
            f"Refusing to replace inputs for a case with existing results: {case_dir}"
        )


def _planned_tags(
    scenario: str,
    count: int,
    base_seed: int,
    template: str,
) -> list[tuple[int, int, str]]:
    """Resolve and validate every tag before creating any case directories."""
    planned: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for index in range(count):
        seed = base_seed + index
        try:
            tag = template.format(scenario=scenario, seed=seed, index=index)
        except (KeyError, ValueError) as exc:
            raise GenerationError(f"Invalid run.tag_template: {template!r}") from exc
        if tag in {".", ".."} or _SAFE_TAG.fullmatch(tag) is None:
            raise GenerationError(
                "Generated tags must start with an ASCII letter or digit and contain "
                f"only letters, digits, '.', '_', or '-'; got {tag!r}"
            )
        if tag in seen:
            raise GenerationError(
                f"run.tag_template produces duplicate case tag {tag!r}"
            )
        seen.add(tag)
        planned.append((index, seed, tag))
    return planned


def generate_dataset(
    config: Mapping[str, Any],
    *,
    force: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Generate all cases requested by a validated configuration mapping."""
    scenario = str(config["scenario"])
    run = config["run"]
    output_root = Path(str(run["output_root"])).expanduser()
    if not output_root.is_absolute():
        output_root = (Path.cwd() / output_root).resolve()
    dataset_dir = output_root / scenario
    dataset_dir.mkdir(parents=True, exist_ok=True)
    dataset_dir = dataset_dir.resolve()
    manifest_path = dataset_dir / "manifest.json"
    generator = get_generator(scenario)
    count = int(run["count"])
    base_seed = int(run["base_seed"])
    resume = bool(run.get("resume", False))
    tag_template = str(run.get("tag_template", "{scenario}_{seed:04d}"))
    planned = _planned_tags(scenario, count, base_seed, tag_template)
    entries: list[dict[str, Any]] = []
    failures: list[str] = []

    for index, seed, tag in planned:
        case_dir = dataset_dir / tag
        resolved_case_dir = case_dir.resolve(strict=False)
        if resolved_case_dir.parent != dataset_dir:
            raise GenerationError(
                f"Case tag resolves outside its dataset directory: {tag!r}"
            )
        case_dir = resolved_case_dir
        mesh_path = case_dir / f"{tag}.g"
        xml_path = case_dir / f"{tag}.xml"
        metadata_path = case_dir / "metadata.json"
        results_dir = case_dir / "results"

        if case_dir.exists():
            if resume and _case_complete(case_dir):
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                entries.append(
                    {
                        "index": index,
                        "seed": seed,
                        "tag": tag,
                        "status": "skipped_complete",
                        "case_dir": str(case_dir.relative_to(dataset_dir)),
                        "mesh_file": str(mesh_path.relative_to(dataset_dir)),
                        "xml_file": str(xml_path.relative_to(dataset_dir)),
                        "metadata_file": str(metadata_path.relative_to(dataset_dir)),
                        "element_count": metadata["geometry"]["element_count"],
                    }
                )
                continue
            if not force:
                raise GenerationError(
                    f"Case directory already exists: {case_dir}. Use run.resume=true "
                    "to reuse complete cases or --force to replace generated inputs."
                )
            _protect_existing_results(case_dir)

        case_dir.mkdir(parents=True, exist_ok=True)
        results_dir.mkdir(exist_ok=True)
        started_at = _utc_now()
        try:
            geometry = generator(
                mesh_path,
                seed,
                config["mesh"],
                config["geometry"],
                verbose=verbose,
            )
            if int(geometry["element_count"]) > int(config["mesh"]["max_elements"]):
                raise GenerationError(
                    f"Backend returned {geometry['element_count']} elements above hard cap"
                )
            xml_resolved = write_peridigm_xml(
                xml_path,
                config,
                geometry,
                mesh_path.name,
                f"results/{tag}",
            )
            metadata = {
                "schema_version": 1,
                "pipeline_version": __version__,
                "scenario": scenario,
                "tag": tag,
                "seed": seed,
                "status": "success",
                "started_at": started_at,
                "completed_at": _utc_now(),
                "files": {
                    "mesh": mesh_path.name,
                    "xml": xml_path.name,
                    "results": "results",
                },
                "sha256": {"mesh": sha256(mesh_path), "xml": sha256(xml_path)},
                "config": public_config(config),
                "geometry": geometry,
                "xml_resolved": xml_resolved,
            }
            _write_json_atomic(metadata_path, metadata)
            validation = validate_generated_case(case_dir)
            entries.append(
                {
                    "index": index,
                    "seed": seed,
                    "tag": tag,
                    "status": "success",
                    "case_dir": str(case_dir.relative_to(dataset_dir)),
                    "mesh_file": str(mesh_path.relative_to(dataset_dir)),
                    "xml_file": str(xml_path.relative_to(dataset_dir)),
                    "metadata_file": str(metadata_path.relative_to(dataset_dir)),
                    "node_count": geometry["node_count"],
                    "element_count": geometry["element_count"],
                    "validation": validation,
                }
            )
        except Exception as exc:
            failure = f"{tag}: {exc}"
            failures.append(failure)
            entries.append(
                {
                    "index": index,
                    "seed": seed,
                    "tag": tag,
                    "status": "error",
                    "case_dir": str(case_dir.relative_to(dataset_dir)),
                    "error": str(exc),
                }
            )
        manifest = {
            "schema_version": 1,
            "pipeline_version": __version__,
            "scenario": scenario,
            "dataset_dir": ".",
            "config_file": config.get("_config_file"),
            "requested_cases": count,
            "completed_cases": sum(
                entry["status"] in {"success", "skipped_complete"} for entry in entries
            ),
            "failed_cases": sum(entry["status"] == "error" for entry in entries),
            "updated_at": _utc_now(),
            "cases": entries,
        }
        _write_json_atomic(manifest_path, manifest)

    if failures:
        raise GenerationError(
            f"{len(failures)}/{count} cases failed; see {manifest_path}. "
            + "; ".join(failures[:3])
        )
    return manifest
