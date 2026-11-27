#!/usr/bin/env python3
"""Fast Back-of-the-Napkin CAD Math & Structural Pre-Check Toolkit (Zero Dependencies).

Eliminates hand-rolled trig, unit-vector bugs, and wasted 3D FEA runs by answering
basic geometry, kinematics, stack-up, and structural sizing questions in <5 ms.

Quick Start (Python):
    import os, sys
    sys.path.insert(0, os.path.expanduser("~/.hermes/profiles/solidcraft/tools"))
    import cad_math as cm

    # 1-second structural check BEFORE meshing 3D FEA:
    cm.beam_check(length_mm=450, force_n=1200, material="6061-T6",
                  shape="tube", od_mm=38, wall_mm=2.0, support="cantilever")
    cm.euler_buckling(length_mm=500, force_n=2500, material="6061-T6",
                      shape="tube", od_mm=31.8, wall_mm=1.6, k_factor=1.0)
    cm.bolt_shear_check(bolt_diameter_mm=12, force_n=4000, num_bolts=1,
                        shear_planes=2, material="7075-T6")
    cm.belt_drive(pitch_mm=8.0, z_front=34, z_rear=21, target_center_dist_mm=442.0)
    cm.stackup_check({"hub": (-74, 74), "belt": (51, 59), "stay_R": (66.5, 86.5)})

Quick Start (CLI):
    python3 ~/.hermes/profiles/solidcraft/tools/cad_math.py beam \\
      --length 450 --force 1200 --material 6061-T6 --shape tube --od 38 --wall 2.0
    python3 ~/.hermes/profiles/solidcraft/tools/cad_math.py buckling \\
      --length 500 --force 2500 --material 6061-T6 --shape tube --od 31.8 --wall 1.6
    python3 ~/.hermes/profiles/solidcraft/tools/cad_math.py bolt \\
      --dia 12 --force 4000 --planes 2 --material 7075-T6
    python3 ~/.hermes/profiles/solidcraft/tools/cad_math.py torsion \\
      --torque-nm 120 --length 400 --material 6061-T6 --shape tube --od 31.8 --wall 2.0
    python3 ~/.hermes/profiles/solidcraft/tools/cad_math.py belt \\
      --pitch 8 --zf 34 --zr 21 --center-dist 442
"""

from __future__ import annotations

import argparse
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Matches solidcraft_fea.py MATERIALS + common fastener grades
# E in MPa (N/mm^2), nu dimensionless, yield_mpa in MPa
MATERIALS: Dict[str, Dict[str, float]] = {
    "6061-T6": {"E": 68900.0, "nu": 0.33, "yield_mpa": 276.0},
    "7075-T6": {"E": 71700.0, "nu": 0.33, "yield_mpa": 503.0},
    "4130-chromoly": {"E": 205000.0, "nu": 0.29, "yield_mpa": 460.0},
    "304-stainless": {"E": 193000.0, "nu": 0.29, "yield_mpa": 215.0},
    "Ti-6Al-4V": {"E": 113800.0, "nu": 0.34, "yield_mpa": 880.0},
    "PLA": {"E": 3500.0, "nu": 0.36, "yield_mpa": 50.0},
    "PETG": {"E": 2100.0, "nu": 0.38, "yield_mpa": 45.0},
    "ABS": {"E": 2300.0, "nu": 0.35, "yield_mpa": 40.0},
    "PA12-CF": {"E": 7500.0, "nu": 0.35, "yield_mpa": 75.0},
    # Fastener / bolt grades
    "bolt-8.8": {"E": 205000.0, "nu": 0.29, "yield_mpa": 640.0},
    "bolt-10.9": {"E": 205000.0, "nu": 0.29, "yield_mpa": 900.0},
    "bolt-12.9": {"E": 205000.0, "nu": 0.29, "yield_mpa": 1080.0},
    "bolt-A2-70": {"E": 193000.0, "nu": 0.29, "yield_mpa": 450.0},
}


# ---------------------------------------------------------------------------
# 1. 3D Vector & 2D Geometry Helpers
# ---------------------------------------------------------------------------

