---
name: cad-engineering-review
description: Load AUTOMATICALLY at the end of a long-running or multi-stage CAD design task (do NOT load for small edits, quick fixes, or single-part tweaks). Performs a Mechanical Design Review (MDR), offers an optional adversarial mechanical soundness review, and offers an optional Python/Gmsh/scikit-fem FEA stress & deflection analysis.
---

# Mechanical Design Review (MDR) & Optional FEA Workflow

## When to Load
- **AUTO-LOAD** after completing a multi-stage or long-running CAD design (e.g., full assembly, vehicle, mechanism, or structural frame).
- **DO NOT LOAD** for small edits, quick parameter tweaks, or simple single-part corrections.

## 1. Self-Check Mechanical Design Review (MDR)
Before presenting a finished multi-stage design, audit these 4 engineering pillars and report any **P0 (broken/colliding)** or **P1 (weak/unbuildable)** issues:
1. **Load Paths & Structural Sizing**: Trace how primary loads travel from input to ground (e.g., applied load -> mounting boss -> primary beam/frame -> support bearings/anchors; reaction torque -> housing). Check wall/tube thickness and stress risers (fillets at load corners).
2. **Kinematics & Envelope**: Verify full range of motion (`joint.sweep` / `motion.interference`), pinch points, ground/housing clearance, and swept-envelope clearance for rotating/pivoting arms.
3. **Assembly & Serviceability**: Can the part be assembled and disassembled in the real world? (bolt tool access, bearing shoulders, removable axles).
4. **Mass & Center of Gravity**: Check `measure` / `occurrence.properties` for realistic mass distribution and stability.

## 1b. End-of-Iteration GPT Triage (`GPT_REVIEW.md`) & Mandatory User Feedback Gate
At the end of every iteration (`iter_01`, `iter_02`, ...), produce `GPT_REVIEW.md` containing:
1. **Part-by-Part Triage Table** — classify EVERY part/subsystem as:
   - **`KEEP`**: Geometrically & structurally sound — freeze `.step` and clone untouched into the next iteration.
   - **`IMPROVE`**: Functional base, specific targeted fixes listed for the next iteration.
   - **`DISCARD`**: Fundamentally flawed/poor quality — exclude during `iter_manager.py clone --discard ...` and redesign cleanly next iteration.
2. **Top 3–5 Next-Iteration Priorities** (ranked by engineering impact).
3. **Update Live Web Dashboard (`iter_manager.py update`)** with `--triage`, `--gpt-summary`, and `--hero` render so `web/index.html` shows step-by-step visual evolution across iterations.
4. **PAUSE for User Feedback (`USER_FEEDBACK.md`)**: Show the user the live SolidCraft assembly, `web/index.html` path, and triage table, and ask for feedback **before** closing the iteration or cloning `iter_0(N+1)`.

## 2. Ask the User Before Running Heavy Follow-Ups
At the end of a completed long-running design, **explicitly ask the user** two short yes/no questions:
1. **Adversarial Mechanical Review**: *"Would you like me to run an independent adversarial mechanical review (fresh reviewer subagent with zero sunk-cost bias) to critique structural soundness, clearances, and manufacturability?"*
   - If the user says **Yes**: Dispatch a fresh read-only subagent (`delegate_task`, `tier="strata"` or `tier="local_4070s"`, or ask permission for `tier="openai_surgical"` if high-stakes) with the parameter table, `measure` summary, `motion.interference` result, and multi-view screenshots (`iso`, `front`, `right`) to return a concise **P0/P1/P2** critique.
2. **FEA Stress & Deflection Analysis**: *"Would you like me to run an FEA (Finite Element Analysis) stress & deflection check on the load-bearing structure?"*
   - If the user says **Yes**: Use the pre-installed Python FEA environment at:
     `/home/mikaelsjoman/.hermes/profiles/solidcraft/fea-venv/bin/python`
     (Installed packages: `gmsh`, `scikit-fem`, `trimesh`, `meshio`, `numpy`, `scipy`).
   - **FEA Workflow**:
     1. Export the load-bearing body/assembly from SolidCraft as `.step` (or `.stl`) via `export`.
     2. Mesh the `.step` geometry into 3D tetrahedra using `gmsh` (coarse/medium mesh first for speed).
     3. Run linear-elastic 3D FEA in `scikit-fem` (or analytical beam/truss FEA in `numpy`/`scipy` for tubular frames) with realistic material properties (e.g., 6061-T6 Aluminum: $E=69\text{ GPa}, \nu=0.33, \sigma_y=276\text{ MPa}$; 4130 Steel: $E=205\text{ GPa}, \sigma_y=460\text{ MPa}$; PETG: $E=2.1\text{ GPa}, \sigma_y=50\text{ MPa}$).
     4. Report **Max von Mises Stress (MPa)**, **Max Deflection (mm)**, **Factor of Safety ($\text{FoS} = \sigma_y / \sigma_{\max}$)**, and the exact hotspot coordinate so you can reinforce it in SolidCraft.
