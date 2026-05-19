#!/usr/bin/env python3
"""
generate_scenes.py — Fragmentation Scene Generator
====================================================
Generates Cubit mesh (.g) + Peridigm XML (.xml) pairs for brittle fracture simulations.

Six scene types:
  freefall_vase   — Vase dropped from ~1.5-2.5 m onto a ground plane
  freefall_mug    — Mug dropped from ~1.5-2.5 m onto a ground plane
  bullet_vase     — Bullet fired at a vase sitting on a ground plane
  bullet_mug      — Bullet fired at a mug sitting on a ground plane
  bullet_vase_nf  — Bullet fired at a floating vase (no floor)
  bullet_mug_nf   — Bullet fired at a floating mug (no floor)

Objects: parametric mug (cylinder + handle) or vase (spline-revolved profile).
Material: Brittle elastic with Critical Stretch damage model.

Units in Cubit: millimeters (mm) — auto-converted to meters on export.

Material properties (SI units):
    Mug/Vase:  density=2200 kg/m^3, K=14.90e9 Pa, G=8.94e9 Pa
    Bullet:    density=7700 kg/m^3, K=160.0e9 Pa, G=78.3e9 Pa
    Damage:    Critical Stretch = 0.0005

Usage:
    # Adjust CUBIT_PATH below, then run:
    python generate_scenes.py
"""

import sys
import os
import math
import json
import random
from pathlib import Path

# ============================================================
# EDIT THIS PATH to match your Coreform Cubit installation
# ============================================================
CUBIT_PATH = r"E:\Program Files\Coreform Cubit 2025.12\bin"
sys.path.append(CUBIT_PATH)
import cubit

from geometry_builder import (
    make_vase_parametric, make_mug_parametric, make_bullet, make_floor,
    make_plate, make_ball,
)
from peridigm_xml import (
    generate_vase_drop_peridigm_xml,
    generate_mug_drop_peridigm_xml,
    generate_bullet_vase_peridigm_xml,
    generate_bullet_mug_peridigm_xml,
    generate_bullet_vase_nf_peridigm_xml,
    generate_bullet_mug_nf_peridigm_xml,
    generate_ball_plate_peridigm_xml,
)


def echo(msg):
    sys.__stdout__.write(str(msg) + "\n")
    sys.__stdout__.flush()


def total_elements(vol_ids):
    total = 0
    for vid in vol_ids:
        cnt = cubit.get_volume_element_count(vid)
        echo(f"  volume {vid}: {cnt} elements")
        total += cnt
    return total


# ---------------------------------------------------------------------------
# Scene builders
# ---------------------------------------------------------------------------

def build_vase_drop_scene(seed: int = 42, max_nodes: int = 50000) -> dict:
    """Vase free-fall: random vase dropped from 1.5-2.5 m with random orientation.

    Block 1 / Nodeset 1: Floor
    Block 2 / Nodeset 2: Vase
    """
    rng = random.Random(seed)
    cubit.cmd("reset")

    r_neck_mm = rng.uniform(10, 20)
    mesh_size = r_neck_mm / 4

    floor_vol = make_floor(mesh_size_mm=mesh_size)
    cubit.cmd(f"block 1 volume {floor_vol}")
    cubit.cmd("block 1 name 'block_1'")
    cubit.cmd(f"nodeset 1 volume {floor_vol}")
    cubit.cmd("nodeset 1 name 'nodelist_1'")

    h_total = rng.uniform(120, 200)
    h_belly_peak = rng.uniform(20, 50)
    h_belly_top = rng.uniform(60, 100)
    h_neck_start = rng.uniform(100, 160)
    h_belly_top = min(h_belly_top, h_neck_start - 15)
    h_belly_peak = min(h_belly_peak, h_belly_top - 15)
    h_neck_start = min(h_neck_start, h_total - 20)
    r_belly = rng.uniform(35, 65)
    r_base = rng.uniform(20, 40)
    r_mouth = rng.uniform(12, 28)
    wall = rng.uniform(1.5, 3.5)
    r_neck_mm = max(r_neck_mm, wall + 2)
    r_mouth = max(r_mouth, wall + 2)
    r_base = max(r_base, wall + 2)

    vase_vol = make_vase_parametric(
        h_total_mm=h_total, wall_mm=wall,
        wall_bottom_mm=rng.uniform(2.0, 4.0),
        r_base_mm=r_base, r_belly_mm=r_belly,
        h_belly_peak_mm=h_belly_peak, h_belly_top_mm=h_belly_top,
        r_neck_mm=r_neck_mm, h_neck_start_mm=h_neck_start,
        r_mouth_mm=r_mouth, mesh_size_mm=mesh_size,
    )

    # Adaptive mesh refinement: increase mesh size by 10% per attempt if over budget
    relaxation = 1.0
    for attempt in range(10):
        n = total_elements([floor_vol, vase_vol])
        if n <= max_nodes:
            break
        relaxation *= 1.1
        mesh_size_new = mesh_size * relaxation
        cubit.init(['cubit', '-nojournal'])
        cubit.cmd("reset")
        floor_vol = make_floor(mesh_size_mm=mesh_size_new)
        cubit.cmd(f"block 1 volume {floor_vol}")
        cubit.cmd("block 1 name 'block_1'")
        cubit.cmd(f"nodeset 1 volume {floor_vol}")
        cubit.cmd("nodeset 1 name 'nodelist_1'")
        vase_vol = make_vase_parametric(
            h_total_mm=h_total, wall_mm=wall,
            wall_bottom_mm=rng.uniform(2.0, 4.0),
            r_base_mm=r_base, r_belly_mm=r_belly,
            h_belly_peak_mm=h_belly_peak, h_belly_top_mm=h_belly_top,
            r_neck_mm=r_neck_mm, h_neck_start_mm=h_neck_start,
            r_mouth_mm=r_mouth, mesh_size_mm=mesh_size_new,
        )

    drop_height = rng.uniform(1500, 2500)
    xy_offset = 20.0
    dx = rng.uniform(-xy_offset, xy_offset)
    dy = rng.uniform(-xy_offset, xy_offset)

    rot_x = rng.uniform(-30, 30)
    rot_y = rng.uniform(-30, 30)
    rot_z = rng.uniform(0, 360)
    cubit.cmd(f"rotate volume {vase_vol} angle {rot_z} about z")
    cubit.cmd(f"rotate volume {vase_vol} angle {rot_x} about x")
    cubit.cmd(f"rotate volume {vase_vol} angle {rot_y} about y")

    bb = cubit.get_center_point("volume", vase_vol)
    cubit.cmd(f"move volume {vase_vol} x {-bb[0]} y {-bb[1]} z {-bb[2]}")
    cubit.cmd(f"move volume {vase_vol} x {dx} y {dy} z {drop_height}")

    cubit.cmd(f"block 2 volume {vase_vol}")
    cubit.cmd("block 2 name 'block_2'")
    cubit.cmd(f"nodeset 2 volume {vase_vol}")
    cubit.cmd("nodeset 2 name 'nodelist_2'")

    cubit.cmd("volume all scale 0.001")

    return {
        "floor_vol": floor_vol, "vase_vol": vase_vol,
        "mesh_size": mesh_size, "drop_height_mm": drop_height,
        "rotation": (rot_x, rot_y, rot_z), "seed": seed,
        "nodes": cubit.get_node_count(),
    }


