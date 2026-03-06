# Mass System Config

Current active config files:

- `base.yaml`: serial mass baseline for current executable multiphysics capability
- `level_profiles_l1_l4.yaml`: proxy/offline L1-L4 level overrides
- `level_profiles_l1_l4_real_strict.yaml`: real COMSOL strict L1-L4 level overrides

Removed legacy config files:

- `s1_s4_gts_profiles.yaml`
- legacy benchmark-only profile files

Current workflow:

1. Choose `run/mass/run_L1.py` to `run/mass/run_L4.py`
2. Use `level_profiles_l1_l4.yaml` for proxy tuning
3. Use `level_profiles_l1_l4_real_strict.yaml` for real COMSOL verification
