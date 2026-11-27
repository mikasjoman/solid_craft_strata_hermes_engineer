#!/usr/bin/env python3
"""Iterative Mechanical Engineering Project & Live Web Dashboard Manager (100% General).

Enforces versioned, clone-and-improve CAD iterations (`iter_01 -> iter_02 -> ...`)
with a rigid `PLAN.md` template, shared `research/`, selective part cloning/discarding,
and a live auto-refreshing `web/index.html` showing stage boards, loop logs, part
triage (`KEEP / IMPROVE / DISCARD`), and side-by-side visual evolution across iterations.

Quick Start:
  # 1. Initialize a new project (creates research/, web/, iter_01/PLAN.md)
  python3 ~/.hermes/profiles/solidcraft/tools/iter_manager.py init ./my_project \\
    --title "Precision Planetary Gearbox" \\
    --stages "P0:Research & Blueprint,S1:Housing,S2:Gear Train,ASM:Master Assembly,FEA:Structural FEA,GPT:Frontier Audit,USR:User Feedback"

  # 2. Update live web dashboard DURING an iteration (after every stage)
  python3 ~/.hermes/profiles/solidcraft/tools/iter_manager.py update ./my_project \\
    --iter iter_01 \\
    --stage S1 done "Oracle PASS, 0 clashes, frozen to s1_housing.step" \\
    --loop Build STRATA "Built Housing body, verified bbox/volume" PASS \\
    --media iter_01/assembly/contact_sheet.png "S1 Housing Contact Sheet"

  # 3. Record GPT triage & hero image at end of iteration
  python3 ~/.hermes/profiles/solidcraft/tools/iter_manager.py update ./my_project \\
    --iter iter_01 \\
    --triage Housing KEEP "Clean fit, SF=3.1" \\
    --triage Carrier IMPROVE "Add fillet at pin root" \\
    --triage CoverPlate DISCARD "Thin wall warps, redesign" \\
    --hero iter_01/assembly/hero.png

  # 4. Clone iter_01 -> iter_02 (keeps working parts, discards bad ones, pre-fills PLAN.md)
  python3 ~/.hermes/profiles/solidcraft/tools/iter_manager.py clone ./my_project \\
    --from iter_01 --to iter_02 --discard CoverPlate
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

TEMPLATE_PATH = Path(
    os.path.expanduser("~/.hermes/profiles/solidcraft/templates/ENGINEERING_PLAN_TEMPLATE.md")
)

DEFAULT_STAGES = [
    ("P0", "Research, COORDINATE_TABLE.json, GATES.json & 2D Blueprint"),
    ("S1", "Core Structure (build, verify, freeze .step)"),
    ("S2", "Primary Sub-assemblies (build, verify, freeze .step)"),
    ("ASM", "Master Assembly via insert_step + Close-Up Fit Checks"),
    ("FEA", "Pre-FEA cad_math + 3D FEA (stress PNG + deform GIF)"),
    ("GPT", "Frontier GPT Audit (KEEP / IMPROVE / DISCARD triage)"),
    ("USR", "User Feedback Gate before locking iteration"),
]

PH_COLORS = {
    "Plan": "#4cc2ff",
    "Design": "#b08cff",
    "Build": "#7ee787",
    "Verify": "#d2a8ff",
    "Fix": "#ffa657",
    "FEA": "#f778ba",
    "Review": "#ff7ab8",
    "User": "#f0883e",
}


def _now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _now_hms() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def _load_state(project: Path) -> Dict[str, Any]:
    sp = project / "web" / "state.json"
    if not sp.exists():
        raise FileNotFoundError(f"Missing {sp} — run `iter_manager.py init` first.")
    return json.loads(sp.read_text(encoding="utf-8"))


def _save_state(project: Path, state: Dict[str, Any]) -> None:
    web_dir = project / "web"
    web_dir.mkdir(parents=True, exist_ok=True)
    (web_dir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    _render_html(project, state)


def _stage_list_from_arg(stages_arg: Optional[str]) -> List[Dict[str, str]]:
    if not stages_arg:
        return [{"key": k, "desc": d, "state": "gate", "note": "pending"} for k, d in DEFAULT_STAGES]
    out = []
    for item in stages_arg.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            k, d = item.split(":", 1)
            out.append({"key": k.strip(), "desc": d.strip(), "state": "gate", "note": "pending"})
        else:
            out.append({"key": item, "desc": item, "state": "gate", "note": "pending"})
    return out


def _render_plan(title: str, iteration: str, parent: str) -> str:
    if TEMPLATE_PATH.exists():
        tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    else:
        tpl = "# Engineering Plan — {{PROJECT_TITLE}} ({{ITERATION}})\n"
    prev_gpt = f"../{parent}/GPT_REVIEW.md" if parent != "none" else "none (baseline iteration)"
    prev_usr = f"../{parent}/USER_FEEDBACK.md" if parent != "none" else "none (baseline iteration)"
    return (
        tpl.replace("{{PROJECT_TITLE}}", title)
        .replace("{{ITERATION}}", iteration)
        .replace("{{PARENT_ITERATION}}", parent)
        .replace("{{PREV_GPT_REVIEW}}", prev_gpt)
        .replace("{{PREV_USER_FEEDBACK}}", prev_usr)
    )


def _copy_asset(project: Path, iteration: str, src_path: str) -> str:
    """Copy media asset into web/assets/<iteration>/ so HTML links stay portable."""
    src = Path(src_path)
    if not src.is_absolute():
        cand = project / src
        if cand.exists():
            src = cand
    if not src.exists():
        return src_path
    dst_dir = project / "web" / "assets" / iteration
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    return f"assets/{iteration}/{dst.name}"


def _render_html(project: Path, state: Dict[str, Any]) -> Path:
    title = html.escape(state.get("title", project.name))
    iters: List[Dict[str, Any]] = state.get("iterations", [])
    active = iters[-1] if iters else {}
    total_loops = sum(len(it.get("loop", [])) for it in iters)
    act_stages = active.get("stages", [])
    done_cnt = sum(1 for s in act_stages if s.get("state") == "done")

    # 1. Evolution Strip (side-by-side hero per iteration)
    evo_cards = []
    for it in iters:
        it_name = html.escape(it["name"])
        hero = it.get("hero")
        summary = html.escape(it.get("summary", ""))
        st_done = sum(1 for s in it.get("stages", []) if s.get("state") == "done")
        st_tot = len(it.get("stages", []))
        if hero:
            img_html = f'<a href="{html.escape(hero)}"><img src="{html.escape(hero)}" loading="lazy"></a>'
        else:
            img_html = '<div class="placeholder">In progress — hero render pending</div>'
        evo_cards.append(
            f'<div class="evo-card"><div class="evo-head"><b>{it_name}</b>'
            f'<span class="mut">{st_done}/{st_tot} stages</span></div>'
            f'{img_html}<div class="evo-sum">{summary or "Active engineering iteration"}</div></div>'
        )
    evo_html = (
        '<h2>Iteration Evolution (Step-by-Step Progress)</h2>'
        f'<div class="evo-grid">{"".join(evo_cards)}</div>'
    ) if evo_cards else ""

    # 2. Per-iteration sections (newest first so current work is right at top)
    iter_sections = []
    badge_map = {
        "done": ("st-done", "&#9989;"),
        "failed": ("st-failed", "&#10060;"),
        "gate": ("st-gate", "&#128275;"),
    }
    triage_cls = {"KEEP": "tr-keep", "IMPROVE": "tr-imp", "DISCARD": "tr-disc", "NEW": "tr-new"}

    for it in reversed(iters):
        it_name = html.escape(it["name"])
        parent = html.escape(it.get("parent", "none"))
        created = html.escape(it.get("created_at", ""))

        # Stage board
        s_rows = []
        for s in it.get("stages", []):
            cls, ico = badge_map.get(s.get("state", "gate"), ("st-gate", "&#128275;"))
            s_rows.append(
                f'<tr><td><b>{html.escape(s["key"])}</b></td>'
                f'<td>{html.escape(s["desc"])}</td>'
                f'<td class="{cls}">{ico} {html.escape(s.get("note", ""))}</td></tr>'
            )
        stage_tbl = (
            '<h3>Stage Board</h3>'
            '<table><tr><th>Stage</th><th>Definition of Done</th><th>Status</th></tr>'
            f'{"".join(s_rows)}</table>'
        )

        # Part Triage table (KEEP / IMPROVE / DISCARD)
        triage = it.get("triage", [])
        if triage:
            t_rows = []
            for tr in triage:
                act = tr.get("action", "KEEP").upper()
                t_rows.append(
                    f'<tr><td><b>{html.escape(tr["part"])}</b></td>'
                    f'<td><span class="ph {triage_cls.get(act, "tr-new")}">{html.escape(act)}</span></td>'
                    f'<td>{html.escape(tr.get("reason", ""))}</td></tr>'
                )
            triage_html = (
                '<h3>GPT / User Part Triage (Carry-Forward Decisions)</h3>'
                '<table><tr><th>Part / Subsystem</th><th>Decision</th><th>Rationale</th></tr>'
                f'{"".join(t_rows)}</table>'
            )
        else:
            triage_html = ""

        # Media gallery
        media = it.get("media", [])
        if media:
            figs = []
            for m in media:
                src = html.escape(m["src"])
                cap = html.escape(m.get("caption", ""))
                if src.lower().endswith((".mp4", ".webm")):
                    figs.append(
                        f'<figure><video src="{src}" controls muted></video>'
                        f'<figcaption>{cap}</figcaption></figure>'
                    )
                else:
                    figs.append(
                        f'<figure><a href="{src}"><img src="{src}" loading="lazy"></a>'
                        f'<figcaption>{cap}</figcaption></figure>'
                    )
            media_html = f'<h3>Renders, Close-Ups &amp; FEA Media</h3><div class="media-grid">{"".join(figs)}</div>'
        else:
            media_html = ""

        # Loop log
        l_rows = []
        for idx, row in enumerate(it.get("loop", []), 1):
            ph = row.get("phase", "Build")
            col = PH_COLORS.get(ph, "#8496a8")
            l_rows.append(
                f'<tr><td>{idx}</td><td>{html.escape(row.get("time", ""))}</td>'
                f'<td><span class="ph" style="background:{col}22;color:{col};border:1px solid {col}55">'
                f'{html.escape(ph)}</span></td>'
                f'<td>{html.escape(row.get("owner", ""))}</td>'
                f'<td>{html.escape(row.get("evidence", ""))}</td>'
                f'<td class="vd">{html.escape(row.get("verdict", ""))}</td></tr>'
            )
        loop_html = (
            '<h3>Engineering Loop Log</h3>'
            '<table><tr><th>#</th><th>Time</th><th>Phase</th><th>Owner</th><th>Action / Evidence</th><th>Verdict</th></tr>'
            f'{"".join(l_rows)}</table>'
        ) if l_rows else ""

        # Notes (GPT summary + User feedback)
        notes_html = ""
        if it.get("gpt_summary"):
            notes_html += f'<div class="note"><b>Frontier GPT Audit:</b> {html.escape(it["gpt_summary"])}</div>'
        if it.get("user_feedback"):
            notes_html += f'<div class="note note-usr"><b>User Feedback:</b> {html.escape(it["user_feedback"])}</div>'

        iter_sections.append(
            f'<section class="iter-box">'
            f'<h2>{it_name} <span class="sub-tag">cloned from {parent} &middot; {created}</span></h2>'
            f'{notes_html}{stage_tbl}{triage_html}{media_html}{loop_html}'
            f'</section>'
        )

    doc = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="15">
<title>{title} — Iterative Engineering Dashboard</title><style>
:root{{--bg:#0b0f14;--panel:#121923;--border:#22303f;--fg:#dbe4ee;--mut:#8496a8;--acc:#4cc2ff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 system-ui,sans-serif}}
header{{padding:26px 24px 10px;max-width:1220px;margin:0 auto}}
h1{{font-size:26px;margin:0 0 4px}}.sub,.mut{{color:var(--mut)}}
main{{max-width:1220px;margin:0 auto;padding:8px 24px 80px}}
h2{{font-size:19px;margin:26px 0 10px;border-left:4px solid var(--acc);padding-left:10px}}
h3{{font-size:14px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:18px 0 8px}}
.sub-tag{{font-size:12px;font-weight:400;color:var(--mut);margin-left:8px}}
.stat{{display:inline-block;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:7px 14px;margin:8px 8px 0 0}}
.stat b{{color:var(--acc)}}
.evo-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin:10px 0 22px}}
.evo-card{{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:10px}}
.evo-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
.evo-card img{{width:100%;height:190px;object-fit:cover;border-radius:6px;border:1px solid var(--border)}}
.placeholder{{height:190px;display:flex;align-items:center;justify-content:center;color:var(--mut);border:1px dashed var(--border);border-radius:6px;font-size:13px}}
.evo-sum{{margin-top:8px;font-size:13px;color:var(--mut)}}
.iter-box{{margin-top:24px;padding-top:6px;border-top:1px solid var(--border)}}
table{{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--border);margin-bottom:10px}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top;text-align:left}}
th{{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.05em}}
.ph{{padding:2px 8px;border-radius:99px;font-size:12px;font-weight:600;display:inline-block}}
.st-done{{color:#3fb96f;font-weight:600}}.st-failed{{color:#e05c5c;font-weight:600}}.st-gate{{color:#e0a63c;font-weight:600}}
.tr-keep{{background:#3fb96f22;color:#3fb96f;border:1px solid #3fb96f55}}
.tr-imp{{background:#4cc2ff22;color:#4cc2ff;border:1px solid #4cc2ff55}}
.tr-disc{{background:#e05c5c22;color:#e05c5c;border:1px solid #e05c5c55}}
.tr-new{{background:#b08cff22;color:#b08cff;border:1px solid #b08cff55}}
.vd{{max-width:260px}}
.media-grid{{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 12px}}
figure{{margin:0;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:8px;max-width:440px}}
figure img,figure video{{max-width:100%;max-height:280px;border-radius:4px;display:block}}
figcaption{{font-size:12px;color:var(--mut);margin-top:6px}}
.note{{background:#142233;border:1px solid #2b4a6f;border-radius:8px;padding:10px 14px;margin:8px 0}}
.note-usr{{background:#241c10;border-color:#7a5a20;color:#ffe4b5}}
</style></head><body>
<header>
<h1>{title}</h1>
<div class="sub">Rigid Iterative Engineering Loop: Plan &rarr; Build (Frozen .step) &rarr; Verify &rarr; GPT Audit &rarr; User Feedback &rarr; Clone &amp; Improve. Updated {html.escape(_now_iso())}.</div>
<div>
  <span class="stat">iterations <b>{len(iters)}</b></span>
  <span class="stat">active <b>{html.escape(active.get("name", "-"))}</b></span>
  <span class="stat">stages done <b>{done_cnt}/{len(act_stages)}</b></span>
  <span class="stat">loop entries <b>{total_loops}</b></span>
</div>
</header>
<main>
{evo_html}
{"".join(iter_sections)}
</main></body></html>
"""
    out = project / "web" / "index.html"
    out.write_text(doc, encoding="utf-8")
    return out