def build_mug_drop_scene(seed: int = 42, max_nodes: int = 50000) -> dict:
    """Mug free-fall: random mug dropped from 1.5-2.5 m with random orientation.

    Block 1 / Nodeset 1: Floor
    Block 2 / Nodeset 2: Mug
    """
    rng = random.Random(seed)
    cubit.cmd("reset")

    body_radius_mm = rng.uniform(30, 50)
    body_height_mm = rng.uniform(70, 115)
    wall_thickness_mm = rng.uniform(2.5, 5.0)
    handle_width_mm = rng.uniform(7, 14)
    handle_height_mm = rng.uniform(35, 60)
    handle_protrusion_mm = rng.uniform(18, 35)
    mesh_size = handle_width_mm / 4

    floor_vol = make_floor(mesh_size_mm=mesh_size)
    cubit.cmd(f"block 1 volume {floor_vol}")
    cubit.cmd("block 1 name 'block_1'")
    cubit.cmd(f"nodeset 1 volume {floor_vol}")
    cubit.cmd("nodeset 1 name 'nodelist_1'")

    mug_vol = make_mug_parametric(
        body_radius_mm=body_radius_mm, body_height_mm=body_height_mm,
        wall_thickness_mm=wall_thickness_mm, handle_width_mm=handle_width_mm,
        handle_height_mm=handle_height_mm, handle_protrusion_mm=handle_protrusion_mm,
        mesh_size_mm=mesh_size,
    )

    relaxation = 1.0
    for attempt in range(10):
        n = total_elements([floor_vol, mug_vol])
        if n <= max_nodes:
            break
        relaxation *= 1.1
        mesh_size_new = mesh_size * relaxation
        cubit.init(['cubit', '-nojournal'])
        cubit.cmd("reset")
        floor_vol = make_floor(mesh_size_mm=mesh_size_new)
        cubit.cmd(f"block 1 volume {floor_vol}")
        cubit.cmd("block 1 name 'block_1'")
        cubit.cmd(f"nodeset 1 volume {floor_vol}")
        cubit.cmd("nodeset 1 name 'nodelist_1'")
        mug_vol = make_mug_parametric(
            body_radius_mm=body_radius_mm, body_height_mm=body_height_mm,
            wall_thickness_mm=wall_thickness_mm, handle_width_mm=handle_width_mm,
            handle_height_mm=handle_height_mm, handle_protrusion_mm=handle_protrusion_mm,
            mesh_size_mm=mesh_size_new,
        )

    drop_height = rng.uniform(1500, 2500)
    xy_offset = 20.0
    dx = rng.uniform(-xy_offset, xy_offset)
    dy = rng.uniform(-xy_offset, xy_offset)

    rot_x = rng.uniform(-30, 30)
    rot_y = rng.uniform(-30, 30)
    rot_z = rng.uniform(0, 360)
    cubit.cmd(f"rotate volume {mug_vol} angle {rot_z} about z")
    cubit.cmd(f"rotate volume {mug_vol} angle {rot_x} about x")
    cubit.cmd(f"rotate volume {mug_vol} angle {rot_y} about y")

    bb = cubit.get_center_point("volume", mug_vol)
    cubit.cmd(f"move volume {mug_vol} x {-bb[0]} y {-bb[1]} z {-bb[2]}")
    cubit.cmd(f"move volume {mug_vol} x {dx} y {dy} z {drop_height}")

    cubit.cmd(f"block 2 volume {mug_vol}")
    cubit.cmd("block 2 name 'block_2'")
    cubit.cmd(f"nodeset 2 volume {mug_vol}")
    cubit.cmd("nodeset 2 name 'nodelist_2'")

    cubit.cmd("volume all scale 0.001")

    return {
        "floor_vol": floor_vol, "vase_vol": mug_vol,
        "mesh_size": mesh_size, "drop_height_mm": drop_height,
        "rotation": (rot_x, rot_y, rot_z), "seed": seed,
        "nodes": cubit.get_node_count(),
    }


