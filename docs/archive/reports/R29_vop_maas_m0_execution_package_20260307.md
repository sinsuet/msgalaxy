# R29 VOP-MaaS M0 可执行研究包冻结说明（2026-03-07）

## 1. 文档目的

本文档用于把 `VOP-MaaS` 的 `M0` 阶段从“研究计划”冻结为一个**当前即可执行、可复现、可审计**的研究包。

本次冻结遵循一个原则：

> 为了迅速定线，`M0` 只保留当前已经跑通的 `L1-L4 + NSGA-III` 主线作为执行口径；其他算法与方法扩展全部后置。

这意味着：

- `NSGA-II`、`MOEA/D` 不进入当前 `M0` 执行包；
- 新的 solver ablation 不进入当前 `M0`；
- `M0` 的目标不是“把所有方法都拉齐”，而是先固定**一条强、稳、已跑通**的基线执行线。

---

## 2. M0 冻结结论

### 2.1 当前唯一执行算法

`M0` 当前唯一保留的执行算法为：

- `NSGA-III`

当前不纳入执行包的算法：

- `NSGA-II`
- `MOEA/D`
- 任何新的 EA / BO / learned policy optimizer

### 2.2 当前唯一执行档位

`M0` 当前唯一保留的执行档位为：

- `L1`
- `L2`
- `L3`
- `L4`

这些档位对应的 BOM 已经在主线中跑通：

- `config/bom/mass/level_L1_foundation_stack.json`
- `config/bom/mass/level_L2_thermal_power_stack.json`
- `config/bom/mass/level_L3_structural_mission_stack.json`
- `config/bom/mass/level_L4_full_stack_operator.json`

### 2.3 当前唯一主 profile

`M0` 的主执行 profile 冻结为：

- `config/system/mass/level_profiles_l1_l4_real_strict.yaml`

其作用不是“代表未来所有实验设置”，而是：

- 复用当前已经跑通的 strict-real 主线；
- 保持 source/operator-family/operator-realization gate 一致；
- 避免在 `M0` 阶段因为 profile 漂移而破坏研究归因。

### 2.4 当前唯一执行内核

当前 `M0` 的执行内核冻结为：

- `mass` 数值优化内核
- `pymoo` 作为唯一搜索核心
- `NSGA-III` 作为唯一启用算法

对于 `vop_maas`，当前约束为：

- 可以作为 experimental/operator-policy 外层控制模式存在；
- 但其执行求解仍必须委托给 `mass`；
- 不允许绕过 `ModelingIntent -> compile -> pymoo -> physics` 主链路。

---

## 3. M0 可执行研究包的组成

### 3.1 执行入口

当前 `M0` 可执行研究包的入口固定为：

- `run/run_scenario.py`
- `run/mass/run_L1.py`
- `run/mass/run_L2.py`
- `run/mass/run_L3.py`
- `run/mass/run_L4.py`
- `run/vop_maas/run_L1.py`
- `run/vop_maas/run_L2.py`
- `run/vop_maas/run_L3.py`
- `run/vop_maas/run_L4.py`

### 3.2 配置组成

#### `mass` backbone

- base config：`config/system/mass/base.yaml`
- level profile：`config/system/mass/level_profiles_l1_l4_real_strict.yaml`

#### `vop_maas` experimental layer

- base config：`config/system/vop_maas/base.yaml`
- level profile：`config/system/mass/level_profiles_l1_l4_real_strict.yaml`

说明：

- 当前 `vop_maas` 已支持消费 `--level-profile`，并将 level profile runtime overrides 注入运行配置；
- 这保证了 `vop_maas` 与当前 strict-real backbone 的实验边界一致。

### 3.3 当前固定算法与种子口径

为了迅速冻结 `M0`，当前口径固定为：

- `pymoo_algorithm = nsga3`
- 默认单种子执行：`pymoo_seed = 42`

后续若要扩展多 seed，不应直接改写 `M0` 定义，而应作为 `M0` 之后的实验扩展：

- 建议后续扩展为 paired multi-seed；
- 但当前 `M0` 不把多算法、多种子同时展开。

---

## 4. M0 当前建议的执行方式

### 4.1 `mass` backbone 复现实验

以下命令用于复现当前已经跑通的 `L1-L4 + NSGA-III + strict-real` backbone：

