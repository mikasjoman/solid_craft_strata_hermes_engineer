---
name: solidcraft-superhero
description: Generic code-first SolidCraft v0.49+ mechanical CAD workflow using an isolated mechanical research subagent, typed Python SDK (solidcraft_builder.py), fast/full verification oracle + 3x2 X-ray contact sheet (solidcraft_oracle.py), 2D blueprint pre-flight (blueprint_2d.py), inline chat image updates (MEDIA:), and FEA load-path checks. Load at the start of any SolidCraft design task.
---

# SolidCraft Superhero Toolkit (`~/.hermes/profiles/solidcraft/tools/`)

```python
import sys
sys.path.insert(0, "~/.hermes/profiles/solidcraft/tools")
from solidcraft_builder import SolidCraftBuilder
from solidcraft_oracle import verify_design
from blueprint_2d import render_blueprint
```

## 0. Phase 0 — Isolated Mechanical Research Subagent (`delegate_task`)
Before writing CAD code on any unfamiliar or coupled multi-part machine, spawn **1 isolated subagent** (`delegate_task`) so web research and reference reading stay in its own disposable context window and **never pollute the main CAD builder's context**:
- Instruct the subagent to research the real-world mechanical anatomy of the target mechanism:
  1. **Find existing 2D/3D reference drawings & spec sheets:** Search for manufacturer technical drawings, exploded assembly diagrams, geometry charts, and open-source 2D/3D CAD references (e.g., ISO/DIN/industry standards, component datasheets, engineering blueprints) for every major subsystem/part. Inspect key 2D drawings/diagrams with vision inside the subagent's isolated context and extract real dimensions, mounting bolt patterns, cross-sections, and profiles so every part starts from real engineering geometry instead of guessing.
  2. **Real silhouette & proportions** (standard industry dimensions, tube/member cross-sections, wall thicknesses, ergonomic/packaging envelopes).
  3. **Physical load paths & real mechanical joints** — how every moving or load-bearing part actually attaches (shafts, axles, spindles, bearings, bores, flanges, clamps, fasteners, welds/gussets). **No visually floating parts** — every sub-assembly must have a physical load-transferring mate.
  4. **Explicit clearance stack-up table** (`[Y_min, Y_max]` / `[Z_min, Z_max]` intervals for adjacent housings, moving parts, rotating members, fasteners, and envelopes).
  5. **Stage-by-stage build plan** (Milestones 1..N) written to `DESIGN_SPEC.md` and `COORDINATE_TABLE.json` in the project directory.
- Have the subagent return **only a ≤15-line summary** + file paths (`DESIGN_SPEC.md`, `COORDINATE_TABLE.json`).
- **Strict speed budget (prevent 20-min research loops):** Cap each research subagent to **≤ 12 tool calls**, **up to 5 key reference drawings/diagrams**, and a concise **≤ 120-line spec** (target **< 4 minutes**). Stop searching as soon as standard dimensions, joint/mounting interfaces, and clearance stack-ups are confirmed — do not exhaustively collect redundant pages or write encyclopedic docs.
- Keep `max_concurrent_children: 1` (1 parent + 1 child = 2 Strata slots, zero GPU contention).

## 1. Pre-3D Blueprint Gate (`blueprint_2d.py`)
Render `COORDINATE_TABLE.json` before building 3D geometry:
```bash
python3 ~/.hermes/profiles/solidcraft/tools/blueprint_2d.py COORDINATE_TABLE.json --out blueprint_2d.png
```
Inspect `blueprint_2d.png` via native vision / `vision_analyze` to verify silhouette, proportions, angles, and clearances, and post it inline in chat (see Section 5).

## 2. Schema-Validated Python Builder (`solidcraft_builder.py`)
Auto-validates all 903 v0.49.1 commands. Generic mechanical helpers:
- **`b.swept_tube(name, points_2d, diameter, plane="XZ", world_xz=True, wall=None)`** — 2D planar spline tube/pipe (`v = -Z` auto-mapped, auto `DOF = 0`).
- **`b.swept_tube_3d(name, points_3d, diameter, wall=None, operation="join", targets=[...])`** — 3D curved/formed tube or duct on an auto-fitted `plane: {origin, x_dir, y_dir}` (auto `DOF = 0`).
- **`b.tube_between(name, p0, p1, diameter, operation="join", targets=[...], extend_start_mm=2, extend_end_mm=2)`** — exact 3D straight strut/shaft with clean join overlap.
- **`b.torus(name, center, major, minor, body_name=...)`** — DOF=0 Z-axis torus (rotate via `solid.move` for O-rings, seals, rings, or toroidal rims).
- **`b.ring_boss(name, plane="XY", offset=0, center=[x,y], outer_diameter=..., inner_diameter=..., height=...)`** — DOF=0 concentric mounting boss / bore.
- **`b.checkpoint()`** — `edit.checkpoint` (takes NO arguments).

