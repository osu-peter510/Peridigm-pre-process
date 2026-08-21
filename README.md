# Peridigm Preprocessing Pipeline

Preprocessing scripts for generating ExodusII meshes (`.g`) and Peridigm simulation configurations (`.xml`) across brittle/soft-body fracture datasets.

A single unified entry point (`generate.py`) covers all scenarios. Each sub-module can also be run independently.

---

## Repository Structure

```
Peridigm-pre-process-claude/
├── generate.py                    # Unified entry point for all scenarios
├── README.md
├── environment.yml
│
├── output/                        # All generated scenes (created at runtime)
│   ├── freefall_vase/
│   ├── freefall_mug/
│   ├── bullet_vase/
│   ├── bullet_mug/
│   ├── bullet_vase_nf/
│   ├── bullet_mug_nf/
│   ├── ball_plate_nf/
│   ├── kw_fracture/
│   └── cloth_fall/
│       └── <scenario>/manifest.json
│
├── ball-plate-nf/                 # Open-source Gmsh/meshio ball/plate backend
│   └── generate_scene.py
│
├── kw-board-impact/               # Kalthoff-Winkler fracture benchmark
│   ├── block_generation.py        # Geometry + mesh builder (importable + standalone)
│   ├── block_generation.ipynb     # Notebook version (reference)
│   ├── kw_fracture.xml            # Peridigm config template
│   ├── kw_fracture.slurm          # SLURM job script
│   └── kw_board_geometry.png      # Reference geometry diagram
│
├── cloth-fall-generation/         # Randomised cloth-fall soft-body dataset
│   ├── cloth_scene_generator.py   # Main class (ClothFallSceneGenerator)
│   ├── generate_scenes.py         # Standalone batch generator
│   ├── examples.py                # Usage examples
│   ├── scene.xml                  # Template Peridigm config
│   └── runpd.slurm                # SLURM job script
│
└── dynamic-impact-generation/     # Brittle fragmentation dataset
    ├── geometry_builder.py        # Cubit geometry primitives (vase/mug/bullet/floor)
    ├── peridigm_xml.py            # Peridigm XML generators (one per scenario)
    ├── generate_scenes.py         # Standalone batch orchestration script
    ├── generate_scenes.ipynb      # Original development notebook (reference)
    ├── pd.slurm                   # SLURM job script
    └── geometry/                  # Pre-built reference geometry files
        ├── mug.cub5
        └── mug.g
```

---

## Dependencies

### Required (manual installation)