def vec_add(a: Sequence[float], b: Sequence[float]) -> List[float]:
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def vec_sub(a: Sequence[float], b: Sequence[float]) -> List[float]:
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def vec_scale(a: Sequence[float], s: float) -> List[float]:
    return [a[0] * s, a[1] * s, a[2] * s]


def vec_dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vec_cross(a: Sequence[float], b: Sequence[float]) -> List[float]:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def vec_norm(a: Sequence[float]) -> float:
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def unit_vec(a: Sequence[float]) -> List[float]:
    n = vec_norm(a)
    if n < 1e-12:
        raise ValueError(f"Zero-length vector: {a}")
    return [a[0] / n, a[1] / n, a[2] / n]


def dist3(a: Sequence[float], b: Sequence[float]) -> float:
    return vec_norm(vec_sub(a, b))


def point_on_ray(origin: Sequence[float], direction: Sequence[float], dist_mm: float) -> List[float]:
    u = unit_vec(direction)
    return [round(origin[i] + u[i] * dist_mm, 4) for i in range(3)]


def segment_clearance_2d(
    p: Sequence[float],
    a: Sequence[float],
    b: Sequence[float],
) -> Dict[str, Any]:
    """Shortest distance from 2D point `p=(x,y)` to segment `a..b`."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        t = 0.0
    else:
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    cx, cy = a[0] + t * dx, a[1] + t * dy
    return {
        "distance_mm": round(math.hypot(p[0] - cx, p[1] - cy), 4),
        "closest_point": [round(cx, 4), round(cy, 4)],
        "t": round(t, 4),
    }


def stackup_check(
    bands: Dict[str, Sequence[float]],
    min_gap_mm: float = 0.0,
) -> Dict[str, Any]:
    """Check 1D axial stack-up intervals `{name: (lo, hi)}` for overlaps and tight gaps."""
    norm = []
    for name, rng in bands.items():
        lo, hi = float(rng[0]), float(rng[1])
        if lo > hi:
            lo, hi = hi, lo
        norm.append((lo, hi, name))
    norm.sort(key=lambda x: (x[0], x[1]))

    overlaps = []
    tight_gaps = []
    adjacent_gaps = []
    for i in range(len(norm)):
        lo_i, hi_i, name_i = norm[i]
        for j in range(i + 1, len(norm)):
            lo_j, hi_j, name_j = norm[j]
            gap = lo_j - hi_i
            if gap < 0.0:
                overlaps.append({
                    "a": name_i,
                    "b": name_j,
                    "overlap_mm": round(-gap, 4),
                })
            elif j == i + 1:
                entry = {"a": name_i, "b": name_j, "gap_mm": round(gap, 4)}
                adjacent_gaps.append(entry)
                if gap < min_gap_mm:
                    tight_gaps.append(entry)

    return {
        "pass": len(overlaps) == 0 and len(tight_gaps) == 0,
        "overlaps": overlaps,
        "tight_gaps": tight_gaps,
        "adjacent_gaps": adjacent_gaps,
    }


def circle_tangents_2d(
    c1: Sequence[float],
    r1: float,
    c2: Sequence[float],
    r2: float,
) -> Dict[str, Any]:
    """Exact outer tangent line segments between two 2D circles `(c1, r1)` and `(c2, r2)`."""
    dx, dy = c2[0] - c1[0], c2[1] - c1[1]
    d = math.hypot(dx, dy)
    if d <= abs(r1 - r2) + 1e-9:
        raise ValueError(f"Circles are nested/concentric (d={d:.3f} <= |r1-r2|={abs(r1-r2):.3f})")
    base_ang = math.atan2(dy, dx)
    gamma = math.acos((r1 - r2) / d)
    tangents = []
    for sign in (+1.0, -1.0):
        ang = base_ang + sign * gamma
        nx, ny = math.cos(ang), math.sin(ang)
        p1 = [round(c1[0] + r1 * nx, 4), round(c1[1] + r1 * ny, 4)]
        p2 = [round(c2[0] + r2 * nx, 4), round(c2[1] + r2 * ny, 4)]
        tangents.append({"p1": p1, "p2": p2, "length_mm": round(math.hypot(p2[0] - p1[0], p2[1] - p1[1]), 4)})
    return {"center_dist_mm": round(d, 4), "tangents": tangents}


def belt_drive(
    pitch_mm: float,
    z_front: int,
    z_rear: int,
    target_center_dist_mm: Optional[float] = None,
    z_belt: Optional[int] = None,
) -> Dict[str, Any]:
    """Exact open belt/chain drive solver (pitch diameters, tooth count, solved center distance)."""
    if target_center_dist_mm is None and z_belt is None:
        raise ValueError("Provide either target_center_dist_mm or z_belt")
    pd_f = pitch_mm * z_front / math.pi
    pd_r = pitch_mm * z_rear / math.pi
    R = max(pd_f, pd_r) / 2.0
    r = min(pd_f, pd_r) / 2.0
    dr = R - r

    def belt_len(c: float) -> float:
        if c <= dr:
            raise ValueError(f"Center distance {c:.3f} mm must exceed |R-r|={dr:.3f} mm")
        alpha = math.asin(dr / c)
        return 2.0 * math.sqrt(c * c - dr * dr) + math.pi * (R + r) + 2.0 * dr * alpha

    if z_belt is None:
        raw_len = belt_len(float(target_center_dist_mm))
        z_belt = int(round(raw_len / pitch_mm))
    target_len = pitch_mm * z_belt

    # Solve L(C) = target_len via Newton-Raphson
    min_len = math.pi * (R + r) + math.pi * dr + 1e-3
    if target_len <= min_len:
        raise ValueError(f"z_belt={z_belt} (L={target_len:.2f} mm) is too short for {z_front}T/{z_rear}T")

    c = max(float(target_center_dist_mm or (target_len - math.pi * (R + r)) / 2.0), dr + 1.0)
    for _ in range(40):
        f = belt_len(c) - target_len
        if abs(f) < 1e-7:
            break
        df = 2.0 * math.sqrt(max(1e-12, 1.0 - (dr / c) ** 2))
        c = max(dr + 1e-3, c - f / df)

    span = math.sqrt(c * c - dr * dr)
    wrap_small_deg = math.degrees(math.pi - 2.0 * math.asin(dr / c))
    return {
        "pitch_mm": pitch_mm,
        "z_front": z_front,
        "z_rear": z_rear,
        "z_belt": z_belt,
        "pd_front_mm": round(pd_f, 3),
        "pd_rear_mm": round(pd_r, 3),
        "belt_length_mm": round(target_len, 3),
        "center_dist_mm": round(c, 3),
        "tangent_span_mm": round(span, 3),
        "wrap_small_pulley_deg": round(wrap_small_deg, 1),
    }


# ---------------------------------------------------------------------------
# 2. Back-of-the-Napkin Structural Pre-Checks (Run BEFORE heavy 3D FEA!)
# ---------------------------------------------------------------------------

def _get_material(material: str) -> Dict[str, float]:
    if material not in MATERIALS:
        raise ValueError(f"Unknown material `{material}`. Known: {sorted(MATERIALS.keys())}")
    return MATERIALS[material]


def section_props(
    shape: str,
    *,
    od_mm: Optional[float] = None,
    wall_mm: Optional[float] = None,
    b_mm: Optional[float] = None,
    h_mm: Optional[float] = None,
) -> Dict[str, float]:
    """Exact cross-section properties (mm^2, mm^4, mm^3) for tube, rod, rect, rect_tube."""
    sh = shape.lower()
    if sh == "rod":
        if od_mm is None or od_mm <= 0:
            raise ValueError("rod requires od_mm > 0")
        ro = od_mm / 2.0
        area = math.pi * ro ** 2
        I = (math.pi / 4.0) * ro ** 4
        J = 2.0 * I
        return {"area_mm2": area, "I_mm4": I, "I_min_mm4": I, "S_mm3": I / ro, "J_mm4": J, "c_mm": ro}

    if sh == "tube":
        if od_mm is None or wall_mm is None or od_mm <= 0 or wall_mm <= 0:
            raise ValueError("tube requires od_mm > 0 and wall_mm > 0")
        ro = od_mm / 2.0
        ri = ro - wall_mm
        if ri <= 0:
            raise ValueError(f"wall_mm ({wall_mm}) must be < od_mm/2 ({ro}); use shape='rod' for solid")
        area = math.pi * (ro ** 2 - ri ** 2)
        I = (math.pi / 4.0) * (ro ** 4 - ri ** 4)
        J = 2.0 * I
        return {"area_mm2": area, "I_mm4": I, "I_min_mm4": I, "S_mm3": I / ro, "J_mm4": J, "c_mm": ro}

    if sh == "rect":
        if b_mm is None or h_mm is None or b_mm <= 0 or h_mm <= 0:
            raise ValueError("rect requires b_mm > 0 and h_mm > 0 (h_mm is height in bending plane)")
        area = b_mm * h_mm
        I = (b_mm * h_mm ** 3) / 12.0
        I_min = min(I, (h_mm * b_mm ** 3) / 12.0)
        a, b = max(b_mm, h_mm), min(b_mm, h_mm)
        J = a * (b ** 3) * (1.0 / 3.0 - 0.21 * (b / a) * (1.0 - (b ** 4) / (12.0 * a ** 4)))
        return {"area_mm2": area, "I_mm4": I, "I_min_mm4": I_min, "S_mm3": I / (h_mm / 2.0), "J_mm4": J, "c_mm": h_mm / 2.0}

    if sh == "rect_tube":
        if b_mm is None or h_mm is None or wall_mm is None or b_mm <= 0 or h_mm <= 0 or wall_mm <= 0:
            raise ValueError("rect_tube requires b_mm, h_mm, wall_mm > 0")
        bi, hi = b_mm - 2.0 * wall_mm, h_mm - 2.0 * wall_mm
        if bi <= 0 or hi <= 0:
            raise ValueError("wall_mm too thick for rect_tube; use shape='rect'")
        area = b_mm * h_mm - bi * hi
        I = (b_mm * h_mm ** 3 - bi * hi ** 3) / 12.0
        I_min = min(I, (h_mm * b_mm ** 3 - hi * bi ** 3) / 12.0)
        Am = (b_mm - wall_mm) * (h_mm - wall_mm)
        J = (2.0 * wall_mm * Am * Am) / (b_mm + h_mm - 2.0 * wall_mm)  # Bredt closed box
        return {"area_mm2": area, "I_mm4": I, "I_min_mm4": I_min, "S_mm3": I / (h_mm / 2.0), "J_mm4": J, "c_mm": h_mm / 2.0, "Am_mm2": Am}

    raise ValueError(f"Unknown shape `{shape}`. Use: tube, rod, rect, rect_tube")


def _verdict(sf: float) -> str:
    if sf >= 2.0:
        return "PASS (SF >= 2.0 — good candidate for 3D FEA confirmation)"
    if sf >= 1.5:
        return "MARGINAL (1.5 <= SF < 2.0 — consider thickening before FEA)"
    return "FAIL (SF < 1.5 — thicken/redesign BEFORE running heavy 3D FEA!)"


def beam_check(
    length_mm: float,
    force_n: float,
    material: str,
    shape: str,
    support: str = "cantilever",
    **dims: Any,
) -> Dict[str, Any]:
    """1-second beam bending + deflection check.

    support: 'cantilever' (tip load), 'cantilever_udl', 'simply_supported' (mid load),
             or 'fixed_fixed' (mid load).
    """
    mat = _get_material(material)
    sec = section_props(shape, **dims)
    L, F = float(length_mm), abs(float(force_n))
    E, sy = mat["E"], mat["yield_mpa"]

    sup = support.lower()
    if sup == "cantilever":
        M, V, delta = F * L, F, (F * L ** 3) / (3.0 * E * sec["I_mm4"])
    elif sup == "cantilever_udl":
        M, V, delta = F * L / 2.0, F, (F * L ** 3) / (8.0 * E * sec["I_mm4"])
    elif sup == "simply_supported":
        M, V, delta = F * L / 4.0, F / 2.0, (F * L ** 3) / (48.0 * E * sec["I_mm4"])
    elif sup == "fixed_fixed":
        M, V, delta = F * L / 8.0, F / 2.0, (F * L ** 3) / (192.0 * E * sec["I_mm4"])
    else:
        raise ValueError(f"Unknown support `{support}`. Use: cantilever, cantilever_udl, simply_supported, fixed_fixed")

    sigma = M / sec["S_mm3"]
    tau = V / sec["area_mm2"]
    vm = math.sqrt(sigma ** 2 + 3.0 * tau ** 2)
    sf = sy / vm if vm > 1e-9 else 999.0
    return {
        "check": "beam_bending",
        "material": material,
        "support": sup,
        "max_moment_nm": round(M / 1000.0, 2),
        "bending_stress_mpa": round(sigma, 2),
        "avg_shear_mpa": round(tau, 2),
        "von_mises_mpa": round(vm, 2),
        "yield_mpa": sy,
        "max_deflection_mm": round(delta, 3),
        "safety_factor": round(sf, 2),
        "verdict": _verdict(sf),
    }


def euler_buckling(
    length_mm: float,
    force_n: float,
    material: str,
    shape: str,
    k_factor: float = 1.0,
    **dims: Any,
) -> Dict[str, Any]:
    """1-second column Euler buckling + axial compression check.

    k_factor: 1.0 pinned-pinned, 2.0 fixed-free, 0.7 fixed-pinned, 0.5 fixed-fixed.
    """
    mat = _get_material(material)
    sec = section_props(shape, **dims)
    L, F, K = float(length_mm), abs(float(force_n)), float(k_factor)
    E, sy = mat["E"], mat["yield_mpa"]

    p_cr = (math.pi ** 2 * E * sec["I_min_mm4"]) / ((K * L) ** 2)
    sigma_c = F / sec["area_mm2"]
    sf_buckle = p_cr / F if F > 1e-9 else 999.0
    sf_yield = sy / sigma_c if sigma_c > 1e-9 else 999.0
    sf = min(sf_buckle, sf_yield)
    mode = "buckling" if sf_buckle < sf_yield else "compression_yield"
    return {
        "check": "euler_buckling",
        "material": material,
        "k_factor": K,
        "critical_load_n": round(p_cr, 1),
        "applied_load_n": round(F, 1),
        "axial_stress_mpa": round(sigma_c, 2),
        "sf_buckling": round(sf_buckle, 2),
        "sf_yield": round(sf_yield, 2),
        "governing_mode": mode,
        "safety_factor": round(sf, 2),
        "verdict": _verdict(sf),
    }


def bolt_shear_check(
    bolt_diameter_mm: float,
    force_n: float,
    num_bolts: int = 1,
    shear_planes: int = 1,
    material: str = "bolt-8.8",
) -> Dict[str, Any]:
    """1-second bolt / pin / thru-axle shear check (von Mises shear yield = 0.577 * Sy)."""
    mat = _get_material(material)
    area = math.pi * (float(bolt_diameter_mm) / 2.0) ** 2
    tau = abs(float(force_n)) / (int(num_bolts) * int(shear_planes) * area)
    tau_yield = 0.57735 * mat["yield_mpa"]
    sf = tau_yield / tau if tau > 1e-9 else 999.0
    return {
        "check": "bolt_shear",
        "material": material,
        "bolt_diameter_mm": bolt_diameter_mm,
        "num_bolts": num_bolts,
        "shear_planes": shear_planes,
        "shear_stress_mpa": round(tau, 2),
        "shear_yield_mpa": round(tau_yield, 2),
        "safety_factor": round(sf, 2),
        "verdict": _verdict(sf),
    }


def torsion_check(
    torque_nm: float,
    length_mm: float,
    material: str,
    shape: str,
    **dims: Any,
) -> Dict[str, Any]:
    """1-second shaft/tube/axle torsion stress & twist angle check."""
    mat = _get_material(material)
    sec = section_props(shape, **dims)
    T = abs(float(torque_nm)) * 1000.0  # N*mm
    G = mat["E"] / (2.0 * (1.0 + mat["nu"]))
    sh = shape.lower()

    if sh in ("rod", "tube"):
        tau = T * sec["c_mm"] / sec["J_mm4"]
    elif sh == "rect_tube":
        tau = T / (2.0 * float(dims["wall_mm"]) * sec["Am_mm2"])
    elif sh == "rect":
        a, b = max(float(dims["b_mm"]), float(dims["h_mm"])), min(float(dims["b_mm"]), float(dims["h_mm"]))
        tau = (T / (a * b * b)) * (3.0 + 1.8 * (b / a))
    else:
        raise ValueError(f"Unsupported shape for torsion: {shape}")

    twist_deg = math.degrees((T * float(length_mm)) / (G * sec["J_mm4"]))
    tau_yield = 0.57735 * mat["yield_mpa"]
    sf = tau_yield / tau if tau > 1e-9 else 999.0
    return {
        "check": "torsion",
        "material": material,
        "torque_nm": torque_nm,
        "shear_stress_mpa": round(tau, 2),
        "shear_yield_mpa": round(tau_yield, 2),
        "twist_deg": round(twist_deg, 3),
        "safety_factor": round(sf, 2),
        "verdict": _verdict(sf),
    }


# ---------------------------------------------------------------------------
# 3. CLI Entry Point
# ---------------------------------------------------------------------------

def _add_section_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--shape", required=True, choices=["tube", "rod", "rect", "rect_tube"])
    p.add_argument("--od", type=float, dest="od_mm")
    p.add_argument("--wall", type=float, dest="wall_mm")
    p.add_argument("--b", type=float, dest="b_mm")
    p.add_argument("--h", type=float, dest="h_mm")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fast CAD Math & Back-of-the-Napkin Structural Pre-Check")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("beam", help="Beam bending & deflection check")
    pb.add_argument("--length", type=float, required=True)
    pb.add_argument("--force", type=float, required=True)
    pb.add_argument("--material", default="6061-T6")
    pb.add_argument("--support", default="cantilever",
                    choices=["cantilever", "cantilever_udl", "simply_supported", "fixed_fixed"])
    _add_section_args(pb)

    pk = sub.add_parser("buckling", help="Euler column buckling check")
    pk.add_argument("--length", type=float, required=True)
    pk.add_argument("--force", type=float, required=True)
    pk.add_argument("--material", default="6061-T6")
    pk.add_argument("--k", type=float, default=1.0, help="Effective length factor K (default 1.0)")
    _add_section_args(pk)

    ps = sub.add_parser("bolt", help="Bolt / pin / axle shear check")
    ps.add_argument("--dia", type=float, required=True)
    ps.add_argument("--force", type=float, required=True)
    ps.add_argument("--bolts", type=int, default=1)
    ps.add_argument("--planes", type=int, default=1)
    ps.add_argument("--material", default="bolt-8.8")

    pt = sub.add_parser("torsion", help="Shaft / tube torsion check")
    pt.add_argument("--torque-nm", type=float, required=True)
    pt.add_argument("--length", type=float, required=True)
    pt.add_argument("--material", default="6061-T6")
    _add_section_args(pt)

    pbl = sub.add_parser("belt", help="Open belt / chain drive solver")
    pbl.add_argument("--pitch", type=float, required=True)
    pbl.add_argument("--zf", type=int, required=True)
    pbl.add_argument("--zr", type=int, required=True)
    pbl.add_argument("--center-dist", type=float, default=None)
    pbl.add_argument("--zb", type=int, default=None)

    args = ap.parse_args()
    dims = {k: getattr(args, k, None) for k in ("od_mm", "wall_mm", "b_mm", "h_mm") if getattr(args, k, None) is not None}

    if args.cmd == "beam":
        out = beam_check(args.length, args.force, args.material, args.shape, args.support, **dims)
    elif args.cmd == "buckling":
        out = euler_buckling(args.length, args.force, args.material, args.shape, args.k, **dims)
    elif args.cmd == "bolt":
        out = bolt_shear_check(args.dia, args.force, args.bolts, args.planes, args.material)
    elif args.cmd == "torsion":
        out = torsion_check(args.torque_nm, args.length, args.material, args.shape, **dims)
    elif args.cmd == "belt":
        out = belt_drive(args.pitch, args.zf, args.zr, args.center_dist, args.zb)
    else:
        raise SystemExit(2)

    print(json.dumps(out, indent=2))
    return 0 if out.get("safety_factor", 2.0) >= 1.5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
