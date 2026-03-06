## MsGalaxy Rules

Scope: execution, documentation governance, repository hygiene, and scientific rigor.

### A. Execution and Editing Safety

1. **Path + Cross-Platform Rule (路径与跨平台规则)**:
   - Always use explicit paths.
   - Prefer workspace-relative paths with forward slashes (`/`).
   - Do not mix `E:\\` and `/e/` style in the same command flow.

2. **Safe Write Rule (安全写入规则)**:
   - Before writing, ensure parent directories exist.
   - If a target file is likely locked by COMSOL/Python, stop and notify before writing.

3. **Strict Edit + Fallback Rule (严格编辑与降级规则)**:
   - Read exact source first, then edit with exact whitespace/context.
   - If edit tooling fails, immediately fallback to script-based rewrite or full block replacement.

4. **Python Runtime Rule (Python运行规则)**:
   - All Python/test commands MUST use:
     - `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy ...`
   - Do not use system `python`/`pytest` directly.
   - For Windows-facing entry scripts (`run/`, `tests/`), ensure UTF-8 stdout/stderr compatibility handling is present when needed.

5. **Model + Secret Rule (模型与密钥规则)**:
   - Default model is `qwen3-max`; do not switch without explicit request.
   - Never request or hardcode API keys; load via env/config.

6. **Root-Cause Rule (根因优先规则)**:
   - No "temporary bypass/mocked pass" for core scientific issues.
   - Fix root cause with physically and architecturally defensible changes.

### B. State Sync and Documentation Governance

7. **Major-Change Sync Gate (重大变更文档同步闸门)**:
   - For major architecture/function changes, update in the same change set:
     - `HANDOFF.md`
     - `README*` (project entry README)
     - relevant docs under `docs/` (when applicable)
     - `AGENTS.md` (if agent behavior/boundary changed)
   - "Major change" includes: optimizer/control-flow changes, constraint semantics changes, new benchmark protocol, new runtime mode, or COMSOL evaluation contract changes.

8. **Sync Order + Consistency Rule (同步顺序与一致性规则)**:
   - Update order MUST be:
     1. `HANDOFF.md`
     2. `README*`
     3. relevant docs under `docs/`
     4. `AGENTS.md` (when guidance changes)
   - Do not leave cross-file contradictions (especially implemented vs planned status).

9. **Stale-State Cleanup Rule (过时状态清理规则)**:
   - "Current status" sections must only contain active truths.
   - Superseded statements must be moved to history/archive sections with date and reason.
   - Do not keep outdated roadmap/task-state text in active status sections.
   - Deprecated but valuable docs should be moved to `docs/archive/` instead of silent deletion.

10. **Important Doc Naming Rule (重要文档命名规则)**:
   - Formal documentation stays under `docs/`, with fixed subfolders:
     - `docs/reports/` for governance/report documents
     - `docs/adr/` for architecture decision records
     - `docs/archive/` for deprecated historical documents
   - New important governance/report docs MUST be placed under `docs/reports/`.
   - Naming format (new files): `docs/reports/R<rule_id>_<topic>_<YYYYMMDD>.md`
   - Example: `docs/reports/R07_v3_hard_constraint_rollout_20260304.md`
   - Existing historical files are not forced to rename.

11. **Architecture Decision Record Rule (架构决策记录规则)**:
   - Major architecture decisions MUST create/update ADR files under `docs/adr/`.
   - ADR naming: `docs/adr/NNNN-<kebab-topic>.md` (zero-padded sequence).
   - ADR must include: `status` (`proposed/accepted/superseded`), context, decision, consequences.

12. **Version + Change Log Rule (版本与变更日志规则)**:
   - `HANDOFF.md` version and timestamp must be updated on major changes.
   - Release-level changes must include concise Added/Changed/Fixed summary in project docs.

### C. Repository Hygiene and Artifact Lifecycle

13. **Temporary File Cleanup Rule (临时文件清理规则)**:
   - Temporary scripts, ad-hoc test files, and one-off intermediate outputs MUST be removed after validation.
   - Do not leave debug-only files in source directories after task completion.

