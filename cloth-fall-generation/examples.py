"""
使用示例：Cloth Fall Scene Generator

固定生成：3个长方体 + 1个球体
Volume ID顺序：1=地板, 2=布料, 3-5=长方体, 6=球体
"""

import sys
# 添加你的 Cubit 路径
sys.path.append(r'E:\Program Files\Coreform Cubit 2025.12\bin')

from cloth_scene_generator import ClothFallSceneGenerator


# ==================== 示例 1: 基础使用 ====================
def example_basic():
    """最简单的使用方式 - 使用默认参数"""
    print("\n" + "="*60)
    print("示例 1: 基础使用（默认参数）")
    print("固定生成：3个长方体 + 1个球体")
    print("="*60)
    
    generator = ClothFallSceneGenerator(
        max_nodes=50000,          # 最大节点数阈值
        random_seed=42             # 随机种子，确保可重复
    )
    
    metadata = generator.generate_scene(
        output_mesh="example1_scene.g",
        output_xml="example1_config.xml"
    )
    
    print(f"\n结果: {metadata['total_nodes']} 个节点")
    print(f"结构: {metadata['structure']}")


# ==================== 示例 2: 自定义场景大小 ====================
def example_custom_sizes():
    """自定义地板、布料和障碍物尺寸"""
    print("\n" + "="*60)
    print("示例 2: 自定义场景尺寸")
    print("="*60)
    
    generator = ClothFallSceneGenerator(
        max_nodes=50000,
        floor_size=(150, 150, 15),     # 更大的地板
        cloth_size=(60, 60, 3),         # 更大的布料
        cloth_height=100.0,             # 更高的初始位置
        clearance=2.0,                  # 物体之间的最小间隙
        brick_size_range=((15, 25), (15, 25), (20, 35)),  # 更大的长方体
        sphere_radius_range=(10, 18),   # 更大的球体
        random_seed=123
    )
    
    metadata = generator.generate_scene(
        output_mesh="example2_large_scene.g",
        output_xml="example2_large_config.xml"
    )
    
    print(f"\n结果: {metadata['total_nodes']} 个节点")


# ==================== 示例 3: 粗网格（快速仿真）====================
def example_coarse_mesh():
    """使用粗网格进行快速原型开发"""
    print("\n" + "="*60)
    print("示例 3: 粗网格（快速仿真）")
    print("="*60)
    
    generator = ClothFallSceneGenerator(
        max_nodes=20000,               # 更低的节点数限制
        mesh_size_multiplier=2.0,      # 起始就用粗网格
        random_seed=456
    )
    
    metadata = generator.generate_scene(
        output_mesh="example3_coarse.g",
        output_xml="example3_coarse.xml"
    )
    
    print(f"\n结果: {metadata['total_nodes']} 个节点")
    print(f"网格乘数: {metadata['mesh_size_multiplier']:.2f}")


# ==================== 示例 4: 高精度网格 ====================
def example_fine_mesh():
    """高精度网格（允许更多节点）"""
    print("\n" + "="*60)
    print("示例 4: 高精度网格")
    print("="*60)
    
    generator = ClothFallSceneGenerator(
        max_nodes=100000,              # 允许更多节点
        mesh_size_multiplier=0.8,      # 更细的网格
        random_seed=789
    )
    
    metadata = generator.generate_scene(
        output_mesh="example4_fine.g",
        output_xml="example4_fine.xml",
        max_refinement_iterations=10   # 允许更多的细化迭代
    )
    
    print(f"\n结果: {metadata['total_nodes']} 个节点")
    print(f"最终网格乘数: {metadata['mesh_size_multiplier']:.2f}")


# ==================== 示例 5: 批量生成数据集 ====================
def example_batch_generation():
    """批量生成多个场景用于训练数据"""
    print("\n" + "="*60)
    print("示例 5: 批量生成训练数据集")
    print("每个场景都有不同的障碍物位置和尺寸")
    print("="*60)
    
    num_scenes = 5
    
    for i in range(num_scenes):
        print(f"\n--- 生成场景 {i+1}/{num_scenes} ---")
        
        generator = ClothFallSceneGenerator(
            max_nodes=50000,
            random_seed=1000 + i  # 不同的随机种子
        )
        
        metadata = generator.generate_scene(
            output_mesh=f"dataset/scene_{i:03d}.g",
            output_xml=f"dataset/scene_{i:03d}.xml"
        )
        
        print(f"场景 {i+1} 完成: {metadata['total_nodes']} 个节点")


# ==================== 示例 6: 紧凑场景（更多变化）====================
def example_compact_scene():
    """创建更紧凑的场景"""
    print("\n" + "="*60)
    print("示例 6: 紧凑场景")
    print("="*60)
    
    generator = ClothFallSceneGenerator(
        max_nodes=60000,
        floor_size=(120, 120, 12),
        cloth_size=(50, 50, 2),
        cloth_height=90.0,
        clearance=0.5,                  # 较小的间隙允许更密集
        brick_size_range=((8, 15), (8, 15), (12, 20)),  # 较小的长方体
        sphere_radius_range=(6, 12),    # 较小的球体
        random_seed=2024
    )
    
    metadata = generator.generate_scene(
        output_mesh="example6_compact.g",
        output_xml="example6_compact.xml"
    )
    
    print(f"\n结果: {metadata['total_nodes']} 个节点")
    print(f"Volume 结构:")
    for name, vid in metadata['volumes'].items():
        print(f"  {name}: Volume {vid}")


# ==================== 主程序 ====================
if __name__ == "__main__":
    import os
    
    # 创建输出目录
    os.makedirs("dataset", exist_ok=True)
    
    # 运行所有示例（注释掉不需要的）
    
    example_basic()
    # example_custom_sizes()
    # example_coarse_mesh()
    # example_fine_mesh()
    # example_batch_generation()
    # example_compact_scene()
    
    print("\n" + "="*60)
    print("所有示例完成!")
    print("="*60)
