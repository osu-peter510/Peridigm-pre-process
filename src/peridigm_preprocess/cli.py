"""Command-line interface for geometry, XML, validation, and quality gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from peridigm_preprocess import __version__
from peridigm_preprocess.config import (
    PRIMARY_SCENARIOS,
    SCENARIO_ALIASES,
    ConfigError,
    load_config,
    public_config,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _config_for_scenario(raw_scenario: str) -> Path:
    normalized = raw_scenario.lower().replace("-", "_")
    scenario = SCENARIO_ALIASES.get(normalized, normalized)
    if scenario not in PRIMARY_SCENARIOS:
        raise ConfigError(
            f"The unified release supports {', '.join(PRIMARY_SCENARIOS)}; "
            f"got legacy scenario {raw_scenario!r}. Its standalone script remains available."
        )
    relative = Path("configs") / f"{scenario}.yaml"
    candidates = (
        REPO_ROOT / relative,
        Path.cwd() / relative,
        Path(sys.prefix) / "share" / "peridigm-preprocess" / relative.name,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ConfigError(
        f"Default config for {scenario!r} was not installed; tried: "
        + ", ".join(str(candidate) for candidate in candidates)
    )


def _legacy_invocation(argv: Sequence[str]) -> int | None:
    """Translate the historical ``generate.py SCENE COUNT`` syntax."""
    if len(argv) < 2 or argv[0].startswith("-"):
        return None
    known = set(PRIMARY_SCENARIOS) | set(SCENARIO_ALIASES)
    normalized = argv[0].lower().replace("-", "_")
    if normalized not in known:
        return None
    try:
        int(argv[1])
    except ValueError:
        return None
    parser = argparse.ArgumentParser(
        prog=f"generate.py {argv[0]}",
        description="Backward-compatible scene generation syntax.",
    )
    parser.add_argument("scenario")
    parser.add_argument("count", type=int)
    parser.add_argument("--base-seed", type=int, default=0)
    parser.add_argument("--max-elements", "--max-nodes", dest="max_elements", type=int)
    parser.add_argument("--output-dir")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--set", action="append", default=[])
    args = parser.parse_args(list(argv))
    overrides = list(args.set)
    overrides.extend(
        [f"run.count={args.count}", f"run.base_seed={args.base_seed}"]
    )
    if args.output_dir:
        overrides.append(f"run.output_root={args.output_dir}")
    if args.resume:
        overrides.append("run.resume=true")
    if args.max_elements is not None:
        overrides.extend(
            [
                f"mesh.max_elements={args.max_elements}",
                f"mesh.target_elements={max(1, int(args.max_elements * 0.9))}",
            ]
        )
    try:
        config = load_config(_config_for_scenario(args.scenario), overrides)
        from peridigm_preprocess.pipeline import generate_dataset

        manifest = generate_dataset(config, force=args.force, verbose=args.verbose)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        f"Generated {manifest['completed_cases']}/{manifest['requested_cases']} "
        f"{manifest['scenario']} case(s)."
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate.py",
        description=(
            "Generate Peridigm geometry + mesh-resolved XML, validate datasets, "
            "and audit simulation results."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List supported unified scenarios.")

    validate_config_parser = subparsers.add_parser(
        "validate-config", help="Parse and validate a YAML/JSON scene config."
    )
    validate_config_parser.add_argument("config")
    validate_config_parser.add_argument("--set", action="append", default=[])

    generate_parser = subparsers.add_parser(
        "generate", help="Generate all geometry and XML described by one config."
    )
    generate_parser.add_argument("config")
    generate_parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override a dotted configuration value; may be repeated.",
    )
    generate_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace generated inputs, but never cases containing result files.",
    )
    generate_parser.add_argument("--verbose", action="store_true")

    validate_parser = subparsers.add_parser(
        "validate", help="Validate one generated case or a dataset manifest."
    )
    validate_parser.add_argument("path")

    quality_parser = subparsers.add_parser(
        "quality", help="Audit Peridigm result shards for one generated case."
    )
    quality_parser.add_argument("case_dir")
    quality_parser.add_argument("--report")

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Compute volume-weighted impact, damage, impulse, and rebound metrics.",
    )
    analyze_parser.add_argument("case_dir")
    analyze_parser.add_argument(
        "--block",
        action="append",
        default=[],
        help="Analyze one logical/Exodus block; may be repeated.",
    )
    analyze_parser.add_argument(
        "--damage-block",
        action="append",
        default=[],
        help="Analyze Damage on a target block independently; may be repeated.",
    )
    analyze_parser.add_argument(
        "--damage-threshold",
        action="append",
        type=float,
        default=[],
        help="Damage threshold for a volume-fraction history; may be repeated.",
    )
    analyze_parser.add_argument(
        "--report",
        help="Full JSON destination (default: CASE_DIR/impact_report.json).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    legacy_result = _legacy_invocation(arguments)
    if legacy_result is not None:
        return legacy_result
    parser = _build_parser()
    args = parser.parse_args(arguments)
    try:
        if args.command == "list":
            print("\n".join(PRIMARY_SCENARIOS))
            return 0
        if args.command == "validate-config":
            config = load_config(args.config, args.set)
            print(json.dumps(public_config(config), indent=2, sort_keys=True))
            return 0
        if args.command == "generate":
            config = load_config(args.config, args.set)
            from peridigm_preprocess.pipeline import generate_dataset

            manifest = generate_dataset(
                config, force=args.force, verbose=args.verbose
            )
            print(json.dumps(
                {
                    "scenario": manifest["scenario"],
                    "requested_cases": manifest["requested_cases"],
                    "completed_cases": manifest["completed_cases"],
                    "failed_cases": manifest["failed_cases"],
                },
                indent=2,
            ))
            return 0
        if args.command == "validate":
            path = Path(args.path).resolve()
            if (path / "manifest.json").is_file():
                from peridigm_preprocess.quality import validate_dataset

                report = validate_dataset(path)
            else:
                from peridigm_preprocess.validation import validate_generated_case

                report = validate_generated_case(path)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        if args.command == "quality":
            from peridigm_preprocess.quality import analyze_results

            report = analyze_results(args.case_dir, report_path=args.report)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["status"] == "pass" else 1
        if args.command == "analyze":
            from peridigm_preprocess.quality import (
                _safe_report_destination,
                _write_json_atomic,
            )
            from peridigm_preprocess.result_analysis import analyze_impact_case

            keyword_arguments = {
                "blocks": args.block or None,
                "damage_blocks": args.damage_block or None,
            }
            if args.damage_threshold:
                keyword_arguments["damage_thresholds"] = args.damage_threshold
            report = analyze_impact_case(args.case_dir, **keyword_arguments)
            destination = _safe_report_destination(
                args.case_dir, args.report, "impact_report.json"
            )
            _write_json_atomic(destination, report)
            print(
                json.dumps(
                    {
                        "status": report["status"],
                        "case": report.get("case"),
                        "scenario": report.get("scenario"),
                        "report": str(destination),
                        "metrics": report.get("metrics", {}),
                        "warnings": report.get("warnings", []),
                        "errors": report.get("errors", []),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0 if report["status"] == "pass" else 1
    except (ConfigError, OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    parser.error(f"Unhandled command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
