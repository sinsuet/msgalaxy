# R30 vop_maas real-LLM 主链打通与回归（2026-03-08）

## 1. 本轮目的

围绕 `vop_maas` 主链，优先收口四件事：

1. real LLM primary round 真正可用；
2. `PolicyPack -> mass` 注入不再停留在 search-space / operator seed，metric 级 focus 也要落到 executable intent；
3. reflective second-pass 能消费首轮 `policy_effect_summary` 与 feedback-aware fidelity recommendation；
4. `L1-L4` 有稳定、可重复的 targeted regression。

## 2. 本轮落地

- `optimization/meta_reasoner.py`
  - 为 `generate_policy_program(...)` 增加真实 DashScope/Qwen 响应容错：
    - 直接 JSON；
    - fenced JSON；
    - list-block message content。
  - 对缺省 `operator_candidates / policy_source / candidate_id / program_id` 做 bounded autofill。
  - 修补后的 payload 显式标记 `policy_source=llm_api_autofill`。

- `workflow/modes/mass/pipeline_service.py`
  - `constraint_focus` 注入补齐：
    - `first_modal_freq`
    - `safety_factor`
    - `power_margin`
  - 使 `structural/power` focus 能真正进入 intent objective，而不是只留下 runtime hint。

- `run/vop_maas/common.py`
  - real-LLM 运行入口优先读取 `DASHSCOPE_API_KEY`，再回退到 `OPENAI_API_KEY`。

- `tests/test_vop_maas_mode.py`
  - 新增 real LLM policy autofill 回归；
  - 新增 `PolicyPack -> mass` metric 注入回归；
  - 新增 `L1-L4` targeted regression，覆盖：
    - `MetaReasoner -> PolicyProgrammer -> VOPPolicyProgramService`
    - primary round
    - reflective second-pass
    - feedback-aware fidelity prompt 注入
    - delegated `mass` rerun。

## 3. 当前边界

- 本轮打通的是 **主链控制流与 schema/注入稳定性**，不是新增完整的 release-grade real COMSOL 实验矩阵。
- feedback-aware fidelity 仍遵守既有边界：
  - 只在逻辑上视为 `comsol` backend 时生成 bounded recommendation；
  - 不把 proxy/simplified run 误报成真实 high-fidelity multiphysics evidence。

## 4. 建议验证

```bash
pytest tests/test_vop_maas_mode.py -q
```

如需手工跑入口：

```bash
python run/vop_maas/run_L1.py --backend simplified --mock-policy --deterministic-intent --max-iterations 1
```

如需 real-LLM primary round：

- 设置 `DASHSCOPE_API_KEY`；
- 若未设置，则回退查找 `OPENAI_API_KEY`。