def _bullet_direction(rot_x, rot_y):
    """Compute bullet flight direction unit vector from rotation angles (degrees)."""
    rx = math.radians(rot_x)
    ry = math.radians(rot_y)
    dx, dy, dz = 0.0, 0.0, 1.0
    dy2 = dy * math.cos(rx) - dz * math.sin(rx)
    dz2 = dy * math.sin(rx) + dz * math.cos(rx)
    dy, dz = dy2, dz2
    dx2 = dx * math.cos(ry) + dz * math.sin(ry)
    dz2 = -dx * math.sin(ry) + dz * math.cos(ry)
    dx, dz = dx2, dz2
    return dx, dy, dz


def build_bullet_vase_scene(seed: int = 42, max_nodes: int = 50000) -> dict:
    """Bullet impact on vase sitting on floor; bullet fires from a random direction.

    Block 1 / Nodeset 1: Floor
    Block 2 / Nodeset 2: Vase
    Block 3 / Nodeset 3: Bullet
    """
    rng = random.Random(seed)
    cubit.cmd("reset")

    r_neck_mm = rng.uniform(10, 20)
    mesh_size = r_neck_mm / 4

    h_total = rng.uniform(120, 200)
    h_belly_peak = rng.uniform(20, 50)
    h_belly_top = rng.uniform(60, 100)
    h_neck_start = rng.uniform(100, 160)
    h_belly_top = min(h_belly_top, h_neck_start - 15)
    h_belly_peak = min(h_belly_peak, h_belly_top - 15)
    h_neck_start = min(h_neck_start, h_total - 20)
    r_belly = rng.uniform(35, 65)
    r_base = rng.uniform(20, 40)
    r_mouth = rng.uniform(12, 28)
    wall = rng.uniform(1.5, 3.5)
    r_neck_mm = max(r_neck_mm, wall + 2)
    r_mouth = max(r_mouth, wall + 2)
    r_base = max(r_base, wall + 2)

    floor_vol = make_floor(mesh_size_mm=mesh_size)
    cubit.cmd(f"block 1 volume {floor_vol}")
    cubit.cmd("block 1 name 'block_1'")
    cubit.cmd(f"nodeset 1 volume {floor_vol}")
    cubit.cmd("nodeset 1 name 'nodelist_1'")

    vase_vol = make_vase_parametric(
        h_total_mm=h_total, wall_mm=wall,
        wall_bottom_mm=rng.uniform(2.0, 4.0),
        r_base_mm=r_base, r_belly_mm=r_belly,
        h_belly_peak_mm=h_belly_peak, h_belly_top_mm=h_belly_top,
        r_neck_mm=r_neck_mm, h_neck_start_mm=h_neck_start,
        r_mouth_mm=r_mouth, mesh_size_mm=mesh_size,
    )
    bullet_vol = make_bullet(mesh_size_mm=mesh_size)

    relaxation = 1.0
    for attempt in range(10):
        n = total_elements([floor_vol, vase_vol, bullet_vol])
        if n <= max_nodes:
            break
        relaxation *= 1.1
        mesh_size_new = mesh_size * relaxation
        cubit.init(['cubit', '-nojournal'])
        cubit.cmd("reset")
        floor_vol = make_floor(mesh_size_mm=mesh_size_new)
        cubit.cmd(f"block 1 volume {floor_vol}")
        cubit.cmd("block 1 name 'block_1'")
        cubit.cmd(f"nodeset 1 volume {floor_vol}")
        cubit.cmd("nodeset 1 name 'nodelist_1'")
        vase_vol = make_vase_parametric(
            h_total_mm=h_total, wall_mm=wall,
            wall_bottom_mm=rng.uniform(2.0, 4.0),
            r_base_mm=r_base, r_belly_mm=r_belly,
            h_belly_peak_mm=h_belly_peak, h_belly_top_mm=h_belly_top,
            r_neck_mm=r_neck_mm, h_neck_start_mm=h_neck_start,
            r_mouth_mm=r_mouth, mesh_size_mm=mesh_size_new,
        )
        bullet_vol = make_bullet(mesh_size_mm=mesh_size_new)

    bullet_dist = 200
    xyz_offset = 15.0
    x_off = rng.uniform(-xyz_offset, xyz_offset)
    y_off = rng.uniform(-xyz_offset, xyz_offset)
    z_off = rng.uniform(-xyz_offset, xyz_offset)

    rot_x_v = rng.uniform(-30, 30)
    rot_y_v = rng.uniform(-30, 30)
    rot_z_v = rng.uniform(0, 360)
    cubit.cmd(f"rotate volume {vase_vol} angle {rot_z_v} about z")
    cubit.cmd(f"rotate volume {vase_vol} angle {rot_x_v} about x")
    cubit.cmd(f"rotate volume {vase_vol} angle {rot_y_v} about y")

    bb = cubit.get_center_point("volume", vase_vol)
    boundingbox = cubit.get_bounding_box("volume", vase_vol)
    cz_bottom = boundingbox[6]
    cubit.cmd(f"move volume {vase_vol} x {-bb[0]} y {-bb[1]} z {-cz_bottom + 2}")
    bb_origin = cubit.get_center_point("volume", vase_vol)

    bulletpos = cubit.get_center_point("volume", bullet_vol)
    cubit.cmd(f"move volume {bullet_vol} x {-bulletpos[0]} y {-bulletpos[1]} z {-bulletpos[2]}")

    rot_x = rng.uniform(-30, 30)
    rot_y = rng.uniform(-30, 30)
    rot_z = rng.uniform(0, 360)
    cubit.cmd(f"rotate volume {bullet_vol} angle {rot_z} about z")
    cubit.cmd(f"rotate volume {bullet_vol} angle {rot_x} about x")
    cubit.cmd(f"rotate volume {bullet_vol} angle {rot_y} about y")

    dx, dy, dz = _bullet_direction(rot_x, rot_y)
    cubit.cmd(f"move volume {bullet_vol} x {bb_origin[0]+x_off} y {bb_origin[1]+y_off} z {bb_origin[2]+z_off}")
    cubit.cmd(f"move volume {bullet_vol} x {dx * bullet_dist} y {dy * bullet_dist} z {dz * bullet_dist}")

    cubit.cmd(f"block 2 volume {vase_vol}")
    cubit.cmd("block 2 name 'block_2'")
    cubit.cmd(f"nodeset 2 volume {vase_vol}")
    cubit.cmd("nodeset 2 name 'nodelist_2'")
    cubit.cmd(f"block 3 volume {bullet_vol}")
    cubit.cmd("block 3 name 'block_3'")
    cubit.cmd(f"nodeset 3 volume {bullet_vol}")
    cubit.cmd("nodeset 3 name 'nodelist_3'")

    cubit.cmd("volume all scale 0.001")
    cubit.cmd("delete free vertex all")
    cubit.cmd("delete free curve all")
    cubit.cmd("delete free surface all")

    return {
        "floor_vol": floor_vol, "vase_vol": vase_vol, "bullet_vol": bullet_vol,
        "rotation": (rot_x, rot_y, rot_z), "seed": seed,
        "elements": n, "mesh_relaxation": relaxation,
        "dx": dx, "dy": dy, "dz": dz, "mesh_size": mesh_size,
    }


