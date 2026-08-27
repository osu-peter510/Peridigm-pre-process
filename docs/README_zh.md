# Peridigm Preprocess 1.0 中文快速使用

这个版本把分散的几何生成、网格密度控制、Peridigm XML、质量检查和
Slurm 提交整理成了一个统一流程。`multiple-dataset-scene` 中约 71 GB 的
结果、日志、分区网格和本机 Peridigm 安装不会复制进 Git；其中可复用的
Gmsh mug、动态 horizon、Progressive Bond Energy、验证和 Slurm gate 逻辑
已经合并进新代码。

## 安装与生成

```bash
conda env create -f environment.yml
conda activate peridigm-preprocess

python generate.py generate configs/kw_fracture.yaml
python generate.py generate configs/ball_plate.yaml
python generate.py generate configs/mug_fall.yaml
python generate.py generate configs/cloth_fall.yaml
```

四个主场景全部使用 Gmsh，不需要 Coreform Cubit。查看帮助或检查配置时也
不会提前导入 Gmsh：

```bash
python generate.py --help
python generate.py validate-config configs/mug_fall.yaml
```

## 控制网格数量

编辑配置中的：

```yaml
mesh:
  target_elements: 10000   # 希望接近的总单元数
  max_elements: 11000      # 绝对上限，超过就报错
  tolerance: 0.03
  initial_size_m: 0.00585
  horizon_multiplier: 3.015
```

`target_elements ± tolerance` 是软目标：若尝试次数内未命中，但存在满足质量门且不
超过 `max_elements` 的候选，程序会选最接近目标者并记录
`mesh_control.target_met: false`。只有硬上限或质量门无法满足时才失败。

也可以在命令行临时覆盖：

```bash
python generate.py generate configs/mug_fall.yaml \
  --set run.count=20 \
  --set mesh.target_elements=40000 \
  --set mesh.max_elements=42000
```

改变网格数量不会改变同一个 seed 对应的几何参数。生成完成后，程序会对
每个 block 从实际网格测量单元中位边长，然后计算：

```text
horizon = 实测 mesh size × horizon_multiplier
```

因此 horizon 不会继续使用旧网格留下来的静态值。最终 mesh size、倍数和
horizon 都会写入 `metadata.json`，并和 XML 逐项校验。

## 配置 damage、物性和求解器

YAML 中可以显式设置：

- `materials`：材料模型、密度、体积模量、剪切模量及额外参数；
- `damage_models`：例如 `Critical Stretch` 或 `Progressive Bond Energy`；
- `solver`：Verlet、终止时间、固定 dt 或基于材料波速自动计算 dt；
- `contact`：接触/搜索半径倍数、搜索频率、刚度、摩擦和阻尼；
- `output`：输出周期或步频及所有输出变量。

`configs/mug_fall_progressive.yaml` 给出了 Progressive Bond Energy 示例。
它会把 `Characteristic Length` 绑定到最终 horizon，并随网格自动重算
`Fracture Energy`。该模型需要包含相应自定义模型的 Peridigm binary。

## 输出与检查

```text
output/<scenario>/<scene>/
├── <scene>.g
├── <scene>.xml
├── metadata.json
└── results/
```

输入检查：

```bash
python generate.py validate output/mug_fall
```

Peridigm 运行后的第一层质量检查：

```bash
python generate.py quality output/mug_fall/mug_fall_0000
```

它会检查分片、终止时间、时间轴、NaN/Inf、Damage 是否在 `[0,1]` 以及
Damage 是否不可逆；同时要求成功的 `run_metadata.json`，并核对当前 mesh/XML
哈希、ranks 和 decomposition sidecar，最后输出 `quality_report.json`。

冲击与损伤指标分析：

```bash
python generate.py analyze output/mug_fall/mug_fall_0000
python generate.py analyze output/ball_plate/ball_plate_0000 \
  --block ball --damage-block plate
```

`analyze` 会合并串行或 Nemesis 分片，以 global element ID 标识单元；若不同分片
重复声明同一个 global element 的 ownership，则硬失败而不是静默去重。分析结果输出
到 `impact_report.json`。`analysis.blocks`/`--block` 只选择运动/冲击体，用于质心、
速度、力/冲量、动量、反弹和恢复系数；`analysis.damage_blocks`/
`--damage-block` 独立选择受损目标，不同目标的结果写在 `damage_analysis`，避免
冲击体和目标的内力在同一次聚合中抵消。Damage 阈值默认读取
`analysis.damage_thresholds`；重复传入 `--damage-threshold` 可覆盖本次分析。

存在有效 element `Volume` 时，报告才输出物理体积、质量、Force_Density 积分力/
冲量、动量闭合以及 Damage 体积加权统计。若缺少物理 `Volume`，这些量纲字段为
`null`，只输出明确命名的 count-weighted 运动学和 Damage 计数统计（完整的 direct
`Force` 字段仍可使用），并给出 warning。能量闭合和 broken-bond 碎片连通图仍需
场景专用分析。

## Slurm

```bash
mkdir -p logs
sbatch hpc/generate.slurm configs/mug_fall.yaml run.count=20

# 默认只打印 sbatch 命令
python hpc/submit_dataset.py output/mug_fall/manifest.json \
  --partition=preempt --concurrency=5

# 显式 --submit 才会真正提交
PERIDIGM_BIN=/path/to/Peridigm \
python hpc/submit_dataset.py output/mug_fall/manifest.json \
  --partition=preempt --concurrency=5 --submit
```

新的 Slurm 脚本不包含个人绝对路径；它从 manifest/metadata 找到 XML 和
mesh，并从 metadata 推断 ranks。每套 decomposition 都用 sidecar 绑定基础 mesh
SHA256、rank 数和精确分片列表；partial、无来源或旧 mesh 的 shards 会被拒绝。
每次运行还会记录实际 ranks、Slurm ID、mesh/XML 哈希、状态和退出码，并保护任何
非空结果目录（包括仅含隐藏文件的目录）；完成 sidecar 还记录精确 Exodus 路径和
文件大小，质量门会再次核对。
