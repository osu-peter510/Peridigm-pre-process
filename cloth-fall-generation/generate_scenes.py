import sys, re
from pathlib import Path
sys.path.append(r'E:\Program Files\Coreform Cubit 2025.12\bin')

from cloth_scene_generator import ClothFallSceneGenerator


# ----------  XML 后处理  --------------------------------------------------
def patch_xml(xml_path: Path, g_filename: str, out_basename: str):
    txt = xml_path.read_text(encoding="utf-8")

    # 1) Input Mesh File  →  scene_XXXX.g
    txt = re.sub(
        r'(<Parameter\s+name="Input Mesh File"\s+type="string"\s+value=")[^"]+(")',
        rf'\1{g_filename}\2',
        txt, count=1)

    # 2) Output Filename  →  cloth_fall_XXXX
    txt = re.sub(
        r'(<Parameter\s+name="Output Filename"\s+type="string"\s+value=")[^"]+(")',
        rf'\1{out_basename}\2',
        txt, count=1)

    xml_path.write_text(txt, encoding="utf-8")
# -------------------------------------------------------------------------


def generate_scenes(total_scenes=1000, output_root="generated_scenes", base_seed=60, max_nodes=20000):
    out_root = Path(output_root).resolve()
    out_root.mkdir(exist_ok=True)

    for i in range(1, total_scenes + 1):
        tag = f"{i:04d}"
        scene_dir  = out_root / f"scene_{tag}"
        scene_dir.mkdir(exist_ok=True)

        g_name   = f"scene_{tag}.g"
        xml_name = f"scene_{tag}.xml"
        g_path   = scene_dir / g_name
        xml_path = scene_dir / xml_name

        print(f"[{i}/{total_scenes}] Generating {scene_dir.name} …")

        gen = ClothFallSceneGenerator(
            max_nodes=max_nodes,
            random_seed=base_seed + i,
            mesh_size_multiplier=1.1,
            clearance=5.0,
        )
        gen.generate_scene(str(g_path), str(xml_path))

        # 修改 XML：Input Mesh File + Output Filename
        patch_xml(xml_path, g_name, f"cloth_fall_{tag}")

    print("All scenes generated successfully.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Batch-generate cloth-fall Peridigm scenes (.g + .xml).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python generate_scenes.py 100
  python generate_scenes.py 500 --base-seed 42
  python generate_scenes.py 200 --output-dir my_cloth_dataset --max-nodes 30000
""",
    )
    parser.add_argument(
        "n_scenes",
        type=int,
        help="Number of scenes to generate.",
    )
    parser.add_argument(
        "--base-seed",
        type=int,
        default=60,
        metavar="N",
        help="Starting random seed (scene i uses seed base_seed+i). Default: 60.",
    )
    parser.add_argument(
        "--output-dir",
        default="generated_scenes",
        metavar="DIR",
        help="Root output directory. Each scene goes into DIR/scene_XXXX/. Default: generated_scenes.",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=20000,
        metavar="N",
        help="Maximum element count; mesh is coarsened until this is met. Default: 20000.",
    )

    args = parser.parse_args()
    generate_scenes(
        total_scenes=args.n_scenes,
        output_root=args.output_dir,
        base_seed=args.base_seed,
        max_nodes=args.max_nodes,
    )