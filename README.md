# Peridigm Preprocess 1.0

This repository generates reproducible 3-D geometry, bounded-size Exodus meshes,
and fully configured Peridigm XML files from one YAML command. Release 1.0
consolidates the useful source logic from the former `multiple-dataset-scene`
research directory without copying its 71 GB of results, logs, decomposed meshes,
or machine-local Peridigm builds.

The four primary scenes are implemented with the open-source Gmsh backend, so
Coreform Cubit is not required:

| Config | Geometry | Canonical blocks |
|---|---|---|
| `configs/kw_fracture.yaml` | Kalthoff-Winkler notched board + projectile | projectile, board |
| `configs/ball_plate.yaml` | randomized ball impact on a free plate | plate, ball |
| `configs/mug_fall.yaml` | randomized hollow mug + handle over a floor | floor, mug |
| `configs/cloth_fall.yaml` | thin volumetric cloth over 3 boxes + sphere | floor, cloth, obstacles |

Chinese quick-start documentation is in [docs/README_zh.md](docs/README_zh.md),
and the source-to-release inventory is in [docs/MIGRATION.md](docs/MIGRATION.md).

## Install

```bash
conda env create -f environment.yml
conda activate peridigm-preprocess
```

The repository-local command works without installing the package. `--help`,
config validation, and scenario listing do not import Gmsh:

```bash
python generate.py --help
python generate.py list
python generate.py validate-config configs/mug_fall.yaml
```

## One-command generation

```bash
python generate.py generate configs/ball_plate.yaml
python generate.py generate configs/kw_fracture.yaml
python generate.py generate configs/mug_fall.yaml
python generate.py generate configs/cloth_fall.yaml
```

Any scalar can be changed without editing YAML:

```bash
python generate.py generate configs/mug_fall.yaml \
  --set run.count=20 \
  --set mesh.target_elements=40000 \
  --set mesh.max_elements=42000 \
  --set mesh.horizon_multiplier=3.2 \
  --set damage_models.mug_fracture.parameters."Critical Stretch"=0.001
```

The historical syntax remains available for the four unified scene aliases:

```bash
python generate.py ball_plate_nf 10 --base-seed 100 --max-elements 12000
python generate.py freefall_mug 10 --max-elements 12000
```

Generated inputs are never silently overwritten. Set `run.resume: true` to skip
complete cases. `--force` can replace generated inputs, but it refuses to touch a
case whose `results/` directory contains files.

## Output contract

Every case is self-contained and the dataset manifest uses relative paths:

```text
output/<scenario>/
├── manifest.json
└── <scenario>_<seed>/
    ├── <scenario>_<seed>.g
    ├── <scenario>_<seed>.xml
    ├── metadata.json
    └── results/
```

`metadata.json` records the source config, sampled geometry, mesh adaptation
history, node/element counts, mesh quality, measured block mesh sizes, resolved
horizons, solver values, derived damage parameters, and SHA256 digests.

All geometry and XML values use SI units. Blocks and node sets are named
`block_N` and `nodelist_N` with stable numeric IDs.

## Mesh count and dynamic horizon

The mesh contract separates the desired count from the hard cap:

```yaml
mesh:
  target_elements: 10000
  max_elements: 11000
  tolerance: 0.03
  initial_size_m: 0.00585
  horizon_multiplier: 3.015
```

The backend holds the sampled geometry fixed while changing only mesh size.
Failure to meet `max_elements` is an error; an over-budget mesh is never exported
as a successful case. `target_elements ± tolerance` is a soft target: if no
attempt lands inside it, the closest quality-passing candidate below the hard
cap is retained and metadata records `mesh_control.target_met: false`.

Horizon is not stored as a static config length. After meshing, the pipeline
measures the median physical cell-edge length separately for every block and
resolves

```text
horizon(block) = measured_mesh_size(block) × horizon_multiplier(block)
```

The measured method, length, multiplier, and final horizon are all written to
metadata and checked against the XML. A block may override the global multiplier,
but a normal config cannot accidentally carry a stale absolute horizon from a
different mesh density.

## Explicit material, damage, solver, and contact configuration

Materials and damage are independent registries. Each block references them by
ID:

```yaml
materials:
  mug:
    name: Mug Material
    model: Elastic
    parameters:
      Density: 2200.0
      Bulk Modulus: 1.490e+10
      Shear Modulus: 8.940e+9

damage_models:
  mug_fracture:
    name: Mug Damage
    model: Critical Stretch
    parameters:
      Critical Stretch: 0.0005
```

Solver time step can be fixed, delegated to Peridigm through a safety factor, or
calculated after meshing from the smallest selected mesh scale and fastest
configured P-wave speed:

```yaml
solver:
  method: Verlet
  final_time: 0.03
  time_step:
    mode: auto
    safety_factor: 0.7
    mesh_scale: min
```