14. **Generated Artifact Placement Rule (生成产物归位规则)**:
   - Human-authored source/docs stay in source/docs paths.
   - Machine-generated outputs must go to designated run/output locations (e.g., `experiments/`, benchmark output dirs), not mixed into source code folders.

15. **`.gitignore` Hygiene Rule (`.gitignore`整洁规则)**:
   - When new temporary/output classes appear, update `.gitignore` promptly.
   - Large/binary/transient files must not be committed unless explicitly required as release artifacts.

16. **Immutable Experiment Artifact Rule (实验产物不可篡改规则)**:
   - Do not manually edit generated run artifacts (`summary.json`, event logs, tables) to "fix" conclusions.
   - Corrections require a new run artifact with a clear delta note.

### D. Scientific Rigor and Anti-Hallucination

17. **Evidence + Provenance Rule (证据与溯源规则)**:
   - Any claim must map to concrete artifacts with explicit `profile/algorithm/seed/backend`.
   - If not yet validated, label as hypothesis; do not present as confirmed.

18. **Reproducibility Bundle Rule (可复现包规则)**:
   - Conclusions must include executable command, env prefix, config/BOM snapshot, seed list, and key runtime knobs.
   - If COMSOL/license/hardware constraints exist, state them explicitly.

19. **Statistical Gate Rule (统计闸门规则)**:
   - No publication-level conclusion from a single-seed run.
   - Comparative claims require `seed >= 3` plus distribution stats.
   - Major claims require at least one independent rerun before final acceptance.

20. **Fair Comparison Rule (公平对照规则)**:
   - Improvement claims require baseline + ablation under matched budgets/constraints/fidelity.
   - Baseline must stay "algorithm-only" when used as control.

21. **MaaS Contract + Constraint Governance Rule (MaaS契约与约束治理规则)**:
   - In `mass`, do not directly output final coordinates; solutions must come from executable pymoo search.
   - Enforce `g(x) <= 0` semantics with explicit `xl/xu/F/G`.
   - Benchmark/release runs are invalid if mandatory hard constraints are not active (`collision/clearance/boundary/thermal/cg_limit`).
   - Unknown/unimplemented hard-constraint metric keys must fail-fast.

22. **LLM/OP/COMSOL Validation Rule (LLM/算子/COMSOL验收规则)**:
   - LLM intent/operator outputs are proposals; they must pass executable chain checks before use.
   - OP claims must include action-level and action-family attribution; identity-heavy branches must be explicitly disclosed.
   - "Real COMSOL validated" claims require audit status disclosure, valid `final_mph_path`, and efficiency metrics (`first_feasible_eval`, `comsol_calls_to_first_feasible`).
   - Audit-off runs are diagnostic-only, not release-grade evidence.

23. **Negative Result + Anomaly Governance Rule (负结果与异常治理规则)**:
   - `no_feasible`, regressions, and failed attempts are first-class evidence and must be retained in analysis outputs.
   - Outlier/anomaly filtering rules must be explicit, versioned, and reproducible.
   - Reports must include dominant violation/failure-reason breakdown for infeasible outcomes.

### E. Architecture Clarity and Test Folder Governance

24. **Canonical Mode Naming Freeze Rule (模式命名冻结规则)**:
   - Active runtime mode and stack names are strictly `agent_loop` and `mass`.
   - Legacy names (`pymoo_maas`, `v2`, `maas_v2`, etc.) must not remain in active configs, run entrypoints, or runtime logs.
   - Historical terms are allowed only in archived docs with explicit "historical" labeling.

25. **No Compatibility Shadow Rule (禁兼容影子规则)**:
   - After an approved mode/stack cutover, do not keep dual-path compatibility wrappers for old names.
   - Remove superseded run scripts/templates/configs in the same change set.
   - Prefer fail-fast contract errors over silent fallback or implicit aliasing.

26. **Stack-Mode-Config Binding Rule (栈-模式-配置强绑定规则)**:
   - L1-L4 run paths must keep strict stack binding: `agent_loop -> agent_loop`, `mass -> mass`.
   - BOM and base-config must stay inside the same stack namespace (`config/bom/<stack>`, `config/system/<stack>`).
   - Cross-stack wiring is invalid and must fail immediately.