def build_bullet_mug_scene(seed: int = 42, max_nodes: int = 30000) -> dict:
    """Bullet impact on mug sitting on floor; bullet fires from a random direction.

    Block 1 / Nodeset 1: Floor
    Block 2 / Nodeset 2: Mug
    Block 3 / Nodeset 3: Bullet
    """
    rng = random.Random(seed)
    cubit.cmd("reset")

    body_radius_mm = rng.uniform(30, 50)
    body_height_mm = rng.uniform(70, 115)
    wall_thickness_mm = rng.uniform(2.5, 5.0)
    handle_width_mm = rng.uniform(7, 14)
    handle_height_mm = rng.uniform(35, 60)
    handle_protrusion_mm = rng.uniform(18, 35)
    mesh_size = handle_width_mm / 4

    floor_vol = make_floor(mesh_size_mm=mesh_size)
    cubit.cmd(f"block 1 volume {floor_vol}")
    cubit.cmd("block 1 name 'block_1'")
    cubit.cmd(f"nodeset 1 volume {floor_vol}")
    cubit.cmd("nodeset 1 name 'nodelist_1'")

    mug_vol = make_mug_parametric(
        body_radius_mm=body_radius_mm, body_height_mm=body_height_mm,
        wall_thickness_mm=wall_thickness_mm, handle_width_mm=handle_width_mm,
        handle_height_mm=handle_height_mm, handle_protrusion_mm=handle_protrusion_mm,
        mesh_size_mm=mesh_size,
    )
    bullet_vol = make_bullet(mesh_size_mm=mesh_size)

    relaxation = 1.0
    for attempt in range(10):
        n = total_elements([floor_vol, mug_vol, bullet_vol])
        if n <= max_nodes:
            break
        relaxation *= 1.1
        mesh_size_new = mesh_size * relaxation
        cubit.init(['cubit', '-nojournal'])
        cubit.cmd("reset")
        floor_vol = make_floor(mesh_size_mm=mesh_size_new)
        cubit.cmd(f"block 1 volume {floor_vol}")
        cubit.cmd("block 1 name 'block_1'")
        cubit.cmd(f"nodeset 1 volume {floor_vol}")
        cubit.cmd("nodeset 1 name 'nodelist_1'")
        mug_vol = make_mug_parametric(
            body_radius_mm=body_radius_mm, body_height_mm=body_height_mm,
            wall_thickness_mm=wall_thickness_mm, handle_width_mm=handle_width_mm,
            handle_height_mm=handle_height_mm, handle_protrusion_mm=handle_protrusion_mm,
            mesh_size_mm=mesh_size_new,
        )
        bullet_vol = make_bullet(mesh_size_mm=mesh_size_new)

    bullet_dist = 200
    xyz_offset = 15.0
    x_off = rng.uniform(-xyz_offset, xyz_offset)
    y_off = rng.uniform(-xyz_offset, xyz_offset)
    z_off = rng.uniform(-xyz_offset, xyz_offset)

    rot_x_m = rng.uniform(-30, 30)
    rot_y_m = rng.uniform(-30, 30)
    rot_z_m = rng.uniform(0, 360)
    cubit.cmd(f"rotate volume {mug_vol} angle {rot_z_m} about z")
    cubit.cmd(f"rotate volume {mug_vol} angle {rot_x_m} about x")
    cubit.cmd(f"rotate volume {mug_vol} angle {rot_y_m} about y")

    bb = cubit.get_center_point("volume", mug_vol)
    boundingbox = cubit.get_bounding_box("volume", mug_vol)
    cz_bottom = boundingbox[6]
    cubit.cmd(f"move volume {mug_vol} x {-bb[0]} y {-bb[1]} z {-cz_bottom + 2}")
    bb_origin = cubit.get_center_point("volume", mug_vol)

    bulletpos = cubit.get_center_point("volume", bullet_vol)
    cubit.cmd(f"move volume {bullet_vol} x {-bulletpos[0]} y {-bulletpos[1]} z {-bulletpos[2]}")

    rot_x = rng.uniform(-30, 30)
    rot_y = rng.uniform(-30, 30)
    rot_z = rng.uniform(0, 360)
    cubit.cmd(f"rotate volume {bullet_vol} angle {rot_z} about z")
    cubit.cmd(f"rotate volume {bullet_vol} angle {rot_x} about x")
    cubit.cmd(f"rotate volume {bullet_vol} angle {rot_y} about y")

    dx, dy, dz = _bullet_direction(rot_x, rot_y)
    cubit.cmd(f"move volume {bullet_vol} x {bb_origin[0]+x_off} y {bb_origin[1]+y_off} z {bb_origin[2]+z_off}")
    cubit.cmd(f"move volume {bullet_vol} x {dx * bullet_dist} y {dy * bullet_dist} z {dz * bullet_dist}")

    cubit.cmd(f"block 2 volume {mug_vol}")
    cubit.cmd("block 2 name 'block_2'")
    cubit.cmd(f"nodeset 2 volume {mug_vol}")
    cubit.cmd("nodeset 2 name 'nodelist_2'")
    cubit.cmd(f"block 3 volume {bullet_vol}")
    cubit.cmd("block 3 name 'block_3'")
    cubit.cmd(f"nodeset 3 volume {bullet_vol}")
    cubit.cmd("nodeset 3 name 'nodelist_3'")

    cubit.cmd("volume all scale 0.001")
    cubit.cmd("delete free vertex all")
    cubit.cmd("delete free curve all")
    cubit.cmd("delete free surface all")

    return {
        "floor_vol": floor_vol, "vase_vol": mug_vol, "bullet_vol": bullet_vol,
        "rotation": (rot_x, rot_y, rot_z), "seed": seed,
        "elements": n, "mesh_relaxation": relaxation,
        "dx": dx, "dy": dy, "dz": dz, "mesh_size": mesh_size,
    }


