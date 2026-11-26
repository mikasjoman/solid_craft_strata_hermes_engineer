#!/usr/bin/env python3
"""Generic 3D Structural FEA + Mechanical Load-Path Checker for SolidCraft STEP Models.

Uses official Gmsh (STEP -> 3D tetrahedral mesh) + scikit-fem / SciPy (3D linear
elasticity solver) + Matplotlib (3D von Mises stress heatmap PNG).

Capabilities:
  1. Load-Path & Attachment Check: detects disconnected/floating solid volumes
     (e.g. wheels or moving parts placed visually without physical axles/pins/joins).
  2. 3D Linear Static FEA: solves displacement (mm), 3D Cauchy stress tensor,
     von Mises stress (MPa), Yield Safety Factor, and exact hotspot (X, Y, Z).
  3. Renders a labeled 2-view von Mises stress heatmap PNG for visual inspection.

Usage:
  ~/.hermes/profiles/solidcraft/fea-venv/bin/python solidcraft_fea.py part.step \
    --material 6061-T6 \
    --fix-box -620,-100,340,-560,100,395 \
    --fix-box 320,-40,720,410,40,860 \
    --load-box -180,-50,280,-120,50,340 \
    --force 0,0,-1500 \
    --mesh-size 12 \
    --out-dir ./fea_out
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import gmsh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from skfem import Basis, ElementTetP1, ElementVector, MeshTet, asm
from skfem.helpers import ddot, eye, sym_grad, trace
from skfem import BilinearForm


MATERIALS: Dict[str, Dict[str, float]] = {
    # E in MPa (N/mm^2), nu dimensionless, yield_mpa in MPa, density in kg/m^3
    "6061-T6": {"E": 68900.0, "nu": 0.33, "yield_mpa": 276.0, "density": 2700.0},
    "7075-T6": {"E": 71700.0, "nu": 0.33, "yield_mpa": 503.0, "density": 2810.0},
    "4130-chromoly": {"E": 205000.0, "nu": 0.29, "yield_mpa": 460.0, "density": 7850.0},
    "304-stainless": {"E": 193000.0, "nu": 0.29, "yield_mpa": 215.0, "density": 8000.0},
    "Ti-6Al-4V": {"E": 113800.0, "nu": 0.34, "yield_mpa": 880.0, "density": 4430.0},
    "PLA": {"E": 3500.0, "nu": 0.36, "yield_mpa": 50.0, "density": 1240.0},
    "PETG": {"E": 2100.0, "nu": 0.38, "yield_mpa": 45.0, "density": 1270.0},
    "ABS": {"E": 2300.0, "nu": 0.35, "yield_mpa": 40.0, "density": 1040.0},
    "PA12-CF": {"E": 7500.0, "nu": 0.35, "yield_mpa": 75.0, "density": 1150.0},
}


def _parse_box(s: str) -> Tuple[float, float, float, float, float, float]:
    vals = [float(x.strip()) for x in s.split(",")]
    if len(vals) != 6:
        raise ValueError(f"Box must have 6 comma-separated numbers (xmin,ymin,zmin,xmax,ymax,zmax), got: {s}")
    return (
        min(vals[0], vals[3]),
        min(vals[1], vals[4]),
        min(vals[2], vals[5]),
        max(vals[0], vals[3]),
        max(vals[1], vals[4]),
        max(vals[2], vals[5]),
    )


def _parse_vec3(s: str) -> Tuple[float, float, float]:
    vals = [float(x.strip()) for x in s.split(",")]
    if len(vals) != 3:
        raise ValueError(f"Force vector must have 3 comma-separated numbers (fx,fy,fz in N), got: {s}")
    return vals[0], vals[1], vals[2]


def mesh_step(step_path: Path, mesh_size: float) -> Tuple[np.ndarray, np.ndarray, int]:
    """Import STEP via Gmsh and return (nodes [3,N], tets [4,M], connected_components)."""
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        gmsh.model.add("part")
        gmsh.model.occ.importShapes(str(step_path))
        gmsh.model.occ.synchronize()

        vols = gmsh.model.getEntities(3)
        if not vols:
            raise RuntimeError("STEP file contains 0 3D solid volumes.")

        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", mesh_size * 0.4)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", mesh_size)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 8)
        gmsh.model.mesh.generate(3)

        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        tag_to_idx = {int(t): i for i, t in enumerate(node_tags)}
        nodes = np.asarray(node_coords, dtype=np.float64).reshape(-1, 3).T  # (3, N)

        elem_types, _, elem_node_tags = gmsh.model.mesh.getElements(3)
        tet_list = []
        for etype, enodes in zip(elem_types, elem_node_tags):
            if etype == 4:  # 4-node tetrahedron
                arr = np.asarray([tag_to_idx[int(t)] for t in enodes], dtype=np.int64).reshape(-1, 4)
                tet_list.append(arr)
        if not tet_list:
            raise RuntimeError("Gmsh generated 0 tetrahedral elements.")
        tets = np.vstack(tet_list).T  # (4, M)

        # Build node adjacency graph to count disconnected solid components
        n_nodes = nodes.shape[1]
        rows = np.concatenate([tets[0], tets[1], tets[2], tets[0], tets[1], tets[2]])
        cols = np.concatenate([tets[1], tets[2], tets[3], tets[3], tets[3], tets[0]])
        adj = sp.coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(n_nodes, n_nodes))
        n_comp, labels = sp.csgraph.connected_components(adj + adj.T, directed=False)
        # Keep only components that actually own tets
        used_comps = len(set(labels[tets.ravel()].tolist()))
        return nodes, tets, used_comps
    finally:
        gmsh.finalize()


def compute_tet_von_mises(nodes: np.ndarray, tets: np.ndarray, u: np.ndarray, E: float, nu: float) -> Tuple[np.ndarray, np.ndarray]:
    """Compute per-element von Mises stress (MPa) and element centroids (3, M) for P1 tets."""
    lam = E * nu / ((1.0 + nu) * (1.0 - 2.0 * nu))
    mu = E / (2.0 * (1.0 + nu))

    # Coordinates of tet vertices: (4, 3, M)
    X = nodes[:, tets]  # (3, 4, M)
    x0 = X[:, 0, :]
    J = np.stack([X[:, 1, :] - x0, X[:, 2, :] - x0, X[:, 3, :] - x0], axis=1)  # (3, 3, M)
    # Transpose to (M, 3, 3) where rows are edge vectors (x1-x0, x2-x0, x3-x0)
    Jm = np.transpose(J, (2, 1, 0))  # (M, 3, 3)
    invJ = np.linalg.inv(Jm)  # (M, 3, 3)

    # Reference gradients of N0..N3: [-1,-1,-1], [1,0,0], [0,1,0], [0,0,1]
    dN_ref = np.array([[-1.0, -1.0, -1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])  # (4, 3)
    # Physical gradients dN_phys[m, a, j] = sum_k invJ[m, j, k] * dN_ref[a, k]
    dN = np.einsum("mjk,ak->maj", invJ, dN_ref)  # (M, 4, 3)

    # Nodal displacements u is (3*N,) in scikit-fem ElementVector layout: [ux0, uy0, uz0, ...] or check basis
    U_nodes = u.reshape(-1, 3)  # (N, 3)
    Ue = U_nodes[tets.T]  # (M, 4, 3)

    # Displacement gradient grad_u[m, i, j] = sum_a Ue[m, a, i] * dN[m, a, j]
    grad_u = np.einsum("mai,maj->mij", Ue, dN)  # (M, 3, 3)
    eps = 0.5 * (grad_u + np.swapaxes(grad_u, 1, 2))
    tr_eps = eps[:, 0, 0] + eps[:, 1, 1] + eps[:, 2, 2]

    sxx = lam * tr_eps + 2.0 * mu * eps[:, 0, 0]
    syy = lam * tr_eps + 2.0 * mu * eps[:, 1, 1]
    szz = lam * tr_eps + 2.0 * mu * eps[:, 2, 2]
    sxy = 2.0 * mu * eps[:, 0, 1]
    syz = 2.0 * mu * eps[:, 1, 2]
    szx = 2.0 * mu * eps[:, 2, 0]

    vm = np.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (sxy ** 2 + syz ** 2 + szx ** 2)
    )
    centroids = np.mean(X, axis=1)  # (3, M)
    return vm, centroids


def render_stress_plot(
    centroids: np.ndarray,
    vm: np.ndarray,
    hotspot: List[float],
    out_png: Path,
    title: str,
) -> Path:
    """Render 2-view 3D von Mises stress heatmap (Side XZ + Iso 3D)."""
    # Subsample if huge mesh for fast clean rendering
    n = len(vm)
    if n > 25000:
        # Always keep top 2000 highest-stress elements + random sample
        top_idx = np.argsort(vm)[-2000:]
        rand_idx = np.random.default_rng(42).choice(n, size=23000, replace=False)
        keep = np.unique(np.concatenate([top_idx, rand_idx]))
    else:
        keep = np.arange(n)

    c = centroids[:, keep]
    v = vm[keep]
    vmax = float(np.percentile(vm, 99.5)) if len(vm) > 10 else float(np.max(vm))
    vmax = max(vmax, 1e-6)

    fig = plt.figure(figsize=(13, 5.6), dpi=130, facecolor="#121620")
    fig.suptitle(title, color="#eef2fa", fontsize=12, fontweight="bold", y=0.97)

    # Panel 1: Side XZ projection
    ax1 = fig.add_subplot(1, 2, 1, facecolor="#181e2c")
    order = np.argsort(v)
    sc1 = ax1.scatter(c[0, order], c[2, order], c=v[order], cmap="turbo", s=4, vmin=0, vmax=vmax, alpha=0.85)
    ax1.scatter([hotspot[0]], [hotspot[2]], color="white", edgecolors="red", s=90, linewidths=2, zorder=10, label="Max Stress")
    ax1.set_aspect("equal", adjustable="box")
    ax1.set_title("Side View (XZ) — Von Mises Stress (MPa)", color="#ffd966", fontsize=10)
    ax1.set_xlabel("X (mm)", color="#b0bed4")
    ax1.set_ylabel("Z (mm)", color="#b0bed4")
    ax1.tick_params(colors="#9fb0c9")
    ax1.legend(loc="upper right", facecolor="#121620", labelcolor="white", fontsize=8)

    # Panel 2: 3D Iso view
    ax2 = fig.add_subplot(1, 2, 2, projection="3d", facecolor="#181e2c")
    sc2 = ax2.scatter(c[0, order], c[1, order], c[2, order], c=v[order], cmap="turbo", s=3, vmin=0, vmax=vmax, alpha=0.75)
    ax2.scatter([hotspot[0]], [hotspot[1]], [hotspot[2]], color="white", edgecolors="red", s=90, linewidths=2)
    ax2.set_title("3D Isometric — Von Mises Stress (MPa)", color="#ffd966", fontsize=10)
    ax2.set_xlabel("X", color="#b0bed4")
    ax2.set_ylabel("Y", color="#b0bed4")
    ax2.set_zlabel("Z", color="#b0bed4")
    ax2.tick_params(colors="#9fb0c9")

    cbar = fig.colorbar(sc1, ax=[ax1, ax2], fraction=0.025, pad=0.03)
    cbar.set_label("Von Mises Stress (MPa)", color="#eef2fa")
    cbar.ax.yaxis.set_tick_params(color="#eef2fa")
    plt.setp(plt.getp(cbar.ax.axes, "yticklabels"), color="#eef2fa")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return out_png


def run_fea(
    step_path: Path,
    material_name: str,
    fix_boxes: List[Tuple[float, float, float, float, float, float]],
    load_boxes: List[Tuple[float, float, float, float, float, float]],
    force: Tuple[float, float, float],
    mesh_size: float,
    out_dir: Path,
    min_safety_factor: float = 1.5,
) -> Dict[str, object]:
    if material_name not in MATERIALS:
        raise ValueError(f"Unknown material `{material_name}`. Choose from: {list(MATERIALS.keys())}")
    mat = MATERIALS[material_name]
    E, nu, yield_mpa = mat["E"], mat["nu"], mat["yield_mpa"]

    out_dir.mkdir(parents=True, exist_ok=True)
    nodes, tets, n_components = mesh_step(step_path, mesh_size)

    report: Dict[str, object] = {
        "pass": False,
        "step": str(step_path),
        "material": material_name,
        "yield_mpa": yield_mpa,
        "nodes": int(nodes.shape[1]),
        "elements": int(tets.shape[1]),
        "connected_components": int(n_components),
        "errors": [],
        "max_displacement_mm": None,
        "max_von_mises_mpa": None,
        "p99_von_mises_mpa": None,
        "safety_factor_max": None,
        "safety_factor_p99": None,
        "hotspot_xyz_mm": None,
        "stress_plot": None,
    }

    if n_components > 1:
        report["errors"].append(
            f"DISCONNECTED LOAD PATH: STEP contains {n_components} separate non-bonded solid components. "
            "Floating/unattached parts (e.g. wheels without physical axles or unjoined parts) cannot transfer structural loads."
        )

    mesh = MeshTet(nodes, tets)
    basis = Basis(mesh, ElementVector(ElementTetP1()))

    lam = E * nu / ((1.0 + nu) * (1.0 - 2.0 * nu))
    mu = E / (2.0 * (1.0 + nu))

    @BilinearForm
    def elasticity(u, v, _w):
        eu = sym_grad(u)
        ev = sym_grad(v)
        return lam * trace(eu) * trace(ev) + 2.0 * mu * ddot(eu, ev)

    K = asm(elasticity, basis)

    # Select fixed nodes inside any fix_box
    x, y, z = nodes[0], nodes[1], nodes[2]
    fix_mask = np.zeros(nodes.shape[1], dtype=bool)
    for (x0, y0, z0, x1, y1, z1) in fix_boxes:
        fix_mask |= (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1) & (z >= z0) & (z <= z1)
    fixed_nodes = np.where(fix_mask)[0]
    if len(fixed_nodes) < 3:
        report["errors"].append(f"Too few fixed nodes ({len(fixed_nodes)}) inside --fix-box regions.")
        (out_dir / "FEA_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    # Select loaded nodes inside any load_box
    load_mask = np.zeros(nodes.shape[1], dtype=bool)
    for (x0, y0, z0, x1, y1, z1) in load_boxes:
        load_mask |= (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1) & (z >= z0) & (z <= z1)
    loaded_nodes = np.where(load_mask)[0]
    if len(loaded_nodes) == 0:
        report["errors"].append("0 nodes found inside --load-box regions.")
        (out_dir / "FEA_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    # Map node indices -> scikit-fem DOFs (ElementVector(ElementTetP1) interleaves 3*n + {0,1,2})
    dof_locs = basis.doflocs  # (3, 3*N)
    fixed_dofs = np.concatenate([3 * fixed_nodes, 3 * fixed_nodes + 1, 3 * fixed_nodes + 2])
    free_dofs = np.setdiff1d(np.arange(K.shape[0]), fixed_dofs)

    f_vec = np.zeros(K.shape[0], dtype=np.float64)
    fx, fy, fz = force
    n_load = float(len(loaded_nodes))
    f_vec[3 * loaded_nodes] = fx / n_load
    f_vec[3 * loaded_nodes + 1] = fy / n_load
    f_vec[3 * loaded_nodes + 2] = fz / n_load

    # Solve K_ff u_f = f_f
    K_ff = K[free_dofs][:, free_dofs].tocsc()
    try:
        u_f = spla.spsolve(K_ff, f_vec[free_dofs])
    except Exception as exc:
        report["errors"].append(f"Stiffness matrix singular / solver failed (floating body?): {exc}")
        (out_dir / "FEA_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    if not np.all(np.isfinite(u_f)):
        report["errors"].append("Non-finite displacement (singular stiffness matrix — check disconnected bodies or missing constraints).")
        (out_dir / "FEA_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    u = np.zeros(K.shape[0], dtype=np.float64)
    u[free_dofs] = u_f

    disp_mag = np.linalg.norm(u.reshape(-1, 3), axis=1)
    max_disp = float(np.max(disp_mag))

    vm, centroids = compute_tet_von_mises(nodes, tets, u, E, nu)
    max_idx = int(np.argmax(vm))
    max_vm = float(vm[max_idx])
    p99_vm = float(np.percentile(vm, 99.0))
    hotspot = [round(float(centroids[0, max_idx]), 2), round(float(centroids[1, max_idx]), 2), round(float(centroids[2, max_idx]), 2)]

    sf_max = round(yield_mpa / max(max_vm, 1e-6), 2)
    sf_p99 = round(yield_mpa / max(p99_vm, 1e-6), 2)

    report["max_displacement_mm"] = round(max_disp, 3)
    report["max_von_mises_mpa"] = round(max_vm, 2)
    report["p99_von_mises_mpa"] = round(p99_vm, 2)
    report["safety_factor_max"] = sf_max
    report["safety_factor_p99"] = sf_p99
    report["hotspot_xyz_mm"] = hotspot
    report["fixed_nodes"] = int(len(fixed_nodes))
    report["loaded_nodes"] = int(len(loaded_nodes))

    if sf_p99 < min_safety_factor:
        report["errors"].append(
            f"STRENGTH FAIL: p99 safety factor {sf_p99} < {min_safety_factor} "
            f"(p99={round(p99_vm, 1)} MPa, peak={round(max_vm, 1)} MPa vs yield={yield_mpa} MPa at {hotspot})"
        )

    report["pass"] = len(report["errors"]) == 0
    status = "PASS" if report["pass"] else "FAIL"
    title = (
        f"[{status}] {step_path.name} ({material_name}) | "
        f"p99={round(p99_vm, 1)} MPa (SF={sf_p99}) | peak={round(max_vm, 1)} MPa | "
        f"disp={round(max_disp, 2)} mm | comps={n_components}"
    )
    plot_path = render_stress_plot(centroids, vm, hotspot, out_dir / "fea_stress.png", title)
    report["stress_plot"] = str(plot_path)
    (out_dir / "FEA_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="SolidCraft 3D Structural FEA + Load-Path Checker")
    ap.add_argument("step", help="Exported STEP file path")
    ap.add_argument("--material", default="6061-T6", choices=list(MATERIALS.keys()))
    ap.add_argument("--fix-box", action="append", required=True, help="xmin,ymin,zmin,xmax,ymax,zmax (repeatable)")
    ap.add_argument("--load-box", action="append", required=True, help="xmin,ymin,zmin,xmax,ymax,zmax (repeatable)")
    ap.add_argument("--force", required=True, help="fx,fy,fz in Newtons (e.g. 0,0,-1500)")
    ap.add_argument("--mesh-size", type=float, default=12.0, help="Max tetrahedral element size in mm (default: 12)")
    ap.add_argument("--min-sf", type=float, default=1.5, help="Minimum required p99 safety factor (default: 1.5)")
    ap.add_argument("--out-dir", default="fea_out", help="Output directory")
    args = ap.parse_args()

    rep = run_fea(
        Path(args.step).resolve(),
        args.material,
        [_parse_box(b) for b in args.fix_box],
        [_parse_box(b) for b in args.load_box],
        _parse_vec3(args.force),
        args.mesh_size,
        Path(args.out_dir).resolve(),
        min_safety_factor=args.min_sf,
    )
    print(json.dumps(rep, indent=2))
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
