"""
peridigm_xml.py — Peridigm XML configuration generators for fragmentation scenes.

One function per scene type.  Each function accepts a mesh filename and the
info dict returned by the corresponding build_*_scene() function in
generate_scenes.py, computes physical parameters (horizon, dt, final_time,
output_frequency) from the mesh info, and writes a Peridigm-compatible XML file.

No Cubit dependency — safe to import without a Cubit licence.

Material constants (SI units):
    Ceramic (vase/mug):  rho=2200 kg/m³, K=14.90e9 Pa, G=8.94e9 Pa
    Steel (floor/bullet): rho=7700 kg/m³, K=160.0e9 Pa, G=78.3e9 Pa
    Damage: Critical Stretch = 0.0005
"""

import math
from pathlib import Path


def _nice_freq(raw: int) -> int:
    """Round *down* to 1 significant figure for clean output numbering.

    Examples: 1429 → 1000, 5432 → 5000, 327 → 300, 14 → 10, 7 → 7.
    """
    if raw < 10:
        return max(1, raw)
    p = 10 ** int(math.log10(raw))
    return max(1, (raw // p) * p)

def _nice_dt(raw: float) -> float:
    """Round dt down to 1 significant figure.

    Examples: 3.5e-7 → 3e-7, 1.8e-6 → 1e-6, 4.2e-5 → 4e-5.
    """
    if raw <= 0:
        return raw
    exp = math.floor(math.log10(raw))
    base = 10 ** exp
    return math.floor(raw / base) * base
# ---------------------------------------------------------------------------
# Freefall scenes
# ---------------------------------------------------------------------------

def generate_vase_drop_peridigm_xml(
    mesh_file: str,
    info: dict,
    output_xml: str | None = None,
    *,
    floor_block: str = "block_1",
    vase_block: str = "block_2",
    floor_nodeset: str = "nodelist_1",
    vase_nodeset: str = "nodelist_2",
    gravity: float = 9.81,
    verbose: bool = False,
):
    """Generate Peridigm XML for a vase free-fall scene."""
    mesh_size_m = info["mesh_size"] * 0.001
    horizon = 3.015 * mesh_size_m
    drop_height_m = info["drop_height_mm"] * 0.001

    vase_density = 2200.0
    vase_bulk = 14.90e9
    vase_shear = 8.94e9
    c_p_ceramic = math.sqrt((vase_bulk + 4 * vase_shear / 3) / vase_density)

    floor_density = 7700.0
    floor_bulk = 160.0e9
    floor_shear = 78.3e9
    c_p_steel = math.sqrt((floor_bulk + 4 * floor_shear / 3) / floor_density)

    dt = _nice_dt(mesh_size_m / max(c_p_ceramic, c_p_steel) * 0.7)
    t_fall = math.sqrt(2 * drop_height_m / gravity)
    final_time = t_fall * 1.5

    total_steps = int(final_time / dt)
    output_frequency = _nice_freq(max(1, round(1.0 / (20000.0 * dt))))

    contact_radius = 1.1 * mesh_size_m
    search_radius = 2.0 * mesh_size_m
    critical_stretch = 0.0005

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<ParameterList name="Peridigm">

  <ParameterList name="Discretization">
    <Parameter name="Type" type="string" value="Exodus"/>
    <Parameter name="Input Mesh File" type="string" value="{mesh_file}"/>
  </ParameterList>

  <ParameterList name="Materials">
    <ParameterList name="Vase Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{vase_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{vase_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{vase_shear:.6e}"/>
    </ParameterList>
    <ParameterList name="Floor Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{floor_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{floor_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{floor_shear:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Damage Models">
    <ParameterList name="Vase Damage">
      <Parameter name="Damage Model" type="string" value="Critical Stretch"/>
      <Parameter name="Critical Stretch" type="double" value="{critical_stretch}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Blocks">
    <ParameterList name="Floor Block">
      <Parameter name="Block Names" type="string" value="{floor_block}"/>
      <Parameter name="Material" type="string" value="Floor Material"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
    <ParameterList name="Vase Block">
      <Parameter name="Block Names" type="string" value="{vase_block}"/>
      <Parameter name="Material" type="string" value="Vase Material"/>
      <Parameter name="Damage Model" type="string" value="Vase Damage"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Contact">
    <Parameter name="Search Radius" type="double" value="{search_radius:.6e}"/>
    <Parameter name="Search Frequency" type="int" value="100"/>
    <ParameterList name="Models">
      <ParameterList name="Vase Floor Contact">
        <Parameter name="Contact Model" type="string" value="Short Range Force"/>
        <Parameter name="Contact Radius" type="double" value="{contact_radius:.6e}"/>
        <Parameter name="Spring Constant" type="double" value="1.0e12"/>
      </ParameterList>
    </ParameterList>
    <ParameterList name="Interactions">
      <ParameterList name="Interaction Vase Floor">
        <Parameter name="First Block" type="string" value="{vase_block}"/>
        <Parameter name="Second Block" type="string" value="{floor_block}"/>
        <Parameter name="Contact Model" type="string" value="Vase Floor Contact"/>
      </ParameterList>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Boundary Conditions">
    <ParameterList name="Fix Floor X">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="{floor_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="x"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Fix Floor Y">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="{floor_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="y"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Fix Floor Z">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="{floor_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Gravity Vase">
      <Parameter name="Type" type="string" value="Body Force"/>
      <Parameter name="Node Set" type="string" value="{vase_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{-gravity * vase_density:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Solver">
    <Parameter name="Verbose" type="bool" value="{str(verbose).lower()}"/>
    <Parameter name="Initial Time" type="double" value="0.0"/>
    <Parameter name="Final Time" type="double" value="{final_time:.6e}"/>
    <ParameterList name="Verlet">
      <Parameter name="Fixed dt" type="double" value="{dt:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Output">
    <Parameter name="Output File Type" type="string" value="ExodusII"/>
    <Parameter name="Output Filename" type="string" value="{Path(mesh_file).stem}"/>
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
      <Parameter name="Global_Kinetic_Energy" type="bool" value="true"/>
      <Parameter name="Global_Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Global_Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Damage" type="bool" value="true"/>
    </ParameterList>
  </ParameterList>

</ParameterList>
'''

    if output_xml is not None:
        Path(output_xml).write_text(xml, encoding="utf-8")
    return xml


def generate_mug_drop_peridigm_xml(
    mesh_file: str,
    info: dict,
    output_xml: str | None = None,
    *,
    floor_block: str = "block_1",
    mug_block: str = "block_2",
    floor_nodeset: str = "nodelist_1",
    mug_nodeset: str = "nodelist_2",
    gravity: float = 9.81,
    verbose: bool = False,
):
    """Generate Peridigm XML for a mug free-fall scene."""
    mesh_size_m = info["mesh_size"] * 0.001
    horizon = 3.015 * mesh_size_m

    mug_density = 2200.0
    mug_bulk = 14.90e9
    mug_shear = 8.94e9
    c_p_ceramic = math.sqrt((mug_bulk + 4 * mug_shear / 3) / mug_density)

    floor_density = 7700.0
    floor_bulk = 160.0e9
    floor_shear = 78.3e9
    c_p_steel = math.sqrt((floor_bulk + 4 * floor_shear / 3) / floor_density)

    dt = _nice_dt(mesh_size_m / max(c_p_ceramic, c_p_steel) * 0.7)
    final_time = 2.0
    fps = 20000
    total_steps = int(final_time / dt)
    output_frequency = _nice_freq(max(1, total_steps // int(final_time * fps)))

    contact_radius = 1.1 * mesh_size_m
    search_radius = 2.0 * mesh_size_m
    critical_stretch = 0.0005

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<ParameterList name="Peridigm">

  <ParameterList name="Discretization">
    <Parameter name="Type" type="string" value="Exodus"/>
    <Parameter name="Input Mesh File" type="string" value="{mesh_file}"/>
  </ParameterList>

  <ParameterList name="Materials">
    <ParameterList name="Mug Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{mug_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{mug_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{mug_shear:.6e}"/>
    </ParameterList>
    <ParameterList name="Floor Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{floor_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{floor_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{floor_shear:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Damage Models">
    <ParameterList name="Mug Damage">
      <Parameter name="Damage Model" type="string" value="Critical Stretch"/>
      <Parameter name="Critical Stretch" type="double" value="{critical_stretch}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Blocks">
    <ParameterList name="Floor Block">
      <Parameter name="Block Names" type="string" value="{floor_block}"/>
      <Parameter name="Material" type="string" value="Floor Material"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
    <ParameterList name="Mug Block">
      <Parameter name="Block Names" type="string" value="{mug_block}"/>
      <Parameter name="Material" type="string" value="Mug Material"/>
      <Parameter name="Damage Model" type="string" value="Mug Damage"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Contact">
    <Parameter name="Search Radius" type="double" value="{search_radius:.6e}"/>
    <Parameter name="Search Frequency" type="int" value="100"/>
    <ParameterList name="Models">
      <ParameterList name="Mug Floor Contact">
        <Parameter name="Contact Model" type="string" value="Short Range Force"/>
        <Parameter name="Contact Radius" type="double" value="{contact_radius:.6e}"/>
        <Parameter name="Spring Constant" type="double" value="1.0e12"/>
      </ParameterList>
    </ParameterList>
    <ParameterList name="Interactions">
      <ParameterList name="Interaction Mug Floor">
        <Parameter name="First Block" type="string" value="{mug_block}"/>
        <Parameter name="Second Block" type="string" value="{floor_block}"/>
        <Parameter name="Contact Model" type="string" value="Mug Floor Contact"/>
      </ParameterList>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Boundary Conditions">
    <ParameterList name="Fix Floor X">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="{floor_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="x"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Fix Floor Y">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="{floor_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="y"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Fix Floor Z">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="{floor_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Gravity Mug">
      <Parameter name="Type" type="string" value="Body Force"/>
      <Parameter name="Node Set" type="string" value="{mug_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{-mug_density * gravity:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Solver">
    <Parameter name="Verbose" type="bool" value="{str(verbose).lower()}"/>
    <Parameter name="Initial Time" type="double" value="0.0"/>
    <Parameter name="Final Time" type="double" value="{final_time:.6e}"/>
    <ParameterList name="Verlet">
      <Parameter name="Fixed dt" type="double" value="{dt:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Output">
    <Parameter name="Output File Type" type="string" value="ExodusII"/>
    <Parameter name="Output Filename" type="string" value="{Path(mesh_file).stem}"/>
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
      <Parameter name="Global_Kinetic_Energy" type="bool" value="true"/>
      <Parameter name="Global_Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Global_Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Damage" type="bool" value="true"/>
    </ParameterList>
  </ParameterList>

</ParameterList>
'''

    if output_xml is not None:
        Path(output_xml).write_text(xml, encoding="utf-8")
    return xml


# ---------------------------------------------------------------------------
# Bullet impact scenes (with floor)
# ---------------------------------------------------------------------------

def generate_bullet_vase_peridigm_xml(
    mesh_file: str,
    info: dict,
    output_xml: str | None = None,
    *,
    floor_block: str = "block_1",
    vase_block: str = "block_2",
    bullet_block: str = "block_3",
    floor_nodeset: str = "nodelist_1",
    vase_nodeset: str = "nodelist_2",
    bullet_nodeset: str = "nodelist_3",
    bullet_speed: float = 400.0,
    gravity: float = 9.81,
    verbose: bool = False,
):
    """Generate Peridigm XML for a bullet-impacts-vase scene (vase on floor)."""
    mesh_size_m = info["mesh_size"] * 0.001
    horizon = 3.015 * mesh_size_m

    vase_density = 2200.0
    vase_bulk = 14.90e9
    vase_shear = 8.94e9

    floor_density = 7700.0
    floor_bulk = 160.0e9
    floor_shear = 78.3e9

    bullet_density = 7700.0
    bullet_bulk = 160.0e9
    bullet_shear = 78.3e9

    c_p_ceramic = math.sqrt((vase_bulk + 4 * vase_shear / 3) / vase_density)
    c_p_steel = math.sqrt((floor_bulk + 4 * floor_shear / 3) / floor_density)
    dt = _nice_dt(mesh_size_m / max(c_p_ceramic, c_p_steel) * 0.7)

    final_time = 0.05
    fps = 20000
    total_steps = int(final_time / dt)
    output_frequency = _nice_freq(max(1, total_steps // int(final_time * fps)))

    contact_radius = 1.1 * mesh_size_m
    search_radius = 2.0 * mesh_size_m
    critical_stretch = 0.0005

    vx = -info["dx"] * bullet_speed
    vy = -info["dy"] * bullet_speed
    vz = -info["dz"] * bullet_speed

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<ParameterList name="Peridigm">

  <ParameterList name="Discretization">
    <Parameter name="Type" type="string" value="Exodus"/>
    <Parameter name="Input Mesh File" type="string" value="{mesh_file}"/>
  </ParameterList>

  <ParameterList name="Materials">
    <ParameterList name="Vase Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{vase_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{vase_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{vase_shear:.6e}"/>
    </ParameterList>
    <ParameterList name="Floor Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{floor_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{floor_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{floor_shear:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{bullet_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{bullet_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{bullet_shear:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Damage Models">
    <ParameterList name="Vase Damage">
      <Parameter name="Damage Model" type="string" value="Critical Stretch"/>
      <Parameter name="Critical Stretch" type="double" value="{critical_stretch}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Blocks">
    <ParameterList name="Floor Block">
      <Parameter name="Block Names" type="string" value="{floor_block}"/>
      <Parameter name="Material" type="string" value="Floor Material"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
    <ParameterList name="Vase Block">
      <Parameter name="Block Names" type="string" value="{vase_block}"/>
      <Parameter name="Material" type="string" value="Vase Material"/>
      <Parameter name="Damage Model" type="string" value="Vase Damage"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Block">
      <Parameter name="Block Names" type="string" value="{bullet_block}"/>
      <Parameter name="Material" type="string" value="Bullet Material"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Contact">
    <Parameter name="Search Radius" type="double" value="{search_radius:.6e}"/>
    <Parameter name="Search Frequency" type="int" value="100"/>
    <ParameterList name="Models">
      <ParameterList name="Bullet Vase Contact">
        <Parameter name="Contact Model" type="string" value="Short Range Force"/>
        <Parameter name="Contact Radius" type="double" value="{contact_radius:.6e}"/>
        <Parameter name="Spring Constant" type="double" value="1.0e13"/>
      </ParameterList>
      <ParameterList name="Vase Floor Contact">
        <Parameter name="Contact Model" type="string" value="Short Range Force"/>
        <Parameter name="Contact Radius" type="double" value="{contact_radius:.6e}"/>
        <Parameter name="Spring Constant" type="double" value="1.0e12"/>
      </ParameterList>
      <ParameterList name="Bullet Floor Contact">
        <Parameter name="Contact Model" type="string" value="Short Range Force"/>
        <Parameter name="Contact Radius" type="double" value="{contact_radius:.6e}"/>
        <Parameter name="Spring Constant" type="double" value="1.0e13"/>
      </ParameterList>
    </ParameterList>
    <ParameterList name="Interactions">
      <ParameterList name="Interaction Bullet Vase">
        <Parameter name="First Block" type="string" value="{bullet_block}"/>
        <Parameter name="Second Block" type="string" value="{vase_block}"/>
        <Parameter name="Contact Model" type="string" value="Bullet Vase Contact"/>
      </ParameterList>
      <ParameterList name="Interaction Vase Floor">
        <Parameter name="First Block" type="string" value="{vase_block}"/>
        <Parameter name="Second Block" type="string" value="{floor_block}"/>
        <Parameter name="Contact Model" type="string" value="Vase Floor Contact"/>
      </ParameterList>
      <ParameterList name="Interaction Bullet Floor">
        <Parameter name="First Block" type="string" value="{bullet_block}"/>
        <Parameter name="Second Block" type="string" value="{floor_block}"/>
        <Parameter name="Contact Model" type="string" value="Bullet Floor Contact"/>
      </ParameterList>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Boundary Conditions">
    <ParameterList name="Fix Floor X">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="{floor_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="x"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Fix Floor Y">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="{floor_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="y"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Fix Floor Z">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="{floor_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Gravity Vase">
      <Parameter name="Type" type="string" value="Body Force"/>
      <Parameter name="Node Set" type="string" value="{vase_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{-vase_density * gravity:.6e}"/>
    </ParameterList>
    <ParameterList name="Gravity Bullet">
      <Parameter name="Type" type="string" value="Body Force"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{-bullet_density * gravity:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Velocity X">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="x"/>
      <Parameter name="Value" type="string" value="{vx:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Velocity Y">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="y"/>
      <Parameter name="Value" type="string" value="{vy:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Velocity Z">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{vz:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Solver">
    <Parameter name="Verbose" type="bool" value="{str(verbose).lower()}"/>
    <Parameter name="Initial Time" type="double" value="0.0"/>
    <Parameter name="Final Time" type="double" value="{final_time:.6e}"/>
    <ParameterList name="Verlet">
      <Parameter name="Fixed dt" type="double" value="{dt:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Output">
    <Parameter name="Output File Type" type="string" value="ExodusII"/>
    <Parameter name="Output Filename" type="string" value="{Path(mesh_file).stem}"/>
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
      <Parameter name="Global_Kinetic_Energy" type="bool" value="true"/>
      <Parameter name="Global_Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Global_Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Damage" type="bool" value="true"/>
    </ParameterList>
  </ParameterList>

</ParameterList>
'''

    if output_xml is not None:
        Path(output_xml).write_text(xml, encoding="utf-8")
    return xml


def generate_bullet_mug_peridigm_xml(
    mesh_file: str,
    info: dict,
    output_xml: str | None = None,
    *,
    floor_block: str = "block_1",
    mug_block: str = "block_2",
    bullet_block: str = "block_3",
    floor_nodeset: str = "nodelist_1",
    mug_nodeset: str = "nodelist_2",
    bullet_nodeset: str = "nodelist_3",
    bullet_speed: float = 400.0,
    gravity: float = 9.81,
    verbose: bool = False,
):
    """Generate Peridigm XML for a bullet-impacts-mug scene (mug on floor)."""
    mesh_size_m = info["mesh_size"] * 0.001
    horizon = 3.015 * mesh_size_m

    mug_density = 2200.0
    mug_bulk = 14.90e9
    mug_shear = 8.94e9

    floor_density = 7700.0
    floor_bulk = 160.0e9
    floor_shear = 78.3e9

    bullet_density = 7700.0
    bullet_bulk = 160.0e9
    bullet_shear = 78.3e9

    c_p_ceramic = math.sqrt((mug_bulk + 4 * mug_shear / 3) / mug_density)
    c_p_steel = math.sqrt((floor_bulk + 4 * floor_shear / 3) / floor_density)
    dt = _nice_dt(mesh_size_m / max(c_p_ceramic, c_p_steel) * 0.7)

    final_time = 0.05
    fps = 20000
    total_steps = int(final_time / dt)
    output_frequency = _nice_freq(max(1, total_steps // int(final_time * fps)))

    contact_radius = 1.1 * mesh_size_m
    search_radius = 2.0 * mesh_size_m
    critical_stretch = 0.0005

    vx = -info["dx"] * bullet_speed
    vy = -info["dy"] * bullet_speed
    vz = -info["dz"] * bullet_speed

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<ParameterList name="Peridigm">

  <ParameterList name="Discretization">
    <Parameter name="Type" type="string" value="Exodus"/>
    <Parameter name="Input Mesh File" type="string" value="{mesh_file}"/>
  </ParameterList>

  <ParameterList name="Materials">
    <ParameterList name="Mug Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{mug_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{mug_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{mug_shear:.6e}"/>
    </ParameterList>
    <ParameterList name="Floor Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{floor_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{floor_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{floor_shear:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{bullet_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{bullet_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{bullet_shear:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Damage Models">
    <ParameterList name="Mug Damage">
      <Parameter name="Damage Model" type="string" value="Critical Stretch"/>
      <Parameter name="Critical Stretch" type="double" value="{critical_stretch}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Blocks">
    <ParameterList name="Floor Block">
      <Parameter name="Block Names" type="string" value="{floor_block}"/>
      <Parameter name="Material" type="string" value="Floor Material"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
    <ParameterList name="Mug Block">
      <Parameter name="Block Names" type="string" value="{mug_block}"/>
      <Parameter name="Material" type="string" value="Mug Material"/>
      <Parameter name="Damage Model" type="string" value="Mug Damage"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Block">
      <Parameter name="Block Names" type="string" value="{bullet_block}"/>
      <Parameter name="Material" type="string" value="Bullet Material"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Contact">
    <Parameter name="Search Radius" type="double" value="{search_radius:.6e}"/>
    <Parameter name="Search Frequency" type="int" value="100"/>
    <ParameterList name="Models">
      <ParameterList name="Bullet Mug Contact">
        <Parameter name="Contact Model" type="string" value="Short Range Force"/>
        <Parameter name="Contact Radius" type="double" value="{contact_radius:.6e}"/>
        <Parameter name="Spring Constant" type="double" value="1.0e13"/>
      </ParameterList>
      <ParameterList name="Mug Floor Contact">
        <Parameter name="Contact Model" type="string" value="Short Range Force"/>
        <Parameter name="Contact Radius" type="double" value="{contact_radius:.6e}"/>
        <Parameter name="Spring Constant" type="double" value="1.0e12"/>
      </ParameterList>
      <ParameterList name="Bullet Floor Contact">
        <Parameter name="Contact Model" type="string" value="Short Range Force"/>
        <Parameter name="Contact Radius" type="double" value="{contact_radius:.6e}"/>
        <Parameter name="Spring Constant" type="double" value="1.0e13"/>
      </ParameterList>
    </ParameterList>
    <ParameterList name="Interactions">
      <ParameterList name="Interaction Bullet Mug">
        <Parameter name="First Block" type="string" value="{bullet_block}"/>
        <Parameter name="Second Block" type="string" value="{mug_block}"/>
        <Parameter name="Contact Model" type="string" value="Bullet Mug Contact"/>
      </ParameterList>
      <ParameterList name="Interaction Mug Floor">
        <Parameter name="First Block" type="string" value="{mug_block}"/>
        <Parameter name="Second Block" type="string" value="{floor_block}"/>
        <Parameter name="Contact Model" type="string" value="Mug Floor Contact"/>
      </ParameterList>
      <ParameterList name="Interaction Bullet Floor">
        <Parameter name="First Block" type="string" value="{bullet_block}"/>
        <Parameter name="Second Block" type="string" value="{floor_block}"/>
        <Parameter name="Contact Model" type="string" value="Bullet Floor Contact"/>
      </ParameterList>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Boundary Conditions">
    <ParameterList name="Fix Floor X">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="{floor_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="x"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Fix Floor Y">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="{floor_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="y"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Fix Floor Z">
      <Parameter name="Type" type="string" value="Prescribed Displacement"/>
      <Parameter name="Node Set" type="string" value="{floor_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="0.0"/>
    </ParameterList>
    <ParameterList name="Gravity Mug">
      <Parameter name="Type" type="string" value="Body Force"/>
      <Parameter name="Node Set" type="string" value="{mug_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{-mug_density * gravity:.6e}"/>
    </ParameterList>
    <ParameterList name="Gravity Bullet">
      <Parameter name="Type" type="string" value="Body Force"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{-bullet_density * gravity:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Velocity X">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="x"/>
      <Parameter name="Value" type="string" value="{vx:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Velocity Y">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="y"/>
      <Parameter name="Value" type="string" value="{vy:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Velocity Z">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{vz:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Solver">
    <Parameter name="Verbose" type="bool" value="{str(verbose).lower()}"/>
    <Parameter name="Initial Time" type="double" value="0.0"/>
    <Parameter name="Final Time" type="double" value="{final_time:.6e}"/>
    <ParameterList name="Verlet">
      <Parameter name="Fixed dt" type="double" value="{dt:.6e}"/>
    </ParameterList>
  </ParameterList>

  <ParameterList name="Output">
    <Parameter name="Output File Type" type="string" value="ExodusII"/>
    <Parameter name="Output Filename" type="string" value="{Path(mesh_file).stem}"/>
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
      <Parameter name="Global_Kinetic_Energy" type="bool" value="true"/>
      <Parameter name="Global_Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Global_Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Damage" type="bool" value="true"/>
    </ParameterList>
  </ParameterList>

</ParameterList>
'''

    if output_xml is not None:
        Path(output_xml).write_text(xml, encoding="utf-8")
    return xml


# ---------------------------------------------------------------------------
# Bullet impact scenes (no floor)
# ---------------------------------------------------------------------------

def generate_bullet_vase_nf_peridigm_xml(
    mesh_file: str,
    info: dict,
    output_xml: str | None = None,
    *,
    vase_block: str = "block_1",
    bullet_block: str = "block_2",
    vase_nodeset: str = "nodelist_1",
    bullet_nodeset: str = "nodelist_2",
    bullet_speed: float = 400.0,
    gravity: float = 9.81,
    verbose: bool = False,
):
    """Generate Peridigm XML for bullet-impacts-vase with no floor."""
    mesh_size_m = info["mesh_size"] * 0.001
    horizon = 3.015 * mesh_size_m
    vase_density, vase_bulk, vase_shear = 2200.0, 14.90e9, 8.94e9
    bullet_density, bullet_bulk, bullet_shear = 7700.0, 160.0e9, 78.3e9
    c_p = max(
        math.sqrt((vase_bulk + 4 * vase_shear / 3) / vase_density),
        math.sqrt((bullet_bulk + 4 * bullet_shear / 3) / bullet_density),
    )
    dt = _nice_dt(mesh_size_m / c_p * 0.7)
    final_time = 0.005
    total_steps = int(final_time / dt)
    output_frequency = _nice_freq(max(1, round(1.0 / (20000.0 * dt))))
    contact_radius = 1.1 * mesh_size_m
    search_radius = 2.0 * mesh_size_m
    vx = -info["dx"] * bullet_speed
    vy = -info["dy"] * bullet_speed
    vz = -info["dz"] * bullet_speed

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<ParameterList name="Peridigm">
  <ParameterList name="Discretization">
    <Parameter name="Type" type="string" value="Exodus"/>
    <Parameter name="Input Mesh File" type="string" value="{mesh_file}"/>
  </ParameterList>
  <ParameterList name="Materials">
    <ParameterList name="Vase Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{vase_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{vase_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{vase_shear:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{bullet_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{bullet_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{bullet_shear:.6e}"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Damage Models">
    <ParameterList name="Vase Damage">
      <Parameter name="Damage Model" type="string" value="Critical Stretch"/>
      <Parameter name="Critical Stretch" type="double" value="0.0005"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Blocks">
    <ParameterList name="Vase Block">
      <Parameter name="Block Names" type="string" value="{vase_block}"/>
      <Parameter name="Material" type="string" value="Vase Material"/>
      <Parameter name="Damage Model" type="string" value="Vase Damage"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Block">
      <Parameter name="Block Names" type="string" value="{bullet_block}"/>
      <Parameter name="Material" type="string" value="Bullet Material"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Contact">
    <Parameter name="Search Radius" type="double" value="{search_radius:.6e}"/>
    <Parameter name="Search Frequency" type="int" value="100"/>
    <ParameterList name="Models">
      <ParameterList name="Bullet Vase Contact">
        <Parameter name="Contact Model" type="string" value="Short Range Force"/>
        <Parameter name="Contact Radius" type="double" value="{contact_radius:.6e}"/>
        <Parameter name="Spring Constant" type="double" value="1.0e13"/>
      </ParameterList>
    </ParameterList>
    <ParameterList name="Interactions">
      <ParameterList name="Interaction Bullet Vase">
        <Parameter name="First Block" type="string" value="{bullet_block}"/>
        <Parameter name="Second Block" type="string" value="{vase_block}"/>
        <Parameter name="Contact Model" type="string" value="Bullet Vase Contact"/>
      </ParameterList>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Boundary Conditions">
    <ParameterList name="Gravity Vase">
      <Parameter name="Type" type="string" value="Body Force"/>
      <Parameter name="Node Set" type="string" value="{vase_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{-vase_density * gravity:.6e}"/>
    </ParameterList>
    <ParameterList name="Gravity Bullet">
      <Parameter name="Type" type="string" value="Body Force"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{-bullet_density * gravity:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Velocity X">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="x"/>
      <Parameter name="Value" type="string" value="{vx:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Velocity Y">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="y"/>
      <Parameter name="Value" type="string" value="{vy:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Velocity Z">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{vz:.6e}"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Solver">
    <Parameter name="Verbose" type="bool" value="{str(verbose).lower()}"/>
    <Parameter name="Initial Time" type="double" value="0.0"/>
    <Parameter name="Final Time" type="double" value="{final_time:.6e}"/>
    <ParameterList name="Verlet">
      <Parameter name="Fixed dt" type="double" value="{dt:.6e}"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Output">
    <Parameter name="Output File Type" type="string" value="ExodusII"/>
    <Parameter name="Output Filename" type="string" value="{Path(mesh_file).stem}"/>
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
      <Parameter name="Global_Kinetic_Energy" type="bool" value="true"/>
      <Parameter name="Global_Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Global_Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Damage" type="bool" value="true"/>
    </ParameterList>
  </ParameterList>
</ParameterList>
'''
    if output_xml is not None:
        Path(output_xml).write_text(xml, encoding="utf-8")
    return xml


def generate_bullet_mug_nf_peridigm_xml(
    mesh_file: str,
    info: dict,
    output_xml: str | None = None,
    *,
    mug_block: str = "block_1",
    bullet_block: str = "block_2",
    mug_nodeset: str = "nodelist_1",
    bullet_nodeset: str = "nodelist_2",
    bullet_speed: float = 400.0,
    gravity: float = 9.81,
    verbose: bool = False,
):
    """Generate Peridigm XML for bullet-impacts-mug with no floor."""
    mesh_size_m = info["mesh_size"] * 0.001
    horizon = 3.015 * mesh_size_m
    mug_density, mug_bulk, mug_shear = 2200.0, 14.90e9, 8.94e9
    bullet_density, bullet_bulk, bullet_shear = 7700.0, 160.0e9, 78.3e9
    c_p = max(
        math.sqrt((mug_bulk + 4 * mug_shear / 3) / mug_density),
        math.sqrt((bullet_bulk + 4 * bullet_shear / 3) / bullet_density),
    )
    dt = _nice_dt(mesh_size_m / c_p * 0.7)
    final_time = 0.05
    total_steps = int(final_time / dt)
    output_frequency = _nice_freq(max(1, round(1.0 / (20000.0 * dt))))
    contact_radius = 1.1 * mesh_size_m
    search_radius = 2.0 * mesh_size_m
    vx = -info["dx"] * bullet_speed
    vy = -info["dy"] * bullet_speed
    vz = -info["dz"] * bullet_speed

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<ParameterList name="Peridigm">
  <ParameterList name="Discretization">
    <Parameter name="Type" type="string" value="Exodus"/>
    <Parameter name="Input Mesh File" type="string" value="{mesh_file}"/>
  </ParameterList>
  <ParameterList name="Materials">
    <ParameterList name="Mug Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{mug_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{mug_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{mug_shear:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Material">
      <Parameter name="Material Model" type="string" value="Elastic"/>
      <Parameter name="Density" type="double" value="{bullet_density}"/>
      <Parameter name="Bulk Modulus" type="double" value="{bullet_bulk:.6e}"/>
      <Parameter name="Shear Modulus" type="double" value="{bullet_shear:.6e}"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Damage Models">
    <ParameterList name="Mug Damage">
      <Parameter name="Damage Model" type="string" value="Critical Stretch"/>
      <Parameter name="Critical Stretch" type="double" value="0.0005"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Blocks">
    <ParameterList name="Mug Block">
      <Parameter name="Block Names" type="string" value="{mug_block}"/>
      <Parameter name="Material" type="string" value="Mug Material"/>
      <Parameter name="Damage Model" type="string" value="Mug Damage"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Block">
      <Parameter name="Block Names" type="string" value="{bullet_block}"/>
      <Parameter name="Material" type="string" value="Bullet Material"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Contact">
    <Parameter name="Search Radius" type="double" value="{search_radius:.6e}"/>
    <Parameter name="Search Frequency" type="int" value="100"/>
    <ParameterList name="Models">
      <ParameterList name="Bullet Mug Contact">
        <Parameter name="Contact Model" type="string" value="Short Range Force"/>
        <Parameter name="Contact Radius" type="double" value="{contact_radius:.6e}"/>
        <Parameter name="Spring Constant" type="double" value="1.0e13"/>
      </ParameterList>
    </ParameterList>
    <ParameterList name="Interactions">
      <ParameterList name="Interaction Bullet Mug">
        <Parameter name="First Block" type="string" value="{bullet_block}"/>
        <Parameter name="Second Block" type="string" value="{mug_block}"/>
        <Parameter name="Contact Model" type="string" value="Bullet Mug Contact"/>
      </ParameterList>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Boundary Conditions">
    <ParameterList name="Gravity Mug">
      <Parameter name="Type" type="string" value="Body Force"/>
      <Parameter name="Node Set" type="string" value="{mug_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{-mug_density * gravity:.6e}"/>
    </ParameterList>
    <ParameterList name="Gravity Bullet">
      <Parameter name="Type" type="string" value="Body Force"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{-bullet_density * gravity:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Velocity X">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="x"/>
      <Parameter name="Value" type="string" value="{vx:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Velocity Y">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="y"/>
      <Parameter name="Value" type="string" value="{vy:.6e}"/>
    </ParameterList>
    <ParameterList name="Bullet Velocity Z">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{bullet_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{vz:.6e}"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Solver">
    <Parameter name="Verbose" type="bool" value="{str(verbose).lower()}"/>
    <Parameter name="Initial Time" type="double" value="0.0"/>
    <Parameter name="Final Time" type="double" value="{final_time:.6e}"/>
    <ParameterList name="Verlet">
      <Parameter name="Fixed dt" type="double" value="{dt:.6e}"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Output">
    <Parameter name="Output File Type" type="string" value="ExodusII"/>
    <Parameter name="Output Filename" type="string" value="{Path(mesh_file).stem}"/>
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
      <Parameter name="Global_Kinetic_Energy" type="bool" value="true"/>
      <Parameter name="Global_Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Global_Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Damage" type="bool" value="true"/>
    </ParameterList>
  </ParameterList>
</ParameterList>
'''
    if output_xml is not None:
        Path(output_xml).write_text(xml, encoding="utf-8")
    return xml


# ---------------------------------------------------------------------------
# Ball-plate impact (no floor)
# ---------------------------------------------------------------------------

def generate_ball_plate_peridigm_xml(
    mesh_file: str,
    info: dict,
    output_xml: str | None = None,
    *,
    plate_block: str = "block_1",
    ball_block: str = "block_2",
    plate_nodeset: str = "nodelist_1",
    ball_nodeset: str = "nodelist_2",
    gravity: float = 9.81,
    dt: float = 2.0e-7,
    final_time: float = 8.0e-4,
    output_frequency: int = 25,
    critical_stretch: float = 0.0005,
    verbose: bool = False,
) -> str:
    """Generate Peridigm XML for ball-plate impact (no floor).

    Block 1 / Nodeset 1: Plate (brittle ceramic, with damage)
    Block 2 / Nodeset 2: Ball  (steel, no damage)
    """
    mesh_size_m = info["mesh_size"] * 0.001
    horizon = 3.015 * mesh_size_m

    plate_density, plate_bulk, plate_shear = 2200.0, 14.90e9, 8.94e9
    ball_density,  ball_bulk,  ball_shear  = 7700.0, 160.0e9, 78.3e9

    if dt <= 0.0:
        raise ValueError("dt must be positive")
    if final_time <= 0.0:
        raise ValueError("final_time must be positive")
    if output_frequency <= 0:
        raise ValueError("output_frequency must be positive")
    if critical_stretch <= 0.0:
        raise ValueError("critical_stretch must be positive")

    contact_radius = info.get("contact_radius_mm", 1.1 * info["mesh_size"]) * 0.001
    search_radius = info.get("search_radius_mm", 1.5 * info["mesh_size"]) * 0.001

    vx = info["dx"] * info["ball_speed"]
    vy = info["dy"] * info["ball_speed"]
    vz = info["dz"] * info["ball_speed"]

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<ParameterList name="Peridigm">
  <ParameterList name="Discretization">
    <Parameter name="Type" type="string" value="Exodus"/>
    <Parameter name="Input Mesh File" type="string" value="{mesh_file}"/>
  </ParameterList>
  <ParameterList name="Materials">
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
      <Parameter name="Critical Stretch" type="double" value="{critical_stretch:.6e}"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Blocks">
    <ParameterList name="Plate Block">
      <Parameter name="Block Names" type="string" value="{plate_block}"/>
      <Parameter name="Material" type="string" value="Plate Material"/>
      <Parameter name="Damage Model" type="string" value="Plate Damage"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
    <ParameterList name="Ball Block">
      <Parameter name="Block Names" type="string" value="{ball_block}"/>
      <Parameter name="Material" type="string" value="Ball Material"/>
      <Parameter name="Horizon" type="double" value="{horizon:.6e}"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Contact">
    <Parameter name="Search Radius" type="double" value="{search_radius:.6e}"/>
    <Parameter name="Search Frequency" type="int" value="1"/>
    <ParameterList name="Models">
      <ParameterList name="Ball Plate Contact">
        <Parameter name="Contact Model" type="string" value="Short Range Force"/>
        <Parameter name="Contact Radius" type="double" value="{contact_radius:.6e}"/>
        <Parameter name="Spring Constant" type="double" value="1.0e13"/>
      </ParameterList>
    </ParameterList>
    <ParameterList name="Interactions">
      <ParameterList name="Interaction Ball Plate">
        <Parameter name="First Block" type="string" value="{ball_block}"/>
        <Parameter name="Second Block" type="string" value="{plate_block}"/>
        <Parameter name="Contact Model" type="string" value="Ball Plate Contact"/>
      </ParameterList>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Boundary Conditions">
    <ParameterList name="Ball Initial Velocity X">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{ball_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="x"/>
      <Parameter name="Value" type="string" value="{vx:.6e}"/>
    </ParameterList>
    <ParameterList name="Ball Initial Velocity Y">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{ball_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="y"/>
      <Parameter name="Value" type="string" value="{vy:.6e}"/>
    </ParameterList>
    <ParameterList name="Ball Initial Velocity Z">
      <Parameter name="Type" type="string" value="Initial Velocity"/>
      <Parameter name="Node Set" type="string" value="{ball_nodeset}"/>
      <Parameter name="Coordinate" type="string" value="z"/>
      <Parameter name="Value" type="string" value="{vz:.6e}"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Solver">
    <Parameter name="Verbose" type="bool" value="{str(verbose).lower()}"/>
    <Parameter name="Initial Time" type="double" value="0.0"/>
    <Parameter name="Final Time" type="double" value="{final_time:.6e}"/>
    <ParameterList name="Verlet">
      <Parameter name="Fixed dt" type="double" value="{dt:.6e}"/>
    </ParameterList>
  </ParameterList>
  <ParameterList name="Output">
    <Parameter name="Output File Type" type="string" value="ExodusII"/>
    <Parameter name="Output Filename" type="string" value="{Path(mesh_file).stem}"/>
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
      <Parameter name="Global_Kinetic_Energy" type="bool" value="true"/>
      <Parameter name="Global_Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Global_Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Linear_Momentum" type="bool" value="true"/>
      <Parameter name="Angular_Momentum" type="bool" value="true"/>
      <Parameter name="Damage" type="bool" value="true"/>
    </ParameterList>
  </ParameterList>
</ParameterList>
'''
    if output_xml is not None:
        Path(output_xml).write_text(xml, encoding="utf-8")
    return xml
