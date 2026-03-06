# R21 L1-L4 涓夌畻娉曚笌 LLM 浼樻晥鎺ㄨ繘鏂规锛?0260306锛?
## 1. 鐩爣鑼冨洿

鏈柟妗堜粎瑕嗙洊褰撳墠宸插疄鐜拌兘鍔涳紙M2/M3锛夛紝涓嶅紩鍏?M4 绁炵粡妯″潡锛屼笉鍋氭渶缁堣鏂囬樁娈电殑澶ц妯℃秷铻嶃€?
鏈樁娈电洰鏍囷細
- G1锛氬湪褰撳墠鐗╃悊鍦轰笌绠楀瓙瀹炵幇涓嬶紝瀹屾垚 `L1-L4 脳 NSGA-II/NSGA-III/MOEAD` 鐨勫彲澶嶇幇璺戦€氾紱
- G2锛氬湪鍚岄绠楀悓绾︽潫鍚岀瀛愪笅锛屽疄鐜?`LLM intent` 鐩稿 deterministic baseline 鐨勫彲閲忓寲鏀硅繘锛?- G3锛氬鍏抽敭缁撴灉鎵ц online COMSOL strict-real 澶嶆牳锛岀‘淇濈粨璁轰笉鍙嶈浆銆?
## 2. 鑳藉姏杈圭晫锛堟湰闃舵锛?
绾冲叆鑳藉姏锛?- 鐗╃悊鍦猴細geometry + thermal + structural + power + mission锛堝閮?evaluator锛?- 绠楀瓙锛歚group_move/cg_recenter/hot_spread/swap/add_heatstrap/set_thermal_contact/add_bracket/stiffener_insert/bus_proximity_opt/fov_keepout_push`
- 闂ㄧ锛歚source_gate/operator_family_gate/operator_realization_gate`锛坰trict锛?- 浼樺寲鍣細`nsga2/nsga3/moead`

鎺掗櫎鑳藉姏锛?- M4锛坒easibility predictor / neural policy / neural scheduler锛?- 缁堢绾у叏閲?ablation

## 3. 鏍稿績鎸囨爣涓庨獙鏀跺彛寰?
涓绘寚鏍囷細
- `diagnosis_feasible_ratio`
- `strict_proxy_feasible_ratio`
- `best_cv_min`锛堝潎鍊?涓綅鏁帮級
- `first_feasible_eval`锛堝潎鍊?涓綅鏁帮級
- `comsol_calls_to_first_feasible`锛堝叧閿粍锛?
杈呭姪鎸囨爣锛?- dominant violation 鍒嗗竷
- `source/operator-family/operator-realization` strict 闃绘柇鐜?- `dset` 閿欒璁℃暟锛堝簲淇濇寔 0锛?
LLM 浼樻晥鍒ゅ畾锛堥樁娈垫€э級锛?- 鍦ㄨ嚦灏?3 seeds 涓嬶紝LLM 缁勭浉瀵?deterministic 缁勬弧瓒充互涓嬩箣涓€锛?  - feasible_ratio 鎻愬崌 >= 0.10锛?  - best_cv_min 鍧囧€间笅闄?>= 15%锛?  - first_feasible_eval 鍧囧€间笅闄?>= 15%銆?
## 4. 鎵ц闃舵

