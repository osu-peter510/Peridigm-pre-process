#!/usr/bin/env python3
"""
generate_scene.py — Deterministic ball-plate-floor scene generator.

Geometry (all mm, exported in meters):
  Floor : circular plate, r=100 mm, h=1 mm,   top face at z=0
  Plate : circular plate, r=37 mm,  h=2.5 mm, bottom  at z=0 (top at z=2.5)
  Ball  : sphere,         r=5 mm,   centre at z=21

Ball bottom at z=16, plate top at z=2.5 → 13.5 mm gap.

Blocks / nodesets:
  block_1 / nodelist_1 : Floor  (steel, fixed)
  block_2 / nodelist_2 : Plate  (ceramic, damage)
  block_3 / nodelist_3 : Ball   (steel, initial velocity)

Usage:
  python generate_scene.py [--output-dir DIR] [--mesh-size MM] [--ball-speed M/S]
"""

import sys
import math
import argparse
from pathlib import Path

# ------------------------------------------------------------------ #
CUBIT_PATH = r"E:\Program Files\Coreform Cubit 2025.12\bin"
# ------------------------------------------------------------------ #

sys.path.append(CUBIT_PATH)
import cubit


def echo(msg: str) -> None:
    sys.__stdout__.write(str(msg) + "\n")
    sys.__stdout__.flush()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nice_dt(raw: float) -> float:
    """Round dt down to 1 significant figure."""
    if raw <= 0:
        return raw
    exp = math.floor(math.log10(raw))
    base = 10 ** exp
    return math.floor(raw / base) * base


