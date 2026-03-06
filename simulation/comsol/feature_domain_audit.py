from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class ComsolFeatureDomainAuditMixin:
    """COMSOL feature/domain level audit for strict real-physics evidence."""

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    @staticmethod
    def _safe_tag_list(loader: Callable[[], Any]) -> List[str]:
        tags: List[str] = []
        try:
            raw = list(loader() or [])
        except Exception:
            raw = []
        seen: set[str] = set()
        for item in raw:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            tags.append(text)
        return tags

    def _safe_domain_count(self) -> int:
        if self.model is None:
            return 0
        try:
            return self._safe_int(self.model.java.geom("geom1").getNDomains(), 0)
        except Exception:
            try:
                return self._safe_int(self.model.java.geom().get("geom1").getNDomains(), 0)
            except Exception:
                return 0

    def _safe_boundary_count(self) -> int:
        if self.model is None:
            return 0
        try:
            return self._safe_int(self.model.java.geom("geom1").getNBoundaries(), 0)
        except Exception:
            return 0

    def _safe_feature_tags(self, physics_tag: str) -> List[str]:
        if self.model is None:
            return []
        physics_name = str(physics_tag or "").strip()
        if not physics_name:
            return []
        return self._safe_tag_list(
            lambda: self.model.java.physics(physics_name).feature().tags()
        )

    @staticmethod
    def _count_prefix(tags: List[str], prefix: str) -> int:
        needle = str(prefix or "").strip().lower()
        if not needle:
            return 0
        return int(
            sum(1 for item in list(tags or []) if str(item).strip().lower().startswith(needle))
        )

    @staticmethod
    def _estimate_expected_contact_pairs(design_state: Any) -> int:
        if design_state is None:
            return 0
        count = 0
        for comp in list(getattr(design_state, "components", []) or []):
            contacts = dict(getattr(comp, "thermal_contacts", {}) or {})
            count += int(len(contacts))
        return int(max(count, 0))

    def _build_comsol_feature_domain_audit(
        self,
        *,
        design_state: Any = None,
        heat_binding_report: Optional[Dict[str, Any]] = None,
        structural_runtime: Optional[Dict[str, Any]] = None,
        power_runtime: Optional[Dict[str, Any]] = None,
        coupled_runtime: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        model_available = bool(self.model is not None)
        expected_component_count = int(len(list(getattr(design_state, "components", []) or [])))
        geometry_domain_count = int(self._safe_domain_count())
        geometry_boundary_count = int(self._safe_boundary_count())

        physics_tags = self._safe_tag_list(
            lambda: self.model.java.physics().tags() if self.model is not None else []
        )
        study_tags = self._safe_tag_list(
            lambda: self.model.java.study().tags() if self.model is not None else []
        )
        dataset_tags = self._safe_tag_list(
            lambda: self.model.java.result().dataset().tags() if self.model is not None else []
        )
        selection_tags = self._safe_tag_list(
            lambda: self.model.java.selection().tags() if self.model is not None else []
        )

        ht_feature_tags = self._safe_feature_tags("ht")
        ec_feature_tags = self._safe_feature_tags("ec")
        solid_feature_tags = self._safe_feature_tags("solid")

        hs_count = int(self._count_prefix(ht_feature_tags, "hs_"))
        tc_count = int(self._count_prefix(ht_feature_tags, "tc_"))
        coating_count = int(self._count_prefix(selection_tags, "boxsel_coating_"))

        heat_report = dict(heat_binding_report or {})
        active_heat_components = int(self._safe_int(heat_report.get("active_components", 0), 0))
        assigned_heat_sources = int(self._safe_int(heat_report.get("assigned_count", 0), 0))
        expected_contacts = int(self._estimate_expected_contact_pairs(design_state))

        struct_rt = dict(structural_runtime or {})
        power_rt = dict(power_runtime or {})
        coupled_rt = dict(coupled_runtime or {})

        required_physics = ["ht"]
        if bool(self.enable_power_comsol_real):
            required_physics.append("ec")
        if bool(self.enable_structural_real):
            required_physics.append("solid")

        required_studies = ["std1"]
        if bool(self.enable_power_comsol_real):
            required_studies.append("std_power")
        if bool(self.enable_structural_real):
            required_studies.extend(["std_struct", "std_modal"])
        if bool(self.enable_coupled_multiphysics_real):
            required_studies.append("std_coupled")

        required_feature_tags: Dict[str, List[str]] = {"ht": ["tl_global", "temp1", "conv_stabilizer"]}
        if bool(self.enable_power_comsol_real):
            required_feature_tags["ec"] = ["ec_term", "ec_gnd"]
        if bool(self.enable_structural_real):
            required_feature_tags["solid"] = ["fix_all", "bndl1"]

        missing_physics = sorted(
            [name for name in required_physics if str(name) not in set(physics_tags)]
        )
        missing_studies = sorted(
            [name for name in required_studies if str(name) not in set(study_tags)]
        )
        missing_feature_tags: Dict[str, List[str]] = {}
        existing_feature_map = {
            "ht": set(ht_feature_tags),
            "ec": set(ec_feature_tags),
            "solid": set(solid_feature_tags),
        }
        for physics_name, expected_tags in required_feature_tags.items():
            existing = existing_feature_map.get(str(physics_name), set())
            missing = [tag for tag in list(expected_tags or []) if str(tag) not in existing]
            if missing:
                missing_feature_tags[str(physics_name)] = sorted(set(missing))

        checks = {
            "model_available": bool(model_available),
            "geometry_domain_count": bool(
                geometry_domain_count >= max(1, expected_component_count)
            ),
            "required_physics_present": len(missing_physics) == 0,
            "required_studies_present": len(missing_studies) == 0,
            "required_feature_tags_present": len(missing_feature_tags) == 0,
            "heat_source_binding_complete": bool(
                active_heat_components <= 0
                or assigned_heat_sources >= active_heat_components
            ),
            "heat_source_feature_count": bool(
                active_heat_components <= 0
                or hs_count >= max(assigned_heat_sources, active_heat_components)
            ),
            "thermal_contact_features_present": bool(
                expected_contacts <= 0
                or tc_count > 0
            ),
            "power_study_solved": bool(
                (not bool(self.enable_power_comsol_real))
                or bool(power_rt.get("stat_solved", False))
            ),
            "structural_studies_solved": bool(
                (not bool(self.enable_structural_real))
                or (
                    bool(struct_rt.get("stat_solved", False))
                    and bool(struct_rt.get("modal_solved", False))
                )
            ),
            "coupled_study_solved": bool(
                (not bool(self.enable_coupled_multiphysics_real))
                or bool(coupled_rt.get("stat_solved", False))
            ),
        }
        failed_checks = sorted([name for name, ok in checks.items() if not bool(ok)])

        return {
            "enabled": True,
            "model_available": bool(model_available),
            "passed": bool(model_available and len(failed_checks) == 0),
            "failed_checks": failed_checks,
            "checks": checks,
            "required": {
                "physics_tags": list(required_physics),
                "study_tags": list(required_studies),
                "feature_tags": required_feature_tags,
            },
            "missing": {
                "physics_tags": missing_physics,
                "study_tags": missing_studies,
                "feature_tags": missing_feature_tags,
            },
            "counts": {
                "expected_component_count": int(expected_component_count),
                "geometry_domain_count": int(geometry_domain_count),
                "geometry_boundary_count": int(geometry_boundary_count),
                "heat_source_feature_count": int(hs_count),
                "thermal_contact_feature_count": int(tc_count),
                "coating_selection_count": int(coating_count),
                "selection_tag_count": int(len(selection_tags)),
                "dataset_count": int(len(dataset_tags)),
                "active_heat_components": int(active_heat_components),
                "assigned_heat_sources": int(assigned_heat_sources),
                "expected_contact_pairs": int(expected_contacts),
            },
            "tags": {
                "physics_tags": list(physics_tags),
                "study_tags": list(study_tags),
                "dataset_tags": list(dataset_tags),
                "selection_tags_sample": list(selection_tags[:60]),
                "ht_feature_tags": list(ht_feature_tags),
                "ec_feature_tags": list(ec_feature_tags),
                "solid_feature_tags": list(solid_feature_tags),
            },
            "runtime": {
                "structural_runtime": dict(struct_rt),
                "power_runtime": dict(power_rt),
                "coupled_runtime": dict(coupled_rt),
            },
            "heat_binding_report": dict(heat_report),
        }
