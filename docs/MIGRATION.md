# `multiple-dataset-scene` 到 `release1.0` 的迁移说明

本文记录 `release1.0` 分支如何把旧研究目录
`multiple-dataset-scene/` 中可复用的能力整理到
`Peridigm-pre-process`，以及哪些大文件、运行产物和一次性实验脚本没有进入
Git。这里的“迁移”主要指提取行为、消除硬编码并重新实现为可配置模块，**不是**
把旧目录原样复制一份。

## 1. 范围与来源边界

- 源研究目录：`multiple-dataset-scene/`。
- 目标仓库：`ball-plate-nf-dataset/Peridigm-pre-process/`。
- 发布分支：`release1.0`。
- 旧目录约 71 GB；在不同 `du` 版本和取整方式下可能显示为 72G。该体积主要
  是 Peridigm 结果、分区文件、日志、渲染产物和本机软件构建，不是发布源码。
- 发布版只保留能够重新生成输入、验证输入、运行算例和执行通用结果质量门所需
  的源码、YAML、测试及 Slurm 模板。生成的 mesh、XML、manifest 和结果属于运行
  产物，不作为迁移快照提交。

场景来源需要特别区分：

| 发布场景 | 发布配置与模块 | 实际来源 |
|---|---|---|
| `mug_fall` | `configs/mug_fall*.yaml`、`scenes/mug_fall.py` | 旧 `multiple-dataset-scene` 中的 Gmsh mug CAD、mesh-density、PBE 和自由落体研究逻辑经过重构后迁入 |
| `kw_fracture` | `configs/kw_fracture.yaml`、`scenes/kw_fracture.py` | **目标仓库原有** `kw-board-impact/` 的几何/XML 参考，改写为统一 Gmsh 后端；不来自 `multiple-dataset-scene` |
| `ball_plate` | `configs/ball_plate.yaml`、`scenes/ball_plate.py` | **目标仓库原有** `ball-plate-nf/` 与 `dynamic-impact-generation/` 的场景逻辑，改写为统一 Gmsh 后端；不来自 `multiple-dataset-scene` |
| `cloth_fall` | `configs/cloth_fall.yaml`、`scenes/cloth_fall.py` | **目标仓库原有** `cloth-fall-generation/` 的场景逻辑，移除发布路径对 Cubit 的依赖并改写为 Gmsh；不来自 `multiple-dataset-scene` |

因此，`release1.0` 是“一个统一入口管理四个场景”，而不是声称四个场景都由
`multiple-dataset-scene` 提供。目标仓库中的旧目录仍保留，用于追溯和对照；发布
CLI 不再依赖其中的硬编码 Cubit 路径或静态 XML。

## 2. 已迁入并重构的功能

### 2.1 Gmsh mug CAD 与 Exodus 场景

旧脚本 `make_mug_12k_mesh.py`、`make_mug_8k_floor200_mesh.py`、
`make_mug_dense_floor200_mesh.py` 以及
`freefall_mug_fracture_rigid_plane/make_mug_only_mesh.py` 中重复出现的关键步骤，
现集中在：

- `src/peridigm_preprocess/scenes/mug_fall.py`：mug/floor 场景本身；
- `src/peridigm_preprocess/exodus.py`：Gmsh 初始化、单元提取、block mesh size
  测量、Exodus 写出与重新检查。

新 mug 后端采用 OpenCASCADE 构造解析 CAD：外圆柱减去内圆柱得到有底空心杯体，
再把裁剪后的 torus 与杯体融合成一个 volume。它还统一处理以下旧脚本中分散的
行为：

- 杯体半径、高度、壁厚、把手宽度/高度/突出量可写成标量或随机范围；
- 同一个 seed 先固定几何参数和旋转，后续 mesh feedback 只改变网格尺寸，不会
  在重网格过程中重新抽样几何；
- mug 使用一阶 TET4；floor 使用一层厚度的结构化 HEX8；
- 保持 `block_1/nodelist_1` 为 floor、`block_2/nodelist_2` 为 mug；
- 旋转后按最低接触 patch 重新居中，再精确施加配置的 floor clearance；
- 检查四面体方向、正体积、CAD 体积与离散体积，并把误差写入 metadata；
- 计算 scaled inverse condition number（SICN），必要时在反馈循环内运行 Netgen
  优化；低于 `mesh.minimum_sicn` 时生成失败。