### Phase A锛氫笁绠楁硶 deterministic 鍩虹嚎鐭╅樀锛坰implified锛?鍛戒护锛?```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/benchmark_matrix.py --profiles operator_program --levels L1,L2,L3,L4 --algorithms nsga2,nsga3,moead --seeds 42,43,44 --backend simplified --thermal-evaluator-mode proxy --max-iterations 2 --pymoo-pop-size 24 --pymoo-n-gen 12 --intent-template v3_multiphysics --hard-constraint-coverage-mode strict --metric-registry-mode strict --experiment-tag l1_l4_algo3_det_baseline
```
杈撳嚭锛?- `matrix_runs.csv`
- `matrix_aggregate_profile_level.csv`
- `matrix_report.md`
- `matrix_strict_gate.json`

### Phase B锛歀LM 瀵圭収鐭╅樀锛坰implified锛?鍛戒护锛?```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 conda run -n msgalaxy python run/mass/benchmark_matrix.py --profiles operator_program --levels L1,L2,L3,L4 --algorithms nsga2,nsga3,moead --seeds 42,43,44 --backend simplified --thermal-evaluator-mode proxy --max-iterations 2 --pymoo-pop-size 24 --pymoo-n-gen 12 --intent-template v3_multiphysics --hard-constraint-coverage-mode strict --metric-registry-mode strict --use-llm-intent --experiment-tag l1_l4_algo3_llm_ab
```
杈撳嚭锛?- 涓?Phase A 鍚岀粨鏋勪骇鐗╋紝渚夸簬閫愬垪 A/B 瀵规瘮銆?
### Phase C锛氬叧閿粍 online COMSOL strict-real 澶嶆牳
绛栫暐锛?- 浠?Phase A/B 涓寫閫夋瘡绠楁硶姣忕瓑绾т唬琛ㄧ粍锛堜紭鍏?best 鍜?borderline 鍙缁勶級锛?- 鍥哄畾 `--backend comsol --thermal-evaluator-mode online_comsol` 鎵ц澶嶆牳锛?- 寮€鍚?strict gate 骞跺璁?`final_mph_path` 涓?`dset` 璁℃暟銆?
## 5. LLM 绛栫暐浼樺寲闂幆锛堜粎鍋氫笌 G2 鐩存帴鐩稿叧鐨勬渶灏忔敼閫狅級

浼樺厛浼樺寲椤癸細
- variable mapping 绋冲畾鎬э紙鍑忓皯榛樿 xyz 鍥為€€锛?- metric mapping 涓?hard constraint 瀵归綈
- operator_program 瑙﹀彂璐ㄩ噺锛堟寜 dominant violation 鏇村尮閰?family锛?- mission keepout 鍒嗘敮鍙鍖栵紙淇濇寔 repair-before-block锛?
姣忔鏀归€犲悗鏈€灏忛獙璇侊細
1. `tests/test_operator_program.py tests/test_operator_program_core.py tests/test_maas_pipeline.py tests/test_comsol_driver_p0.py`
2. `run/mass/run_T2_real2.py --require-strict-pass`
3. 閲嶆柊璺戝彈褰卞搷绛夌骇/绠楁硶鐨?A/B 瀛愮煩闃?
## 6. 椋庨櫓涓庨槻鍥炲綊

- 椋庨櫓1锛歂SGA-III/MOEAD 鍦?strict 鍙ｅ緞涓嬪彲琛岀巼浣?  - 澶勭疆锛氭寜绛夌骇鍗曠嫭璋?`pop_size/n_gen/mass_max_attempts/mcts_budget`
- 椋庨櫓2锛歀LM 寮曞叆鍣０瀵艰嚧鍙鐜囦笅闄?  - 澶勭疆锛氬 LLM 杈撳嚭鍋?contract 瑁佸壀涓?fallback 瀹¤
- 椋庨櫓3锛歰nline COMSOL 鎴愭湰杩囬珮
  - 澶勭疆锛氫粎瀵瑰叧閿粍鍋氬鏍革紝涓嶅鍏ㄧ煩闃电洿鎺ヤ笂 COMSOL
- 椋庨櫓4锛歞set 閿欒鍥炲綊
  - 澶勭疆锛氭瘡娆?COMSOL 澶嶆牳鍚庡繀鍋氭棩蹇楄鏁板璁★紙搴斾负 0锛?
## 7. 闃舵瀹屾垚瀹氫箟锛圖oD锛?
- D1锛歅hase A/B 鍏ㄩ儴瀹屾垚骞朵骇鍑哄彲瀵规瘮鎶ュ憡锛?- D2锛氳嚦灏?1 杞?LLM 绛栫暐浼樺寲鍚庯紝杈惧埌闃舵鎬?LLM 浼樻晥闃堝€硷紱
- D3锛氬叧閿粍 online COMSOL strict-real 澶嶆牳閫氳繃锛屼笖鏃?`dset` 閿欒椋庢毚锛?- D4锛歚HANDOFF.md`銆乣`銆乣README.md` 涓庢湰鏂规淇濇寔涓€鑷淬€?