27. **Test Folder Unification Rule (测试目录统一规则)**:
   - All maintained test code and manual test scripts must live under `tests/`.
   - Root-level `scripts/` and `workspace/` must not be used as canonical test directories.
   - Manual scripts under `tests/` must avoid `test_*.py` naming to prevent accidental pytest collection.

28. **Runtime Artifact Placement Rule (运行产物归位规则)**:
   - Default runtime-generated files must write to designated artifact paths (`experiments/` or `tests/manual/artifacts/` for manual scripts).
   - Experiment-scoped logs MUST be written under the corresponding `experiments/<run>/` directory; root `logs/` is reserved for long-lived global service logs only.
   - Do not hardcode root `workspace/` as a default output directory in active runtime code.

29. **Rename Completion Gate Rule (命名迁移完成闸门规则)**:
   - Namespace refactors must include repository-wide token checks (`rg`) to ensure old tokens are cleared from active paths.
   - Required check scope includes: `run/`, `workflow/`, `optimization/`, `config/`, `tests/`, and top-level docs.
   - Any leftover old token in active code/config blocks completion.

30. **Benchmark Artifact Naming Rule (Benchmark产物命名规则)**:
   - Benchmark artifact directories under `benchmarks/` MUST use short fixed-slot names:
     - `bm_<stack>_<scope>_<algo>_<intent>_<eval>[_sNN][_tag]`
   - Token set is fixed:
     - `stack`: `m` (`mass`) or `a` (`agent_loop`)
     - `scope`: `l1`, `l2`, `l3`, `l4`, `l1-4`
     - `algo`: `n2`, `n3`, `md`, `mix`
     - `intent`: `det`, `llm`
     - `eval`: `px`, `real`
     - `sNN`: optional single-seed marker such as `s42`
     - `tag`: optional short note, max 8 chars
   - Do not use verbose filler tokens such as `benchmark`, `baseline`, `single`, `override`, `verify`, `postopt`, `forcecov`, `fixmap2` in directory names.
   - Directory basename SHOULD stay within 32 characters after the `bm_` prefix chain; detailed rationale goes into `summary.json` / manifest metadata, not the folder name.

31. **Run Entry + Runtime Artifact Naming Rule (Run入口与运行产物命名规则)**:
   - User-facing run entrypoints are standardized as:
     - `run/run_scenario.py`
     - `run/<stack>/run_L1.py` to `run/<stack>/run_L4.py`
   - Future stack-local helper scripts must use short type prefixes and must not repeat stack names in the basename:
     - `bm_<scope>.py` for benchmark runners
     - `tool_<topic>.py` for utilities
     - `audit_<topic>.py` for validation/audit helpers
   - Do not encode algorithm, backend, seed, date, or temporary fix labels into script filenames; those belong to CLI flags or runtime metadata.
   - Runtime single-run directories under `experiments/` SHOULD use:
     - `experiments/<YYYYMMDD>/<HHMM>_<stack>_<level>_<algo>_<intent>_<eval>[_tag]`
   - Runtime artifact basenames SHOULD stay concise and sortable; uncontrolled suffix chains are forbidden.

### F. New Mode Expansion Governance

32. **Mode Lifecycle State Rule (模式生命周期状态规则)**:
   - Every new mode (e.g., `vop_mass`) must declare lifecycle state: `proposed | experimental | stable | deprecated`.
   - Only `stable` modes can be exposed in default L1-L4 stack registry.
   - `experimental` modes must be opt-in and clearly marked in logs/reports.

33. **Mode Namespace Isolation Rule (模式命名空间隔离规则)**:
   - New mode code must be placed in isolated namespaces:
     - `workflow/modes/<mode>/`
     - `optimization/modes/<mode>/`
     - `run/<mode>/`
     - `config/system/<mode>/` and `config/bom/<mode>/` (when applicable)
   - Do not implement new-mode business logic directly in shared monolith files (e.g., avoid re-bloating `workflow/orchestrator.py`).