def build_bullet_vase_nf_scene(seed: int = 42, max_nodes: int = 50000) -> dict:
    """Bullet impact on floating vase (no floor).

    Block 1 / Nodeset 1: Vase
    Block 2 / Nodeset 2: Bullet
    """
    rng = random.Random(seed)
    cubit.cmd("reset")

    r_neck_mm = rng.uniform(10, 20)
    mesh_size = r_neck_mm / 4

    h_total = rng.uniform(120, 200)
    h_belly_peak = rng.uniform(20, 50)
    h_belly_top = rng.uniform(60, 100)
    h_neck_start = rng.uniform(100, 160)
    h_neck_start = min(h_neck_start, h_total - 20)
    h_belly_top = min(h_belly_top, h_neck_start - 15)
    h_belly_peak = min(h_belly_peak, h_belly_top - 15)
    r_belly = rng.uniform(35, 65)
    r_base = rng.uniform(20, 40)
    r_mouth = rng.uniform(12, 28)
    wall = rng.uniform(1.5, 3.5)
    r_neck_mm = max(r_neck_mm, wall + 2)
    r_mouth = max(r_mouth, wall + 2)
    r_base = max(r_base, wall + 2)

    vase_vol = make_vase_parametric(
        h_total_mm=h_total, wall_mm=wall,
        wall_bottom_mm=rng.uniform(2.0, 4.0),
        r_base_mm=r_base, r_belly_mm=r_belly,
        h_belly_peak_mm=h_belly_peak, h_belly_top_mm=h_belly_top,
        r_neck_mm=r_neck_mm, h_neck_start_mm=h_neck_start,
        r_mouth_mm=r_mouth, mesh_size_mm=mesh_size,
    )
    bullet_vol = make_bullet(mesh_size_mm=mesh_size)

    relaxation = 1.0
    for attempt in range(10):
        n = total_elements([vase_vol, bullet_vol])
        if n <= max_nodes:
            break
        relaxation *= 1.1
        mesh_size_new = mesh_size * relaxation
        cubit.init(['cubit', '-nojournal'])
        cubit.cmd("reset")
        vase_vol = make_vase_parametric(
            h_total_mm=h_total, wall_mm=wall,
            wall_bottom_mm=rng.uniform(2.0, 4.0),
            r_base_mm=r_base, r_belly_mm=r_belly,
            h_belly_peak_mm=h_belly_peak, h_belly_top_mm=h_belly_top,
            r_neck_mm=r_neck_mm, h_neck_start_mm=h_neck_start,
            r_mouth_mm=r_mouth, mesh_size_mm=mesh_size_new,
        )
        bullet_vol = make_bullet(mesh_size_mm=mesh_size_new)

    bullet_dist = 200
    xyz_offset = 15.0
    x_off = rng.uniform(-xyz_offset, xyz_offset)
    y_off = rng.uniform(-xyz_offset, xyz_offset)
    z_off = rng.uniform(-xyz_offset, xyz_offset)

    bb = cubit.get_center_point("volume", vase_vol)
    boundingbox = cubit.get_bounding_box("volume", vase_vol)
    cubit.cmd(f"move volume {vase_vol} x {-bb[0]} y {-bb[1]} z {-boundingbox[6] + 2}")
    bb_origin = cubit.get_center_point("volume", vase_vol)

    bulletpos = cubit.get_center_point("volume", bullet_vol)
    cubit.cmd(f"move volume {bullet_vol} x {-bulletpos[0]} y {-bulletpos[1]} z {-bulletpos[2]}")

    rot_x = rng.uniform(-30, 30)
    rot_y = rng.uniform(-30, 30)
    rot_z = rng.uniform(0, 360)
    cubit.cmd(f"rotate volume {bullet_vol} angle {rot_z} about z")
    cubit.cmd(f"rotate volume {bullet_vol} angle {rot_x} about x")
    cubit.cmd(f"rotate volume {bullet_vol} angle {rot_y} about y")

    dx, dy, dz = _bullet_direction(rot_x, rot_y)
    cubit.cmd(f"move volume {bullet_vol} x {bb_origin[0]+x_off} y {bb_origin[1]+y_off} z {bb_origin[2]+z_off}")
    cubit.cmd(f"move volume {bullet_vol} x {dx * bullet_dist} y {dy * bullet_dist} z {dz * bullet_dist}")

    cubit.cmd(f"block 1 volume {vase_vol}")
    cubit.cmd("block 1 name 'block_1'")
    cubit.cmd(f"nodeset 1 volume {vase_vol}")
    cubit.cmd("nodeset 1 name 'nodelist_1'")
    cubit.cmd(f"block 2 volume {bullet_vol}")
    cubit.cmd("block 2 name 'block_2'")
    cubit.cmd(f"nodeset 2 volume {bullet_vol}")
    cubit.cmd("nodeset 2 name 'nodelist_2'")

    cubit.cmd("volume all scale 0.001")
    cubit.cmd("delete free vertex all")
    cubit.cmd("delete free curve all")
    cubit.cmd("delete free surface all")

    return {
        "vase_vol": vase_vol, "bullet_vol": bullet_vol,
        "rotation": (rot_x, rot_y, rot_z), "seed": seed,
        "elements": n, "mesh_relaxation": relaxation,
        "dx": dx, "dy": dy, "dz": dz, "mesh_size": mesh_size,
    }


