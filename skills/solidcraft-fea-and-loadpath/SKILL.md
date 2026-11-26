---
name: solidcraft-fea-and-loadpath
description: Generic mechanical load-path attachment discipline and 3D structural FEA strength testing (Gmsh + scikit-fem von Mises stress, safety factor, deflection, and disconnected-part detection) for any SolidCraft STEP model. Load whenever designing load-bearing parts, assemblies with shafts/axles/bearings, or running strength verification.
---

# Mechanical Load Paths & 3D Structural FEA (`solidcraft_fea.py`)

## 1. Mandatory Mechanical Attachment Rules (No Visually Floating Parts!)
Before declaring any multi-part assembly complete, verify **every load-bearing or moving component has a physical load path** to the main structure:
- **Rotating members (wheels, pulleys, gears, levers, links):** Must be physically linked to their support housing/dropouts by a **shaft, spindle, or thru-axle** passing concentrically through both the support bore(s) AND the rotating hub/boss bore, with defined radial clearance (e.g. `0.2–0.5 mm` or bearing sleeve). Never place a wheel/gear visually near a frame without an axle/spindle bridging them.
- **Steered / pivoted columns (steerer tubes, pivot shafts, hinges):** Must pass physically through their bore/bearing seats with collars, clamps, or crowns above/below the housing.
- **Static structural attachments (brackets, flanges, mounts, stays):** Must overlap and `join` cleanly (`>= 1 mm` engagement) or connect via physical bolts/pins/lugs.

## 1b. Kinematic & 360° Rotational-Clearance Gate (Assert in Python Before CAD!)
Never rely on static visual inspection alone for rotating or coupled mechanisms. Add explicit Python `assert` statements before emitting CAD commands:
1. **Strict monotonic lateral/axial stack-up:** Assert non-overlapping ordered intervals from centerline outward (e.g., `stay_y_max < drive_ring_y_min < drive_ring_y_max < crank_arm_y_min < crank_arm_y_max < pedal_y_min`).
2. **Opposing-member kinematic phase:** For 180°-opposed cranks, levers, or blades, assert `dot(dir_A, dir_B) < -0.99` so symmetric loops (`for side in (-1, +1)`) can never accidentally point both arms the same direction.
3. **Full 360° swept-envelope clearance:** Verify that every rotating arm, pulley, rotor, or linkage clears adjacent stationary structure and belts/chains across its **entire 360° swept circle**, not just at its static build angle.
4. **Top (`XY`) + Side (`XZ`) visual gate:** Always inspect a close-up Top + Side render via `vision_analyze` (`gpt-6.1-sol`) specifically checking phase opposition, lateral ordering, and rotational clearance.

## 2. Running 3D Structural FEA (`solidcraft_fea.py`)
1. Export the load-bearing solid or assembly from SolidCraft to STEP:
   ```bash
   solidcraft-cli run --in model.sc --out part.step
   ```
2. Run 3D linear static FEA using `~/.hermes/profiles/solidcraft/fea-venv/bin/python`:
   ```bash
   ~/.hermes/profiles/solidcraft/fea-venv/bin/python \
     ~/.hermes/profiles/solidcraft/tools/solidcraft_fea.py part.step \
     --material 6061-T6 \
     --fix-box=xmin,ymin,zmin,xmax,ymax,zmax \
     --load-box=xmin,ymin,zmin,xmax,ymax,zmax \
     --force=0,0,-1500 \
     --mesh-size 12 \
     --min-sf 1.5 \
     --out-dir ./fea_out
   ```
   *(Always use `=` with `--fix-box=`, `--load-box=`, and `--force=` so negative coordinates parse cleanly.)*
   - **Materials:** `6061-T6`, `7075-T6`, `4130-chromoly`, `304-stainless`, `Ti-6Al-4V`, `PLA`, `PETG`, `ABS`, `PA12-CF`.
   - Repeat `--fix-box` and `--load-box` for multiple supports or load zones.
   - Start with `--mesh-size 10..15` (coarse/fast, ~5–15s) for large parts (>300 mm) and `--mesh-size 3..6` for small brackets (<150 mm).

## 3. Reading `FEA_REPORT.json` & `fea_stress.png`
- **`connected_components > 1` (`DISCONNECTED LOAD PATH`):** Parts are floating / unattached — add physical axles, shafts, pins, or joins before trusting any structural result.
- **`safety_factor_p99` & `hotspot_xyz_mm`:** Require `safety_factor_p99 >= 1.5` (use `p99` to ignore single-element sharp-corner singularities, and inspect `fea_stress.png` via `vision_analyze`).
- **If `pass: false`:** Increase wall thickness, tube OD, rib depth, or add a gusset/fillet at `hotspot_xyz_mm`, rebuild, and re-run FEA until `pass: true`.
- **Chat visual report:** Always emit `MEDIA:/absolute/path/to/fea_stress.png` on its own line in chat alongside `max_displacement_mm`, `von_mises_mpa.p99`, `safety_factor_p99`, and `hotspot_xyz_mm`.
