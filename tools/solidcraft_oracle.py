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
import re
import statistics
import subprocess
import tempfile
import time
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


def _overlay_som_and_rulers(
    im: Image.Image,
    bodies: List[Dict[str, Any]],
    view_name: str,
    outlier_names: set[str],
) -> Image.Image:
    """Stamp Set-of-Mark [1]..[N] part badges + axis extent rulers onto ortho tiles."""
    if not bodies or view_name not in ("front", "right", "top", "back"):
        return im
    valid = []
    for idx, b in enumerate(bodies, 1):
        bb = b.get("bbox")
        if isinstance(bb, dict) and "min" in bb and "max" in bb:
            mn, mx = bb["min"], bb["max"]
            valid.append((
                idx,
                str(b.get("name") or f"Body{idx}"),
                [float(mn[0]), float(mn[1]), float(mn[2])],
                [float(mx[0]), float(mx[1]), float(mx[2])],
            ))
    if not valid:
        return im

    gmin = [min(v[2][i] for v in valid) for i in range(3)]
    gmax = [max(v[3][i] for v in valid) for i in range(3)]

    # Map 3D axes -> 2D tile (u_axis, v_axis, u_sign, v_sign, u_label, v_label)
    proj_map = {
        "front": (0, 2, 1.0, 1.0, "X", "Z"),
        "back":  (0, 2, -1.0, 1.0, "-X", "Z"),
        "right": (1, 2, 1.0, 1.0, "Y", "Z"),
        "top":   (0, 1, 1.0, 1.0, "X", "Y"),
    }
    ua, va, us, vs, ulab, vlab = proj_map[view_name]
    w, h = im.size
    draw = ImageDraw.Draw(im)
    font_sm = _load_font(11)

    u_span = max(gmax[ua] - gmin[ua], 1.0)
    v_span = max(gmax[va] - gmin[va], 1.0)
    scale = min((w * 0.76) / u_span, (h * 0.72) / v_span)
    u_mid = 0.5 * (gmin[ua] + gmax[ua])
    v_mid = 0.5 * (gmin[va] + gmax[va])

    def to_px(u_val: float, v_val: float) -> Tuple[int, int]:
        px = int(round(w * 0.5 + us * (u_val - u_mid) * scale))
        py = int(round(h * 0.52 - vs * (v_val - v_mid) * scale))
        return max(14, min(w - 22, px)), max(34, min(h - 24, py))

    # Draw dashed Z=0 datum line on elevation views if visible
    if va == 2 and gmin[2] <= 50.0 and gmax[2] >= -50.0:
        _, zy = to_px(u_mid, 0.0)
        if 34 < zy < h - 22:
            for x in range(8, w - 8, 12):
                draw.line([(x, zy), (min(x + 6, w - 8), zy)], fill=(255, 110, 90), width=1)
            draw.text((w - 38, max(32, zy - 13)), "Z=0", fill=(255, 130, 110), font=font_sm)

    # Bottom-left axis extent readout in mm
    extent_txt = f"{ulab}:[{gmin[ua]:.0f}..{gmax[ua]:.0f}] {vlab}:[{gmin[va]:.0f}..{gmax[va]:.0f}]mm"
    draw.rectangle([4, h - 18, min(w - 4, 8 + len(extent_txt) * 7), h - 2], fill=(12, 15, 22))
    draw.text((8, h - 16), extent_txt, fill=(185, 215, 255), font=font_sm)

    # Stamp Set-of-Mark [N] badges at each body centroid (with collision nudge)
    placed: List[Tuple[int, int]] = []
    for idx, name, mn, mx in valid[:30]:
        cu = 0.5 * (mn[ua] + mx[ua])
        cv = 0.5 * (mn[va] + mx[va])
        px, py = to_px(cu, cv)
        for _ in range(6):
            if any(abs(px - ox) < 22 and abs(py - oy) < 15 for ox, oy in placed):
                py = max(34, min(h - 26, py - 16))
            else:
                break
        placed.append((px, py))
        is_bad = name in outlier_names or bool(re.search(r"\(\d+\)$", name))
        bg = (190, 35, 45) if is_bad else (20, 28, 44)
        fg = (255, 255, 255) if is_bad else (255, 232, 130)
        tag = str(idx)
        bw = 10 + 7 * len(tag)
        draw.rectangle([px - bw // 2, py - 8, px + bw // 2, py + 7], fill=bg, outline=(255, 232, 130) if not is_bad else (255, 180, 180), width=1)
        draw.text((px - bw // 2 + 4, py - 7), tag, fill=fg, font=font_sm)
    return im


def _append_som_legend(
    sheet: Image.Image,
    bodies: List[Dict[str, Any]],
    outlier_names: set[str],
) -> Image.Image:
    """Append a compact Set-of-Mark [1]..[N] part legend bar to the bottom of a sheet."""
    if not bodies:
        return sheet
    font = _load_font(12)
    w, h = sheet.size
    cols = max(3, min(6, w // 230))
    items = bodies[:30]
    rows = (len(items) + cols - 1) // cols
    leg_h = 10 + rows * 18
    out = Image.new("RGB", (w, h + leg_h), (16, 19, 26))
    out.paste(sheet, (0, 0))
    draw = ImageDraw.Draw(out)
    draw.line([(0, h), (w, h)], fill=(70, 78, 95), width=1)
    col_w = w // cols
    for i, b in enumerate(items, 1):
        name = str(b.get("name") or f"Body{i}")
        is_bad = name in outlier_names or bool(re.search(r"\(\d+\)$", name))
        r, c = (i - 1) // cols, (i - 1) % cols
        tx, ty = 10 + c * col_w, h + 5 + r * 18
        label = f"[{i}] {name[:24]}" + (" !" if is_bad else "")
        draw.text((tx, ty), label, fill=(255, 110, 110) if is_bad else (210, 222, 240), font=font)
    return out


def _render_clash_zooms(
    design_path: Path,
    out_png: Path,
    clashes: List[Dict[str, Any]],
    bodies: List[Dict[str, Any]],
    *,
    max_clashes: int = 2,
) -> Optional[Path]:
    """Auto-render tight 2-view (ISO + FRONT) close-ups of top hard clashes (A=RED, B=CYAN)."""
    dict_clashes = [c for c in clashes if isinstance(c, dict) and c.get("a") and c.get("b")]
    if not dict_clashes:
        return None
    dict_clashes.sort(key=lambda c: float(c.get("volume_mm3") or 0.0), reverse=True)
    top = dict_clashes[:max_clashes]
    by_name = {str(b.get("name")): b for b in bodies if b.get("name")}
    all_names = list(by_name.keys())

    tile_w, tile_h, row_hdr_h = 440, 300, 30
    total_h = len(top) * (tile_h + row_hdr_h)
    sheet = Image.new("RGB", (tile_w * 2, total_h), (18, 20, 26))
    draw = ImageDraw.Draw(sheet)
    font = _load_font(14)
    ok_any = False

    with tempfile.TemporaryDirectory(prefix="sc_clash_") as td:
        for r_idx, c in enumerate(top):
            a, b = str(c["a"]), str(c["b"])
            vol = c.get("volume_mm3")
            center = c.get("center")
            bb_a = (by_name.get(a) or {}).get("bbox") or {}
            bb_b = (by_name.get(b) or {}).get("bbox") or {}
            if not (isinstance(center, list) and len(center) == 3):
                if "min" in bb_a and "min" in bb_b:
                    center = [
                        0.25 * (bb_a["min"][i] + bb_a["max"][i] + bb_b["min"][i] + bb_b["max"][i])
                        for i in range(3)
                    ]
                else:
                    center = [0.0, 0.0, 0.0]

            # Tight camera distance from the smaller clashing body's bbox diagonal
            diags = []
            for bb in (bb_a, bb_b):
                if "min" in bb and "max" in bb:
                    d = sum((float(bb["max"][i]) - float(bb["min"][i])) ** 2 for i in range(3)) ** 0.5
                    if d > 1.0:
                        diags.append(d)
            dist = max(80.0, min(550.0, (min(diags) if diags else 180.0) * 1.6))

            body_flags: List[str] = [
                "--body", f"{a}:opacity=0.95,color=#ff3344",
                "--body", f"{b}:opacity=0.95,color=#00d4ff",
            ]
            for other in all_names:
                if other not in (a, b):
                    body_flags.extend(["--body", f"{other}:opacity=0.08,color=#888888"])

            y_row = r_idx * (tile_h + row_hdr_h)
            draw.rectangle([0, y_row, tile_w * 2, y_row + row_hdr_h], fill=(42, 22, 28))
            vol_s = f"{vol} mm3" if vol is not None else "overlap"
            hdr = f"CLASH #{r_idx + 1}: {a} (RED) vs {b} (CYAN) | {vol_s} | ({center[0]:.1f}, {center[1]:.1f}, {center[2]:.1f})"
            draw.text((10, y_row + 7), hdr[:115], fill=(255, 225, 225), font=font)

            cams = [
                ("ISO ZOOM", {"target": center, "yaw": 0.78, "pitch": 0.42, "distance": round(dist, 1)}),
                ("FRONT ZOOM", {"target": center, "yaw": 0.0, "pitch": 0.05, "distance": round(dist, 1)}),
            ]
            for c_idx, (lbl, cam) in enumerate(cams):
                shot = Path(td) / f"c{r_idx}_{c_idx}.png"
                res = _run_cli(
                    [
                        "snapshot", "--in", str(design_path), "--out", str(shot),
                        "--width", str(tile_w), "--height", str(tile_h),
                        "--camera", json.dumps(cam),
                        *body_flags,
                    ],
                    timeout=60,
                )
                x0, y0 = c_idx * tile_w, y_row + row_hdr_h
                if res.returncode == 0 and shot.exists():
                    sheet.paste(Image.open(shot).convert("RGB").resize((tile_w, tile_h)), (x0, y0))
                    ok_any = True
                draw.rectangle([x0 + 6, y0 + 6, x0 + 130, y0 + 26], fill=(15, 18, 24))
                draw.text((x0 + 10, y0 + 8), lbl, fill=(255, 230, 120), font=font)
                draw.rectangle([x0, y0, x0 + tile_w - 1, y0 + tile_h - 1], outline=(90, 50, 55), width=1)

    if not ok_any:
        return None
    out_png.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_png)
    return out_png


def render_contact_sheet(
    design_path: Path,
    out_png: Path,
    *,
    section: str = "y=0",
    tile_w: int = 480,
    tile_h: int = 320,
    banner_text: str = "",
    bodies: Optional[List[Dict[str, Any]]] = None,
    outlier_names: Optional[set[str]] = None,
) -> Path:
    """Render 6 labeled views (5 ortho/iso + 1 X-ray section cut) with Set-of-Mark badges into a PNG."""
    views: List[Tuple[str, str, List[str]]] = [
        ("ISO", "iso", ["--view", "iso"]),
        ("FRONT (Side)", "front", ["--view", "front"]),
        ("RIGHT (End)", "right", ["--view", "right"]),
        ("TOP", "top", ["--view", "top"]),
        ("BACK", "back", ["--view", "back"]),
        (f"SECTION ({section})", "section", ["--view", "iso", "--section", section]),
    ]

    banner_h = 38
    sheet = Image.new("RGB", (tile_w * 3, tile_h * 2 + banner_h), (18, 20, 26))
    draw = ImageDraw.Draw(sheet)
    font_title = _load_font(16)
    font_banner = _load_font(15)
    out_set = outlier_names or set()

    draw.rectangle([0, 0, tile_w * 3, banner_h], fill=(28, 32, 42))
    draw.text((12, 10), banner_text or f"SolidCraft Oracle: {design_path.name}", fill=(235, 240, 250), font=font_banner)

    with tempfile.TemporaryDirectory(prefix="sc_snap_") as td:
        for idx, (label, vkey, extra_flags) in enumerate(views):
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
                if bodies:
                    im = _overlay_som_and_rulers(im, bodies, vkey, out_set)
                sheet.paste(im, (x0, y0))
            else:
                draw.rectangle([x0, y0, x0 + tile_w, y0 + tile_h], fill=(45, 22, 22))
                err = (res.stderr or res.stdout or "snapshot failed").strip()[:90]
                draw.text((x0 + 12, y0 + tile_h // 2), err, fill=(255, 180, 180), font=font_title)

            # Label pill
            draw.rectangle([x0 + 6, y0 + 6, x0 + 190, y0 + 30], fill=(15, 18, 24))
            draw.text((x0 + 12, y0 + 10), label, fill=(255, 230, 120), font=font_title)
            draw.rectangle([x0, y0, x0 + tile_w - 1, y0 + tile_h - 1], outline=(70, 78, 95), width=1)

    if bodies:
        sheet = _append_som_legend(sheet, bodies, out_set)
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
    allow_split_bodies: bool = False,
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
        "split_bodies": [],
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
            bname = str(b.get("name") or "")
            if b.get("closed") is False:
                report["open_bodies"].append(bname)
            if re.search(r"\(\d+\)$", bname):
                report["split_bodies"].append(bname)
            elif re.match(r"^Body\d+$", bname):
                report["warnings"].append(f"unnamed default body `{bname}` — assign an explicit body_name")

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

    outliers = _detect_outlier_bodies(report["bodies"])
    report["outlier_bodies"] = outliers
    if outliers:
        names = ", ".join(o["name"] for o in outliers[:6])
        report["warnings"].append(
            f"OUTLIER_BBOX: {names} displaced far from assembly median Z — inspect preview.png for unshifted/floating parts!"
        )

    report["pass"] = (
        len(report["errors"]) == 0
        and len(report["unconstrained_sketches"]) == 0
        and len(report["open_bodies"]) == 0
        and (allow_split_bodies or len(report["split_bodies"]) == 0)
        and len(report["hard_clashes"]) == 0
        and len(report["clearance_violations"]) == 0
        and len(report["sweep_failures"]) == 0
        and len(report["bodies"]) > 0
    )

    outlier_name_set = {o["name"] for o in outliers}

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
        render_contact_sheet(
            sc_out,
            sheet_path,
            section=section,
            banner_text=banner,
            bodies=report["bodies"],
            outlier_names=outlier_name_set,
        )
        report["contact_sheet"] = str(sheet_path)
        if report["hard_clashes"]:
            cz = _render_clash_zooms(sc_out, out / "clash_zoom.png", report["hard_clashes"], report["bodies"])
            if cz:
                report["clash_zoom"] = str(cz)
    elif fast and sc_out.exists():
        # Smart periodic 2-view sanity check (~2s): first build, >5 min stale, or outlier bbox
        prev_path = out / "preview.png"
        sheet_path = out / "contact_sheet.png"
        newest = max(
            (p.stat().st_mtime for p in (prev_path, sheet_path) if p.exists()),
            default=0.0,
        )
        if newest == 0.0 or (time.time() - newest) > 300.0 or bool(outliers):
            tag = "OUTLIER CHECK" if outliers else "PERIODIC VISUAL CHECK"
            banner = f"[{tag}] bodies={len(report['bodies'])} | clashes={len(report['hard_clashes'])} | split={len(report['split_bodies'])}"
            if _render_quick_preview(
                sc_out,
                prev_path,
                banner_text=banner,
                bodies=report["bodies"],
                outlier_names=outlier_name_set,
            ):
                report["preview_image"] = str(prev_path)
                report["visual_check_hint"] = (
                    f"Inspect {prev_path} with vision_analyze — verify alignment, orientation, and no floating/underground parts."
                )
        if report["hard_clashes"]:
            cz = _render_clash_zooms(sc_out, out / "clash_zoom.png", report["hard_clashes"], report["bodies"], max_clashes=1)
            if cz:
                report["clash_zoom"] = str(cz)

    _write_state(out, report)
    return report


def _detect_outlier_bodies(bodies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flag bodies displaced far from the primary structure (e.g. unshifted/underground parts)."""
    valid = []
    for b in bodies:
        bb = b.get("bbox")
        if isinstance(bb, dict) and "min" in bb and "max" in bb:
            cx = 0.5 * (float(bb["min"][0]) + float(bb["max"][0]))
            cy = 0.5 * (float(bb["min"][1]) + float(bb["max"][1]))
            cz = 0.5 * (float(bb["min"][2]) + float(bb["max"][2]))
            vol = float(b.get("volume_mm3") or 1.0)
            valid.append((str(b.get("name") or "?"), cx, cy, cz, vol, bb))
    if len(valid) < 3:
        return []
    # Anchor Z reference to the top-3 largest bodies so a cluster of small unshifted parts can't skew it
    top = sorted(valid, key=lambda v: v[4], reverse=True)[:3]
    ref_z = sum(v[3] * v[4] for v in top) / max(sum(v[4] for v in top), 1.0)
    ref_span_z = max(float(v[5]["max"][2]) - float(v[5]["min"][2]) for v in top)
    z_thresh = max(ref_span_z * 0.75, 250.0)
    outliers: List[Dict[str, Any]] = []
    for name, cx, cy, cz, vol, bb in valid:
        z_min = float(bb["min"][2])
        if abs(cz - ref_z) > z_thresh or (ref_z > 200.0 and z_min < -80.0):
            outliers.append({
                "name": name,
                "center_z": round(cz, 1),
                "ref_z": round(ref_z, 1),
                "z_range": [round(z_min, 1), round(float(bb["max"][2]), 1)],
            })
    return outliers


def _render_quick_preview(
    design_path: Path,
    out_png: Path,
    banner_text: str = "",
    bodies: Optional[List[Dict[str, Any]]] = None,
    outlier_names: Optional[set[str]] = None,
) -> Optional[Path]:
    """Fast 2-view (ISO + FRONT) sanity strip (~2s) with Set-of-Mark badges & legend."""
    tile_w, tile_h, banner_h = 480, 320, 34
    sheet = Image.new("RGB", (tile_w * 2, tile_h + banner_h), (18, 20, 26))
    draw = ImageDraw.Draw(sheet)
    font = _load_font(15)
    out_set = outlier_names or set()
    draw.rectangle([0, 0, tile_w * 2, banner_h], fill=(28, 32, 42))
    draw.text((10, 8), banner_text or f"Quick Visual Check: {design_path.name}", fill=(235, 240, 250), font=font)
    ok_any = False
    with tempfile.TemporaryDirectory(prefix="sc_prev_") as td:
        for idx, (label, view) in enumerate([("ISO", "iso"), ("FRONT (Side)", "front")]):
            shot = Path(td) / f"p{idx}.png"
            res = _run_cli(
                ["snapshot", "--in", str(design_path), "--out", str(shot),
                 "--width", str(tile_w), "--height", str(tile_h), "--view", view],
                timeout=60,
            )
            x0, y0 = idx * tile_w, banner_h
            if res.returncode == 0 and shot.exists():
                im = Image.open(shot).convert("RGB").resize((tile_w, tile_h))
                if bodies:
                    im = _overlay_som_and_rulers(im, bodies, view, out_set)
                sheet.paste(im, (x0, y0))
                ok_any = True
            draw.rectangle([x0 + 6, y0 + 6, x0 + 160, y0 + 28], fill=(15, 18, 24))
            draw.text((x0 + 12, y0 + 9), label, fill=(255, 230, 120), font=font)
    if not ok_any:
        return None
    if bodies:
        sheet = _append_som_legend(sheet, bodies, out_set)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_png)
    return out_png


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
        "split_bodies": report.get("split_bodies", [])[:10],
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
    ap.add_argument("--allow-split-bodies", action="store_true",
                    help="Allow auto-suffixed duplicate bodies like `Part (1)` without failing pass")
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
        allow_split_bodies=args.allow_split_bodies,
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
    img = rep.get("contact_sheet") or rep.get("preview_image")
    if img:
        print(f"MEDIA:{img}")
    if rep.get("clash_zoom"):
        print(f"MEDIA:{rep['clash_zoom']}")
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
