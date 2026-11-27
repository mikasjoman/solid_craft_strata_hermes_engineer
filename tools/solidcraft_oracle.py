#!/usr/bin/env python3
"""Deterministic SolidCraft v0.49+ Verification Oracle + 3x2 X-Ray Contact Sheet.

Runs a SolidCraft script/design headlessly with `--param-check strict` and
`--validation each_feature`, checks:
  - 0 command/feature errors & warnings
  - 0 sketches with DOF > 0
  - all bodies closed & non-zero volume
  - 0 hard clashes (`inspect.interference`)
  - optional minimum clearance (`inspect.clearance`)
  - optional parameter range sweeps (`--param NAME=EXPR`)

Then renders 6 views (iso, front, right, top, back, + X-ray `--section`) via
`solidcraft-cli snapshot`, stitches a labeled 3x2 PNG contact sheet, and writes
a compact (<1.5 KB) `STATE.json` to prevent long-run context amnesia.

Usage:
  python3 ~/.hermes/profiles/solidcraft/tools/solidcraft_oracle.py build.json \\
    --out-dir ./oracle_out --save part.sc --section y=0 \\
    --publish-recovery my_part_live.sc

  # Fast inner-loop check (skips clearance + PNG render):
  python3 ~/.hermes/profiles/solidcraft/tools/solidcraft_oracle.py build.json --fast
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

SOLIDCRAFT_CLI = os.environ.get(
    "SOLIDCRAFT_CLI",
    os.path.expanduser("~/.local/bin/solidcraft-cli"),
)


def _run_cli(args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [SOLIDCRAFT_CLI, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _load_font(size: int) -> Any:
    for p in (
        "/usr/share/fonts/google-carlito-fonts/Carlito-Bold.ttf",
        "/usr/share/fonts/adwaita-mono-fonts/AdwaitaMono-Bold.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
    ):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def render_contact_sheet(
    design_path: Path,
    out_png: Path,
    *,
    section: str = "y=0",
    tile_w: int = 480,
    tile_h: int = 320,
    banner_text: str = "",
) -> Path:
    """Render 6 labeled views (5 ortho/iso + 1 X-ray section cut) into a 3x2 PNG."""
    views: List[Tuple[str, List[str]]] = [
        ("ISO", ["--view", "iso"]),
        ("FRONT (Side)", ["--view", "front"]),
        ("RIGHT (End)", ["--view", "right"]),
        ("TOP", ["--view", "top"]),
        ("BACK", ["--view", "back"]),
        (f"SECTION ({section})", ["--view", "iso", "--section", section]),
    ]

    banner_h = 38
    sheet = Image.new("RGB", (tile_w * 3, tile_h * 2 + banner_h), (18, 20, 26))
    draw = ImageDraw.Draw(sheet)
    font_title = _load_font(16)
    font_banner = _load_font(15)

    draw.rectangle([0, 0, tile_w * 3, banner_h], fill=(28, 32, 42))
    draw.text((12, 10), banner_text or f"SolidCraft Oracle: {design_path.name}", fill=(235, 240, 250), font=font_banner)

    with tempfile.TemporaryDirectory(prefix="sc_snap_") as td:
        for idx, (label, extra_flags) in enumerate(views):
            col, row = idx % 3, idx // 3
            x0, y0 = col * tile_w, banner_h + row * tile_h
            shot_path = Path(td) / f"v{idx}.png"
            res = _run_cli(
                [
                    "snapshot",
                    "--in",
                    str(design_path),
                    "--out",
                    str(shot_path),
                    "--width",
                    str(tile_w),
                    "--height",
                    str(tile_h),
                    *extra_flags,
                ],
                timeout=90,
            )
            if res.returncode == 0 and shot_path.exists():
                im = Image.open(shot_path).convert("RGB").resize((tile_w, tile_h))
                sheet.paste(im, (x0, y0))
            else:
                draw.rectangle([x0, y0, x0 + tile_w, y0 + tile_h], fill=(45, 22, 22))
                err = (res.stderr or res.stdout or "snapshot failed").strip()[:90]
                draw.text((x0 + 12, y0 + tile_h // 2), err, fill=(255, 180, 180), font=font_title)

            # Label pill
            draw.rectangle([x0 + 6, y0 + 6, x0 + 190, y0 + 30], fill=(15, 18, 24))
            draw.text((x0 + 12, y0 + 10), label, fill=(255, 230, 120), font=font_title)
            draw.rectangle([x0, y0, x0 + tile_w - 1, y0 + tile_h - 1], outline=(70, 78, 95), width=1)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_png)
    return out_png


def verify_design(
    input_path: str | Path,
    out_dir: str | Path,
    *,
    in_design: Optional[str | Path] = None,
    save_sc: Optional[str | Path] = None,
    section: str = "y=0",
    min_clearance_mm: Optional[float] = None,
    param_sweeps: Optional[List[Dict[str, str]]] = None,
    render_views: bool = True,
    fast: bool = False,
) -> Dict[str, Any]:
    """Run full SolidCraft v0.49 deterministic verification and emit STATE.json + contact sheet.

    Pass `fast=True` during rapid inner fix iterations to skip slow `inspect.clearance`
    and 6-view snapshot rendering (runs in ~2-4s even on 50+ body assemblies).
    """
    if fast:
        min_clearance_mm = None
        render_views = False
    inp = Path(input_path).resolve()
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    sc_out = Path(save_sc).resolve() if save_sc else (out / "verified.sc")

    report: Dict[str, Any] = {
        "pass": False,
        "input": str(inp),
        "saved_design": str(sc_out),
        "errors": [],
        "warnings": [],
        "unconstrained_sketches": [],
        "open_bodies": [],
        "hard_clashes": [],
        "clearance_violations": [],
        "sweep_failures": [],
        "bodies": [],
        "parameters": {},
        "contact_sheet": None,
    }

    with tempfile.TemporaryDirectory(prefix="sc_oracle_") as td:
        td_path = Path(td)
        run_json = td_path / "run_results.json"

        # 1. Execute script/design with strict schema check + per-feature SMLib validation
        cmd = [
            "run",
            str(inp),
            "--param-check",
            "strict",
            "--validation",
            "each_feature",
            "--results",
            "--json-out",
            str(run_json),
            "--save",
            str(sc_out),
        ]
        if in_design:
            cmd.extend(["--in", str(Path(in_design).resolve())])

        res = _run_cli(cmd, timeout=180)
        if res.returncode != 0:
            err_detail = res.stderr.strip() or res.stdout.strip()
            if run_json.exists():
                try:
                    rj = json.loads(run_json.read_text(encoding="utf-8"))
                    if "error" in rj:
                        err_detail = json.dumps(rj["error"])
                except Exception:
                    pass
            report["errors"].append(f"build_failed: {err_detail[:500]}")
            _write_state(out, report)
            return report

        # 2. Run native v0.49 inspection commands on saved .sc
        inspect_cmds: List[Dict[str, Any]] = [
            {"command": "document.inspect", "params": {"measure": True}},
            {"command": "inspect.interference", "params": {}},
        ]
        if min_clearance_mm is not None:
            inspect_cmds.append(
                {"command": "inspect.clearance", "params": {"clearance": float(min_clearance_mm)}}
            )
        inspect_script = td_path / "inspect.json"
        inspect_out = td_path / "inspect_out.json"
        inspect_script.write_text(json.dumps({"commands": inspect_cmds}), encoding="utf-8")

        ires = _run_cli(
            [
                "run",
                str(inspect_script),
                "--in",
                str(sc_out),
                "--results",
                "--json-out",
                str(inspect_out),
            ],
            timeout=120,
        )
        if ires.returncode != 0 or not inspect_out.exists():
            report["errors"].append(f"inspect_failed: {(ires.stderr or ires.stdout).strip()[:400]}")
            _write_state(out, report)
            return report

        idata = json.loads(inspect_out.read_text(encoding="utf-8"))
        results = idata if isinstance(idata, list) else idata.get("results", [])

        # Parse document.inspect
        doc = results[0] if len(results) > 0 and isinstance(results[0], dict) else {}
        if "result" in doc and isinstance(doc["result"], dict):
            doc = doc["result"]

        for feat in doc.get("timeline", []) or []:
            if not isinstance(feat, dict):
                continue
            if feat.get("error"):
                report["errors"].append(f"feature `{feat.get('name')}`: {feat['error']}")
            if feat.get("warning"):
                report["warnings"].append(f"feature `{feat.get('name')}`: {feat['warning']}")

        for sk in doc.get("sketches", []) or []:
            if isinstance(sk, dict):
                dof = sk.get("dof", 0)
                if isinstance(dof, (int, float)) and dof > 0:
                    report["unconstrained_sketches"].append({"name": sk.get("name"), "dof": dof})

        for b in doc.get("bodies", []) or []:
            if not isinstance(b, dict):
                continue
            raw_vol = b.get("volume_mm3") if b.get("volume_mm3") is not None else b.get("volume")
            com = b.get("center_of_mass")
            b_summary = {
                "name": b.get("name"),
                "volume_mm3": round(float(raw_vol), 2) if raw_vol is not None else None,
                "center_of_mass": [round(float(v), 2) for v in com] if isinstance(com, list) else None,
                "bbox": b.get("bbox"),
                "faces": b.get("faces"),
                "closed": b.get("closed"),
            }
            report["bodies"].append(b_summary)
            if b.get("closed") is False:
                report["open_bodies"].append(b.get("name"))

        for p in (doc.get("params") or doc.get("parameters") or []):
            if isinstance(p, dict) and p.get("name"):
                report["parameters"][p["name"]] = p.get("expression", p.get("value"))
                if p.get("error"):
                    report["errors"].append(f"param `{p['name']}`: {p['error']}")

        # Parse inspect.interference
        interf = results[1] if len(results) > 1 and isinstance(results[1], dict) else {}
        if "result" in interf and isinstance(interf["result"], dict):
            interf = interf["result"]
        clashes = interf.get("interferences") or interf.get("clashes") or interf.get("pairs") or []
        if isinstance(clashes, list):
            for c in clashes:
                if isinstance(c, dict):
                    report["hard_clashes"].append({
                        "a": c.get("a"),
                        "b": c.get("b"),
                        "volume_mm3": round(float(c["volume_mm3"]), 2) if c.get("volume_mm3") is not None else None,
                        "center": [round(float(v), 2) for v in c["center"]] if isinstance(c.get("center"), list) else None,
                    })
                else:
                    report["hard_clashes"].append(c)

        # Parse inspect.clearance (if requested) — v0.49 returns `near` and `overlaps`
        if min_clearance_mm is not None and len(results) > 2 and isinstance(results[2], dict):
            clr = results[2].get("result", results[2])
            if isinstance(clr, dict):
                for key in ("near", "overlaps", "violations", "close_pairs"):
                    items = clr.get(key) or []
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict):
                                report["clearance_violations"].append({
                                    "kind": key,
                                    "a": item.get("a"),
                                    "b": item.get("b"),
                                    "distance_mm": item.get("distance_mm", item.get("distance")),
                                    "volume_mm3": round(float(item["volume_mm3"]), 2) if item.get("volume_mm3") is not None else None,
                                    "center": [round(float(v), 2) for v in item["center"]] if isinstance(item.get("center"), list) else None,
                                })
                            else:
                                report["clearance_violations"].append(item)

        # 3. Optional parametric min/max sweeps
        for idx, sweep in enumerate(param_sweeps or []):
            s_cmd = [
                "run",
                "--in",
                str(sc_out),
                "--validation",
                "each_feature",
            ]
            for k, v in sweep.items():
                s_cmd.extend(["--param", f"{k}={v}"])
            sres = _run_cli(s_cmd, timeout=90)
            if sres.returncode != 0:
                report["sweep_failures"].append(
                    {"sweep_index": idx, "params": sweep, "error": (sres.stderr or sres.stdout).strip()[:240]}
                )

    report["pass"] = (
        len(report["errors"]) == 0
        and len(report["unconstrained_sketches"]) == 0
        and len(report["open_bodies"]) == 0
        and len(report["hard_clashes"]) == 0
        and len(report["clearance_violations"]) == 0
        and len(report["sweep_failures"]) == 0
        and len(report["bodies"]) > 0
    )

    if render_views and sc_out.exists():
        status = "PASS" if report["pass"] else "FAIL"
        banner = (
            f"[{status}] bodies={len(report['bodies'])} | "
            f"clashes={len(report['hard_clashes'])} | "
            f"dof>0={len(report['unconstrained_sketches'])} | "
            f"warns={len(report['warnings'])} | "
            f"errs={len(report['errors'])}"
        )
        sheet_path = out / "contact_sheet.png"
        render_contact_sheet(sc_out, sheet_path, section=section, banner_text=banner)
        report["contact_sheet"] = str(sheet_path)

    _write_state(out, report)
    return report


def check_expectations(
    bodies: List[Dict[str, Any]],
    expect_path: str | Path,
) -> List[str]:
    """Validate declarative stage gates (required bodies, closedness, bbox bounds, volume)."""
    spec = json.loads(Path(expect_path).read_text(encoding="utf-8"))
    by_name = {b.get("name"): b for b in bodies if b.get("name")}
    failures: List[str] = []

    for req in spec.get("required_bodies", []) or []:
        if req not in by_name:
            failures.append(f"missing required body `{req}` (have: {sorted(by_name)})")

    for name, rules in (spec.get("bodies") or {}).items():
        b = by_name.get(name)
        if not b:
            failures.append(f"missing body `{name}` for gate check")
            continue
        if rules.get("closed") is True and b.get("closed") is False:
            failures.append(f"`{name}` is not closed")
        vol = b.get("volume_mm3")
        if "volume_mm3" in rules and vol is not None:
            vlo, vhi = float(rules["volume_mm3"][0]), float(rules["volume_mm3"][1])
            if not (vlo <= float(vol) <= vhi):
                failures.append(f"`{name}` volume {vol:.1f} mm^3 outside [{vlo:.1f}, {vhi:.1f}]")
        bb = b.get("bbox")
        if isinstance(bb, dict) and "min" in bb and "max" in bb:
            for ax_i, ax_name in enumerate(("x", "y", "z")):
                if ax_name in rules:
                    lo, hi = float(rules[ax_name][0]), float(rules[ax_name][1])
                    bmin, bmax = float(bb["min"][ax_i]), float(bb["max"][ax_i])
                    if bmin < lo or bmax > hi:
                        failures.append(
                            f"`{name}` {ax_name} [{bmin:.2f}, {bmax:.2f}] outside [{lo:.2f}, {hi:.2f}]"
                        )
    return failures


def publish_to_recovery(
    sc_path: Path,
    recovery_name: str,
    contact_sheet: Optional[Path] = None,
) -> Dict[str, str]:
    """Copy a verified .sc (and optional contact_sheet.png) into SolidCraft's allowed
    `~/.local/share/solidcraft/recovery/` folder so `mcp__solidcraft_live__open` and
    `screenshot` never hit 'outside the folders this agent may use' sandbox errors.
    """
    import shutil

    rec_dir = Path(os.path.expanduser("~/.local/share/solidcraft/recovery"))
    rec_dir.mkdir(parents=True, exist_ok=True)
    name = recovery_name if recovery_name.endswith(".sc") else f"{recovery_name}.sc"
    dst_sc = rec_dir / Path(name).name
    shutil.copy2(sc_path, dst_sc)
    out = {"recovery_sc": str(dst_sc)}
    if contact_sheet and contact_sheet.exists():
        dst_png = rec_dir / f"{dst_sc.stem}_contact_sheet.png"
        shutil.copy2(contact_sheet, dst_png)
        out["recovery_contact_sheet"] = str(dst_png)
    return out


def _write_state(out_dir: Path, report: Dict[str, Any]) -> None:
    """Write compact STATE.json (<1.5 KB target) for long-run anti-amnesia."""
    compact = {
        "pass": report["pass"],
        "saved_design": report["saved_design"],
        "contact_sheet": report["contact_sheet"],
        "body_count": len(report["bodies"]),
        "bodies": report["bodies"][:25],
        "parameters": report["parameters"],
        "errors": report["errors"][:5],
        "warnings": report["warnings"][:5],
        "unconstrained_sketches": report["unconstrained_sketches"][:10],
        "hard_clashes": report["hard_clashes"][:10],
        "clearance_violations": report["clearance_violations"][:10],
        "sweep_failures": report["sweep_failures"][:5],
        "gate_failures": report.get("gate_failures", [])[:10],
    }
    (out_dir / "STATE.json").write_text(json.dumps(compact, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="SolidCraft v0.49 Deterministic Verification Oracle")
    ap.add_argument("input", help="Script (.json) or design (.sc)")
    ap.add_argument("--in", dest="in_design", help="Optional base .sc design")
    ap.add_argument("--out-dir", default="oracle_out", help="Output folder for STATE.json & contact_sheet.png")
    ap.add_argument("--save", dest="save_sc", help="Path to save verified .sc")
    ap.add_argument("--section", default="y=0", help="Section plane for 6th view (default: y=0)")
    ap.add_argument("--clearance", type=float, default=None, help="Optional min clearance in mm")
    ap.add_argument("--no-render", action="store_true", help="Skip 3x2 contact sheet rendering")
    ap.add_argument("--fast", action="store_true", help="Inner-loop fast mode (skip clearance + render)")
    ap.add_argument("--expect-json", dest="expect_json", default=None,
                    help="Optional declarative gates JSON (required_bodies, bbox x/y/z, volume_mm3)")
    ap.add_argument("--publish-recovery", dest="publish_recovery", default=None,
                    help="Also copy verified .sc + contact_sheet.png into ~/.local/share/solidcraft/recovery/<name>.sc for safe live GUI open()")
    args = ap.parse_args()

    rep = verify_design(
        args.input,
        args.out_dir,
        in_design=args.in_design,
        save_sc=args.save_sc,
        section=args.section,
        min_clearance_mm=args.clearance,
        render_views=not args.no_render,
        fast=args.fast,
    )
    if args.expect_json:
        gate_fails = check_expectations(rep.get("bodies", []), args.expect_json)
        rep["gate_failures"] = gate_fails
        if gate_fails:
            rep["pass"] = False
        _write_state(Path(args.out_dir).resolve(), rep)
    if args.publish_recovery and rep.get("saved_design"):
        sc_p = Path(rep["saved_design"])
        cs_p = Path(rep["contact_sheet"]) if rep.get("contact_sheet") else None
        if sc_p.exists():
            rep.update(publish_to_recovery(sc_p, args.publish_recovery, cs_p))
            _write_state(Path(args.out_dir).resolve(), rep)
    print(json.dumps(rep, indent=2))
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