def build_bullet_mug_nf_scene(seed: int = 42, max_nodes: int = 50000) -> dict:
    """Bullet impact on floating mug (no floor).

    Block 1 / Nodeset 1: Mug
    Block 2 / Nodeset 2: Bullet
    """
    rng = random.Random(seed)
    cubit.cmd("reset")

    body_radius_mm = rng.uniform(30, 50)
    body_height_mm = rng.uniform(70, 115)
    wall_thickness_mm = rng.uniform(2.5, 5.0)
    handle_width_mm = rng.uniform(7, 14)
    handle_height_mm = rng.uniform(35, 60)
    handle_protrusion_mm = rng.uniform(18, 35)
    mesh_size = handle_width_mm / 4

    mug_vol = make_mug_parametric(
        body_radius_mm=body_radius_mm, body_height_mm=body_height_mm,
        wall_thickness_mm=wall_thickness_mm, handle_width_mm=handle_width_mm,
        handle_height_mm=handle_height_mm, handle_protrusion_mm=handle_protrusion_mm,
        mesh_size_mm=mesh_size,
    )
    bullet_vol = make_bullet(mesh_size_mm=mesh_size)

    relaxation = 1.0
    for attempt in range(10):
        n = total_elements([mug_vol, bullet_vol])
        if n <= max_nodes:
            break
        relaxation *= 1.1
        mesh_size_new = mesh_size * relaxation
        cubit.init(['cubit', '-nojournal'])
        cubit.cmd("reset")
        mug_vol = make_mug_parametric(
            body_radius_mm=body_radius_mm, body_height_mm=body_height_mm,
            wall_thickness_mm=wall_thickness_mm, handle_width_mm=handle_width_mm,
            handle_height_mm=handle_height_mm, handle_protrusion_mm=handle_protrusion_mm,
            mesh_size_mm=mesh_size_new,
        )
        bullet_vol = make_bullet(mesh_size_mm=mesh_size_new)

    bullet_dist = 200
    xyz_offset = 15.0
    x_off = rng.uniform(-xyz_offset, xyz_offset)
    y_off = rng.uniform(-xyz_offset, xyz_offset)
    z_off = rng.uniform(-xyz_offset, xyz_offset)

    bb = cubit.get_center_point("volume", mug_vol)
    boundingbox = cubit.get_bounding_box("volume", mug_vol)
    cubit.cmd(f"move volume {mug_vol} x {-bb[0]} y {-bb[1]} z {-boundingbox[6] + 2}")
    bb_origin = cubit.get_center_point("volume", mug_vol)

    bulletpos = cubit.get_center_point("volume", bullet_vol)
    cubit.cmd(f"move volume {bullet_vol} x {-bulletpos[0]} y {-bulletpos[1]} z {-bulletpos[2]}")

    rot_x = rng.uniform(-30, 30)
    rot_y = rng.uniform(-30, 30)
    rot_z = rng.uniform(0, 360)
    cubit.cmd(f"rotate volume {bullet_vol} angle {rot_z} about z")
    cubit.cmd(f"rotate volume {bullet_vol} angle {rot_x} about x")
    cubit.cmd(f"rotate volume {bullet_vol} angle {rot_y} about y")

    dx, dy, dz = _bullet_direction(rot_x, rot_y)
    cubit.cmd(f"move volume {bullet_vol} x {bb_origin[0]+x_off} y {bb_origin[1]+y_off} z {bb_origin[2]+z_off}")
    cubit.cmd(f"move volume {bullet_vol} x {dx * bullet_dist} y {dy * bullet_dist} z {dz * bullet_dist}")

    cubit.cmd(f"block 1 volume {mug_vol}")
    cubit.cmd("block 1 name 'block_1'")
    cubit.cmd(f"nodeset 1 volume {mug_vol}")
    cubit.cmd("nodeset 1 name 'nodelist_1'")
    cubit.cmd(f"block 2 volume {bullet_vol}")
    cubit.cmd("block 2 name 'block_2'")
    cubit.cmd(f"nodeset 2 volume {bullet_vol}")
    cubit.cmd("nodeset 2 name 'nodelist_2'")

    cubit.cmd("volume all scale 0.001")
    cubit.cmd("delete free vertex all")
    cubit.cmd("delete free curve all")
    cubit.cmd("delete free surface all")

    return {
        "mug_vol": mug_vol, "bullet_vol": bullet_vol,
        "rotation": (rot_x, rot_y, rot_z), "seed": seed,
        "elements": n, "mesh_relaxation": relaxation,
        "dx": dx, "dy": dy, "dz": dz, "mesh_size": mesh_size,
    }


