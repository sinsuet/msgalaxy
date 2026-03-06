"""
Physics-based DC power network solver for in-loop evaluation.

This module solves a resistive nodal network (Kirchhoff current law) from
layout geometry and component power loads. It is deterministic and designed
as a higher-fidelity replacement for purely heuristic power proxies.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(default)
    if not np.isfinite(parsed):
        return float(default)
    return float(parsed)


def _component_arrays(
    design_state,
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
    powers: List[float] = []
    centers: List[List[float]] = []
    categories: List[str] = []
    ids: List[str] = []

    for comp in list(getattr(design_state, "components", []) or []):
        ids.append(str(getattr(comp, "id", "") or ""))
        categories.append(str(getattr(comp, "category", "") or "").strip().lower())
        powers.append(max(_safe_float(getattr(comp, "power", 0.0)), 0.0))
        centers.append(
            [
                _safe_float(getattr(getattr(comp, "position", None), "x", 0.0)),
                _safe_float(getattr(getattr(comp, "position", None), "y", 0.0)),
                _safe_float(getattr(getattr(comp, "position", None), "z", 0.0)),
            ]
        )

    return (
        np.asarray(powers, dtype=float),
        np.asarray(centers, dtype=float),
        categories,
        ids,
    )


def _pick_source_indices(categories: List[str], ids: List[str], powers: np.ndarray) -> List[int]:
    source_tokens_cat = ("power",)
    source_tokens_id = ("battery", "pdu")
    indices: List[int] = []
    for idx, (cat, comp_id) in enumerate(zip(categories, ids)):
        comp_id_lower = str(comp_id).strip().lower()
        if any(token in cat for token in source_tokens_cat):
            indices.append(idx)
            continue
        if any(token in comp_id_lower for token in source_tokens_id):
            indices.append(idx)
            continue
    if not indices and powers.size > 0:
        indices = [int(np.argmax(powers))]
    return sorted(set(indices))


def _build_edges(
    centers: np.ndarray,
    source_indices: List[int],
    k_neighbors: int,
) -> List[Tuple[int, int, float]]:
    n = int(centers.shape[0])
    if n <= 1:
        return []

    edges: Dict[Tuple[int, int], float] = {}

    for i in range(n):
        deltas = centers - centers[i]
        dists = np.linalg.norm(deltas, axis=1)
        order = np.argsort(dists)
        picked = 0
        for j in order:
            j = int(j)
            if j == i:
                continue
            key = (min(i, j), max(i, j))
            edges[key] = float(max(dists[j], 1e-3))
            picked += 1
            if picked >= max(1, int(k_neighbors)):
                break

    for i in range(n):
        if i in source_indices:
            continue
        src_dist = []
        for s in source_indices:
            d = float(np.linalg.norm(centers[i] - centers[s]))
            src_dist.append((d, int(s)))
        if not src_dist:
            continue
        src_dist.sort(key=lambda item: item[0])
        nearest_source = int(src_dist[0][1])
        key = (min(i, nearest_source), max(i, nearest_source))
        edges[key] = float(max(src_dist[0][0], 1e-3))

    return [(i, j, dist) for (i, j), dist in edges.items()]


def solve_dc_power_network_metrics(
    design_state,
    *,
    max_power_w: float = 500.0,
    bus_voltage_v: float = 28.0,
    cable_resistance_ohm_per_m: float = 0.0085,
    k_neighbors: int = 3,
    congestion_alpha: float = 0.12,
) -> Dict[str, float]:
    """
    Solve a DC nodal network and return power metrics.

    Returns keys:
    - total_power, peak_power, power_margin, voltage_drop
    - avg_harness_path_mm, current_a, power_budget_used_w
    - min_node_voltage_v, solver_residual, source_count
    """

    powers, centers, categories, ids = _component_arrays(design_state)
    n = int(powers.size)
    total_power = float(np.sum(np.clip(powers, 0.0, None)))
    if n == 0:
        return {
            "total_power": 0.0,
            "peak_power": 0.0,
            "power_margin": 100.0,
            "voltage_drop": 0.0,
            "avg_harness_path_mm": 0.0,
            "current_a": 0.0,
            "power_budget_used_w": float(max_power_w),
            "min_node_voltage_v": float(bus_voltage_v),
            "solver_residual": 0.0,
            "source_count": 0.0,
        }

    source_indices = _pick_source_indices(categories, ids, powers)
    if not source_indices:
        source_indices = [0]

    edges = _build_edges(
        centers=centers,
        source_indices=source_indices,
        k_neighbors=max(1, int(k_neighbors)),
    )

    peak_factor = float(np.clip(1.08 + 0.08 * (float(np.max(powers)) / max(float(np.mean(powers)), 1e-6) - 1.0), 1.05, 1.35))
    peak_power = float(total_power * peak_factor)
    load_current = np.asarray(
        [float(max(p, 0.0) / max(float(bus_voltage_v), 1.0)) for p in powers],
        dtype=float,
    )

    if not edges:
        budget = float(max(max_power_w, peak_power * 1.15))
        power_margin = float((budget - peak_power) / max(budget, 1e-6) * 100.0)
        return {
            "total_power": float(total_power),
            "peak_power": float(peak_power),
            "power_margin": float(power_margin),
            "voltage_drop": 0.0,
            "avg_harness_path_mm": 0.0,
            "current_a": float(peak_power / max(float(bus_voltage_v), 1.0)),
            "power_budget_used_w": float(budget),
            "min_node_voltage_v": float(bus_voltage_v),
            "solver_residual": 0.0,
            "source_count": float(len(source_indices)),
        }

    adjacency: Dict[int, List[Tuple[int, float]]] = {i: [] for i in range(n)}
    weighted_path_sum = 0.0
    power_sum = float(np.sum(np.clip(powers, 0.0, None)))
    for i, j, length_mm in edges:
        pair_power = float((powers[i] + powers[j]) * 0.5)
        congestion = 1.0 + float(congestion_alpha) * float(pair_power / max(power_sum, 1e-6))
        resistance = float(max(cable_resistance_ohm_per_m, 1e-6) * (length_mm / 1000.0) * congestion)
        resistance = max(resistance, 1e-6)
        adjacency[i].append((j, resistance))
        adjacency[j].append((i, resistance))
        weighted_path_sum += float(length_mm) * float(pair_power)

    source_set = set(int(i) for i in source_indices)
    unknown_indices = [idx for idx in range(n) if idx not in source_set]
    voltage = np.full((n,), float(bus_voltage_v), dtype=float)

    solver_residual = 0.0
    if unknown_indices:
        m = len(unknown_indices)
        idx_map = {node: row for row, node in enumerate(unknown_indices)}
        a = np.zeros((m, m), dtype=float)
        b = np.zeros((m,), dtype=float)

        for node in unknown_indices:
            row = idx_map[node]
            for nbr, resistance in adjacency.get(node, []):
                conductance = 1.0 / max(float(resistance), 1e-9)
                a[row, row] += conductance
                if nbr in idx_map:
                    a[row, idx_map[nbr]] -= conductance
                else:
                    b[row] += conductance * float(bus_voltage_v)
            b[row] -= float(load_current[node])

        try:
            solved = np.linalg.solve(a, b)
        except Exception:
            solved, *_ = np.linalg.lstsq(a, b, rcond=None)

        for node in unknown_indices:
            row = idx_map[node]
            voltage[node] = float(solved[row])

        voltage = np.clip(voltage, 0.0, float(bus_voltage_v))
        residual = a.dot(np.asarray([voltage[node] for node in unknown_indices], dtype=float)) - b
        solver_residual = float(np.linalg.norm(residual, ord=2))

    min_node_voltage = float(np.min(voltage)) if voltage.size > 0 else float(bus_voltage_v)
    voltage_drop = float(max(float(bus_voltage_v) - min_node_voltage, 0.0))
    avg_path_mm = float(weighted_path_sum / max(power_sum, 1e-6))

    budget = float(_safe_float(max_power_w, 0.0))
    if budget <= 0.0:
        budget = float(peak_power * 1.15)
    power_margin = float((budget - peak_power) / max(budget, 1e-6) * 100.0)

    return {
        "total_power": float(total_power),
        "peak_power": float(peak_power),
        "power_margin": float(power_margin),
        "voltage_drop": float(voltage_drop),
        "avg_harness_path_mm": float(avg_path_mm),
        "current_a": float(peak_power / max(float(bus_voltage_v), 1.0)),
        "power_budget_used_w": float(budget),
        "min_node_voltage_v": float(min_node_voltage),
        "solver_residual": float(solver_residual),
        "source_count": float(len(source_indices)),
    }

