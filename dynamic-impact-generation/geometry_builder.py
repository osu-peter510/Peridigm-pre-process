"""
geometry_builder.py — Coreform Cubit geometry primitives for fragmentation scenes.

Provides four functions that create and mesh geometry volumes via the Cubit API.
All dimensions are in millimeters (mm); caller is responsible for mm→m scaling
at export time (cubit.cmd("volume all scale 0.001")).

Requires Cubit to be initialised by the caller before any function is used.
"""

import sys

# Adjust this path to match your Coreform Cubit installation.
CUBIT_PATH = r"E:\Program Files\Coreform Cubit 2025.12\bin"
sys.path.append(CUBIT_PATH)
import cubit


def make_vase_parametric(
    h_total_mm=170.0, wall_mm=2.0, wall_bottom_mm=3.0,
    r_base_mm=30.0, r_belly_mm=50.0, h_belly_peak_mm=30.0, h_belly_top_mm=85.0,
    r_neck_mm=10.0, h_neck_start_mm=130.0,
    r_mouth_mm=20.0,
    mesh_size_mm=4.0,
):
    """Create a parametric vase via spline profile revolved 360° around the Z axis.

    Returns the Cubit volume ID of the meshed vase.
    """
    h = h_total_mm
    t = wall_mm
    tb = wall_bottom_mm

    outer_pts = [
        (0, 0), (r_base_mm, 0), (r_belly_mm, h_belly_peak_mm),
        (r_base_mm, h_belly_top_mm), (r_neck_mm, h_neck_start_mm), (r_mouth_mm, h),
    ]
    inner_pts = [
        (r_mouth_mm-t, h), (r_neck_mm-t, h_neck_start_mm),
        (r_base_mm-t, h_belly_top_mm), (r_belly_mm-t, h_belly_peak_mm),
        (r_base_mm, tb), (0, tb),
    ]

    all_pts = outer_pts + inner_pts
    vert_ids = []
    for x, z in all_pts:
        cubit.cmd(f"create vertex {x} 0 {z}")
        vert_ids.append(cubit.get_last_id("vertex"))

    n_outer = len(outer_pts)
    curves = []

    cubit.cmd(f"create curve vertex {vert_ids[0]} {vert_ids[1]}")
    curves.append(cubit.get_last_id("curve"))

    cubit.cmd(f"create curve spline vertex {' '.join(str(vert_ids[i]) for i in range(1, n_outer))}")
    curves.append(cubit.get_last_id("curve"))

    cubit.cmd(f"create curve vertex {vert_ids[n_outer-1]} {vert_ids[n_outer]}")
    curves.append(cubit.get_last_id("curve"))

    cubit.cmd(f"create curve spline vertex {' '.join(str(vert_ids[n_outer+i]) for i in range(len(inner_pts)-1))}")
    curves.append(cubit.get_last_id("curve"))

    cubit.cmd(f"create curve vertex {vert_ids[-2]} {vert_ids[-1]}")
    curves.append(cubit.get_last_id("curve"))

    cubit.cmd(f"create curve vertex {vert_ids[-1]} {vert_ids[0]}")
    curves.append(cubit.get_last_id("curve"))

    cubit.cmd(f"create surface curve {' '.join(str(c) for c in curves)}")
    cubit.cmd(f"sweep surface {cubit.get_last_id('surface')} zaxis angle 360")
    vol_id = cubit.get_last_id("volume")

    cubit.cmd(f"volume {vol_id} size {mesh_size_mm}")
    cubit.cmd(f"volume {vol_id} scheme tetmesh")
    cubit.cmd(f"mesh volume {vol_id}")
    return vol_id


