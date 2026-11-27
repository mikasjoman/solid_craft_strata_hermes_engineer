# Trials, Forensics & Engineering Learnings

This is the chronological, data-backed record of every bottleneck we discovered, every dead end we hit, and every fix we verified while building an autonomous parametric CAD + FEA engineering stack on local **Dual Tesla V100 32GB** hardware.

---

## Hardware & Software Baseline
- **AI Server:** HP Z8 G4 — Dual Xeon Gold 6140 (72 threads, AVX-512), 64 GB DDR4-2666 ECC (61 GiB usable), 2× Tesla V100 PCIe 32GB (64 GB HBM2, `sm_70`, no NVLink), 233 GB NVMe.
- **Inference Engine:** [Niko1221/Strata](https://github.com/Niko1221/Strata) compiled locally with CUDA 12 (`CMAKE_CUDA_ARCHITECTURES=70`, `g++-13`, Release) serving `Qwen3.8-Flash-Next (UD-Q4_K_XL)` (176B total / 6B active, hybrid GDN + sparse attention, `98,304` ctx, `--kv int8`, `--parallel 2`, GPU `strata-vision`).
- **Agent Harness:** Hermes Agent (`default` + dedicated `solidcraft` profile) driving SolidCraft `v0.49.1` (`solidcraft-cli`, 903 commands).

---

## Trial 1 — Forensic Audit of Baseline Failures (Why Stock Autonomous CAD Failed)

Auditing Hermes's SQLite `state.db` revealed **5 silent killers** before we wrote a single new tool:

1. **Deferred `tool_call` proxy had an 86.7% failure rate (137/158 failed):**
   - **Fix:** `tools.tool_search.enabled: "off"` in `config.yaml`.
   - **Result:** Dropped to **0.0% `tool_call` errors** immediately.
2. **Poison `reasoning_budget_tokens: 1024` in provider `extra_body`:**
   - Choked model thinking to a ~135-token median, causing half-baked 3D math.
   - **Fix:** Removed from `extra_body`; set `agent.reasoning_effort: medium` and tuned server-side `reasoning_budget_tokens`.
   - **Later refinement:** `12288` was *too* loose (allowed occasional 5,300–6,170 token / 54–93s rambling turns on routine steps). **`4096`** is the practical sweet spot for agentic tool loops.
3. **Large hand-written MCP `batch` calls (>12 commands) failed 50.6% of the time:**
   - 1,700 of 3,777 commands (**45.0%**) were lost to all-or-nothing rollbacks, and GUI sessions hit SolidCraft's 35-operation-per-message limit.
   - **Fix:** 2-Tier architecture — **Code-first Python SDK (`solidcraft_builder.py`) + headless `solidcraft-cli run`** for complex builds; small MCP `batch` (≤3 cmds, 1 for fillets/booleans) for live GUI tweaks only.
4. **Vision was disabled (`supports_vision: false`):**
   - 81.5% of rendered screenshots were never inspected.
   - **Fix:** Enabled `model.supports_vision: true` and `agent.image_input_mode: native`.
5. **Stale CAD skills (`v0.21.0` vs installed `solidcraft-cli 0.49.1`):**
   - Synced all 6 official v0.49.1 skills (`modeling`, `editing`, `checking`, `mechanisms`, `sheetmetal`, `reporting`) and automated future syncing in the update script.

---

## Trial 2 — Skipping SolidCraft's Embedded ACP Chat → Hermes Desktop

**What broke in SolidCraft's embedded ACP chat:**
- `write_file` stalled for **300 seconds** waiting on manual approval dialogs in autonomous mode.
- Detached async `delegate_task` wakeups broke UI turn ownership (`"Ended after 8 min"` and unresponsive Stop button).
- **Ghost multiplexed session collision:** With `gateway.multiplex_profiles: true`, an abandoned `solidcraft` ACP session kept running inside the same gateway PID alongside a new Hermes Desktop `default` session — both simultaneously overwriting the same project files!

**Fix:**
- Run long CAD sessions in **Hermes Desktop** on the dedicated **`solidcraft` (`S`) profile** (bottom-left sidebar switcher) — cuts **~15k baseline tokens/turn** (only 8 CAD skills + `solidcraft-live` MCP + lean `SOUL.md` instead of 78 skills + 4 MCPs).
- Added a clean `terminal` heredoc fallback rule in `SOUL.md` for ACP environments.

---

## Trial 3 — Building the Code-First CAD & FEA Toolkit (Real API Traps Caught)

Building and live-testing against `solidcraft-cli 0.49.1` uncovered critical API traps that we encoded into our Python tools so the LLM never wastes turns rediscovering them:

- `solidcraft-cli commands` bulk list omits full JSON schemas → `solidcraft_builder.py` lazily fetches & caches `solidcraft-cli commands --id <cmd>` per command ID.
- `sketch.create` requires nested `plane={"origin": ..., "x_dir": ..., "y_dir": ...}` → auto-normalized in `solidcraft_builder.py`.
- `sketch.spline.fit_point` creates curve `s1` (not `sp1`) with `dof > 0` → `swept_tube()` auto-fixes `s1` with `sketch.constraint.fix` for `DOF=0`.
- `solidcraft-cli run --results --json-out` outputs a JSON **list** directly (not `{"results": [...]}`).
- `document.inspect` uses `volume_mm3` and `params`; `inspect.clearance` uses `near` and `overlaps`.
- `edit.checkpoint` takes 0 params; `solid.torus` has no `axis` param; `solid.extrude` `direction` is enum `"positive"`/`"negative"`, not a vector.
- **Incremental stage loading (`--in prev_stage.sc`):** `solidcraft-cli run` natively accepts `--in <design.sc>`. Passing `--in` + `--fast` in `solidcraft_oracle.py` cuts late-stage inner fix loops from **~20–25s → ~2–4s** (5×–10× faster).
- **3D Structural FEA (`solidcraft_fea.py`):** Built on official **Gmsh (`4.15.2`) + `scikit-fem` (`12.0.2`) + `scipy`**, exporting SolidCraft `.sc` → `.step` → 3D tetrahedral mesh → linear static elasticity. Automatically flags disconnected/floating solids (`connected_components > 1`), computes `max_displacement_mm`, `von_mises_mpa`, `safety_factor_p99`, `hotspot_xyz_mm`, and renders a 2-view `fea_stress.png`.

---

## Trial 4 — Forensic Audit of Mid-Run Slowdowns (The 5 Hidden Time Sinks)

A 12-hour `state.db` audit caught 5 non-obvious workflow bugs that made local runs feel 10× slower than cloud Codex despite fast GPU tok/s:

1. **87 minutes wasted on 256 one-by-one MCP screenshots:**
   - 4 subagents spent 15–25 min each calling `mcp__solidcraft_live__screenshot` 24 times sequentially.
   - **Fix:** Banned one-by-one MCP screenshot loops in `solidcraft-superhero/SKILL.md`; use a Python loop calling `solidcraft-cli snapshot` (**~8 seconds** for 24 frames).
2. **Runaway subagents (`delegation.max_iterations: 250`):**
   - One subagent looped for **362 messages / 44.9 min**; research subagents burned **85–155 messages / 17–22 min** downloading 14 drawings sequentially.
   - **Fix:** Capped `delegation.max_iterations: 30`, added a strict research budget (**≤12 tool calls, up to 5 key reference drawings, ≤120-line spec, <4 min**), and a **max 3 oracle loops / <8 min per stage** cap.
3. **71 context compactions in 12 hours (causing 32-second Strata re-prefills):**
   - Caused by `inspect_design` returning 18–43 KB/call (65% error rate) and `compression.threshold: 0.65` + `idle_compact_after_seconds: 300` + `auxiliary.compression.reasoning_effort: low`.
   - **Fix:** Raised `threshold: 0.82`, `proactive_prune_tokens: 65536`, `idle_compact_after_seconds: 1800`, set `auxiliary.compression.reasoning_effort: none`, and banned raw `inspect_design` in favor of `<1.5 KB` `STATE.json`.
4. **Silent `qwen3.5:9b` vision critic on `default` profile:**
   - Every `vision_analyze` call on `default` was silently hitting tiny `qwen3.5:9b`, which approved obvious 3D kinematic bugs (both opposing arms pointing forward, rotating arm inboard of drive element).
   - **Fix:** Switched `auxiliary.vision` and `auxiliary.review` to `openai-api / gpt-6.1-sol` (~`$0.01`/image) + added **Python Kinematic & 360° Swept-Clearance Assertions** (`solidcraft-fea-and-loadpath/SKILL.md` §1b).
5. **Missing inline pictures in Hermes Desktop chat:**
   - Hermes Desktop does **not** render local Markdown `![alt](/path)` — local images only render inline via `MEDIA:/absolute/path/to/file.png` on its own line.
   - **Fix:** Baked mandatory `MEDIA:` emission into `SOUL.md`, `solidcraft-superhero`, and `solidcraft-fea-and-loadpath`.

---

## Trial 5 — Upgrading Strata `v0.2.19 (0.1.65)` → `v0.2.22 (0.1.69)` on Dual V100s

Upgrading Strata and rebuilding `strata` for CUDA 12 `sm_70` (`g++-13`, Release) unlocked fused `EXP_MODE=8` for all 13 Unsloth expert pairs on Volta `sm_70` + AVX-512 cold-expert kernels on Dual Xeon Gold 6140:

| Benchmark (`Qwen3.8-Flash-Next UD-Q4_K_XL`, `temp=0.0`) | `0.1.65` | `0.1.69` | Delta |
|---|---|---|---|
| Cold run (engine decode) | `37.1 tok/s` (`6.40s` wall) | **`67.5 tok/s`** (`4.39s` wall) | **+81.9% decode** |
| Warm run (engine decode) | `62.2 tok/s` (`2.76s` wall) | **`68.3 tok/s`** (peak `70.7`) | **+9.8% decode** |
| Live agentic turns (verified in `journalctl`) | `55–93 tok/s` | **`65–117.1 tok/s`** | Peak **117.1 tok/s** |
| Expert cache hit rate | `97.1%–99.0%` | **`98.9%–99.4%`** | Higher hit rate |

---

## Hardware Tuning Physics (Verified Live)

- **Context window (`98,304` vs `131,072`):** At `98,304` (`--kv int8`, `--parallel 2`), V100 VRAM stays ~99% utilized with hot MoE experts (98.9–99.4% hit rate) and host RAM sits at 42/61 GiB. With `threshold: 0.82`, compaction fires at ~80.6k tokens — right before multi-constraint 3D context rot. `131,072` is the upper ceiling; switching to the lean `solidcraft` profile gives +15k free usable tokens inside 98k with zero VRAM/speed penalty.
- **V100 Power Limit (`140W` vs `170W`):** Decode is **HBM2 memory-bandwidth bound** (`877 MHz`, 900 GB/s stays locked even down to 115W). `140W` delivers ~98–100% of `170W` decode speed with far lower thermals and less thermal-guard throttling.
- **Why no other local model beats `Qwen3.8-Flash-Next (UD-Q4_K_XL)` on 2× V100 + 64 GB RAM:** Larger MoEs (`GLM-5.3-Flash` 321B at 120–195 GB, `DeepSeek-V4.1-Flash` 552B) spill heavily to disk/RAM (4–12 tok/s); smaller models (`GLM-4.7-Flash` 30B, `GLM-4.5-Air` 106B) are substantially weaker at parametric CAD.

---

## The Winning Hybrid Pattern (Near-Zero Cloud Spend)

- **Local-First (`~$0.05–$0.50` / complex assembly):** Local Strata `0.1.69` drives 95%+ of all tokens (`$0`), with `gpt-6.1-sol` used strictly for milestone visual QA (`vision_analyze`) and 2-strike deadlocks.
- **Manager-Worker (`~$0.50–$1.20` / complex assembly, ~10× faster wall-clock):** Frontier `gpt-6.1-sol` acts as top-level **Manager** (architecture, `COORDINATE_TABLE.json`, kinematic gates, visual sign-off) while offloading all heavy Python CAD coding, `--fast` oracle loops, and FEA runs to **local Strata subagent workers (`$0`)** that cannot self-certify stages (`GATES.json`).

---

## 10. Nov 26–27 Breakthrough: Why `mtb_29er_v2` Succeeded 100% Locally (Zero GPT Calls)

After applying the full stack, the `mtb_29er_v2` wheel & frame stages jumped dramatically in geometric precision and speed **using 100% local inference (`qwen3.8-flash-next` on Dual V100 + `qwen3.5:9b` on local RTX 4070S, `$0.00` cloud spend)**:

| Metric | Monolithic `v1` (`Nov 26 AM`) | Test-Driven Modular `v2` (`Nov 27 AM`) |
|---|---|---|
| **Median Turn Latency** | `16.9s` (spiking to `161s` during compression loops) | **`8.4s`** (`93.4%` KV cache hit rate) |
| **Part File Size & Rebuild Time** | `21.8 MB` monolith (`323` cmds, `60–90s`/edit) | **`620–836 KB`** per-part `.sc` (`~2–4s` rebuild) |
| **Compression Thrashing** | `protect_last_n: 12` reclaimed only `1.4k–6.9k` tok/compression | **`protect_last_n: 6` reclaimed `23.8k` tok/compression** |
| **Loop Safety** | `tool_loop_guardrails.hard_stop_enabled: false` (endless loops) | **`hard_stop_enabled: true`** (`exact_failure: 4`, `no_progress: 4`) |
| **GPU Division of Labor** | V100 interrupted by auxiliary approvals | **40 V100 calls + 18 local RTX 4070S (`qwen3.5:9b`) calls**, zero V100 slot theft |

### The 5 Compounding Reasons a Local 4-Bit MoE Beat Earlier Monolithic Runs
1. **Test-Driven CAD (`assert_s1.py` / `assert_s2.py` + `GATES.json`):** Running `solidcraft-cli eval` and mathematically asserting every body's exact bounding box, lateral Y-span, axle collar radius, and merged spoke count within `±0.5 mm` turns CAD into a deterministic compiler/test loop.
2. **True Part Isolation (`620 KB` Per-Part Files + Frozen `.step` Assembly):** Iterating on a `620 KB` wheel file takes seconds instead of replaying a `21 MB` 323-command timeline.
3. **Single Source of Truth (`geometry.py` → `COORDINATE_TABLE.json` → `blueprint_v2.png`):** Every part builder imports coordinates from one Python math file — eliminating hand-transcribed coordinate drift.
4. **Tiered Self-Learned Memory (`solidcraft_lessons.md` + `lessons_*.md`):** Distilled SolidCraft traps (`solid.cylinder` `base` vs `center`, `XZ` extrude `-Y`, `solid.pattern.circular` separate-body merging) prevented repeating earlier API mistakes.
5. **Eliminating Compression Death-Spirals & Silent Loops:** Lowering `protect_last_n` from `12 → 6`, enabling `tool_loop_guardrails.hard_stop_enabled: true`, offloading `approval` / housekeeping to the local RTX 4070S (`qwen3.5:9b` pinned at 32K ctx `Forever`), and enforcing live SolidCraft GUI + `MEDIA:` checkpoints kept context clean and fast.