34. **Mode Entry Contract Rule (模式入口契约规则)**:
   - New mode must register through a single stack/mode contract path (`run/stack_contract.py` + scenario registry).
   - No ad-hoc entry scripts outside the contracted run routing.
   - New stack must fail-fast on mismatched mode/BOM/base-config wiring.

35. **Experimental Switch Guard Rule (实验开关防护规则)**:
   - Experimental mode activation must require explicit flag/config opt-in.
   - Default runtime behavior must remain unchanged when experimental mode is disabled.
   - Add explicit runtime log banner for experimental mode activation.

36. **Mode DoD Gate Rule (模式完成定义闸门规则)**:
   - A new mode cannot be promoted beyond `experimental` unless all are satisfied:
     - deterministic smoke run command exists and passes,
     - mode-specific tests exist under `tests/` and pass,
     - observability artifacts (`summary/events/tables`) are emitted with mode tag,
     - docs are synchronized (`HANDOFF -> README -> AGENTS if needed`).

37. **No Shared-File Ambiguity Rule (共享文件去歧义规则)**:
   - Shared modules may expose contracts/facades only; mode-specific policy logic must live in mode folders.
   - If a shared file grows due to mode-specific branches, split it before merge.
   - PRs introducing `if mode == ...` chains in shared modules must include refactor rationale and extraction plan.

38. **Mode Sunset Cleanup Rule (模式退役清理规则)**:
   - When replacing or retiring a mode, remove obsolete run/config/templates/tests in the same change set.
   - Keep only archived documentation for historical reference; no active runtime alias should point to retired mode names.

### G. COMSOL Implementation and Debug Governance

39. **COMSOL Official-Doc Search Rule (COMSOL官方文档检索规则)**:
   - Any implementation change involving COMSOL operators, physics features, studies, solvers, material/BC setup, or multiphysics coupling MUST include online search against official COMSOL documentation before coding.
   - Any COMSOL runtime error investigation MUST include online troubleshooting with official documentation (and official support/knowledge-base pages when relevant) before applying fixes.
   - Change notes and validation summaries MUST record the consulted references (URLs), and clearly distinguish documented facts from local inferences.

### H. Shell Deletion Safety

40. **Safe Deletion Command Rule (安全删除命令规则)**:
   - For cleanup of known empty directories or single files, prefer the least-destructive explicit command first:
     - file: `Remove-Item -LiteralPath <path> -Force`
     - empty directory: `Remove-Item -LiteralPath <path> -Force`
   - Do **not** use `Remove-Item -Recurse -Force` by default for routine cleanup when the target is expected to be empty or narrowly scoped.
   - Do **not** chain deletion commands together with read-only checks/search/status commands in one compound shell line; run deletion as a separate command step.
   - Always use `-LiteralPath` (or equivalently explicit non-glob path handling) for deletion commands to avoid wildcard/path-expansion surprises on Windows.
   - Before recursive deletion, first verify the target contents in a separate command, and only recurse when the target is confirmed to be intentionally removable.
   - When working through Codex/agent tooling, prefer “inspect -> delete -> verify” as three separate steps, because recursive force-delete commands are more likely to trigger safety-policy interception even when local filesystem permissions are sufficient.

### I. Windows Conda Python Invocation

41. **Windows Conda Inline Python Rule (Windows 下 conda 内联 Python 调用规则)**:
   - Keep the mandatory runtime prefix from Rule 4: `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy ...`.
   - On Windows PowerShell, do **not** rely on piping a here-string or other stdin content into `conda run -n msgalaxy python -` for multi-line scripts; this path is not reliable for Codex/terminal automation and may drop stdin or fall into interactive REPL behavior.
   - Preferred order for Python snippets under Windows:
     - short one-liners / compact probes: `conda run -n msgalaxy python -c "..."`
     - multi-line analysis / AST scan / structured output: write a temporary `.py` file first, then run `conda run -n msgalaxy python <temp_script.py>`
   - Do **not** treat `conda run --no-capture-output` as a fix for stdin-piped `python -`; it may still behave interactively instead of executing the intended script.
   - Temporary helper scripts for this purpose should be placed in OS temp or another explicit temporary path, not mixed into repository source directories, and cleanup should be executed as a separate command step.
