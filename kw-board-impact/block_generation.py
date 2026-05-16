"""
block_generation.py — Kalthoff-Winkler board geometry and mesh generation.

All geometry is built in millimeters; the mesh is scaled to meters on export.
Cubit must be initialised by the caller before calling build_kw_scene().

Standalone usage:
    python block_generation.py [--output-dir DIR]
"""

import sys
from pathlib import Path

try:
    import cubit
except ImportError:
    cubit = None


def echo(msg: str) -> None:
    sys.__stdout__.write(str(msg) + "\n")
    sys.__stdout__.flush()


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def make_Kalthoff_Winkler_board(x, y, z, hole_gap, hole_width, hole_depth):
    if x <= 0 or y <= 0 or z <= 0:
        raise ValueError("x, y, z must be > 0")
    if hole_gap < 0 or hole_width <= 0 or hole_depth <= 0:
        raise ValueError("hole_gap must be >= 0; hole_width and hole_depth must be > 0")
    if hole_depth > y:
        raise ValueError("hole_depth cannot exceed height y")

    cubit.cmd(f"brick x {x} y {y} z {z}")
    board_id = cubit.get_last_id("volume")

    radius = hole_width * 0.5
    slot_rect_depth = hole_depth - radius
    if slot_rect_depth <= 0:
        raise ValueError("hole_depth must be greater than hole_width/2 for a rounded slot")

    x0 = -0.5 * hole_gap
    x1 =  0.5 * hole_gap
    y_top = 0.5 * y

    def _make_slot_volume(x_center):
        cubit.cmd(f"brick x {hole_width} y {hole_depth} z {z+2}")
        rect_id = cubit.get_last_id("volume")
        cubit.cmd(f"move volume {rect_id} x {x_center} y {y_top - 0.5 * hole_depth} z 0")

        cubit.cmd(f"create cylinder radius {radius} height {z+2}")
        cyl_id = cubit.get_last_id("volume")
        cubit.cmd(f"move volume {cyl_id} x {x_center} y {y_top - hole_depth} z 0")

        cubit.cmd(f"unite volume {rect_id} {cyl_id}")
        return cubit.get_last_id("volume")

    slot1_id = _make_slot_volume(x0)
    slot2_id = _make_slot_volume(x1)
    cubit.cmd(f"subtract volume {slot1_id} {slot2_id} from volume {board_id}")
    return board_id


def make_Kalthoff_Winkler_cylinder(pos_x, pos_y, pos_z, radius, height, board_y):
    if radius <= 0 or height <= 0:
        raise ValueError("radius and height must be > 0")
    if pos_y < board_y / 2 + height / 2:
        raise ValueError("pos_y too low — cylinder overlaps the board")

    cubit.cmd(f"create cylinder radius {radius} height {height}")
    vid = cubit.get_last_id("volume")
    cubit.cmd(f"rotate volume {vid} angle -90 about x")
    cubit.cmd(f"move volume {vid} x {pos_x} y {pos_y} z {pos_z}")
    return vid


def _vol_info(vid):
    bb = cubit.volume(vid).bounding_box()
    xmin, ymin, zmin, xmax, ymax, zmax = bb
    return (vid,
            0.5*(xmin+xmax), 0.5*(ymin+ymax), 0.5*(zmin+zmax),
            xmin, ymin, zmin, xmax, ymax, zmax)


def _volume_center_x(vid):
    xmin, _, _, xmax, _, _ = cubit.volume(vid).bounding_box()
    return 0.5 * (xmin + xmax)


def cut_Kalthoff_Winkler_board(y, board_id):
    before = set(cubit.get_entities("volume"))
    cubit.cmd(f"webcut volume {board_id} with plane yplane offset {y/2-5}")
    cubit.cmd("imprint volume all")

    after  = set(cubit.get_entities("volume"))
    new_vols = sorted(after - before)

    left_vols  = [v for v in new_vols if _volume_center_x(v) <  50]
    right_vols = [v for v in new_vols if _volume_center_x(v) >= 50]

    cubit.cmd("merge volume " + " ".join(map(str, new_vols)))
    merged_id = cubit.get_last_id("volume")
    return merged_id, left_vols, right_vols