def _nice_freq(raw: int) -> int:
    """Round down to 1 significant figure for clean output numbering."""
    if raw < 10:
        return max(1, raw)
    p = 10 ** int(math.log10(raw))
    return max(1, (raw // p) * p)


# ---------------------------------------------------------------------------
# Geometry builder
# ---------------------------------------------------------------------------

def build_geometry(mesh_size_mm: float = 2.0):
    """Create floor + plate + ball in Cubit and mesh them.

    Returns (floor_vol, plate_vol, ball_vol).
    """
    cubit.cmd("reset")

    # --- Floor: r=100, h=1, top at z=0 ---
    cubit.cmd("create cylinder height 1 radius 100")
    floor_vol = cubit.get_last_id("volume")
    cubit.cmd(f"move volume {floor_vol} z {-0.5}")

    # --- Plate: r=37, h=2.5, bottom at z=0 ---
    cubit.cmd("create cylinder height 2.5 radius 37")
    plate_vol = cubit.get_last_id("volume")
    cubit.cmd(f"move volume {plate_vol} z {1.25}")

    # --- Ball: r=5, centre at z=21 ---
    cubit.cmd("create sphere radius 5")
    ball_vol = cubit.get_last_id("volume")
    cubit.cmd(f"move volume {ball_vol} z 21")

    # --- Mesh ---
    for vol in [floor_vol, plate_vol, ball_vol]:
        cubit.cmd(f"volume {vol} size {mesh_size_mm}")
        cubit.cmd(f"volume {vol} scheme tetmesh")
        cubit.cmd(f"mesh volume {vol}")

    # --- Blocks / nodesets ---
    cubit.cmd(f"block 1 volume {floor_vol}")
    cubit.cmd(f"nodeset 1 volume {floor_vol}")
    cubit.cmd("nodeset 1 name 'nodelist_1'")

    cubit.cmd(f"block 2 volume {plate_vol}")
    cubit.cmd(f"nodeset 2 volume {plate_vol}")
    cubit.cmd("nodeset 2 name 'nodelist_2'")

    cubit.cmd(f"block 3 volume {ball_vol}")
    cubit.cmd(f"nodeset 3 volume {ball_vol}")
    cubit.cmd("nodeset 3 name 'nodelist_3'")

    return floor_vol, plate_vol, ball_vol


# ---------------------------------------------------------------------------
# XML writer
# ---------------------------------------------------------------------------

def write_xml(
    mesh_file: str,
    mesh_size_mm: float,
    ball_speed: float,
    output_xml: str,
):
    """Write Peridigm XML for the ball-plate-floor scene."""
    mesh_size_m = mesh_size_mm * 0.001
    horizon = 3.015 * mesh_size_m

    # Materials (SI)
    floor_density, floor_bulk, floor_shear = 7700.0, 160.0e9, 78.3e9
    plate_density, plate_bulk, plate_shear = 2200.0, 14.90e9, 8.94e9
    ball_density,  ball_bulk,  ball_shear  = 7700.0, 160.0e9, 78.3e9

    c_p = max(
        math.sqrt((plate_bulk + 4 * plate_shear / 3) / plate_density),
        math.sqrt((ball_bulk  + 4 * ball_shear  / 3) / ball_density),
    )
    dt = _nice_dt(mesh_size_m / c_p * 0.7)
    final_time = 0.005
    output_frequency = _nice_freq(max(1, round(1.0 / (100000.0 * dt))))

    contact_radius = 1.1 * mesh_size_m
    search_radius  = 2.0 * mesh_size_m

    # Ball velocity: straight down (-z)
    vz = -ball_speed

    stem = Path(mesh_file).stem

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<ParameterList name="Peridigm">
  <ParameterList name="Discretization">
    <Parameter name="Type" type="string" value="Exodus"/>
    <Parameter name="Input Mesh File" type="string" value="{mesh_file}"/>
  </ParameterList>

  <ParameterList name="Materials">
    <ParameterList name="Floor Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{floor_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{floor_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{floor_shear:.6e}"/>
    </ParameterList>
    <ParameterList name="Plate Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{plate_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{plate_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{plate_shear:.6e}"/>
    </ParameterList>
    <ParameterList name="Ball Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{ball_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{ball_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{ball_shear:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Damage Models">
    <ParameterList name="Plate Damage">
      <Parameter name="Damage Model" type="string" value="Critical Stretch"/>
      <Parameter name="Critical Stretch" type="double" value="0.0005"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Blocks">
    <ParameterList name="Floor Block">
      <Parameter name="Block Names" type="string" value="block_1"/>
      <Parameter name="Material" type="string" value="Floor Material"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
    <ParameterList name="Plate Block">
      <Parameter name="Block Names" type="string" value="block_2"/>
      <Parameter name="Material" type="string" value="Plate Material"/>
      <Parameter name="Damage Model" type="string" value="Plate Damage"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
    <ParameterList name="Ball Block">
      <Parameter name="Block Names" type="string" value="block_3"/>
      <Parameter name="Material" type="string" value="Ball Material"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Contact">
    <Parameter name="Search Radius" type="double" value="{search_radius:.6e}"/>
    <Parameter name="Search Frequency" type="int" value="100"/>
    <ParameterList name="Models">
      <ParameterList name="My Contact Model">
        <Parameter name="Contact Model" type="string" value="Short Range Force"/>
        <Parameter name="Contact Radius" type="double" value="{contact_radius:.6e}"/>
        <Parameter name="Spring Constant" type="double" value="1.0e13"/>
      </ParameterList>
    </ParameterList>
    <ParameterList name="Interactions">
      <ParameterList name="General Contact">
        <Parameter name="Contact Model" type="string" value="My Contact Model"/>
      </ParameterList>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Boundary Conditions">
    <ParameterList name="Fix Floor X">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="nodelist_1"/>
      <Parameter name="Coordinate" type="string" value="x"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Fix Floor Y">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="nodelist_1"/>
      <Parameter name="Coordinate" type="string" value="y"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Fix Floor Z">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="nodelist_1"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Ball Initial Velocity Z">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="nodelist_3"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{vz:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Solver">
    <Parameter name="Verbose" type="bool" value="false"/>
    <Parameter name="Initial Time" type="double" value="0.0"/>
    <Parameter name="Final Time" type="double" value="{final_time:.6e}"/>
    <ParameterList name="Verlet">
      <Parameter name="Fixed dt" type="double" value="{dt:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Output">
    <Parameter name="Output File Type" type="string" value="ExodusII"/>
    <Parameter name="Output Filename" type="string" value="{stem}"/>
    <Parameter name="Output Frequency" type="int" value="{output_frequency}"/>
    <ParameterList name="Output Variables">
      <Parameter name="Coordinates" type="bool" value="true"/>
      <Parameter name="Displacement" type="bool" value="true"/>
      <Parameter name="Velocity" type="bool" value="true"/>
      <Parameter name="Force" type="bool" value="true"/>
      <Parameter name="Force_Density" type="bool" value="true"/>
      <Parameter name="Contact_Force_Density" type="bool" value="true"/>
      <Parameter name="Block_Id" type="bool" value="true"/>
      <Parameter name="Dilatation" type="bool" value="true"/>
      <Parameter name="Kinetic_Energy" type="bool" value="true"/>
      <Parameter name="Weighted_Volume" type="bool" value="true"/>
      <Parameter name="Volume" type="bool" value="true"/>
      <Parameter name="Damage" type="bool" value="true"/>
      <Parameter name="Global_Kinetic_Energy" type="bool" value="true"/>
      <Parameter name="Global_Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Global_Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Angular_Momentum" type="bool" value="true"/>
    </ParameterList>
  </ParameterList>
</ParameterList>
'''

    Path(output_xml).write_text(xml.strip() + "\n", encoding="utf-8")
    echo(f"  xml  -> {output_xml}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_scene(
    out_dir: str = ".",
    tag: str = "ball_plate_floor",
    mesh_size_mm: float = 2.0,
    ball_speed: float = 100.0,
):
    """Build geometry, mesh, export .g, write .xml.

    Returns dict with mesh_file and xml_file paths.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    build_geometry(mesh_size_mm)

    # Scale mm → m and export
    cubit.cmd("volume all scale 0.001")
    mesh_path = out_dir / f"{tag}.g"
    cubit.cmd(f'export mesh "{mesh_path}" overwrite')
    echo(f"  mesh -> {mesh_path}")

    # Write XML
    xml_path = out_dir / f"{tag}.xml"
    write_xml(f"{tag}.g", mesh_size_mm, ball_speed, str(xml_path))

    return {"mesh_file": str(mesh_path), "xml_file": str(xml_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate deterministic ball-plate-floor scene.",
    )
    parser.add_argument("--output-dir", default=".", help="Output directory (default: cwd)")
    parser.add_argument("--tag", default="ball_plate_floor", help="Output file tag")
    parser.add_argument("--mesh-size", type=float, default=2.0, help="Mesh size in mm (default: 2.0)")
    parser.add_argument("--ball-speed", type=float, default=100.0, help="Ball speed in m/s (default: 100.0)")
    args = parser.parse_args()

    cubit.init(["cubit", "-nojournal"])
    build_scene(
        out_dir=args.output_dir,
        tag=args.tag,
        mesh_size_mm=args.mesh_size,
        ball_speed=args.ball_speed,
    )