def make_mug_parametric(
    body_radius_mm: float = 40.0,
    body_height_mm: float = 90.0,
    wall_thickness_mm: float = 4.0,
    handle_width_mm: float = 10.0,
    handle_height_mm: float = 50.0,
    handle_protrusion_mm: float = 25.0,
    mesh_size_mm: float = 4.0,
):
    """Create a hollow cylindrical mug with a torus-derived handle.

    Returns the Cubit volume ID of the meshed mug.
    """
    body_r = body_radius_mm
    body_h = body_height_mm
    wall_t = wall_thickness_mm
    h_w = handle_width_mm
    h_h = handle_height_mm
    h_p = handle_protrusion_mm

    vols_before = set(cubit.get_entities("volume"))

    cubit.cmd(f"create cylinder height {body_h} radius {body_r}")
    outer_cyl = max(set(cubit.get_entities("volume")) - vols_before)
    cubit.cmd(f"move volume {outer_cyl} z {body_h / 2}")

    vols_before2 = set(cubit.get_entities("volume"))
    inner_r = body_r - wall_t
    inner_h = body_h - wall_t
    cubit.cmd(f"create cylinder height {inner_h} radius {inner_r}")
    inner_cyl = max(set(cubit.get_entities("volume")) - vols_before2)
    cubit.cmd(f"move volume {inner_cyl} z {wall_t + inner_h / 2}")

    cubit.cmd(f"subtract volume {inner_cyl} from volume {outer_cyl}")
    body_vol = max(cubit.get_entities("volume"))

    handle_center_x = body_r + h_p / 2
    handle_center_z = body_h * 0.55
    handle_major_r = h_h / 2
    handle_minor_r = h_w / 2

    vols_before3 = set(cubit.get_entities("volume"))
    cubit.cmd(f"create torus major radius {handle_major_r} minor radius {handle_minor_r}")
    torus_vol = max(set(cubit.get_entities("volume")) - vols_before3)

    cubit.cmd(f"rotate volume {torus_vol} angle 90 about x")
    cubit.cmd(f"move volume {torus_vol} x {handle_center_x} z {handle_center_z}")

    vols_before4 = set(cubit.get_entities("volume"))
    cut_size = max(body_h, body_r * 4) * 2
    cubit.cmd(f"brick x {cut_size} y {cut_size} z {cut_size}")
    cut_vol = max(set(cubit.get_entities("volume")) - vols_before4)
    cubit.cmd(f"move volume {cut_vol} x {body_r - wall_t/2 - cut_size / 2} z {handle_center_z}")
    cubit.cmd(f"subtract volume {cut_vol} from volume {torus_vol}")

    all_vols_now = set(cubit.get_entities("volume"))
    handle_vols = all_vols_now - {body_vol} - vols_before
    if handle_vols:
        handle_vol = max(handle_vols)
        cubit.cmd(f"unite volume {body_vol} {handle_vol}")

    final_vol = max(cubit.get_entities("volume"))

    cubit.cmd(f"volume {final_vol} size {mesh_size_mm}")
    cubit.cmd(f"volume {final_vol} scheme tetmesh")
    cubit.cmd(f"mesh volume {final_vol}")
    return final_vol


def make_bullet(
    radius_mm: float = 4.0,
    length_mm: float = 15.0,
    mesh_size_mm: float = 4.0,
) -> int:
    """Create a simple cylindrical bullet and mesh it with a sweep scheme.

    Returns the Cubit volume ID of the meshed bullet.
    """
    cubit.cmd(f"create cylinder height {length_mm} radius {radius_mm}")
    vol_id = cubit.get_last_id("volume")
    assert vol_id > 0, "create cylinder failed"

    cubit.cmd(f"volume {vol_id} size {mesh_size_mm}")
    cubit.cmd(f"volume {vol_id} scheme sweep")
    cubit.cmd(f"mesh volume {vol_id}")
    return vol_id


def make_plate(
    radius_mm: float = 100.0,
    thickness_mm: float = 3.0,
    mesh_size_mm: float = 4.0,
) -> int:
    """Create a circular plate (thin cylinder) centred at origin, top face at z=0.

    Returns the Cubit volume ID of the meshed plate.
    """
    vols_before = set(cubit.get_entities("volume"))
    cubit.cmd(f"create cylinder height {thickness_mm} radius {radius_mm}")
    vol_id = max(set(cubit.get_entities("volume")) - vols_before)
    cubit.cmd(f"move volume {vol_id} z {-thickness_mm / 2}")

    cubit.cmd(f"volume {vol_id} size {mesh_size_mm}")
    cubit.cmd(f"volume {vol_id} scheme tetmesh")
    cubit.cmd(f"mesh volume {vol_id}")
    return vol_id


def make_ball(
    radius_mm: float = 10.0,
    mesh_size_mm: float = 4.0,
) -> int:
    """Create a sphere centred at the origin.

    Returns the Cubit volume ID of the meshed ball.
    """
    vols_before = set(cubit.get_entities("volume"))
    cubit.cmd(f"create sphere radius {radius_mm}")
    vol_id = max(set(cubit.get_entities("volume")) - vols_before)

    cubit.cmd(f"volume {vol_id} size {mesh_size_mm}")
    cubit.cmd(f"volume {vol_id} scheme tetmesh")
    cubit.cmd(f"mesh volume {vol_id}")
    return vol_id


def make_floor(
    size_x_mm: float = 150.0,
    size_y_mm: float = 150.0,
    thickness_mm: float = 2.0,
    mesh_size_mm: float = 4.0,
) -> int:
    """Create a flat rectangular floor slab centred at the XY plane (top face at z=0).

    Returns the Cubit volume ID of the meshed floor.
    """
    vols_before = set(cubit.get_entities("volume"))
    cubit.cmd(f"brick x {size_x_mm} y {size_y_mm} z {thickness_mm}")
    vol_id = max(set(cubit.get_entities("volume")) - vols_before)
    cubit.cmd(f"move volume {vol_id} z {-thickness_mm / 2}")

    cubit.cmd(f"volume {vol_id} size {mesh_size_mm}")
    cubit.cmd(f"volume {vol_id} scheme sweep")
    cubit.cmd(f"mesh volume {vol_id}")
    return vol_id