**Coreform Cubit 2025.12** — required by the unified entry point and all
scenarios except the standalone `ball-plate-nf` Gmsh backend.
Download from [coreform.com](https://coreform.com/products/coreform-cubit/).

Set `CUBIT_PATH` once at the top of `generate.py` (default: `E:\Program Files\Coreform Cubit 2025.12\bin`). When using sub-module scripts standalone, update their local `CUBIT_PATH` variable as well.

### Python environment

```bash
conda env create -f environment.yml
conda activate peridigm-preprocess
```

For the license-free `ball-plate-nf` backend, create the dedicated Conda
environment instead:

```bash
conda env create -f environment-gmsh.yml
conda activate peridigm-gmsh
```

This environment uses Gmsh for tetrahedral meshing, meshio for ExodusII output,
and netCDF4 to add Peridigm-compatible element-block and node-set metadata.

---

## Scenarios

| Scenario | Module | Description |
|---|---|---|
| `freefall_vase`  | dynamic-impact | Randomised vase dropped from 1.5–2.5 m onto a steel floor |
| `freefall_mug`   | dynamic-impact | Randomised mug dropped from 1.5–2.5 m onto a steel floor |
| `bullet_vase`    | dynamic-impact | 400 m/s bullet impacts a vase sitting on a floor |
| `bullet_mug`     | dynamic-impact | 400 m/s bullet impacts a mug sitting on a floor |
| `bullet_vase_nf` | dynamic-impact | 400 m/s bullet impacts a floating vase (no floor) |
| `bullet_mug_nf`  | dynamic-impact | 400 m/s bullet impacts a floating mug (no floor) |
| `ball_plate_nf`  | Gmsh/meshio | Random steel ball impacts a floating ceramic plate at 20-100 m/s |
| `ball_plate`     | dynamic-impact | Backward-compatible alias for `ball_plate_nf` |
| `kw_fracture`    | kw-board-impact | Kalthoff-Winkler notched steel board + cylindrical projectile |
| `cloth_fall`     | cloth-fall | Randomised cloth falling over 3 bricks + 1 sphere |

---

## Quick Start

### Unified entry point (recommended)

Edit `CUBIT_PATH` at the top of `generate.py` once, then run from the repo root:

```bash
# python generate.py <scenario> <n_scenes> [--base-seed N] [--max-nodes N]

python generate.py freefall_vase   50
python generate.py freefall_mug    50
python generate.py bullet_vase     20 --base-seed 100
python generate.py bullet_mug      20 --base-seed 100
python generate.py bullet_vase_nf  30 --max-nodes 30000
python generate.py bullet_mug_nf   30 --max-nodes 30000
python generate.py ball_plate_nf    50 --max-nodes 30000
python generate.py kw_fracture      1
python generate.py cloth_fall     100 --max-nodes 20000

python generate.py --help
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--base-seed N` | `0` | Starting random seed; scene `i` uses seed `base_seed + i` |
| `--max-nodes N` | `50000` | Element budget; mesh is coarsened until this is met |

Output is written to `output/<scenario>/`. Each run appends a `manifest.json` with file paths and status for every scene attempted.

### Per-module entry points (standalone)

Each sub-module can still be run independently, writing output to its own directory:

```bash
# Randomized ball/plate impact without a floor
conda activate peridigm-gmsh
python ball-plate-nf/generate_scene.py --n-scenes 1000 --seed 0 --output-dir output/ball_plate_nf --max-elements 12000 --resume

# KW board impact
cd kw-board-impact
python block_generation.py [--output-dir DIR]

# Cloth fall
cd cloth-fall-generation
python generate_scenes.py 100
python generate_scenes.py 500 --base-seed 42 --output-dir my_cloth_dataset --max-nodes 30000

# Dynamic impact
cd dynamic-impact-generation
python generate_scenes.py freefall_vase 50
python generate_scenes.py bullet_mug 20 --base-seed 100 --output-dir my_dataset
```

---

## Module Descriptions

### `ball-plate-nf/`

Generates a free-floating ceramic plate and an incoming steel ball without a
floor or Cubit license. Plate diameter (100–150 mm), plate thickness (2–4 mm),
ball diameter (10–15 mm), impact position/direction, and speed (20–100 m/s) are
sampled reproducibly from the scene seed. The initial ball position is solved
from its speed, direction, and Peridigm contact radii. Every scene begins with
the ball surface strictly outside the contact search radius; the short-range
contact model activates within `1e-4` to `2e-4` seconds. The generator writes a
validated
ExodusII mesh, Peridigm XML, per-scene JSON metadata, and a resumable batch
manifest. First-order tetrahedra are adaptively coarsened to a hard default cap
of 12,000 elements per scene. Exodus blocks are named `block_1` (plate) and
`block_2` (ball), with matching `nodelist_1` and `nodelist_2` node sets. The
default solver settings are a `2.0e-7` s fixed step, `8.0e-4` s final time, and
an output frequency of 25 steps. Dataset exports can share each scene mesh under
`geometry/NNNN/` while keeping the 0.0005 and 0.0015 damage variants under
`critical-stretch-0p0005/NNNN/` and `critical-stretch-0p0015/NNNN/`.

**Key files:** `generate_scene.py`, `environment-gmsh.yml`

### `kw-board-impact/`

Generates the Kalthoff-Winkler notched steel board impacted by a cylindrical projectile — a standard fracture mechanics benchmark. The board (200×100×9 mm) has two symmetric slotted notches; a steel cylinder (r=25 mm, h=60 mm) impacts at 100 m/s. Boolean subtraction cuts the notches, a webcut splits the board into two halves, and both are tetrahedral-meshed at 5 mm. The geometry is deterministic (no randomisation).

`block_generation.py` exposes `build_kw_scene(out_dir, tag)` for import by `generate.py`, and can also be run standalone. `kw_fracture.xml` is the Peridigm config template; `build_kw_scene` patches the mesh filename and output basename before writing the final XML.

**Key files:** `block_generation.py`, `kw_fracture.xml`

### `cloth-fall-generation/`

Generates randomised soft-body simulations of cloth falling over obstacles, intended as a training dataset for ML models. Each scene has a fixed topology: 1 floor + 1 cloth sheet + 3 random bricks + 1 random sphere. Object sizes and positions are randomised with collision avoidance. Mesh is adaptively coarsened until total node count falls within a configurable budget (default 20 000).

`ClothFallSceneGenerator` in `cloth_scene_generator.py` handles all geometry, meshing, and XML generation. `generate_scenes.py` wraps it for standalone batch use.

**Key files:** `cloth_scene_generator.py`, `generate_scenes.py`

### `dynamic-impact-generation/`

Generates brittle fragmentation simulations for ceramic objects (vase or mug) under two loading types. Geometry parameters (shape, size, wall thickness, handle dimensions) are randomised per seed. Mesh size adapts to stay within a node budget. Peridigm parameters (horizon, dt, final_time, output_frequency) are computed automatically from mesh size via CFL stability conditions:

- `horizon = 3.015 × mesh_size`
- `dt = mesh_size / c_p × 0.7`
- `output_frequency` → 2000 fps
- `contact_radius = 1.1 × mesh_size`, `search_radius = 2.0 × mesh_size`

The code is split across three modules:
- `geometry_builder.py` — Cubit geometry primitives (vase, mug, bullet, floor); no I/O
- `peridigm_xml.py` — XML generator functions, one per scenario; no Cubit dependency
- `generate_scenes.py` — scene builder functions + standalone CLI

**Key files:** `geometry_builder.py`, `peridigm_xml.py`, `generate_scenes.py`

---

## Output Format

All scenarios produce the same paired output:

| File | Format | Description |
|---|---|---|
| `*.g` | Exodus II | FE mesh with blocks and nodesets; units in **meters** |
| `*.xml` | Peridigm XML | Simulation config: materials, damage, contact, BCs, solver, output |
| `manifest.json` | JSON | Scene index: file paths, seeds, and success/error status |

Geometry is created in Cubit in **millimeters (mm)** and scaled to **meters (m)** at export via `cubit.cmd("volume all scale 0.001")`.

**Block / nodeset convention:**

| Block | Nodeset | Contents |
|---|---|---|
| `block_1` | `nodelist_1` | Floor or board (fixed BCs) |
| `block_2` | `nodelist_2` | Main object (vase / mug / cloth) |
| `block_3` | `nodelist_3` | Bullet (bullet scenarios only) |

`bullet_*_nf` scenes have no floor: `block_1` = vase/mug, `block_2` = bullet.

**Output variables (all scenarios):**
`Coordinates`, `Displacement`, `Velocity`, `Force`, `Force_Density`, `Contact_Force_Density`,
`Block_Id`, `Dilatation`, `Kinetic_Energy`, `Weighted_Volume`, `Volume`,
`Global_Kinetic_Energy`, `Global_Linear_Momentum`, `Global_Angular_Momentum`,
`Linear_Momentum`, `Angular_Momentum`

---

## HPC / SLURM

Each module includes a `.slurm` script for running Peridigm on a cluster. The SLURM scripts call `mpirun -np 36 Peridigm scene.xml`. Adjust the node/task counts to match your cluster configuration.

### Parallel scene generation — all scenarios at once

Run every scenario simultaneously as background processes on the login node. Each writes to its own `output/<scenario>/` folder so there are no conflicts.

**Bash:**
```bash
mkdir -p logs
SCENES=50

for scenario in freefall_vase freefall_mug bullet_vase bullet_mug bullet_vase_nf bullet_mug_nf ball_plate_nf kw_fracture cloth_fall; do
    python generate.py $scenario $SCENES --max-nodes 50000 > logs/${scenario}.log 2>&1 &
done

wait
echo "All done."
```

**PowerShell (Windows):**
```powershell
New-Item -ItemType Directory -Force logs | Out-Null
$scenarios = @("freefall_vase","freefall_mug","bullet_vase","bullet_mug","bullet_vase_nf","bullet_mug_nf","ball_plate_nf","kw_fracture","cloth_fall")
$jobs = $scenarios | ForEach-Object {
    Start-Process python -ArgumentList "generate.py $_ 50 --max-nodes 50000" `
        -RedirectStandardOutput "logs\$_.log" -NoNewWindow -PassThru
}
$jobs | Wait-Process
Write-Host "All done."
```

### Submitting all generated scenes to SLURM

After generation, loop over the output directory to decompose and submit every scene:

```bash
# All scenarios
for xml in output/*/*.xml; do
    decomp -p 36 ${xml%.xml}.g
    sbatch --job-name=$(basename $xml .xml) pd.slurm $xml
done
```

```bash
# Single scenario
for xml in output/freefall_vase/*.xml; do
    decomp -p 36 ${xml%.xml}.g
    sbatch --job-name=$(basename $xml .xml) pd.slurm $xml
done
```