发布版不读取旧的固定 mug `.g` 文件，也不需要 Coreform Cubit 才能生成这四个主
场景。`gmsh`、`meshio`、`netCDF4` 和 NumPy 是发布环境的一部分。

### 2.2 目标单元数反馈与硬上限

旧 8k/12k/40k/100k 脚本各自写死目标区间，并用 TET4 数量约随 `h^-3` 变化的关系
迭代 Gmsh size。发布版把这个约定提升为四个 Gmsh 场景共同使用的配置：

```yaml
mesh:
  target_elements: 10000
  max_elements: 11000
  tolerance: 0.03
  initial_size_m: 0.00585
  max_attempts: 10
  minimum_sicn: 0.10
  horizon_multiplier: 3.015
```

语义如下：

- `target_elements` 是希望落入容差区间的**完整场景总单元数**；
- `max_elements` 是不可越过的硬上限，而不是建议值；
- 每次实际划分后，根据实测数量反馈下一个 nominal size；
- mug 场景先从总预算中扣除不随 Gmsh size 改变的结构化 floor HEX8 数，再对 mug
  TET4 使用立方比例反馈，避免 floor 密度改变时系统性欠划分 mug；
- 反馈历史、尝试次数、每次 nominal size、block 计数和质量写入
  `metadata.json`；
- 容差区间是 soft target；若尝试次数内没有落入区间，但存在满足硬上限和质量门的
  mesh，则选择最接近目标的候选并在 metadata 中记录 `target_met: false`；若无法满足
  `max_elements` 或质量门，case 才标记为错误。超预算 mesh 不会作为成功产物继续
  生成 XML。

这与部分旧脚本的一个重要差异是：旧名称中的 “8k/40k/100k” 常指 **mug 单独的
TET4 数**，新 `target_elements` 指 **floor + mug 的总数**。例如旧
`make_mug_8k_floor200_mesh.py` 的 floor 固定为 `44^2 = 1936` 个 HEX8，因此等价的
新总目标约为 `1936 + 8000 = 9936`，而不是 8000。

临时改变密度不需要新增脚本：

```bash
python generate.py generate configs/mug_fall.yaml \
  --set mesh.target_elements=40000 \
  --set mesh.max_elements=42000
```

### 2.3 YAML 配置与统一 XML 生成

旧研究代码通常复制 XML 模板，再用字符串替换或 ElementTree 单独修改 horizon、
damage、接触频率和输出路径。发布版改为：

- `src/peridigm_preprocess/config.py`：加载 YAML/JSON、应用
  `--set dotted.key=value`、填充默认值并检查交叉引用；
- `src/peridigm_preprocess/xml_writer.py`：在 mesh 完成后解析所有依赖网格的量，
  一次写出完整 Peridigm XML；
- `configs/*.yaml`：保存可审查的场景、材料、损伤、接触、边界条件、求解器、
  输出和 runtime 配置；
- `src/peridigm_preprocess/pipeline.py`：把 geometry、Exodus、XML、metadata 和
  dataset manifest 组成一个原子化程度更高的流程。

配置中可显式说明：

- `materials`：材料模型、密度、体积模量、剪切模量和模型附加参数；
- `damage_models`：damage criterion 名称及全部参数，例如 `Critical Stretch` 或
  `Progressive Bond Energy`；
- `blocks`：Exodus block、材料、damage model 以及可选的 block 级 horizon 倍数；
- `contact`：搜索半径倍数、搜索频率、接触模型、接触半径倍数、刚度、摩擦等；
- `boundary_conditions`：位移、初速度、体力；重力可由材料密度和加速度解析；
- `solver`：方法、终止时间，以及 `fixed`、`auto` 或 `safety_factor` 时间步；
- `output`：输出时间间隔/步频和变量集合；
- `runtime`：例如预期 MPI ranks 和所需 Peridigm capability。

`validate-config` 会在 mesh 前检查：场景名、正数范围、
`target_elements <= max_elements`、材料必需参数、block/material/damage/contact 引用、
求解器 time-step mode 和输出配置。检查配置本身不需要导入 Gmsh：

```bash
python generate.py validate-config configs/mug_fall.yaml
```

### 2.4 动态 horizon、接触尺度和自动时间步