def build_ball_plate_scene(seed: int = 42, max_nodes: int = 50000) -> dict:
    """Ball impacts a circular plate from a random direction.

    Ball: fixed radius 5 mm (steel), no damage.
    Plate: random radius 50-150 mm, random thickness 2-5 mm (ceramic, with damage).
    Speed: 50-300 m/s.  Angle from vertical: 0-30 deg.

    Block 1 / Nodeset 1: Plate
    Block 2 / Nodeset 2: Ball
    """
    rng = random.Random(seed)
    cubit.cmd("reset")

    # --- Plate parameters (randomised) ---
    plate_radius_mm    = rng.uniform(50, 150)
    plate_thickness_mm = rng.uniform(2, 5)

    # --- Ball parameters (fixed size) ---
    ball_radius_mm = 15.0

    # Mesh size: driven by ball diameter (need ≥3 elements across ball diameter)
    mesh_size = ball_radius_mm / 3

    # --- Build geometry ---
    plate_vol = make_plate(
        radius_mm=plate_radius_mm,
        thickness_mm=plate_thickness_mm,
        mesh_size_mm=mesh_size,
    )
    ball_vol = make_ball(radius_mm=ball_radius_mm, mesh_size_mm=mesh_size)

    # Adaptive mesh refinement
    relaxation = 1.0
    for attempt in range(10):
        n = total_elements([plate_vol, ball_vol])
        if n <= max_nodes:
            break
        relaxation *= 1.1
        mesh_size_new = mesh_size * relaxation
        cubit.init(['cubit', '-nojournal'])
        cubit.cmd("reset")
        plate_vol = make_plate(
            radius_mm=plate_radius_mm,
            thickness_mm=plate_thickness_mm,
            mesh_size_mm=mesh_size_new,
        )
        ball_vol = make_ball(radius_mm=ball_radius_mm, mesh_size_mm=mesh_size_new)

    # --- Position ball above plate centre ---
    # Impact angle: elevation from vertical (0 = straight down, 30 = oblique)
    elevation_deg = rng.uniform(0, 30)
    azimuth_deg   = rng.uniform(0, 360)
    ball_speed    = rng.uniform(50, 300)

    elev_rad = math.radians(elevation_deg)
    azim_rad = math.radians(azimuth_deg)

    # Direction vector pointing toward the plate (downward = -z dominant)
    dx =  math.sin(elev_rad) * math.cos(azim_rad)
    dy =  math.sin(elev_rad) * math.sin(azim_rad)
    dz = -math.cos(elev_rad)  # negative z = downward

    # Random impact offset on plate surface (within 60% of plate radius)
    impact_offset = rng.uniform(0, plate_radius_mm * 0.6)
    impact_angle  = rng.uniform(0, 360)
    impact_x = impact_offset * math.cos(math.radians(impact_angle))
    impact_y = impact_offset * math.sin(math.radians(impact_angle))

    # Place ball 80 mm above plate along the approach direction
    standoff = 80.0
    cubit.cmd(f"move volume {ball_vol} "
              f"x {impact_x - dx * standoff} "
              f"y {impact_y - dy * standoff} "
              f"z {-dz * standoff}")

    # Assign blocks and nodesets
    cubit.cmd(f"block 1 volume {plate_vol}")
    cubit.cmd("block 1 name 'block_1'")
    cubit.cmd(f"nodeset 1 volume {plate_vol}")
    cubit.cmd("nodeset 1 name 'nodelist_1'")

    cubit.cmd(f"block 2 volume {ball_vol}")
    cubit.cmd("block 2 name 'block_2'")
    cubit.cmd(f"nodeset 2 volume {ball_vol}")
    cubit.cmd("nodeset 2 name 'nodelist_2'")

    cubit.cmd("volume all scale 0.001")
    cubit.cmd("delete free vertex all")
    cubit.cmd("delete free curve all")
    cubit.cmd("delete free surface all")

    return {
        "plate_vol": plate_vol, "ball_vol": ball_vol,
        "plate_radius_mm": plate_radius_mm,
        "plate_thickness_mm": plate_thickness_mm,
        "ball_radius_mm": ball_radius_mm,
        "ball_speed": ball_speed,
        "elevation_deg": elevation_deg,
        "azimuth_deg": azimuth_deg,
        "dx": dx, "dy": dy, "dz": dz,
        "mesh_size": mesh_size * relaxation,
        "seed": seed,
    }


