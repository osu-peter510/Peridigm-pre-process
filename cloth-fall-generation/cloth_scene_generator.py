"""
Cloth Fall Scene Generator for Peridynamics PointConv Training
Automatically adjusts mesh density if node count exceeds threshold
"""

import sys
import random
import re
import xml.etree.ElementTree as ET
from typing import Tuple, Optional, List, Dict

# Add your Cubit path
# sys.path.append(r'E:\Program Files\Coreform Cubit 2025.12\bin')
import cubit


class ClothFallSceneGenerator:
    """
    Complete scene generator for cloth fall simulations with peridynamics.
    Supports automatic mesh refinement when node count exceeds threshold.
    """
    
    def __init__(
        self,
        max_nodes: int = 30000,
        floor_size: Tuple[float, float, float] = (150, 150, 1),
        cloth_size: Tuple[float, float, float] = (140, 140, 1),
        cloth_height: float = 80.0,
        mesh_size_multiplier: float = 1.0,
        clearance: float = 1.0,
        random_seed: Optional[int] = None,
        # 固定的障碍物尺寸范围
        brick_size_range: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]] = 
            ((12, 18), (12, 18), (26, 40)),  # x, y, z 范围
        brick_size_range_tall: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]] = 
            ((12, 18), (12, 18), (50, 60)),  
        sphere_radius_range: Tuple[float, float] = (8.0, 12.0)
    ):
        """
        Initialize the scene generator.
        固定生成：3个长方体 + 1个球体
        
        Args:
            max_nodes: Maximum number of nodes allowed (triggers mesh refinement)
            floor_size: (x_len, y_len, z_len) for floor in mm
            cloth_size: (x_len, y_len, z_len) for cloth in mm
            cloth_height: Initial height of cloth above floor in mm
            mesh_size_multiplier: Global mesh size multiplier (larger = coarser mesh)
            clearance: Minimum clearance between objects in mm
            random_seed: Random seed for reproducibility
            brick_size_range: Size ranges for bricks ((x_min,x_max), (y_min,y_max), (z_min,z_max))
            sphere_radius_range: Radius range for sphere (min, max)
        """
        self.max_nodes = max_nodes
        self.floor_size = floor_size
        self.cloth_size = cloth_size
        self.cloth_height = cloth_height
        self.mesh_size_multiplier = mesh_size_multiplier
        self.clearance = clearance
        self.brick_size_range = brick_size_range
        self.brick_size_range_tall = brick_size_range_tall
        self.sphere_radius_range = sphere_radius_range
        
        if random_seed is not None:
            random.seed(random_seed)
        
        # Initialize Cubit
        cubit.init(['cubit', '-nojournal'])
        
        # Storage for created entities (固定结构)
        self.volumes = {}
        self.blocks = {}
        self.nodesets = {}
        
    def echo(self, msg):
        """Print message to stdout even if Cubit redirects it"""
        sys.__stdout__.write(str(msg) + "\n")
        sys.__stdout__.flush()
    
    # ==================== Geometry Helpers ====================
    
    def get_volume_center(self, vid: int) -> Tuple[float, float, float]:
        """Get the center coordinates of a volume"""
        v = cubit.volume(vid)
        for attr in ("center_point", "centroid", "center"):
            fn = getattr(v, attr, None)
            if callable(fn):
                c = fn()
                if isinstance(c, (list, tuple)) and len(c) == 3:
                    return float(c[0]), float(c[1]), float(c[2])
        return 0.0, 0.0, 0.0
    
    def move_volume_to_center(self, vid: int, tgt_x: float, tgt_y: float, tgt_z: float):
        """Move volume center to target position"""
        cx, cy, cz = self.get_volume_center(vid)
        dx, dy, dz = tgt_x - cx, tgt_y - cy, tgt_z - cz
        cubit.cmd(f"move volume {vid} x {dx} y {dy} z {dz}")
    
    def aabb_bbox(self, vid: int) -> Tuple[float, float, float, float, float, float]:
        """Get axis-aligned bounding box of volume"""
        return cubit.volume(vid).bounding_box()
    
    def aabb_disjoint_with_clearance(self, b1, b2, clearance: float = 0.0) -> bool:
        """Check if two bounding boxes are disjoint with clearance"""
        xmin1, ymin1, zmin1, xmax1, ymax1, zmax1 = b1
        xmin2, ymin2, zmin2, xmax2, ymax2, zmax2 = b2
        
        # 扩大 box1（加 clearance 缓冲）
        xmin1 -= clearance; ymin1 -= clearance; zmin1 -= clearance
        xmax1 += clearance; ymax1 += clearance; zmax1 += clearance
        
        if xmax1 < xmin2 or xmax2 < xmin1: return True
        if ymax1 < ymin2 or ymax2 < ymin1: return True
        if zmax1 < zmin2 or zmax2 < zmin1: return True
        return False
        
    def intersects_any(self, vid: int, existing: List[int], clearance: float = 0.0) -> bool:
        """Check if volume intersects any existing volumes"""
        b_vid = self.aabb_bbox(vid)
        for other in existing:
            if other == vid:
                continue
            b_oth = self.aabb_bbox(other)
            if not self.aabb_disjoint_with_clearance(b_vid, b_oth, clearance=clearance):
                return True
        return False
    
    # ==================== Object Creation ====================
    
    def make_brick_and_block(
        self,
        x_len: float,
        y_len: float,
        z_len: float,
        block_id: Optional[int] = None,
        block_name: Optional[str] = None,
        nodeset_id: Optional[int] = None,
        nodeset_name: Optional[str] = None,
        mesh: bool = False,
        mesh_scheme: str = 'tetmesh',
        mesh_size: Optional[float] = None
    ) -> Tuple[int, int, Optional[int]]:
        """
        Create a brick, add to block, optionally mesh and create nodeset.
        
        Returns:
            (volume_id, block_id, nodeset_id)
        """
        vols_before = set(cubit.get_entities("volume"))
        blocks_before = set(cubit.get_entities("block"))
        nsets_before = set(cubit.get_entities("nodeset"))
        
        # Create brick
        if not cubit.cmd(f"brick x {x_len} y {y_len} z {z_len}"):
            raise RuntimeError("Failed to create brick.")
        vol_id = list(set(cubit.get_entities("volume")) - vols_before)[0]
        
        # Create/assign block
        if block_id is None:
            block_id = (max(blocks_before) + 1) if blocks_before else 1
        if not cubit.cmd(f"block {block_id} add volume {vol_id}"):
            raise RuntimeError(f"Failed to add volume {vol_id} to block {block_id}.")
        if block_name:
            cubit.cmd(f'block {block_id} name "{block_name}"')
        
        # Set mesh size if specified
        if mesh_size is not None:
            actual_size = mesh_size * self.mesh_size_multiplier
            cubit.cmd(f"volume {vol_id} size {actual_size}")
        
        # Mesh if requested
        if mesh:
            cubit.cmd(f"volume {vol_id} scheme sweep")
            cubit.cmd(f"mesh volume {vol_id}")
            if not cubit.cmd(f"mesh volume {vol_id}"):
                raise RuntimeError(f"Meshing failed for volume {vol_id}.")
        
        # Create nodeset if requested
        created_nodeset_id = None
        if nodeset_id is not None or nodeset_name is not None:
            if nodeset_id is None:
                nodeset_id = (max(nsets_before) + 1) if nsets_before else 1
            created_nodeset_id = nodeset_id
            
            # Ensure meshed
            if not mesh:
                cubit.cmd(f"volume {vol_id} scheme {mesh_scheme}")
                if mesh_size is not None:
                    actual_size = mesh_size * self.mesh_size_multiplier
                    cubit.cmd(f"volume {vol_id} size {actual_size}")
                if not cubit.cmd(f"mesh volume {vol_id}"):
                    raise RuntimeError(f"Meshing (for nodeset) failed for volume {vol_id}.")
            
            # Populate nodeset
            if not cubit.cmd(f"nodeset {nodeset_id} add node in volume {vol_id}"):
                raise RuntimeError(f"Failed to add nodes from volume {vol_id} to nodeset {nodeset_id}.")
            if nodeset_name:
                cubit.cmd(f'nodeset {nodeset_id} name "{nodeset_name}"')
        
        self.echo(f"[Created] volume={vol_id}, block={block_id}, nodeset={created_nodeset_id}")
        return vol_id, block_id, created_nodeset_id
    
    def create_brick_random(
        self,
        x_len: float,
        y_len: float,
        z_len: float,
        x_range: Tuple[float, float] = (-40, 40),
        y_range: Tuple[float, float] = (-40, 40),
        z_range: Tuple[float, float] = (20, 60),
        block_id: Optional[int] = None,
        block_name: Optional[str] = None,
        nodeset_id: Optional[int] = None,
        nodeset_name: Optional[str] = None,
        max_tries: int = 200,
        mesh_size: Optional[float] = None
    ) -> Tuple[int, int, Optional[int]]:
        """Create a brick at random position without overlap"""
        vols_before = set(cubit.get_entities("volume"))
        blocks_before = set(cubit.get_entities("block"))
        nsets_before = set(cubit.get_entities("nodeset"))
        
        # Create brick
        if not cubit.cmd(f"brick x {x_len} y {y_len} z {z_len}"):
            raise RuntimeError("Failed to create brick.")
        vol_id = list(set(cubit.get_entities("volume")) - vols_before)[0]
        
        # Try to place randomly
        obstacles_only = [v for v in cubit.get_entities("volume") 
                        if v != vol_id 
                        and v != self.volumes.get('floor')
                        and v != self.volumes.get('cloth')]
        
        placed = False
        for attempt in range(1, max_tries + 1):
            rx = random.uniform(*x_range)
            ry = random.uniform(*y_range)
            rz = z_len / 2 + 0.5
            self.move_volume_to_center(vol_id, rx, ry, rz)
            
            if not self.intersects_any(vol_id, obstacles_only, clearance=self.clearance):
                self.echo(f"[Placed] Brick at ({rx:.2f}, {ry:.2f}, {rz:.2f}) in {attempt} tries")
                placed = True
                break
        
        if not placed:
            cubit.cmd(f"delete volume {vol_id}")
            raise RuntimeError(f"Failed to place brick within {max_tries} tries.")
        
        # Create block
        if block_id is None:
            block_id = (max(blocks_before) + 1) if blocks_before else 1
        if not cubit.cmd(f"block {block_id} add volume {vol_id}"):
            raise RuntimeError(f"Failed to add volume {vol_id} to block {block_id}.")
        if block_name:
            cubit.cmd(f'block {block_id} name "{block_name}"')
        
        # Set mesh size
        if mesh_size is not None:
            actual_size = mesh_size * self.mesh_size_multiplier
            cubit.cmd(f"volume {vol_id} size {actual_size}")
        
        # Mesh
        cubit.cmd(f"volume {vol_id} scheme sweep")
        cubit.cmd(f"mesh volume {vol_id}")
        if not cubit.cmd(f"mesh volume {vol_id}"):
            raise RuntimeError(f"Meshing failed for volume {vol_id}.")
        
        # Create nodeset
        created_nodeset_id = None
        if nodeset_id is not None or nodeset_name is not None:
            if nodeset_id is None:
                nodeset_id = (max(nsets_before) + 1) if nsets_before else 1
            created_nodeset_id = nodeset_id
            
            if not cubit.cmd(f"nodeset {nodeset_id} add node in volume {vol_id}"):
                raise RuntimeError(f"Failed to add nodes to nodeset {nodeset_id}.")
            if nodeset_name:
                cubit.cmd(f'nodeset {nodeset_id} name "{nodeset_name}"')
        
        return vol_id, block_id, created_nodeset_id
    
    def create_cylinder_random(
        self,
        radius: float,
        height: float,
        x_range: Tuple[float, float] = (-40, 40),
        y_range: Tuple[float, float] = (-40, 40),
        z_range: Tuple[float, float] = (20, 60),
        block_id: Optional[int] = None,
        block_name: Optional[str] = None,
        nodeset_id: Optional[int] = None,
        nodeset_name: Optional[str] = None,
        max_tries: int = 200,
        mesh_size: Optional[float] = None
    ) -> Tuple[int, int, Optional[int]]:
        """Create a cylinder at random position without overlap"""
        vols_before = set(cubit.get_entities("volume"))
        blocks_before = set(cubit.get_entities("block"))
        nsets_before = set(cubit.get_entities("nodeset"))
        
        # Create cylinder
        if not cubit.cmd(f"create cylinder height {height} radius {radius}"):
            raise RuntimeError("Failed to create cylinder.")
        vol_id = list(set(cubit.get_entities("volume")) - vols_before)[0]
        
        # Try to place randomly
        obstacles_only = [v for v in cubit.get_entities("volume") 
                        if v != vol_id 
                        and v != self.volumes.get('floor')
                        and v != self.volumes.get('cloth')]
        
        placed = False
        for attempt in range(1, max_tries + 1):
            rx = random.uniform(*x_range)
            ry = random.uniform(*y_range)
            rz = height / 2 + 0.5
            self.move_volume_to_center(vol_id, rx, ry, rz)
            
            if not self.intersects_any(vol_id, obstacles_only, clearance=self.clearance):
                self.echo(f"[Placed] Brick at ({rx:.2f}, {ry:.2f}, {rz:.2f}) in {attempt} tries")
                placed = True
                break
        
        if not placed:
            cubit.cmd(f"delete volume {vol_id}")
            raise RuntimeError(f"Failed to place cylinder within {max_tries} tries.")
        
        # Create block
        if block_id is None:
            block_id = (max(blocks_before) + 1) if blocks_before else 1
        if not cubit.cmd(f"block {block_id} add volume {vol_id}"):
            raise RuntimeError(f"Failed to add volume {vol_id} to block {block_id}.")
        if block_name:
            cubit.cmd(f'block {block_id} name "{block_name}"')
        
        # Set mesh size
        if mesh_size is not None:
            actual_size = mesh_size * self.mesh_size_multiplier
            cubit.cmd(f"volume {vol_id} size {actual_size}")
        
        # Mesh
        cubit.cmd(f"volume {vol_id} scheme tetmesh")
        if not cubit.cmd(f"mesh volume {vol_id}"):
            raise RuntimeError(f"Meshing failed for volume {vol_id}.")
        
        # Create nodeset
        created_nodeset_id = None
        if nodeset_id is not None or nodeset_name is not None:
            if nodeset_id is None:
                nodeset_id = (max(nsets_before) + 1) if nsets_before else 1
            created_nodeset_id = nodeset_id
            
            if not cubit.cmd(f"nodeset {nodeset_id} add node in volume {vol_id}"):
                raise RuntimeError(f"Failed to add nodes to nodeset {nodeset_id}.")
            if nodeset_name:
                cubit.cmd(f'nodeset {nodeset_id} name "{nodeset_name}"')
        
        return vol_id, block_id, created_nodeset_id
    
    def create_sphere_random(
        self,
        radius: float,
        x_range: Tuple[float, float] = (-40, 40),
        y_range: Tuple[float, float] = (-40, 40),
        z_range: Tuple[float, float] = (20, 60),
        block_id: Optional[int] = None,
        block_name: Optional[str] = None,
        nodeset_id: Optional[int] = None,
        nodeset_name: Optional[str] = None,
        max_tries: int = 200,
        mesh_size: Optional[float] = None
    ) -> Tuple[int, int, Optional[int]]:
        """Create a sphere at random position without overlap"""
        vols_before = set(cubit.get_entities("volume"))
        blocks_before = set(cubit.get_entities("block"))
        nsets_before = set(cubit.get_entities("nodeset"))
        
        # Create sphere
        if not cubit.cmd(f"create sphere radius {radius}"):
            raise RuntimeError("Failed to create sphere.")
        vol_id = list(set(cubit.get_entities("volume")) - vols_before)[0]
        
        # Try to place randomly
        obstacles_only = [v for v in cubit.get_entities("volume") 
                        if v != vol_id 
                        and v != self.volumes.get('floor')
                        and v != self.volumes.get('cloth')]
        
        placed = False
        for attempt in range(1, max_tries + 1):
            rx = random.uniform(*x_range)
            ry = random.uniform(*y_range)
            rz = radius + 0.5
            self.move_volume_to_center(vol_id, rx, ry, rz)
            
            if not self.intersects_any(vol_id, obstacles_only, clearance=self.clearance):
                self.echo(f"[Placed] Brick at ({rx:.2f}, {ry:.2f}, {rz:.2f}) in {attempt} tries")
                placed = True
                break
        
        if not placed:
            cubit.cmd(f"delete volume {vol_id}")
            raise RuntimeError(f"Failed to place sphere within {max_tries} tries.")
        
        # Create block
        if block_id is None:
            block_id = (max(blocks_before) + 1) if blocks_before else 1
        if not cubit.cmd(f"block {block_id} add volume {vol_id}"):
            raise RuntimeError(f"Failed to add volume {vol_id} to block {block_id}.")
        if block_name:
            cubit.cmd(f'block {block_id} name "{block_name}"')
        
        # Set mesh size
        if mesh_size is not None:
            actual_size = mesh_size * self.mesh_size_multiplier
            cubit.cmd(f"volume {vol_id} size {actual_size}")
        
        # Mesh
        cubit.cmd(f"volume {vol_id} scheme tetmesh")
        if not cubit.cmd(f"mesh volume {vol_id}"):
            raise RuntimeError(f"Meshing failed for volume {vol_id}.")
        
        # Create nodeset
        created_nodeset_id = None
        if nodeset_id is not None or nodeset_name is not None:
            if nodeset_id is None:
                nodeset_id = (max(nsets_before) + 1) if nsets_before else 1
            created_nodeset_id = nodeset_id
            
            if not cubit.cmd(f"nodeset {nodeset_id} add node in volume {vol_id}"):
                raise RuntimeError(f"Failed to add nodes to nodeset {nodeset_id}.")
            if nodeset_name:
                cubit.cmd(f'nodeset {nodeset_id} name "{nodeset_name}"')
        
        return vol_id, block_id, created_nodeset_id
    
    # ==================== Node Count & Mesh Refinement ====================
    
    def get_total_node_count(self) -> int:
        """Get total number of nodes in the mesh"""
        return len(cubit.get_entities("node"))
    
    def check_and_adjust_mesh(self) -> bool:
        """
        Check if node count exceeds threshold and adjust mesh if needed.
        
        Returns:
            True if mesh was adjusted, False otherwise
        """
        total_nodes = self.get_total_node_count()
        self.echo(f"[Mesh Check] Total nodes: {total_nodes}")
        
        if total_nodes > self.max_nodes:
            self.echo(f"[WARNING] Node count {total_nodes} exceeds threshold {self.max_nodes}")
            self.echo(f"[Action] Increasing mesh size multiplier and regenerating...")
            
            # Calculate new multiplier (increase by 20% each iteration)
            old_multiplier = self.mesh_size_multiplier
            self.mesh_size_multiplier *= 1.2
            
            self.echo(f"[Mesh Refinement] Multiplier: {old_multiplier:.2f} -> {self.mesh_size_multiplier:.2f}")
            return True
        
        self.echo(f"[OK] Node count within limit")
        return False
    
    # ==================== Scene Generation ====================
    
    def generate_scene(
        self,
        output_mesh: str = "cloth_fall.g",
        output_xml: str = "cloth_fall.xml",
        max_refinement_iterations: int = 5
    ) -> Dict:
        """
        Generate complete cloth fall scene with automatic mesh refinement.
        固定生成：Volume 1=地板, 2=布料, 3-5=长方体, 6=球体
        
        Args:
            output_mesh: Output Exodus mesh filename
            output_xml: Output Peridigm XML config filename
            max_refinement_iterations: Maximum number of mesh refinement attempts
        
        Returns:
            Dictionary containing all entity IDs and metadata
        """
        
        for iteration in range(max_refinement_iterations):
            self.echo(f"\n{'='*60}")
            self.echo(f"ITERATION {iteration + 1} (mesh multiplier: {self.mesh_size_multiplier:.2f})")
            self.echo(f"{'='*60}\n")
            
            # Clear all entities from previous iteration
            if iteration > 0:
                cubit.cmd("reset")
                self.volumes.clear()
                self.blocks.clear()
                self.nodesets.clear()
            
            # 1. Create floor (Volume 1, Block 1, Nodeset 1)
            self.echo("[1/6] Creating floor (Block 1, Nodeset 1)...")
            floor_vol, floor_block, floor_ns = self.make_brick_and_block(
                *self.floor_size,
                block_id=1,              # 显式指定Block ID
                block_name="block_1",
                nodeset_id=1,            # 显式指定Nodeset ID
                nodeset_name="nodelist_1",
                mesh=True,
                mesh_size=2.0
            )
            self.move_volume_to_center(floor_vol, 0, 0, -self.floor_size[2]/2)
            assert floor_block == 1, f"Floor block ID should be 1, got {floor_block}"
            assert floor_ns == 1, f"Floor nodeset ID should be 1, got {floor_ns}"
            self.volumes['floor'] = floor_vol
            self.blocks['floor'] = floor_block
            self.nodesets['floor'] = floor_ns
            
            # 2. Create cloth (Volume 2, Block 2, Nodeset 2)
            self.echo("[2/6] Creating cloth (Block 2, Nodeset 2)...")
            cloth_vol, cloth_block, cloth_ns = self.make_brick_and_block(
                *self.cloth_size,
                block_id=2,              # 显式指定Block ID
                block_name="block_2",
                nodeset_id=2,            # 显式指定Nodeset ID
                nodeset_name="nodelist_2",
                mesh=True,
                mesh_size=2.0
            )
            self.move_volume_to_center(cloth_vol, 0, 0, self.cloth_height)
            assert cloth_block == 2, f"Cloth block ID should be 2, got {cloth_block}"
            assert cloth_ns == 2, f"Cloth nodeset ID should be 2, got {cloth_ns}"
            self.volumes['cloth'] = cloth_vol
            self.blocks['cloth'] = cloth_block
            self.nodesets['cloth'] = cloth_ns
            
            # 3. Create 3 bricks (Blocks 3-5, Nodesets 3-5)
            self.echo("[3/6] Creating 3 bricks (Blocks 3-5, Nodesets 3-5)...")
            self.volumes['bricks'] = []
            self.blocks['bricks'] = []
            self.nodesets['bricks'] = []
            
            for i in range(2):
                # Random size within range
                x_len = random.uniform(*self.brick_size_range[0])
                y_len = random.uniform(*self.brick_size_range[1])
                z_len = random.uniform(*self.brick_size_range[2])
                
                expected_block_id = 3 + i
                expected_nodeset_id = 3 + i
                
                try:
                    vol, blk, ns = self.create_brick_random(
                        x_len=x_len,
                        y_len=y_len,
                        z_len=z_len,
                        x_range=(-60, 60),
                        y_range=(-60, 60),
                        z_range=(20, 60),
                        block_id=expected_block_id,        # 显式指定Block ID
                        block_name=f"block_{i+3}",
                        nodeset_id=expected_nodeset_id,    # 显式指定Nodeset ID
                        nodeset_name=f"nodelist_{expected_nodeset_id}",
                        mesh_size=2.0
                    )
                    
                    assert blk == expected_block_id, f"Brick {i+1} block ID should be {expected_block_id}, got {blk}"
                    assert ns == expected_nodeset_id, f"Brick {i+1} nodeset ID should be {expected_nodeset_id}, got {ns}"
                    
                    self.volumes['bricks'].append(vol)
                    self.blocks['bricks'].append(blk)
                    self.nodesets['bricks'].append(ns)
                    
                except RuntimeError as e:
                    self.echo(f"[Warning] Could not place brick {i+1}: {e}")
                    # If placement fails, we need to restart
                    raise RuntimeError(f"Failed to place brick {i+1}, restarting scene generation...")
                
            for i in range(1):
                # Random size within range
                x_len = random.uniform(*self.brick_size_range_tall[0])
                y_len = random.uniform(*self.brick_size_range_tall[1])
                z_len = random.uniform(*self.brick_size_range_tall[2])
                
                expected_block_id = 5 + i
                expected_nodeset_id = 5 + i
                
                try:
                    vol, blk, ns = self.create_brick_random(
                        x_len=x_len,
                        y_len=y_len,
                        z_len=z_len,
                        x_range=(-40, 40),
                        y_range=(-40, 40),
                        z_range=(20, 60),
                        block_id=expected_block_id,        # 显式指定Block ID
                        block_name=f"block_{i+5}",
                        nodeset_id=expected_nodeset_id,    # 显式指定Nodeset ID
                        nodeset_name=f"nodelist_{expected_nodeset_id}",
                        mesh_size=2.0
                    )
                    
                    assert blk == expected_block_id, f"Brick {i+3} block ID should be {expected_block_id}, got {blk}"
                    assert ns == expected_nodeset_id, f"Brick {i+3} nodeset ID should be {expected_nodeset_id}, got {ns}"
                    
                    self.volumes['bricks'].append(vol)
                    self.blocks['bricks'].append(blk)
                    self.nodesets['bricks'].append(ns)
                    
                except RuntimeError as e:
                    self.echo(f"[Warning] Could not place brick {i+3}: {e}")
                    # If placement fails, we need to restart
                    raise RuntimeError(f"Failed to place brick {i+3}, restarting scene generation...")
            
            # 4. Create 1 sphere (Block 6, Nodeset 6)
            self.echo("[4/6] Creating sphere (Block 6, Nodeset 6)...")
            radius = random.uniform(*self.sphere_radius_range)
            
            try:
                sphere_vol, sphere_block, sphere_ns = self.create_sphere_random(
                    radius=radius,
                    x_range=(-40, 40),
                    y_range=(-40, 40),
                    z_range=(20, 60),
                    block_id=6,          # 显式指定Block ID
                    block_name="block_6",
                    nodeset_id=6,        # 显式指定Nodeset ID
                    nodeset_name="nodelist_6",
                    mesh_size=2.0
                )
                
                assert sphere_block == 6, f"Sphere block ID should be 6, got {sphere_block}"
                assert sphere_ns == 6, f"Sphere nodeset ID should be 6, got {sphere_ns}"
                
                self.volumes['sphere'] = sphere_vol
                self.blocks['sphere'] = sphere_block
                self.nodesets['sphere'] = sphere_ns
                
            except RuntimeError as e:
                self.echo(f"[Warning] Could not place sphere: {e}")
                raise RuntimeError("Failed to place sphere, restarting scene generation...")
            
            # 5. Verify all IDs
            self.echo("[5/6] Verifying ID consistency...")
            all_blocks = list(cubit.get_entities("block"))      # 转为列表
            all_nodesets = list(cubit.get_entities("nodeset"))  # 转为列表
            
            self.echo(f"  Block IDs: {all_blocks}")
            self.echo(f"  Nodeset IDs: {all_nodesets}")
            
            assert all_blocks == [1, 2, 3, 4, 5, 6], f"Block IDs should be [1,2,3,4,5,6], got {all_blocks}"
            assert all_nodesets == [1, 2, 3, 4, 5, 6], f"Nodeset IDs should be [1,2,3,4,5,6], got {all_nodesets}"
            
            self.echo("  ✓ Block IDs: [1, 2, 3, 4, 5, 6]")
            self.echo("  ✓ Nodeset IDs: [1, 2, 3, 4, 5, 6]")
            
            # 6. Check mesh and refine if needed
            self.echo("[6/6] Checking mesh node count...")
            needs_refinement = self.check_and_adjust_mesh()
            
            if not needs_refinement:
                # Success! Export files
                self.echo(f"\n{'='*60}")
                self.echo("MESH REFINEMENT COMPLETE - Exporting files...")
                self.echo(f"{'='*60}\n")
                
                self._export_mesh(output_mesh)
                self._export_peridigm_xml(output_xml, output_mesh)
                
                return self._get_metadata()
            
            if iteration == max_refinement_iterations - 1:
                self.echo(f"\n[ERROR] Failed to achieve target node count after {max_refinement_iterations} iterations")
                self.echo(f"[INFO] Current nodes: {self.get_total_node_count()}, Target: {self.max_nodes}")
                self.echo(f"[INFO] Consider increasing max_nodes or reducing object sizes")
                
                # Export anyway
                self._export_mesh(output_mesh)
                self._export_peridigm_xml(output_xml, output_mesh)
                return self._get_metadata()
    
    def _export_mesh(self, filename: str):
        """Export Exodus mesh file with unit conversion (mm -> m)"""
        self.echo(f"[Export] Converting units (mm -> m) by scaling 0.01...")
        cubit.cmd("volume all scale 0.01")
        
        self.echo(f"[Export] Writing mesh to {filename}...")
        cubit.cmd(f'export mesh "{filename}" dimension 3 overwrite')
        self.echo(f"[Success] Mesh exported to {filename}")
    
    def _export_peridigm_xml(self, filename: str, mesh_filename: str):
        """Generate and export Peridigm XML configuration for fixed structure"""
        self.echo(f"[Export] Writing Peridigm config to {filename}...")
        def _plist(parent, name):
            return ET.SubElement(parent, "ParameterList", {"name": name})
        
        def _p(parent, name, ptype, value):
            ET.SubElement(parent, "Parameter", {"name": name, "type": ptype, "value": str(value)})
        
        def _pretty(elem, level=0):
            indent = "  "
            i = "\n" + level * indent
            if len(elem):
                if not elem.text or not elem.text.strip():
                    elem.text = i + indent
                if not elem.tail or not elem.tail.strip():
                    elem.tail = i
                for child in elem:
                    _pretty(child, level + 1)
                if not child.tail or not child.tail.strip():
                    child.tail = i
            else:
                if level and (not elem.tail or not elem.tail.strip()):
                    elem.tail = i
            return ET.tostring(elem, encoding='unicode')
        
        # Materials
        materials_rigid = {
            "Material Model": ("string", "Elastic"),
            "Density": ("double", 7800.0),
            "Bulk Modulus": ("double", 140.90e9),
            "Shear Modulus": ("double", 78.94e9),
        }
        materials_cloth = {
            "Material Model": ("string", "Viscoelastic"),
            "Density": ("double", 300.0),
            "Bulk Modulus": ("double", "8.33e5"),
            "Shear Modulus": ("double", "3.85e5"),
            "lambda i": ("double", 0.8),
            "tau b": ("double", 0.001),
        }
        
        # Build blocks list (固定顺序)
        blocks = [
            (f"block_{self.blocks['floor']}", "Rigid Material", "Floor Block"),
            (f"block_{self.blocks['cloth']}", "Cloth Material", "Cloth Block"),
            (f"block_{self.blocks['bricks'][0]}", "Rigid Material", "Brick Block 1"),
            (f"block_{self.blocks['bricks'][1]}", "Rigid Material", "Brick Block 2"),
            (f"block_{self.blocks['bricks'][2]}", "Rigid Material", "Brick Block 3"),
            (f"block_{self.blocks['sphere']}", "Rigid Material", "Sphere Block"),
        ]
        
        # Gravity nodesets (所有物体除了地板)
        gravity_sets = [
            (f"nodelist_{self.nodesets['cloth']}", 300.0),
            # (f"nodelist_{self.nodesets['bricks'][0]}", 7800.0),
            # (f"nodelist_{self.nodesets['bricks'][1]}", 7800.0),
            # (f"nodelist_{self.nodesets['bricks'][2]}", 7800.0),
            # (f"nodelist_{self.nodesets['sphere']}", 7800.0),
        ]

        # Fixed nodesets (根据pritesh要求，所有物体除了布料都要静止)

        fixed_sets = [
            (f"nodelist_{self.nodesets['floor']}"),
            (f"nodelist_{self.nodesets['bricks'][0]}"),
            (f"nodelist_{self.nodesets['bricks'][1]}"),
            (f"nodelist_{self.nodesets['bricks'][2]}"),
            (f"nodelist_{self.nodesets['sphere']}"),
        ]
        
        # Create XML
        root = ET.Element("ParameterList", {"name": "ClothFallSimulation"})
        _p(root, "Verbose", "bool", "true")
        
        # Discretization
        disc = _plist(root, "Discretization")
        _p(disc, "Type", "string", "Exodus")
        _p(disc, "Input Mesh File", "string", mesh_filename.split('/')[-1])
        
        # Materials
        mats = _plist(root, "Materials")
        m_rigid = _plist(mats, "Rigid Material")
        for k, (t, v) in materials_rigid.items():
            _p(m_rigid, k, t, v)
        m_cloth = _plist(mats, "Cloth Material")
        for k, (t, v) in materials_cloth.items():
            _p(m_cloth, k, t, v)
        
        # Blocks (Horizon = 0.08)
        blks = _plist(root, "Blocks")
        for blk_name, matref, title in blocks:
            b = _plist(blks, title)
            _p(b, "Block Names", "string", blk_name)
            _p(b, "Material", "string", matref)
            _p(b, "Horizon", "double", 0.06)
        
        # Contact
        cnt = _plist(root, "Contact")
        _p(cnt, "Verbose", "bool", "true")
        _p(cnt, "Search Radius", "double", 0.05)
        _p(cnt, "Search Frequency", "int", 2)

        # --- Contact Models ---
        models = _plist(cnt, "Models")

        m1 = _plist(models, "Steel Cloth Contact")
        _p(m1, "Contact Model", "string", "Short Range Force")
        _p(m1, "Contact Radius", "double", 0.02)
        _p(m1, "Spring Constant", "double", "5.0e6")
        _p(m1, "Friction Coefficient", "double", 0.3)
        _p(m1, "Damping Coefficient", "double", 0.05)

        # m2 = _plist(models, "Steel Steel Contact")
        # _p(m2, "Contact Model", "string", "Short Range Force")
        # _p(m2, "Contact Radius", "double", 0.05)
        # _p(m2, "Spring Constant", "double", "1.0e10")
        # _p(m2, "Friction Coefficient", "double", 0.2)
        # _p(m2, "Damping Coefficient", "double", 0.005)

        # --- Interactions ---
        inter = _plist(cnt, "Interactions")

        # Floor (1) <-> Bricks/Sphere (3,4,5,6)
        # for bid in [3, 4, 5, 6]:
        #     pair = _plist(inter, f"Floor Block{bid}")
        #     _p(pair, "Contact Model", "string", "Steel Steel Contact")
        #     _p(pair, "First Block", "string", "block_1")
        #     _p(pair, "Second Block", "string", f"block_{bid}")

        # Floor (1) <-> Cloth (2)
        fc = _plist(inter, "Floor Cloth")
        _p(fc, "Contact Model", "string", "Steel Cloth Contact")
        _p(fc, "First Block", "string", "block_1")
        _p(fc, "Second Block", "string", "block_2")

        # Bricks/Sphere (3,4,5,6) <-> Cloth (2)
        for bid in [3, 4, 5, 6]:
            pair = _plist(inter, f"Block{bid} Cloth")
            _p(pair, "Contact Model", "string", "Steel Cloth Contact")
            _p(pair, "First Block", "string", f"block_{bid}")
            _p(pair, "Second Block", "string", "block_2")

        # Bricks/Sphere 之间 (3-4, 3-5, 3-6, 4-5, 4-6, 5-6)
        # from itertools import combinations
        # for a, b in combinations([3, 4, 5, 6], 2):
        #     pair = _plist(inter, f"Block{a} Block{b}")
        #     _p(pair, "Contact Model", "string", "Steel Steel Contact")
        #     _p(pair, "First Block", "string", f"block_{a}")
        #     _p(pair, "Second Block", "string", f"block_{b}")
                
        # Boundary Conditions
        bcs = _plist(root, "Boundary Conditions")
        
        # Floor fixed
        # for coord in ("x", "y", "z"):
        #     bc = _plist(bcs, f"Floor {coord.upper()}")
        #     _p(bc, "Type", "string", "Prescribed Displacement")
        #     _p(bc, "Node Set", "string", f"nodelist_{self.nodesets['floor']}")
        #     _p(bc, "Coordinate", "string", coord)
        #     _p(bc, "Value", "string", "0.0")


        for ns_name in fixed_sets:
            for coord in ("x", "y", "z"):
                bc = _plist(bcs, f"{ns_name} {coord.upper()}")
                _p(bc, "Type", "string", "Prescribed Displacement")
                _p(bc, "Node Set", "string", ns_name)
                _p(bc, "Coordinate", "string", coord)
                _p(bc, "Value", "string", "0.0")
        # Gravity (body force = rho * g)
        g = 9.81
        for ns_name, rho in gravity_sets:
            bc = _plist(bcs, f"Gravity on {ns_name}")
            _p(bc, "Type", "string", "Body Force")
            _p(bc, "Node Set", "string", ns_name)
            _p(bc, "Coordinate", "string", "z")
            _p(bc, "Value", "string", f"-{rho*g}")
        
        # Solver
        sol = _plist(root, "Solver")
        _p(sol, "Rebalance Frequency", "int", "1e5")
        _p(sol, "Initial Time", "double", 0.0)
        _p(sol, "Final Time", "double", 3)
        ver = _plist(sol, "Verlet")
        _p(ver, "Fixed dt", "double", "2e-6")
        
        # Output
        out = _plist(root, "Output")
        _p(out, "Output File Type", "string", "ExodusII")
        _p(out, "Output Filename", "string", "cloth_fall")
        _p(out, "Output Frequency", "int", 250)  # 2000 fps at dt=2e-6
        ov = _plist(out, "Output Variables")
        for var in ["Coordinates", "Displacement", "Velocity", "Force", "Force_Density",
                    "Contact_Force_Density", "Block_Id", "Dilatation", "Kinetic_Energy",
                    "Weighted_Volume", "Volume", "Global_Kinetic_Energy", "Global_Linear_Momentum",
                    "Global_Angular_Momentum", "Linear_Momentum", "Angular_Momentum",
                    ]:
            _p(ov, var, "bool", "true")
        
        # Write file
        xml_text = _pretty(root)
        with open(filename, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write(xml_text)
        
        self.echo(f"[Success] Peridigm config exported to {filename}")

    def _get_metadata(self) -> Dict:
        """Get metadata about generated scene"""
        return {
            'total_nodes': self.get_total_node_count(),
            'mesh_size_multiplier': self.mesh_size_multiplier,
            'volumes': {
                'block_1': self.volumes['floor'],
                'block_2': self.volumes['cloth'],
                'block_3': self.volumes['bricks'][0],
                'block_4': self.volumes['bricks'][1],
                'block_5': self.volumes['bricks'][2],
                'block_6': self.volumes['sphere'],
            },
            'blocks': self.blocks,
            'nodesets': self.nodesets,
            'structure': '固定: 1=地板, 2=布料, 3-5=长方体, 6=球体',
        }


# ==================== Example Usage ====================

if __name__ == "__main__":
    # Example: 基础使用，固定生成3个长方体 + 1个球体
    generator = ClothFallSceneGenerator(
        max_nodes=30000,
        random_seed=42
    )
    
    metadata = generator.generate_scene(
        output_mesh="cloth_fall_scene.g",
        output_xml="cloth_fall_config.xml"
    )
    
    print("\n" + "="*60)
    print("GENERATION COMPLETE")
    print("="*60)
    print(f"Total nodes: {metadata['total_nodes']}")
    print(f"Mesh multiplier: {metadata['mesh_size_multiplier']:.2f}")
    print(f"Structure: {metadata['structure']}")
    print("\nVolume IDs:")
    for name, vid in metadata['volumes'].items():
        print(f"  {name}: Volume {vid}")
    print("="*60)