旧 mesh-density 代码先从旧单元计数按立方根缩放一个静态 horizon，再逐个替换 XML
中的 `Horizon` 和 `Characteristic Length`。这比完全写死 horizon 更合理，但仍依赖
某个旧参考网格和旧计数。

发布版不接受普通配置中的静态绝对 horizon。mesh 写出后，对每个 Exodus block
测量物理单元边长的中位数，再解析：

```text
horizon(block) = measured_mesh_size(block) * horizon_multiplier(block)
```

- `mesh.horizon_multiplier` 是全局倍数，默认 `3.015`；
- `blocks.<id>.horizon_multiplier` 可覆盖单个 block；
- measured size、测量方法、倍数和最终 horizon 都写入 `metadata.json`；
- XML 中每个 block 的 `Horizon` 会由静态 validator 与 metadata 逐项核对；
- 接触/search radius 也可由选定的 `min`、`max` 或具体 block mesh scale 乘倍数
  得出；
- `solver.time_step.mode: auto` 会使用选择的实测 mesh scale、配置材料中最大的
  P-wave speed 和 safety factor 计算 dt，并可向下取整；
- 因此改变目标单元数或 horizon 倍数后，不会意外沿用另一套密度的绝对长度。

### 2.5 Progressive Bond Energy（PBE）

旧 `generate_parameter_sweeps.py`、`prepare_mug_mesh_density_study.py`、
`prepare_sf5_lowmesh_sweeps.py` 和
`freefall_mug_analytical_floor/build_sweep.py` 中的 PBE 参数关系已集中到 XML resolver。
示例见 `configs/mug_fall_progressive.yaml`：

```yaml
damage_models:
  mug_fracture:
    model: Progressive Bond Energy
    parameters:
      Damage Initiation Stretch: 0.0005
      Characteristic Length: {source: horizon}
      Tensile Modulus: 2.235e+10
      Fracture Energy:
        derive: progressive_fracture_energy
        tensile_modulus_parameter: Tensile Modulus
        failure_stretch_ratio: 4.0
```

解析关系为：

```text
sf = s0 * failure_stretch_ratio
Gc = 0.5 * Et * s0 * sf * characteristic_length
   = 0.5 * Et * s0^2 * failure_stretch_ratio * horizon
```

`Characteristic Length` 绑定最终的 block horizon，`Fracture Energy` 随 mesh size
或 horizon multiplier 自动重算。若同一个 damage entry 被不同 horizon 的多个 block
共享，resolver 会拒绝这种有歧义的配置，要求拆成独立 damage entries。

该迁移只负责生成并验证相应 XML；它**不会**把 PBE 或其他自定义材料模型添加
到 stock Peridigm。运行 progressive 示例仍需用户通过 `PERIDIGM_BIN` 指定具备相应
模型的自定义 binary，并自行保证该 binary 与配置兼容。

### 2.6 可复现输出与覆盖保护

统一入口为：

```bash
python generate.py generate configs/mug_fall.yaml
```

每个 case 的契约为：

```text
output/<scenario>/
├── manifest.json
└── <scenario>_<seed>/
    ├── <scenario>_<seed>.g
    ├── <scenario>_<seed>.xml
    ├── metadata.json
    └── results/
```

`metadata.json` 保存 seed、抽样几何、网格反馈历史、block 数量和 mesh size、质量、
解析后的 horizon/damage/solver 值、相对文件名及 mesh/XML SHA256。dataset
`manifest.json` 只保存相对 case 路径，避免旧脚本中的个人 home 和机器路径。

- 默认不覆盖已有 case；
- `run.resume: true` 只跳过能够重新通过静态验证的完整 case；
- `--force` 可重建输入，但只要 `results/` 非空就拒绝执行；
- 旧调用形式 `python generate.py SCENE COUNT` 对四个统一场景和别名仍可用，例如
  `freefall_mug`、`ball_plate_nf`。

## 3. 验证、结果质量与 Slurm gates

### 3.1 静态输入验证

旧 `validate_sweeps.py`、`validate_sf5_lowmesh_sweeps.py` 及各研究子目录中的
`validate_sweep.py` 含有很多重复的 XML/mesh/路径检查。与具体 DOE 无关的部分现在
由以下入口承担：

```bash
python generate.py validate output/mug_fall
python generate.py validate output/mug_fall/mug_fall_0000
```

`validation.py`/`quality.validate_dataset()` 会检查：

