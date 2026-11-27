#!/usr/bin/env python3
"""2D Orthographic Engineering Blueprint Pre-Flight Renderer (Pure Pillow).

Plots a `COORDINATE_TABLE.json` into a clean dimensioned Side (XZ) + Top (XY)
blueprint PNG BEFORE any 3D solid is created, enabling a fast ~$0.03 Frontier
Visual Gate to catch proportion, angle, and clearance errors upfront.

Expected JSON schema:
{
  "title": "Tubular Truss Frame",
  "keypoints": {"J1": [0, 0, 100], "J2": [600, 0, 480], ...},
  "members": [{"p0": "J1", "p1": "J2", "label": "MainBeam", "color": "#4ea8de"}],
  "circles": [{"center": [0, 0, 100], "radius": 60, "plane": "XZ", "label": "Bearing Bore OD 120"}],
  "notes": ["Angle = 32.3 deg", "Span = 600 mm"]
}
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont


def _load_font(size: int) -> Any:
    for p in (
        "/usr/share/fonts/google-carlito-fonts/Carlito-Regular.ttf",
        "/usr/share/fonts/adwaita-sans-fonts/AdwaitaSans-Regular.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
    ):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _resolve_pt(ref: Any, keypoints: Dict[str, List[float]]) -> Tuple[float, float, float]:
    if isinstance(ref, str) and ref in keypoints:
        p = keypoints[ref]
        return float(p[0]), float(p[1]), float(p[2])
    if isinstance(ref, (list, tuple)) and len(ref) == 3:
        return float(ref[0]), float(ref[1]), float(ref[2])
    raise ValueError(f"Cannot resolve 3D point reference: {ref!r}")


def render_blueprint(spec_path: str | Path, out_png: str | Path) -> Path:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    kps: Dict[str, List[float]] = spec.get("keypoints", {})
    members: List[Dict[str, Any]] = spec.get("members", [])
    circles: List[Dict[str, Any]] = spec.get("circles", [])
    notes: List[str] = spec.get("notes", [])
    title: str = spec.get("title", "2D Engineering Blueprint")

    # Collect bounds across all keypoints, member endpoints, and circles
    xs, ys, zs = [], [], []
    for p in kps.values():
        xs.append(float(p[0]))
        ys.append(float(p[1]))
        zs.append(float(p[2]))
    for m in members:
        for key in ("p0", "p1"):
            x, y, z = _resolve_pt(m[key], kps)
            xs.append(x); ys.append(y); zs.append(z)
    for c in circles:
        cx, cy, cz = _resolve_pt(c["center"], kps)
        r = float(c["radius"])
        pl = str(c.get("plane", "XZ")).upper()
        if pl == "XZ":
            xs.extend([cx - r, cx + r]); zs.extend([cz - r, cz + r]); ys.append(cy)
        elif pl == "XY":
            xs.extend([cx - r, cx + r]); ys.extend([cy - r, cy + r]); zs.append(cz)
        else:
            ys.extend([cy - r, cy + r]); zs.extend([cz - r, cz + r]); xs.append(cx)

    if not xs:
        xs, ys, zs = [0.0, 100.0], [-50.0, 50.0], [0.0, 100.0]

    W, H = 1440, 1040
    im = Image.new("RGB", (W, H), (14, 20, 32))
    draw = ImageDraw.Draw(im)
    f_title = _load_font(20)
    f_sub = _load_font(15)
    f_lbl = _load_font(12)

    # Header bar
    draw.rectangle([0, 0, W, 46], fill=(22, 32, 52))
    draw.text((16, 12), f"2D BLUEPRINT PRE-FLIGHT: {title}", fill=(235, 244, 255), font=f_title)
    if notes:
        draw.text((W - 560, 15), " | ".join(notes[:4]), fill=(160, 210, 255), font=f_sub)

    def draw_panel(
        box: Tuple[int, int, int, int],
        panel_title: str,
        u_vals: List[float],
        v_vals: List[float],
        plane_name: str,
    ) -> None:
        x0, y0, x1, y1 = box
        draw.rectangle([x0, y0, x1, y1], fill=(18, 26, 42), outline=(70, 95, 130), width=2)
        draw.text((x0 + 12, y0 + 8), panel_title, fill=(255, 224, 120), font=f_sub)

        pad = 54
        pw, ph = (x1 - x0) - 2 * pad, (y1 - y0) - 2 * pad - 18
        umin, umax = min(u_vals), max(u_vals)
        vmin, vmax = min(v_vals), max(v_vals)
        span_u = max(umax - umin, 10.0)
        span_v = max(vmax - vmin, 10.0)
        scale = min(pw / span_u, ph / span_v) * 0.92

        cu = 0.5 * (umin + umax)
        cv = 0.5 * (vmin + vmax)
        cx_px = 0.5 * (x0 + x1)
        cy_px = 0.5 * (y0 + 22 + y1)

        def to_px(u: float, v: float) -> Tuple[int, int]:
            return int(cx_px + (u - cu) * scale), int(cy_px - (v - cv) * scale)

        # Grid lines (nice step)
        raw_step = max(span_u, span_v) / 8.0
        mag = 10 ** math.floor(math.log10(max(raw_step, 1e-3)))
        step = mag * (1 if raw_step / mag < 2 else (2 if raw_step / mag < 5 else 5))
        gu = math.floor(umin / step) * step
        while gu <= umax + step:
            px, _ = to_px(gu, cv)
            if x0 + 20 < px < x1 - 10:
                draw.line([(px, y0 + 28), (px, y1 - 16)], fill=(30, 42, 66), width=1)
                draw.text((px + 2, y1 - 15), f"{int(gu)}", fill=(105, 130, 165), font=f_lbl)
            gu += step
        gv = math.floor(vmin / step) * step
        while gv <= vmax + step:
            _, py = to_px(cu, gv)
            if y0 + 28 < py < y1 - 18:
                draw.line([(x0 + 36, py), (x1 - 10, py)], fill=(30, 42, 66), width=1)
                draw.text((x0 + 4, py - 6), f"{int(gv)}", fill=(105, 130, 165), font=f_lbl)
            gv += step

        # Ground Z=0 or Symmetry Y=0 highlight
        _, py0 = to_px(cu, 0.0)
        if y0 + 28 < py0 < y1 - 18:
            draw.line([(x0 + 36, py0), (x1 - 10, py0)], fill=(90, 140, 90), width=2)

        # Circles
        for c in circles:
            if str(c.get("plane", "XZ")).upper() != plane_name:
                continue
            cx, cy, cz = _resolve_pt(c["center"], kps)
            u, v = (cx, cz) if plane_name == "XZ" else (cx, cy)
            r_px = int(float(c["radius"]) * scale)
            px, py = to_px(u, v)
            draw.ellipse([px - r_px, py - r_px, px + r_px, py + r_px], outline=(110, 185, 255), width=2)
            if c.get("label"):
                draw.text((px - r_px + 6, py - r_px + 4), str(c["label"]), fill=(140, 205, 255), font=f_lbl)

        # Members
        for m in members:
            xA, yA, zA = _resolve_pt(m["p0"], kps)
            xB, yB, zB = _resolve_pt(m["p1"], kps)
            uA, vA = (xA, zA) if plane_name == "XZ" else (xA, yA)
            uB, vB = (xB, zB) if plane_name == "XZ" else (xB, yB)
            pA, pB = to_px(uA, vA), to_px(uB, vB)
            line_color = m.get("color") or (245, 175, 70)
            draw.line([pA, pB], fill=line_color, width=4)
            if m.get("label"):
                mx, my = (pA[0] + pB[0]) // 2, (pA[1] + pB[1]) // 2
                dist = math.sqrt((xB - xA) ** 2 + (yB - yA) ** 2 + (zB - zA) ** 2)
                draw.text((mx + 4, my - 14), f"{m['label']} ({dist:.0f}mm)", fill=(255, 215, 140), font=f_lbl)

        # Keypoints
        for name, p in kps.items():
            u, v = (float(p[0]), float(p[2])) if plane_name == "XZ" else (float(p[0]), float(p[1]))
            px, py = to_px(u, v)
            draw.ellipse([px - 4, py - 4, px + 4, py + 4], fill=(255, 95, 110))
            draw.text((px + 6, py - 12), f"{name} ({u:.0f},{v:.0f})", fill=(255, 185, 195), font=f_lbl)

    draw_panel((16, 58, W - 16, 600), "SIDE ELEVATION - XZ Plane (mm, Z=0 green ground line)", xs, zs, "XZ")
    draw_panel((16, 614, W - 16, H - 16), "TOP PLAN - XY Plane (mm, Y=0 green symmetry plane)", xs, ys, "XY")

    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Render 2D Side+Top Blueprint PNG from COORDINATE_TABLE.json")
    ap.add_argument("spec", help="Path to COORDINATE_TABLE.json")
    ap.add_argument("--out", default="blueprint_2d.png", help="Output PNG path")
    args = ap.parse_args()
    out = render_blueprint(args.spec, args.out)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