# ---------------------------------------------------------------------------
# Scenario registry  (name → build_fn, xml_fn)
# ---------------------------------------------------------------------------

SCENARIOS = {
    "freefall_vase":  (build_vase_drop_scene,      generate_vase_drop_peridigm_xml),
    "freefall_mug":   (build_mug_drop_scene,       generate_mug_drop_peridigm_xml),
    "bullet_vase":    (build_bullet_vase_scene,    generate_bullet_vase_peridigm_xml),
    "bullet_mug":     (build_bullet_mug_scene,     generate_bullet_mug_peridigm_xml),
    "bullet_vase_nf": (build_bullet_vase_nf_scene, generate_bullet_vase_nf_peridigm_xml),
    "bullet_mug_nf":  (build_bullet_mug_nf_scene,  generate_bullet_mug_nf_peridigm_xml),
    "ball_plate":     (build_ball_plate_scene,     generate_ball_plate_peridigm_xml),
}


# ---------------------------------------------------------------------------
# Batch generation
# ---------------------------------------------------------------------------

def main(
    scenario: str,
    n_scenes: int,
    base_seed: int = 0,
    output_dir: str = "fragmentation_dataset",
    max_nodes: int = 50000,
):
    cubit.init(['cubit', '-nojournal'])
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario}'. Choose from: {list(SCENARIOS)}")

    build_fn, xml_fn = SCENARIOS[scenario]
    out_root = Path(output_dir) / scenario
    out_root.mkdir(parents=True, exist_ok=True)

    manifest = []
    for i in range(n_scenes):
        seed = base_seed + i
        tag = f"{scenario}_{seed:04d}"
        echo(f"\n=== [{i+1}/{n_scenes}] {tag} ===")

        try:
            info = build_fn(seed=seed, max_nodes=max_nodes)

            mesh_path = out_root / f"{tag}.g"
            xml_path  = out_root / f"{tag}.xml"

            cubit.cmd(f'export mesh "{mesh_path}" overwrite')
            xml_fn(str(mesh_path.name), info, str(xml_path))

            manifest.append({
                "index": i,
                "tag": tag,
                "scenario": scenario,
                "seed": seed,
                "mesh_file": str(mesh_path),
                "xml_file":  str(xml_path),
                "status": "success",
            })
            echo(f"  -> {mesh_path.name}  {xml_path.name}")
        except Exception as exc:
            echo(f"  ERROR: {exc}")
            manifest.append({
                "index": i,
                "tag": tag,
                "scenario": scenario,
                "seed": seed,
                "status": "error",
                "error": str(exc),
            })

    manifest_path = out_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    echo(f"\nDone. {n_scenes} scenes attempted. Manifest: {manifest_path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Peridigm mesh+XML pairs for one fragmentation scenario.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
scenarios:
  freefall_vase   — vase dropped from ~1.5-2.5 m onto a steel floor
  freefall_mug    — mug  dropped from ~1.5-2.5 m onto a steel floor
  bullet_vase     — 400 m/s bullet impacts a vase sitting on a floor
  bullet_mug      — 400 m/s bullet impacts a mug  sitting on a floor
  bullet_vase_nf  — 400 m/s bullet impacts a floating vase (no floor)
  bullet_mug_nf   — 400 m/s bullet impacts a floating mug  (no floor)
  ball_plate      — steel ball impacts a ceramic plate at 50-300 m/s

examples:
  python generate_scenes.py freefall_vase  50
  python generate_scenes.py bullet_mug     20 --base-seed 100 --max-nodes 30000
  python generate_scenes.py bullet_vase    10 --output-dir my_dataset
  python generate_scenes.py bullet_vase_nf 30
""",
    )
    parser.add_argument(
        "scenario",
        choices=list(SCENARIOS),
        help="Which scene type to generate.",
    )
    parser.add_argument(
        "n_scenes",
        type=int,
        help="Number of scenes to generate.",
    )
    parser.add_argument(
        "--base-seed",
        type=int,
        default=0,
        metavar="N",
        help="Starting random seed (scene i uses seed base_seed+i). Default: 0.",
    )
    parser.add_argument(
        "--output-dir",
        default="fragmentation_dataset",
        metavar="DIR",
        help="Root output directory. Scenes go into <DIR>/<scenario>/. Default: fragmentation_dataset.",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=50000,
        metavar="N",
        help="Maximum element count; mesh is coarsened until this is met. Default: 50000.",
    )

    args = parser.parse_args()
    main(
        scenario=args.scenario,
        n_scenes=args.n_scenes,
        base_seed=args.base_seed,
        output_dir=args.output_dir,
        max_nodes=args.max_nodes,
    )