- metadata、mesh 和 XML 是否存在且非空；
- mesh/XML SHA256 是否与 metadata 一致；
- Exodus 是否可读、node/element/block 计数是否一致；
- 最终文件是否仍满足 `max_elements`；
- XML 中 `Input Mesh File` 是否是指向当前 case mesh 的可移植相对引用；
- 每个 block 的动态 horizon 是否与实测值和 metadata 一致；
- final time、解析后的 fixed dt、damage parameters 是否与配置/metadata 一致；
- manifest 中每个成功 case 是否都能独立通过上述检查。

### 3.2 通用结果质量门与冲击/损伤分析

Peridigm 运行后执行：

```bash
python generate.py quality output/mug_fall/mug_fall_0000
```

`src/peridigm_preprocess/quality.py` 会重新运行输入验证，然后扫描 result Exodus
文件，检查：

- 是否存在非空结果文件；
- 是否拒绝 serial/sharded 混用，并要求分片为精确 suffix `0..ranks-1`，且 ranks
  与 config 和 `run_metadata.json` 一致；
- 所有分片 time axis 是否有限、严格递增且彼此一致；
- 最终时间是否达到配置的 `solver.final_time`；
- transient fields 是否含 NaN/Inf；
- Damage 是否位于 `[0, 1]`，并在时间上不可逆（不下降）。
- `run_metadata.json` 是否记录成功结束，并与当前 mesh/XML 哈希、ranks 和
  decomposition sidecar 一致。

报告原子写入 `quality_report.json`；错误使 CLI 返回非零状态。

旧 `freefall_mug_analytical_floor/analyze_pilot_pairs.py`、
`analyze_production.py` 和 `analyze_dynamics.py` 中可移植的分片合并和冲击指标另由
`src/peridigm_preprocess/result_analysis.py` 提供：

```bash
python generate.py analyze output/mug_fall/mug_fall_0000
python generate.py analyze output/ball_plate/ball_plate_0000 \
  --block ball --damage-block plate
```

该分析器自动发现串行/完整 Nemesis 结果，严格拒绝分片时间轴差异，以 global element
ID 标识单元，并在不同分片重复声明同一个 global element ownership 时硬失败；不会
静默采用 first-wins。nodal fields 则通过 block connectivity 映射到物质点。
`analysis.blocks`/`--block` 选择单一运动体；`analysis.damage_blocks`/
`--damage-block` 独立选择受损目标，后者写入嵌套 `damage_analysis`，不会把目标和
冲击体混成一个动量/传力聚合。`analysis.damage_thresholds` 是默认阈值来源，CLI
的 `--damage-threshold` 可显式覆盖。

有效 element `Volume` 存在时，`impact_report.json` 可包含体积/质量加权质心与速度、
Force_Density 积分力、冲量、扣除配置 body force 后的动量闭合、Damage 体积统计、
首次接触/损伤、反弹速度和恢复系数。若缺 `Volume` 且只能使用 unit weights，则仅
输出明确标记的 count-weighted 运动学/Damage 计数统计；物理体积、质量、
Force_Density 力/冲量和动量字段为 unavailable/`null`，不会把元素计数伪装成 m³。
完整 direct `Force` 可独立使用。缺失或覆盖不全的字段会产生明确 warning。
`compare_convergence_reports()` 可对 paired-dt 或 mesh 报告应用绝对/相对阈值 gate。

仍需场景专用实现或人工解释的部分包括：

- 全局能量/动量平衡；
- 由 broken bonds 构造碎片连通图和体积加权碎片统计。

质量门和 impact analysis 不会假装仅凭一个 Damage 标量场就能准确得到碎片图，
也不会把一个数值差异阈值自动等同于实验标定。需要正式物理结论时，仍应增加相应
场景的能量、broken-bond 连通和实验对照分析。

### 3.3 可移植 Slurm 流程

旧 `decomp.slurm`、`run_all.slurm`、各子目录 `run_array.slurm` 和
`submit_all.sh` 写死了个人绝对路径、Peridigm/Trilinos 安装、case table 和数组范围。
发布版对应为：

- `hpc/generate.slurm`：在 compute node 上运行统一生成命令；
- `hpc/submit_dataset.py`：从 manifest 选择成功 case，构造数组；默认只打印
  `sbatch` 命令，只有 `--submit` 才真正提交；
