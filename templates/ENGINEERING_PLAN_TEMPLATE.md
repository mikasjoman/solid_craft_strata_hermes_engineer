# Engineering Plan — {{PROJECT_TITLE}} (`{{ITERATION}}`)

> **Rule:** Every iteration follows this exact template. Do NOT reinvent working parts from prior iterations — clone `KEEP` parts untouched (`.step`), refine `IMPROVE` parts, and redesign only `DISCARD` parts.
>
> **Human-Style Build Rhythm (1 Component Per File — No Monoliths):**
> 1. Build **1 component per file** (`parts/p01_<name>.py` → verify alone in ~2–4s → export `parts/out/p01_<name>.step`).
> 2. **Snap 2–3 mating `.step` parts together early** — either directly in `assembly.py` OR into an optional **sub-assembly** (`subassemblies/sub_<group>.py` → `subassemblies/out/sub_<group>.step`) where a coupled group makes sense — and inspect the fit (`contact_sheet.png` / `clash_zoom.png`) before building the next group.
> 3. **Tweak ONE part at a time** (re-export only that single `.step` and re-check the sub-assembly / `assembly.py` in ~5s) — never rebuild the entire machine in one giant script.

## 1. Iteration Inputs & Locked Constraints
- **Iteration:** `{{ITERATION}}` (cloned from: `{{PARENT_ITERATION}}`)
- **Shared Research:** `../research/MECHANICS_STUDY.md`, `../research/references/`
- **Previous GPT Audit:** `{{PREV_GPT_REVIEW}}`
- **Previous User Feedback:** `{{PREV_USER_FEEDBACK}}`
- **Key Requirements & Hard Dimensions:**
  - _[List target envelope, materials, load cases, interfaces, tolerances]_

## 2. Part Triage Table (`KEEP` / `IMPROVE` / `DISCARD`)
*(For `iter_01`, mark all planned subsystems as `NEW`. For `iter_02+`, populate directly from `GPT_REVIEW.md` + `USER_FEEDBACK.md`.)*

| Part / Subsystem | Action (`NEW` / `KEEP` / `IMPROVE` / `DISCARD`) | Frozen `.step` Source | Target Changes for This Iteration |
|---|---|---|---|
| `Subassembly_A` | `NEW` | — | _[Exact geometric/structural goal]_ |

## 3. Stage-by-Stage Build Plan & Exit Gates (`GATES.json`)
| Stage | Scope | Exit Gate (`GATES.json` / Oracle / Math) | Status |
|---|---|---|---|
| **P0** | Research, `COORDINATE_TABLE.json`, `GATES.json`, 2D blueprint | `blueprint_2d.png` verified; `cad_math.py` stack-up & pre-FEA checks PASS | `pending` |
| **S1..SN** | Build/update only `NEW` / `IMPROVE` / `DISCARD` parts; freeze each passed stage as `.step` | `solidcraft_oracle.py --expect-json GATES.json` PASS (0 errors, 0 clashes, 0 split bodies) | `pending` |
| **ASM** | Master `assembly.sc` via `file.insert_step` + close-up multi-angle fit checks | Published to `recovery/`; live GUI opened; overview + interface close-ups verified | `pending` |
| **FEA** | Load-case research + `cad_math.py` pre-check + `solidcraft_fea.py` (when load-bearing) | `fea_stress.png`, `fea_deform.gif`, `FEA_REPORT.json` (`SF >= 1.5`, 1 component) | `pending` |
| **GPT** | Frontier GPT audit (`GPT_REVIEW.md`) | Part-by-part `KEEP / IMPROVE / DISCARD` triage + top 3–5 next-iteration upgrades | `pending` |
| **USR** | **User Feedback Gate (`USER_FEEDBACK.md`)** | User reviews live GUI + `web/index.html` + `GPT_REVIEW.md` and approves close / next iteration | `pending` |

## 4. Live Web Dashboard Updates (`iter_manager.py update`)
- Update `../web/index.html` **after every stage** (status + loop log + renders) AND at iteration close (hero image + triage table + GPT/user summary).

## 5. End-of-Iteration Closeout Checklist (Do NOT Skip)
1. [ ] `assembly/assembly.sc` published to `~/.local/share/solidcraft/recovery/` and opened in live SolidCraft GUI.
2. [ ] `GPT_REVIEW.md` written with explicit `KEEP / IMPROVE / DISCARD` verdict for every part.
3. [ ] `../web/index.html` updated with hero render, contact sheet, close-ups, and triage table.
4. [ ] **PAUSED for user feedback** — user notes saved to `USER_FEEDBACK.md` before closing or cloning `iter_0(N+1)`.
