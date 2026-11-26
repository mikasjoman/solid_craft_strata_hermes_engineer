# `solid_craft_strata_hermes_engineer`

**Turning a Local Dual-Tesla V100 AI Server + Hermes Agent + SolidCraft v0.49+ into a Code-First Mechanical CAD & FEA Engineering Engine — for Almost Zero Cloud Token Spend.**

This repository documents the full engineering journey, forensic telemetry, dead ends, and production-tested tools/skills/configs we built to make a local **176B MoE (`Qwen3.8-Flash-Next`, 6B active)** running on **Strata (`v0.2.22 / engine 0.1.69`)** + **2× Tesla V100 32GB PCIe (`sm_70`)** design complex, multi-part, mechanically sound 3D CAD assemblies with **real load paths, kinematic assertions, 3×2 X-ray visual gates, and 3D structural FEA** — while barely touching cloud API quota.

---

## Executive Results (Before vs. After)

| Metric | Initial Setup (SolidCraft ACP + Stock Config) | Tuned `solid_craft_strata_hermes_engineer` Stack |
|---|---|---|
| **Deferred `tool_call` failure rate** | **86.7%** (137/158 failed) | **0.0%** (`tools.tool_search.enabled: "off"`) |
| **Batched CAD command rollback loss** | **45.0%** (1,700/3,777 lost; 50.6% fail at >12 cmds) | **~0%** (schema-validated Python SDK across all 903 v0.49.1 commands) |
| **Strata decode speed (`sm_70` Dual V100)** | `37.1 tok/s` cold / `62.2 tok/s` warm (`0.1.65`) | **`67.5 tok/s` cold / `68.3–117.1 tok/s` live** (`0.1.69`) |
| **Visual inspection coverage** | `supports_vision: false` (81.5% renders uninspected) → then `qwen3.5:9b` missed obvious 3D bugs | **Native V100 `strata-vision` + `gpt-6.1-sol` visual gate** (~`$0.01`/image) |
| **Mechanical & kinematic validity** | Visually floating parts, loose unmerged sub-bodies, static-only checks | **Physical axles/shafts/bores, Python kinematic & 360° sweep assertions, merged bodies, 3D Gmsh+scikit-fem FEA** |
| **Inner-loop verification time** | 20–90s full GUI replay / 24-min MCP screenshot loops | **~2–4s** incremental `--in prev_stage.sc --fast` headless oracle |
| **OpenAI API spend** | N/A (or `$5–$15+` all-cloud) | **Pennies** (`~$0.05–$0.50` per complex assembly; 95%+ tokens run locally at `$0`) |

---

## Why the Final Stack Works So Much Better (Key Learnings)

1. **Skip SolidCraft's embedded ACP chat for long autonomous runs → use Hermes Desktop (`solidcraft` profile):**
   - SolidCraft's embedded ACP chat timed out waiting 300s on `write_file` approvals, hit the 35-operation GUI cap, and broke UI turn ownership on async subagent wakeups.
   - Running in **Hermes Desktop** on a dedicated **`solidcraft` profile** (only 8 CAD skills + `solidcraft-live` MCP + lean `SOUL.md`) saves **~15k baseline tokens/turn** and avoids ghost-session contention.
2. **Code-first Python SDK + Headless Oracle beats raw JSON MCP batching for complex assemblies:**
   - SolidCraft's official docs themselves note that MCP `batch` is all-or-nothing (best for simple single parts & live tweaks), while `solidcraft-cli run` scripts are built for complex, reproducible, and subagent work.
   - [`tools/solidcraft_builder.py`](tools/solidcraft_builder.py) lazily caches all 903 `solidcraft-cli 0.49.1` schemas and rejects bad command IDs/args *before* SolidCraft runs.
   - [`tools/solidcraft_oracle.py`](tools/solidcraft_oracle.py) supports incremental `--in prev_stage.sc --fast` (~2–4s inner loop) and full milestone gates (DOF=0, interference, clearance, 6-view + `y=0` X-ray `contact_sheet.png`, and `<1.5 KB` `STATE.json`).
3. **Executable Python Kinematic Assertions + Load-Path Discipline > Walls of Research Text:**
   - A 6B-active local model can read a 20 KB spec and still write a sign/mirror bug (e.g. symmetric arms pointing the same direction, or a rotating arm placed inboard of a drive element).
   - Putting hard `assert` statements in Python (`geometry.py` / `assert_*.py`) for monotonic lateral stack-ups, opposing-arm phase (`dot < -0.99`), physical shafts/bores, and 360° swept-circle clearance catches bugs in **10 milliseconds** before 3D B-Rep even runs.
4. **Never use a 9B VLM as a 3D CAD visual critic:**
   - Routing `vision_analyze` to local `qwen3.5:9b` silently rubber-stamped major 3D spatial bugs.
   - Keeping **95%+ of token generation on local Strata (`$0`)** and spending **pennies** on a frontier visual critic (`gpt-6.1-sol`) at stage gates catches spatial defects in 2 seconds.
5. **Workers build; Main verifies (`GATES.json`):**
   - Subagents on Strata (`max_concurrent_children: 1` matching `--parallel 2`, `max_iterations: 30`) build modular parts and report artifacts — they **cannot** self-certify a stage as done. The main agent runs the oracle, assertions, and visual gate before advancing.