- `hpc/run_manifest_case.slurm`：由数组 index 解析 manifest 中的相对 case 路径；
- `hpc/run_case.slurm`：读取 case metadata 中的 mesh/XML/results 文件名，分解并
  运行单个 case；
- `hpc/validate_results.slurm`：数组式执行 `generate.py quality`，适合使用
  `afterany` 依赖，即使计算 task 失败也保留质量报告/错误信息。

运行路径通过 `PERIDIGM_BIN`、`PERIDIGM_ENV`、`DECOMP_BIN`、`MPI_LAUNCHER`、
`MPI_RANKS` 等环境变量注入。runner 的硬门包括：

- 完整 decomposition 只有在 sidecar 中的基础 mesh SHA256、rank 数和精确 suffix
  列表都匹配时才复用；
- 存在 0 个分片时才调用 `decomp`；
- 只存在部分分片、分片数量不等于 ranks 或任一分片为空时拒绝运行；
- `submit_dataset.py` 从所有 runnable case metadata 推断 ranks，并拒绝 case 之间或
  显式参数与 metadata 不一致；
- 每次 launch 在 `results/run_metadata.json` 中记录实际 ranks、mesh/XML hash、
  精确 Exodus 相对路径/大小、Slurm ID、状态和退出码；
- `results/` 存在任何条目（包括隐藏文件）时一律拒绝覆盖；如需重跑，调用者应先
  人工检查并整体移动旧结果目录；
- mesh/XML 缺失或 Peridigm binary 不可执行时尽早失败；
- 线程数默认固定为 1，避免 MPI rank 内意外 oversubscription。

典型流程为：

```bash
# 1. compute node 生成
mkdir -p logs
sbatch hpc/generate.slurm configs/mug_fall.yaml run.count=20

# 2. 默认 dry-run；确认命令后再增加 --submit
python hpc/submit_dataset.py output/mug_fall/manifest.json \
  --partition=preempt --concurrency=5

# 3. 指定运行 binary 后提交
PERIDIGM_BIN=/path/to/Peridigm \
python hpc/submit_dataset.py output/mug_fall/manifest.json \
  --partition=preempt --concurrency=5 --submit
```

`validate_results.slurm` 的 `afterany` 依赖需在站点提交命令中显式设置；
`submit_dataset.py` 当前不会自动组装旧 analytical-floor 的完整
“decomp → paired-dt pilot → physics validator → production → final validator”链。
其中 decomposition 完整性、结果保护和通用 content gate 已迁入；paired-dt/mesh
报告和传力指标可通过上一节的 impact/convergence API 处理，能量和 fragment graph
仍需场景专用实现。

## 4. 旧脚本到新模块/命令的映射