## 3. v0.49.1 Golden Recipes (Zero Probing Needed)
- **Custom sketch plane:** Always nest inside `plane`: `b.cmd("sketch.create", name="Sk", plane={"origin":[x,y,z], "x_dir":[...], "y_dir":[...]})`.
- **Closed polygon sketch:** `b.cmd("sketch.line", points=[[x0,y0],[x1,y1],...], closed=True)` (NOT `p0`/`p1`).
- **Circular patterns (keep body count low):** `solid.pattern.circular` names extra instances `"Name (1)"`, `"Name (2)"`, ...; always pass explicit `axis={"origin":[x,y,z], "dir":[dx,dy,dz]}` and merge patterned features (teeth, ribs, bosses, spokes, fins) into their parent body via `solid.combine` (`operation="join"`) so each sub-assembly stays 1–3 bodies instead of 20+.
- **Appearances:** `b.cmd("appearance.assign", bodies=["Housing"], color="#2f3e46", roughness=0.65, metallic=0.2)`.
- **Hide construction before final render:** `b.cmd("browser.visibility", origin=False, sketches=False, construction=False)`.

## 4. Fast vs. Full Oracle (`solidcraft_oracle.py`) & Hard Per-Stage Cap
- **Strict per-stage cap (NEVER get stuck):** Maximum **3 oracle fix loops** per stage (target **< 8 minutes per stage**). On strike 2 of the same defect, escalate once to `delegate_task` (`gpt-6.1-sol`) with a <300-word diff + error; on strike 3, simplify non-critical detail and advance.
- **Incremental stage inner loop (~2–4s, loads previous stage `.sc` instead of replaying earlier stages from scratch):**
  ```bash
  python3 ~/.hermes/profiles/solidcraft/tools/solidcraft_oracle.py stage_X_delta.json --in ./stage_prev/model.sc --out-dir ./stage_X --save ./stage_X/model.sc --fast
  ```
- **From-scratch inner loop (Stage 1 or full rebuild):**
  ```bash
  python3 ~/.hermes/profiles/solidcraft/tools/solidcraft_oracle.py build.json --out-dir ./stage_X --save ./stage_X/model.sc --fast
  ```
- **Final milestone gate (full clash + clearance + 6-view + X-ray section contact sheet + `STATE.json`):**
  ```bash
  python3 ~/.hermes/profiles/solidcraft/tools/solidcraft_oracle.py stage_X_delta.json --in ./stage_prev/model.sc --out-dir ./stage_X --save ./stage_X/model.sc --section y=0 --clearance 2.0
  ```

## 5. Mandatory Inline Picture Updates in Chat (`MEDIA:`)
Users watching in Hermes Desktop cannot see images unless you emit a **`MEDIA:` tag** (local Markdown `![alt](/path)` does **not** render inline in Hermes Desktop!).
- After **every milestone** (`blueprint_2d.png`, every passing `stage_X/contact_sheet.png`, and `fea_stress.png`), include the exact absolute path on its own line in your update message:
  ```text
  MEDIA:/absolute/path/to/stage_X/contact_sheet.png
  ```
- Also open `./stage_X/model.sc` via MCP `open` once per passing stage so the user sees the live 3D model update in SolidCraft alongside the picture in chat.
- **Speed & Anti-Bloat Rules (NEVER violate):**
  - **NEVER take multi-frame/turntable screenshots via one-by-one `mcp__solidcraft_live__screenshot` calls** (taking 24 screenshots via MCP wastes ~20 minutes). Always render multi-view or turntable frames in **one Python loop calling `solidcraft-cli snapshot`** (or `motion.export_animation`) — 24 frames in ~8 seconds.
  - **NEVER spam raw `inspect_design` (18–43 KB/call) or `describe_command`:** Read compact `STATE.json` (<1.5 KB) from `solidcraft_oracle.py`, and let `solidcraft_builder.py` auto-validate all 903 command schemas.

## 6. Final Load-Path & Structural FEA Gate (`solidcraft-fea-and-loadpath`)
When the assembly is complete, load `solidcraft-fea-and-loadpath` (or delegate the STEP export + `solidcraft_fea.py` run to **1 isolated subagent** so verbose meshing/solver logs don't bloat the main context). Verify `connected_components == 1` on load-bearing structures, check safety factor & peak stress hotspots, and post `MEDIA:/absolute/path/to/fea_stress.png` in chat.