```bash
python run/mass/run_L1.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
python run/mass/run_L2.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
python run/mass/run_L3.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
python run/mass/run_L4.py --backend comsol --thermal-evaluator-mode online_comsol --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --deterministic-intent --run-naming-strategy compact
```

### 4.2 `vop_maas` 当前建议执行方式

`vop_maas` 在 `M0` 阶段的执行建议分为两层：

#### 开发验线层

```bash
python run/vop_maas/run_L1.py --backend simplified --level-profile config/system/mass/level_profiles_l1_l4_real_strict.yaml --mock-policy --deterministic-intent --max-iterations 1
```

这一层的目标是：

- 验证 routing
- 验证 `VOPG` / `VOPPolicyPack`
- 验证 screening / fallback / metadata

#### 后续研究执行层

当 `vop_maas` 进入正式对照实验时，仍必须遵循：

- level 只用 `L1-L4`
- algorithm 只用 `NSGA-III`
- level profile 只用 `config/system/mass/level_profiles_l1_l4_real_strict.yaml`

也即：

> `vop_maas` 可以变的是“策略层”，不能在 `M0` 阶段同时把“算法层”也变掉。

---

## 5. M0 研究包中的实验组冻结

为了避免 `M0` 范围膨胀，当前建议只冻结以下主对照线：

- `mass_baseline`
- `mass_llm_intent_only`
- `vop_static`
- `vop_screened`

当前明确不进入 `M0` 主执行包的组：

- `vop_reflective`
- `vop_reflective + memory`
- 神经 guidance 组
- 多算法组

---

## 6. M0 研究包中的指标冻结

当前 `M0` 只冻结以下主指标：

- feasibility rate
- `first_feasible_eval`
- `COMSOL_calls_to_first_feasible`
- best CV
- best feasible objective vector
- real-source coverage
- operator-family coverage
- policy validity rate
- policy fallback rate

说明：

- 这些指标已经足以支撑第一篇方法论文的主叙事；
- 额外指标可以保留在 trace / summary 中，但不进入 `M0` 主叙事核心。

---

## 7. M0 研究包中的不做项

当前 `M0` 明确不做：

1. 不做多算法对照
2. 不做大规模 seed sweep
3. 不做 reflective replanning
4. 不做 policy memory
5. 不做训练型神经预测器
6. 不做 end-to-end LLM 直接布局生成
7. 不做 free-form heuristic/code generation

---

## 8. M0 研究包与当前实现的对应关系

### 8.1 已具备的执行基础

当前仓库已具备：

- `L1-L4` strict-real `NSGA-III` backbone 已跑通；
- `vop_maas` experimental mode 已可运行；
- `VOPG` / `VOPPolicyPack` / screening / fallback 已接入；
- `vop_maas` 已支持复用 `mass` 的 level profile 运行口径。

### 8.2 尚未完成但不阻碍 M0 冻结的项

以下尚未完全完成，但不阻碍 `M0` 作为“单算法冻结执行包”成立：

- release-grade `first_feasible` 审计字段尚未完全稳定写出；
- `vop_maas` 真实 COMSOL 对照尚未形成完整实验矩阵；
- benchmark 批量资产尚未重建。

这类问题影响的是“论文最终产出质量”，不影响当前先冻结一条强主线。

---

## 9. 对后续阶段的边界要求

后续若要扩展以下内容：

- `NSGA-II`
- `MOEA/D`
- 其他 level profile
- 多 seed campaign
- reflective replanning
- memory / template evolution

必须遵循：

1. 先保持 `M0` 不变；
2. 以新 report/ADR 记录扩展边界；
3. 在 paired setting 下单独证明收益；
4. 不能 retroactively 改写 `M0` 的原始冻结定义。

---

## 10. 最终冻结口径

截至 2026-03-07，`VOP-MaaS` 的 `M0` 可执行研究包冻结为：

- 执行档位：`L1-L4`
- 执行算法：`NSGA-III`
- 主 profile：`config/system/mass/level_profiles_l1_l4_real_strict.yaml`
- backbone：`mass`
- experimental overlay：`vop_maas`
- 当前唯一推荐叙事：先用已跑通的 `NSGA-III` 主线把研究线定住，再逐步补其他算法与增强模块

本冻结定义与以下文档共同构成当前研究基线：

- `docs/reports/R28_vop_maas_master_plan_20260307.md`
- `docs/adr/0007-vop-maas-verified-operator-policy-experimental-mode.md`
- `HANDOFF.md`