| 旧路径或脚本 | 新位置/命令 | 迁移说明 |
|---|---|---|
| `make_mug_12k_mesh.py` | `scenes/mug_fall.py` + `exodus.py` | OCC mug CAD、TET4 提取、方向/体积/质量检查和 Exodus 写出已模块化；12k 不再是脚本名，而是 YAML/`--set` 的总网格预算 |
| `make_mug_8k_floor200_mesh.py` | `scenes/mug_fall.py` + `configs/mug_fall.yaml` | 200 mm floor、44×44 HEX8、mug feedback 和 CAD 复用；注意新目标为 scene total |
| `make_mug_dense_floor200_mesh.py` | `generate ... --set mesh.target_elements=... --set mesh.max_elements=...` | 40k/100k 变体改为配置，不再为每个密度维护复制脚本 |
| `freefall_mug_fracture_rigid_plane/make_mug_only_mesh.py` | `scenes/mug_fall.py` 中的 mug CAD/mesh 逻辑 | CAD 能力保留；发布主配置默认生成实体 floor，旧解析刚性平面这一特定 Peridigm 二次开发没有伪装成 stock 功能 |
| `prepare_mug_mesh_density_study.py` | `xml_writer.py` 的 per-block measured horizon + YAML mesh budget | 不再从旧 8k 计数缩放静态 horizon；每个实际 mesh 重新测量并解析 |
| `generate_parameter_sweeps.py` | `configs/*.yaml`、`config.py`、`pipeline.py`、`generate.py generate` | 材料/damage/contact/solver/XML/manifest 的共同逻辑统一；seed batch 用 `run.count/base_seed` |
| `make_frequency_suite_8k_floor200.py`、`make_sf1_8k_floor200_*.py`、`prepare_sf5_lowmesh_sweeps.py` | `contact.search_frequency`、mesh 配置及命令行 `--set` | 搜索频率、密度和 damage 参数成为显式配置，不再复制 XML |
| 各研究目录 `build_sweep.py` | YAML + 重复 `--set`，或一个读取设计表的外部 driver | 公共 case 生成能力已迁入；L9、44-case 接触 DOE 等任意设计矩阵尚不是 CLI 的一等子命令，不能把单个 release config 等同于完整旧 DOE |
| `validate_sweeps.py`、`validate_sf5_lowmesh_sweeps.py`、各目录 `validate_sweep.py` | `generate.py validate`、`validation.py`、`quality.py` | 抽取公共的 hash、Exodus、XML、horizon、结果完整性、时间轴和 Damage 检查；特定设计矩阵行数/参数组合仍应由设计 driver 验证 |
| `analyze_dynamics.py`、`analyze_pilot_pairs.py`、`analyze_production.py` | `generate.py analyze`、`result_analysis.py` | 使用 global ID 合并分片，并对重复 global-element ownership 硬失败；有 Volume 时执行物理加权、接触/合力冲量和动量闭合，缺失时仅 count-weighted 且抑制量纲积分；冲击体与 Damage 目标分角色选择；paired-dt/mesh 报告可用 convergence API 比较 |
| fragment scripts | 尚无通用 fragment graph 子命令 | scalar Damage 统计已迁入；broken-bond 连通图与碎片拓扑没有被错误近似为 Damage 阈值 |
| `decomp.slurm`、各 `run_array.slurm`、`run_all.slurm` | `hpc/run_case.slurm` + `hpc/run_manifest_case.slurm` | manifest 驱动数组，移除个人路径；用 mesh SHA sidecar 验证完整分片，拒绝 partial/stale/untracked shards 和非空结果 |
| `submit_all.sh`、各 `submit.sh`/`submit_search.sh` | `hpc/submit_dataset.py` | 默认 dry-run，`--submit` 才产生外部状态；资源和并发由参数指定 |
| `validate_pilots.slurm`、`validate_results.slurm` | `hpc/validate_results.slurm` | 提供通用 `afterany` content gate；旧 paired-dt physics gate 不在通用脚本内 |
| `render_*.py`、`make_*montage*.py`、`encode_*videos.sh` | 未进入 release 核心 | 它们是从大结果生成可视化的场景/机器专用后处理，不是几何/XML/通用质量门的依赖 |

命令级替代示例：

```bash
# 旧：为一个密度复制/修改专用 Python 文件
# 新：同一后端、同一 YAML schema，仅覆盖预算
python generate.py generate configs/mug_fall.yaml \
  --set run.count=10 \
  --set run.base_seed=100 \
  --set mesh.target_elements=40000 \
  --set mesh.max_elements=42000 \
  --set mesh.horizon_multiplier=3.2

# 显式替换 damage criterion 参数
python generate.py generate configs/mug_fall.yaml \
  --set damage_models.mug_fracture.model='Critical Stretch' \
  --set damage_models.mug_fracture.parameters.'Critical Stretch'=0.001

# 输入与结果两层 gate
python generate.py validate output/mug_fall
python generate.py quality output/mug_fall/mug_fall_0100
```

## 5. 约 71 GB 中明确排除的内容

以下内容没有从 `multiple-dataset-scene` 迁入 Git。对应的 output、results、logs、
shard、build 和媒体文件模式已由 `.gitignore` 设置防护；其余历史清单/快照也按本
迁移政策排除：

1. **Peridigm 数值结果**

   - 各 case 的 `results/`；
   - 串行或并行 Exodus/Nemesis 输出，如 `*.e`、`*.e.36.*`、`*.exo`；
   - 25 ms pilot、100 ms production、2 s long-run 等历史结果；
   - 已生成的 fragment/trajectory/DOE summary 属于结果派生物，不作为发布输入。

2. **日志、状态和历史作业记录**

   - `logs/*.out`、`logs/*.err`、Peridigm stdout/stderr；
   - `status/*.status`、`*.lock`、submission locks；
   - `submission.json` 中的旧 Job ID、时间戳和依赖链；
   - 针对已经结束的 `2104...`/`2105...` 作业写死的 retry Slurm 文件；
   - 历史队列状态不具备可移植性，也不应让新用户误认为作业仍有效。