6. **Hardware & Inference Tuning (`Dual Tesla V100 32GB PCIe`, `Dual Xeon Gold 6140`, `64 GB RAM`):**
   - Upgrading Strata `v0.2.19 (0.1.65)` → **`v0.2.22 (0.1.69)`** unlocked fused `EXP_MODE=8` for `sm_70` V100s and AVX-512 cold-expert kernels: **+81.9% faster cold decode**, **84–117 tok/s** live!
   - **98,304 (96k) context** is the sweet spot on 64 GB VRAM + 64 GB RAM (`compression.threshold: 0.82` compacts at ~80.6k before context rot while keeping GPU expert cache at ~99% hit rate).
   - **140W power limit** on passive V100s delivers ~98–100% of 170W decode speed (HBM2 stays locked at full `877 MHz` / 900 GB/s even down to 115W) with far less thermal throttling.

---

## Repository Structure

```text
solid_craft_strata_hermes_engineer/
├── README.md                              # Overview, benchmarks, architecture, quickstart
├── docs/
│   └── TRIALS_AND_LEARNINGS.md            # Complete forensic log of every trial, failure & fix
├── tools/
│   ├── solidcraft_builder.py              # Auto-schema-validated Python SDK (903 v0.49+ cmds)
│   ├── solidcraft_oracle.py               # Headless verifier + --in/--fast + 3x2 X-ray contact sheet
│   ├── blueprint_2d.py                    # Pre-3D 2D orthographic dimensioned blueprint renderer
│   └── solidcraft_fea.py                  # Generic STEP -> Gmsh 3D tet -> scikit-fem von Mises FEA
├── skills/
│   ├── solidcraft-superhero/SKILL.md      # Generic code-first CAD workflow, MEDIA: & stage caps
│   └── solidcraft-fea-and-loadpath/SKILL.md # Physical load-path, 360° kinematics & FEA guide
└── configs/
    ├── SOUL.md                            # Lean 2-tier CAD system prompt
    ├── hermes-solidcraft-config.yaml      # Sanitized Hermes profile config
    ├── strata-dual-v100.json              # Sanitized Strata v0.2.22 server config
    └── strata-server.service              # Tuned Volta (sm_70) systemd unit
```

---

## Prerequisites
- **Hermes Agent / Hermes Desktop** installed (`~/.hermes/`).
- **SolidCraft v0.49+** (`solidcraft-cli` in `PATH` or `~/.local/bin/solidcraft-cli`) with **Preferences → Assistant → control channel enabled on `127.0.0.1:27878`**.
- **Python 3.10+ with `pillow`** (`pip install --user pillow`) for `blueprint_2d.py` and `solidcraft_oracle.py`.
- **Strata AI Server (`v0.2.22 / engine 0.1.69+`)** running `Qwen3.8-Flash-Next (UD-Q4_K_XL)` on your local GPU server.
  - *Dual Tesla V100 (`sm_70`, CUDA 12) build tip:* Compile with `CMAKE_CUDA_ARCHITECTURES=70`, `CMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-13`, `CMAKE_BUILD_TYPE=Release`, and build `--target strata` directly (avoids GCC 13 AMX header errors in the unrelated `inkling2` target).
- **`OPENAI_API_KEY`** in `~/.hermes/.env` (used only for low-cost `gpt-6.1-sol` visual QA / optional Manager-Worker orchestration).

## Quickstart

1. **Clone and run the one-line installer** (add `--with-fea` for Gmsh + scikit-fem FEA; add `--manager-worker` to default starter config to Preset B):
   ```bash
   git clone https://github.com/mikasjoman/solid_craft_strata_hermes_engineer.git
   cd solid_craft_strata_hermes_engineer
   chmod +x install.sh && ./install.sh --with-fea
   ```
2. **Edit `~/.hermes/profiles/solidcraft/config.yaml`** (replace `<STRATA_HOST>` with your Strata server IP/hostname). Two ready-to-use presets are included:
   - [`configs/hermes-solidcraft-config.yaml`](configs/hermes-solidcraft-config.yaml) — **Local-first** (`~$0.05–$0.50`/assembly: Strata builds 95%+ at `$0`, OpenAI `gpt-6.1-sol` does visual QA & 2-strike deadlocks).
   - [`configs/hermes-manager-worker-config.yaml`](configs/hermes-manager-worker-config.yaml) — **Manager-Worker** (`~$0.50–$1.20`/assembly, ~10× faster wall-clock: OpenAI `gpt-6.1-sol` orchestrates & reviews, local Strata workers execute all CAD coding, oracle loops & FEA at `$0`).
3. **Optional — Server-side Dual-V100 Strata configs:** See [`configs/strata-dual-v100.json`](configs/strata-dual-v100.json) and [`configs/strata-server.service`](configs/strata-server.service) (Volta `sm_70` CUDA 12 flags + `STRATA_SPLIT_LEND_RESERVE=0`).
4. **Run in Hermes Desktop** on the dedicated `solidcraft` profile (bottom-left sidebar) — local renders appear inline in chat via `MEDIA:/absolute/path/to/image.png`, and each passing stage opens live in SolidCraft via MCP `open`.

---

See [`docs/TRIALS_AND_LEARNINGS.md`](docs/TRIALS_AND_LEARNINGS.md) for the complete chronological forensic breakdown of every failure, benchmark, and optimization.
