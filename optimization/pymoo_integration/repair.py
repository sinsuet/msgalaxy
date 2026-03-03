"""
Custom repair operator for mild overlap removal.
"""

from __future__ import annotations

import traceback
from typing import Optional

import numpy as np

from .codec import DesignStateVectorCodec
from .constraints import pairwise_separation

try:
    from pymoo.core.repair import Repair
except Exception:  # pragma: no cover - only raised when pymoo is absent
    Repair = object  # type: ignore[misc, assignment]


class CentroidPushApartRepair(Repair):
    """
    Push slightly-overlapping components apart along centerline direction.

    This is intentionally conservative:
    - Only addresses mild overlaps.
    - Always clips results back to [xl, xu].
    """

    def __init__(
        self,
        codec: DesignStateVectorCodec,
        max_passes: int = 2,
        push_ratio: float = 0.55,
        jitter_eps: float = 1e-9,
        cg_limit_mm: Optional[float] = None,
        cg_nudge_ratio: float = 0.90,
    ) -> None:
        super().__init__()
        self.codec = codec
        self.max_passes = max(1, int(max_passes))
        self.push_ratio = float(push_ratio)
        self.jitter_eps = float(jitter_eps)
        self.cg_limit_mm = (
            max(float(cg_limit_mm), 0.0) if cg_limit_mm is not None else None
        )
        self.cg_nudge_ratio = float(max(cg_nudge_ratio, 0.0))

    def _apply_cg_recenter(
        self,
        state,
        centers: np.ndarray,
        half_sizes: np.ndarray,
    ) -> np.ndarray:
        """
        尝试通过整体平移降低 CG 偏移，不破坏组件相对布局。
        """
        if self.cg_limit_mm is None or self.cg_limit_mm <= 0.0:
            return centers

        masses = np.asarray(
            [max(float(comp.mass), self.jitter_eps) for comp in state.components],
            dtype=float,
        )
        total_mass = float(np.sum(masses))
        if total_mass <= self.jitter_eps or centers.shape[0] == 0:
            return centers

        if state.envelope.origin == "center":
            target_center = np.zeros(3, dtype=float)
        else:
            target_center = np.asarray(
                [
                    float(state.envelope.outer_size.x) * 0.5,
                    float(state.envelope.outer_size.y) * 0.5,
                    float(state.envelope.outer_size.z) * 0.5,
                ],
                dtype=float,
            )

        com = np.sum(centers * masses.reshape(-1, 1), axis=0) / total_mass
        offset_vec = com - target_center
        offset_norm = float(np.linalg.norm(offset_vec))
        if offset_norm <= self.cg_limit_mm + self.jitter_eps:
            return centers

        # 只移动超限部分，避免过度修复。
        exceed = max(offset_norm - self.cg_limit_mm, 0.0)
        desired_shift = -offset_vec * (exceed / max(offset_norm, self.jitter_eps)) * self.cg_nudge_ratio

        env_min, env_max = self.codec.envelope_bounds
        lower_centers = env_min.reshape(1, 3) + half_sizes
        upper_centers = env_max.reshape(1, 3) - half_sizes

        # 叠加决策变量边界，避免后续 clip 导致组件相对关系被破坏。
        comp_index = {comp.id: idx for idx, comp in enumerate(state.components)}
        axis_index = {"x": 0, "y": 1, "z": 2}
        for spec in self.codec.variable_specs:
            comp_i = comp_index.get(spec.component_id)
            axis_i = axis_index.get(spec.axis)
            if comp_i is None or axis_i is None:
                continue
            lower_centers[comp_i, axis_i] = max(
                float(lower_centers[comp_i, axis_i]),
                float(spec.lower_bound),
            )
            upper_centers[comp_i, axis_i] = min(
                float(upper_centers[comp_i, axis_i]),
                float(spec.upper_bound),
            )
        lower_centers = np.minimum(lower_centers, upper_centers)

        shift_low = np.max(lower_centers - centers, axis=0)
        shift_high = np.min(upper_centers - centers, axis=0)
        feasible_shift = np.clip(desired_shift, shift_low, shift_high)

        if float(np.linalg.norm(feasible_shift)) <= self.jitter_eps:
            return centers
        return centers + feasible_shift

    def _do(self, problem, X, **kwargs):  # noqa: D401 - pymoo signature
        repaired = np.asarray(X, dtype=float).copy()

        for idx in range(repaired.shape[0]):
            candidate = repaired[idx, :].copy()

            try:
                for _ in range(self.max_passes):
                    state = self.codec.decode(candidate)
                    centers, half_sizes = self.codec.geometry_arrays_from_state(state)
                    pair_sep, tri = pairwise_separation(centers, half_sizes)
                    if pair_sep.shape[0] == 0:
                        break

                    overlap_mask = np.all(pair_sep < 0.0, axis=1)
                    if not np.any(overlap_mask):
                        break

                    for pair_idx in np.where(overlap_mask)[0]:
                        i = int(tri[0][pair_idx])
                        j = int(tri[1][pair_idx])
                        sep = pair_sep[pair_idx]
                        penetration = float(np.min(-sep))
                        if penetration <= 0.0:
                            continue

                        vec = centers[j] - centers[i]
                        norm = float(np.linalg.norm(vec))
                        if norm <= self.jitter_eps:
                            vec = np.array([1.0, 0.0, 0.0], dtype=float)
                            norm = 1.0
                        direction = vec / norm

                        # Move each component half distance in opposite directions.
                        delta = 0.5 * penetration * self.push_ratio * direction
                        centers[i] -= delta
                        centers[j] += delta

                    # Write updated centers back to state then re-encode.
                    for comp_i, comp in enumerate(state.components):
                        comp.position.x = float(centers[comp_i, 0])
                        comp.position.y = float(centers[comp_i, 1])
                        comp.position.z = float(centers[comp_i, 2])

                    candidate = self.codec.clip(self.codec.encode(state))

                # 当几何冲突已基本消除后，追加一次 CG 回中平移修复。
                state = self.codec.decode(candidate)
                centers, half_sizes = self.codec.geometry_arrays_from_state(state)
                centers = self._apply_cg_recenter(state, centers, half_sizes)
                for comp_i, comp in enumerate(state.components):
                    comp.position.x = float(centers[comp_i, 0])
                    comp.position.y = float(centers[comp_i, 1])
                    comp.position.z = float(centers[comp_i, 2])
                candidate = self.codec.clip(self.codec.encode(state))

            except Exception:
                # Preserve original candidate and continue robustly.
                _ = traceback.format_exc()
                candidate = self.codec.clip(repaired[idx, :])

            repaired[idx, :] = self.codec.clip(candidate)

        return repaired