3. **生成 mesh 和 decomposition shards**

   - 源目录 `geometry/*.g`；
   - `*.g.36.*` 等 decomp/nem_spread 分区；
   - `.decomp*.lock`、partial shards、零字节失败产物；
   - 这些现在应由 YAML + seed 重新生成，再由通用 runner 按 ranks 分解。

4. **本机 binary、安装树和构建中间文件**

   - `peridigm_install*/`、隔离复制的 `Peridigm` executable；
   - Trilinos/Peridigm build tree、静态/动态库和缓存；
   - 本机 binary SHA 文件只能描述旧机器上的可执行文件，不能代替可复现依赖；
   - 自定义 PBE/analytical-floor/contact-damping patch 与 binary 不会自动成为 stock
     Peridigm 的一部分。发布配置通过 capability 说明和 `PERIDIGM_BIN` 外部注入处理。

5. **可视化和分析产物**

   - render frames、PNG montage、MP4/AVI、ParaView 临时文件；
   - `analysis/` 下从历史结果生成的大表、JSON 和视频选择结果；
   - `geometry_preview.png` 等研究预览不是一键生成流程的运行依赖。

6. **生成的 case 快照与缓存**

   - 数百份 `cases/*/input.xml`、`parameters.json`、`case_table.tsv`、`sweep.csv`；
   - 旧 manifest 中的绝对路径和历史哈希快照；
   - `__pycache__`、`*.pyc`、conda/venv、本机 Cubit journal/工作文件；
   - release 保留代表性 YAML 与生成规则，case/XML/manifest 由命令重新产生。

排除这些文件不是丢弃科学来源：旧研究目录仍是历史实验和定制物理分析的归档；
release 仓库保存的是可维护、可测试、能够从头生成新输入的最小来源集合。

## 6. 迁移后的行为变化与限制

- 所有发布几何与 XML 统一使用 SI；旧脚本中隐含的 mesh/horizon 常量不再跨密度
  复用。
- `target_elements` 是 soft target，`max_elements` 才是 hard cap；目标容差不是
  “保证精确等于某个整数”。未命中 soft target 可成功并记录 `target_met: false`；
  只有硬上限或质量门无法满足时才失败。
- 同一 seed 的 geometry sampling 与 mesh feedback 分离，便于进行纯 mesh/horizon
  convergence 对比。
- `Horizon`、接触尺度、可选 auto dt 和 PBE fracture energy 在 mesh 完成后解析，
  所有最终值进入 metadata 并参与验证。
- 四个发布场景都使用 Gmsh；目标仓库原有 Cubit/Notebook 仍是参考资料，不是发布
  CLI 的依赖。
- 发布版生成输入而不捆绑 Peridigm。PBE、Neo-Hookean、解析刚性平面或接触阻尼
  二次开发是否可运行，取决于外部 binary 的真实 capability。
- 通用 quality gate 证明“结果文件在共同结构/数值不变量上合格”，不等于证明断裂
  物理已经网格收敛、时间步收敛或实验标定。
- 通用 Slurm 文件不包含某个站点的账号、绝对 home、partition 或 module 命令；这些
  通过 `sbatch` 参数和环境注入。

## 7. 发布验收路径

不产生 mesh 的快速检查：

```bash
python generate.py --help
python generate.py list
python generate.py validate-config configs/kw_fracture.yaml
python generate.py validate-config configs/ball_plate.yaml
python generate.py validate-config configs/mug_fall.yaml
python generate.py validate-config configs/mug_fall_progressive.yaml
python generate.py validate-config configs/cloth_fall.yaml
python -m unittest discover -s tests -v
```

具备 Gmsh 依赖后，分别生成四个场景并运行静态验证：

```bash
python generate.py generate configs/kw_fracture.yaml
python generate.py generate configs/ball_plate.yaml
python generate.py generate configs/mug_fall.yaml
python generate.py generate configs/cloth_fall.yaml

python generate.py validate output/kw_fracture
python generate.py validate output/ball_plate
python generate.py validate output/mug_fall
python generate.py validate output/cloth_fall
```

运行 Peridigm 后，再对每个 case 执行 `quality` 或提交
`hpc/validate_results.slurm`。网格、XML、logs、results 和
`quality_report.json` 都属于可再生/运行产物，应留在工作目录或外部数据存储，而不
应提交回 release 分支。