def cmd_init(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    for d in (
        project / "research" / "references",
        project / "web" / "assets",
        project / "iter_01" / "parts",
        project / "iter_01" / "assembly",
    ):
        d.mkdir(parents=True, exist_ok=True)

    plan_path = project / "iter_01" / "PLAN.md"
    if not plan_path.exists() or args.force:
        plan_path.write_text(_render_plan(args.title, "iter_01", "none"), encoding="utf-8")

    state = {
        "title": args.title,
        "created_at": _now_iso(),
        "iterations": [
            {
                "name": "iter_01",
                "parent": "none",
                "created_at": _now_iso(),
                "summary": "Baseline v1 iteration",
                "hero": None,
                "gpt_summary": "",
                "user_feedback": "",
                "stages": _stage_list_from_arg(args.stages),
                "triage": [],
                "media": [],
                "loop": [],
            }
        ],
    }
    _save_state(project, state)
    print(json.dumps({
        "ok": True,
        "project": str(project),
        "plan": str(plan_path),
        "web": str(project / "web" / "index.html"),
    }, indent=2))
    return 0


def cmd_clone(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    state = _load_state(project)
    src_dir = project / args.src_iter
    dst_dir = project / args.dst_iter
    if not src_dir.exists():
        raise FileNotFoundError(f"Source iteration not found: {src_dir}")
    if dst_dir.exists() and not args.force:
        raise FileExistsError(f"Target iteration already exists: {dst_dir} (pass --force to overwrite)")

    discard_set = {x.strip().lower() for x in (args.discard or "").split(",") if x.strip()}
    (dst_dir / "parts").mkdir(parents=True, exist_ok=True)
    (dst_dir / "assembly").mkdir(parents=True, exist_ok=True)

    kept_files: List[str] = []
    discarded_files: List[str] = []

    # Copy top-level spec/gate files
    for fname in ("COORDINATE_TABLE.json", "GATES.json", "DESIGN_SPEC.md", "geometry.py"):
        f = src_dir / fname
        if f.exists():
            shutil.copy2(f, dst_dir / fname)
            kept_files.append(fname)

    # Copy parts/ skipping discarded parts
    src_parts = src_dir / "parts"
    if src_parts.exists():
        for item in sorted(src_parts.iterdir()):
            low = item.stem.lower()
            if any(d in low for d in discard_set):
                discarded_files.append(item.name)
                continue
            dst_item = dst_dir / "parts" / item.name
            if item.is_dir():
                shutil.copytree(item, dst_item, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst_item)
            kept_files.append(f"parts/{item.name}")

    # Create fresh PLAN.md from rigid template
    plan_txt = _render_plan(state["title"], args.dst_iter, args.src_iter)
    (dst_dir / "PLAN.md").write_text(plan_txt, encoding="utf-8")

    # Inherit stage definitions reset to pending
    src_iter_obj = next((it for it in state["iterations"] if it["name"] == args.src_iter), None)
    base_stages = (
        [{"key": s["key"], "desc": s["desc"], "state": "gate", "note": "pending"} for s in src_iter_obj["stages"]]
        if src_iter_obj
        else _stage_list_from_arg(None)
    )
    state["iterations"] = [it for it in state["iterations"] if it["name"] != args.dst_iter]
    state["iterations"].append({
        "name": args.dst_iter,
        "parent": args.src_iter,
        "created_at": _now_iso(),
        "summary": args.summary or f"Cloned from {args.src_iter}; discarded: {', '.join(sorted(discard_set)) or 'none'}",
        "hero": None,
        "gpt_summary": "",
        "user_feedback": "",
        "stages": base_stages,
        "triage": [],
        "media": [],
        "loop": [],
    })
    _save_state(project, state)
    print(json.dumps({
        "ok": True,
        "from": args.src_iter,
        "to": args.dst_iter,
        "kept_count": len(kept_files),
        "discarded": discarded_files,
        "plan": str(dst_dir / "PLAN.md"),
        "web": str(project / "web" / "index.html"),
    }, indent=2))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    state = _load_state(project)
    it = next((x for x in state["iterations"] if x["name"] == args.iter_name), None)
    if it is None:
        raise ValueError(f"Unknown iteration `{args.iter_name}`. Have: {[x['name'] for x in state['iterations']]}")

    for key, st, note in args.stage or []:
        matched = False
        for s in it["stages"]:
            if s["key"] == key:
                s["state"] = st
                s["note"] = note
                matched = True
                break
        if not matched:
            it["stages"].append({"key": key, "desc": key, "state": st, "note": note})

    for phase, owner, evidence, verdict in args.loop or []:
        it["loop"].append({
            "time": _now_hms(),
            "phase": phase,
            "owner": owner,
            "evidence": evidence,
            "verdict": verdict,
        })

    for part, action, reason in args.triage or []:
        act = action.upper()
        it["triage"] = [t for t in it["triage"] if t["part"] != part]
        it["triage"].append({"part": part, "action": act, "reason": reason})

    for path, caption in args.media or []:
        rel = _copy_asset(project, args.iter_name, path)
        it["media"].append({"src": rel, "caption": caption})

    if args.hero:
        it["hero"] = _copy_asset(project, args.iter_name, args.hero)
    if args.summary:
        it["summary"] = args.summary
    if args.gpt_summary:
        it["gpt_summary"] = args.gpt_summary
    if args.user_feedback:
        it["user_feedback"] = args.user_feedback
        fb_path = project / args.iter_name / "USER_FEEDBACK.md"
        fb_path.write_text(f"# User Feedback — {args.iter_name}\n\n{args.user_feedback}\n", encoding="utf-8")

    _save_state(project, state)
    print(json.dumps({"ok": True, "iteration": args.iter_name, "web": str(project / "web" / "index.html")}, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Iterative CAD Project & Live Web Dashboard Manager")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Initialize project folder, iter_01/PLAN.md, and web/index.html")
    p_init.add_argument("project", help="Project root directory")
    p_init.add_argument("--title", required=True, help="Human-readable project title")
    p_init.add_argument("--stages", default=None, help="Comma-separated KEY:Description stages")
    p_init.add_argument("--force", action="store_true")

    p_clone = sub.add_parser("clone", help="Clone working parts & specs into next iteration (skipping discarded parts)")
    p_clone.add_argument("project", help="Project root directory")
    p_clone.add_argument("--from", dest="src_iter", required=True, help="Source iteration (e.g. iter_01)")
    p_clone.add_argument("--to", dest="dst_iter", required=True, help="Target iteration (e.g. iter_02)")
    p_clone.add_argument("--discard", default="", help="Comma-separated part name substrings to discard")
    p_clone.add_argument("--summary", default="", help="Short summary of this iteration's focus")
    p_clone.add_argument("--force", action="store_true")

    p_upd = sub.add_parser("update", help="Update live web dashboard (stages, loop log, triage, media, hero, feedback)")
    p_upd.add_argument("project", help="Project root directory")
    p_upd.add_argument("--iter", dest="iter_name", required=True, help="Iteration name (e.g. iter_01)")
    p_upd.add_argument("--stage", nargs=3, action="append", metavar=("KEY", "STATE", "NOTE"))
    p_upd.add_argument("--loop", nargs=4, action="append", metavar=("PHASE", "OWNER", "EVIDENCE", "VERDICT"))
    p_upd.add_argument("--triage", nargs=3, action="append", metavar=("PART", "ACTION", "REASON"))
    p_upd.add_argument("--media", nargs=2, action="append", metavar=("PATH", "CAPTION"))
    p_upd.add_argument("--hero", default=None, help="Hero render PNG for Iteration Evolution card")
    p_upd.add_argument("--summary", default=None, help="Iteration summary line")
    p_upd.add_argument("--gpt-summary", default=None, help="Frontier GPT audit summary")
    p_upd.add_argument("--user-feedback", default=None, help="User feedback recorded before closing iteration")

    args = ap.parse_args()
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "clone":
        return cmd_clone(args)
    if args.cmd == "update":
        return cmd_update(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