Contact and search radii are also expressed as multipliers of a measured mesh
scale, with an explicit search frequency and model parameters.

### Progressive Bond Energy

`configs/mug_fall_progressive.yaml` demonstrates the migrated mesh-density logic.
`Characteristic Length` is bound to the final mug horizon, and fracture energy is
recomputed whenever the mesh or horizon multiplier changes:

```text
sf = s0 × failure_stretch_ratio
Gc = 0.5 × Et × s0 × sf × characteristic_length
```

Progressive Bond Energy requires a compatible custom Peridigm build; generating
valid XML does not add that model (or any separately configured custom material)
to stock Peridigm. The example emits the model-required `Tensile Modulus` and
uses it in the derived energy formula.

## Validation and result quality

Static validation reopens Exodus and XML, verifies hashes, topology, counts,
relative mesh references, solver values, damage parameters, and every dynamic
horizon:

```bash
python generate.py validate output/mug_fall
python generate.py validate output/mug_fall/mug_fall_0000
```

After Peridigm runs, the generic first quality gate checks exact result files,
time-axis agreement and final time, NaN/Inf, damage bounds, and damage
irreversibility. It also requires successful `run_metadata.json` provenance
bound to the current mesh/XML hashes, ranks, and decomposition sidecar. It
writes `quality_report.json`:

```bash
python generate.py quality output/mug_fall/mug_fall_0000
```

For physics-oriented impact metrics, `analyze` merges serial or complete
Nemesis output, identifies elements by global element ID, rejects duplicate
global-element ownership across shards, and maps nodal fields through block
connectivity:

```bash
python generate.py analyze output/mug_fall/mug_fall_0000
python generate.py analyze output/ball_plate/ball_plate_0000 \
  --block ball --damage-block plate
```

`analysis.blocks`/`--block` selects the single moving body used for velocity,
force/impulse, momentum, rebound, and restitution. Independently,
`analysis.damage_blocks`/`--damage-block` selects the damaged target; a distinct
target is reported under `damage_analysis`. This prevents target and projectile
forces from cancelling in one reduction. Damage fraction thresholds come from
`analysis.damage_thresholds` when configured; repeated `--damage-threshold`
arguments override them for one analysis.

With valid element `Volume`, `impact_report.json` contains volume/mass-weighted
histories, Force_Density integration, impulse and momentum closure, plus
volume-weighted Damage means/fractions. If physical `Volume` is unavailable,
dimensional volume, mass, Force_Density force, impulse, and momentum fields are
left unavailable; only explicitly named count-weighted kinematics/Damage
statistics are emitted (a complete direct `Force` field remains usable). Missing
or partial fields are explicit warnings. Ambiguous multi-block impact reductions
are suppressed unless the config explicitly opts in.

`compare_convergence_reports()` in `result_analysis.py` provides a
machine-readable absolute/relative gate for paired-dt or mesh comparisons.
Energy closure and broken-bond fragment connectivity remain scenario-specific;
a scalar Damage field is not treated as an exact fragment graph.

## Slurm

The scripts under `hpc/` read case metadata and manifests rather than hard-coded
home directories or filenames.

```bash
# Generate on a compute node
mkdir -p logs
sbatch hpc/generate.slurm configs/mug_fall.yaml run.count=20

# Preview, then submit a Peridigm array
python hpc/submit_dataset.py output/mug_fall/manifest.json \
  --partition=preempt --concurrency=5

PERIDIGM_BIN=/path/to/Peridigm \
python hpc/submit_dataset.py output/mug_fall/manifest.json \
  --partition=preempt --concurrency=5 --submit
```

The submitter infers ranks from every case's metadata and rejects mismatches.
The runner binds each decomposition to the base-mesh SHA256 in a sidecar,
rejects partial/untracked/stale shards, records actual run ranks and exit status,
binds the launch to both mesh and XML hashes, and refuses to overwrite any
nonempty result directory (including hidden entries). The completed run sidecar
also records the exact Exodus paths and sizes consumed by the quality gate. See
[hpc/README.md](hpc/README.md) for
environment variables and the `afterany` result-validation gate.

## Repository layout

```text
configs/                       editable release configs
src/peridigm_preprocess/       unified package, XML resolver, validation
src/peridigm_preprocess/scenes all-Gmsh primary scene backends
hpc/                           portable Slurm generation/run/quality workflow
tests/                         config/XML/CLI/regression tests
ball-plate-nf/                 retained legacy standalone backend
dynamic-impact-generation/     retained Cubit research backends
kw-board-impact/               retained KW reference material
cloth-fall-generation/         retained Cubit reference backend
```

The legacy directories remain for provenance and backwards comparison. The
release CLI does not depend on their hard-coded Cubit paths or static XML.