# ---------------------------------------------------------------------------
# Scene builder
# ---------------------------------------------------------------------------

def build_kw_scene(out_dir: Path, tag: str) -> dict:
    """Build, mesh, and export a Kalthoff-Winkler board+cylinder scene.

    Writes <tag>.g and <tag>.xml into out_dir.
    Returns a dict with mesh and xml file paths.
    """
    out_dir = Path(out_dir)

    cubit.cmd("reset")

    # --- Geometry parameters (mm) ---
    x, y, z            = 200.0, 100.0, 9.0
    hole_gap            = 51.5
    hole_width          = 1.5
    hole_depth          = 50.0
    cyl_pos_x           = 0.0
    cyl_pos_y           = 100.0
    cyl_pos_z           = 0.0
    cyl_radius          = 25.0
    cyl_height          = 60.0

    make_Kalthoff_Winkler_cylinder(
        cyl_pos_x, cyl_pos_y, cyl_pos_z, cyl_radius, cyl_height, board_y=y
    )
    board_id = make_Kalthoff_Winkler_board(x, y, z, hole_gap, hole_width, hole_depth)
    board_id, left_vols, right_vols = cut_Kalthoff_Winkler_board(y, board_id)

    # --- Meshing ---
    h = 5.0
    cubit.cmd("curve all scheme equal")
    cubit.cmd(f"curve all size {h}")

    cubit.cmd("nodeset 1 volume 1")
    cubit.cmd("nodeset 3 volume 2 8")
    cubit.cmd("nodeset 2 volume 7 9")
    cubit.cmd("block 1 add volume 1")
    cubit.cmd("block 2 add volume 2 7 8 9")

    cubit.cmd("delete mesh volume all")
    cubit.cmd("delete mesh surface all")
    cubit.cmd("set default element type tet")
    cubit.cmd("volume all scheme tetmesh")
    cubit.cmd(f"volume all size {h}")
    cubit.cmd("mesh volume all")

    cubit.cmd("volume all scale 0.001")

    # --- Export mesh ---
    mesh_path = out_dir / f"{tag}.g"
    cubit.cmd(f'export mesh "{mesh_path}" overwrite')
    echo(f"  mesh -> {mesh_path}")

    # --- Patch and write XML ---
    xml_template = Path(__file__).parent / "kw_fracture.xml"
    xml_text = xml_template.read_text(encoding="utf-8")
    xml_text = xml_text.replace(
        'name="Input Mesh File" type="string" value="kw_board.g"',
        f'name="Input Mesh File" type="string" value="{tag}.g"',
    )
    xml_text = xml_text.replace(
        'name="Output Filename" type="string" value="kw_fracture"',
        f'name="Output Filename" type="string" value="{tag}"',
    )
    xml_path = out_dir / f"{tag}.xml"
    xml_path.write_text(xml_text, encoding="utf-8")
    echo(f"  xml  -> {xml_path}")

    return {"tag": tag, "mesh_file": str(mesh_path), "xml_file": str(xml_path)}


# ---------------------------------------------------------------------------
# Standalone entry point (original behaviour)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    CUBIT_PATH = r"E:\Program Files\Coreform Cubit 2025.12\bin"
    sys.path.append(CUBIT_PATH)
    import cubit  # noqa: F811
    cubit.init(['cubit'])

    parser = argparse.ArgumentParser(
        description="Generate a single Kalthoff-Winkler board mesh and Peridigm XML."
    )
    parser.add_argument(
        "--output-dir", default=".", metavar="DIR",
        help="Directory to write kw_board.g and kw_board.xml into. Default: current dir.",
    )
    args = parser.parse_args()

    build_kw_scene(Path(args.output_dir), "kw_board")
    echo("Done.")
