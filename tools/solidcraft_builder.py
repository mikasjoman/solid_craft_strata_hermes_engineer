"""Auto-generated schema-validated Python SDK for SolidCraft CLI (v0.49+).

Reads and caches the live JSON Schema catalog from `solidcraft-cli commands`
keyed by `solidcraft-cli --version`, so every command name and parameter key
is validated in Python BEFORE SolidCraft executes.
"""

from __future__ import annotations

import difflib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SOLIDCRAFT_CLI = os.environ.get(
    "SOLIDCRAFT_CLI",
    os.path.expanduser("~/.local/bin/solidcraft-cli"),
)
SCHEMA_CACHE_DIR = Path(
    os.path.expanduser("~/.hermes/profiles/solidcraft/cache")
)


class SolidCraftSchemaError(ValueError):
    """Raised when a command id or parameter violates the live SolidCraft schema."""


def _cli_version() -> str:
    res = subprocess.run(
        [SOLIDCRAFT_CLI, "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip().replace(" ", "_")


def _cache_path() -> Path:
    SCHEMA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return SCHEMA_CACHE_DIR / f"commands_schema_{_cli_version()}.json"


def load_command_catalog(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """Return {command_id: {required: [...] | None, properties: [...] | None}}."""
    cache_path = _cache_path()
    if cache_path.exists() and not force_refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    res = subprocess.run(
        [SOLIDCRAFT_CLI, "commands"],
        capture_output=True,
        text=True,
        check=True,
    )
    raw = json.loads(res.stdout)
    entries = raw if isinstance(raw, list) else raw.get("commands", [])
    catalog: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, dict) and "id" in entry:
            catalog[entry["id"]] = {"required": None, "properties": None}
    cache_path.write_text(json.dumps(catalog), encoding="utf-8")
    return catalog


def _ensure_command_schema(catalog: Dict[str, Dict[str, Any]], command_id: str) -> Dict[str, Any]:
    """Lazily fetch & cache full JSON schema for a single command via `commands --id`."""
    spec = catalog[command_id]
    if spec.get("properties") is not None:
        return spec
    res = subprocess.run(
        [SOLIDCRAFT_CLI, "commands", "--id", command_id],
        capture_output=True,
        text=True,
        check=True,
    )
    detail = json.loads(res.stdout)
    inp = detail.get("input") or {}
    if not isinstance(inp, dict):
        inp = {}
    spec["required"] = list(inp.get("required") or [])
    spec["properties"] = list((inp.get("properties") or {}).keys())
    try:
        _cache_path().write_text(json.dumps(catalog), encoding="utf-8")
    except Exception:
        pass
    return spec


class SolidCraftBuilder:
    """Typed, schema-checked SolidCraft script builder with mechanical helpers."""

    def __init__(self, validate: bool = True) -> None:
        self.commands: List[Dict[str, Any]] = []
        self.validate = validate
        self._catalog: Optional[Dict[str, Dict[str, Any]]] = (
            load_command_catalog() if validate else None
        )
        self._seen_names: set[str] = set()

    def _check_command(self, command_id: str, params: Dict[str, Any]) -> None:
        if not self.validate or self._catalog is None:
            return
        if command_id not in self._catalog:
            close = difflib.get_close_matches(command_id, self._catalog.keys(), n=5)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            raise SolidCraftSchemaError(f"Unknown SolidCraft command `{command_id}`.{hint}")
        spec = _ensure_command_schema(self._catalog, command_id)
        missing = [r for r in (spec["required"] or []) if r not in params]
        if missing:
            raise SolidCraftSchemaError(
                f"Command `{command_id}` missing required params: {missing}. "
                f"Allowed params: {spec['properties']}"
            )
        allowed = set(spec["properties"] or [])
        if allowed:
            extra = [k for k in params if k not in allowed]
            if extra:
                raise SolidCraftSchemaError(
                    f"Command `{command_id}` got unknown params: {extra}. "
                    f"Allowed params: {spec['properties']}"
                )

    def cmd(self, command_id: str, **params: Any) -> "SolidCraftBuilder":
        """Append a validated SolidCraft command."""
        clean_params = {k: v for k, v in params.items() if v is not None}
        # Auto-normalize SolidCraft v0.49 custom sketch plane syntax if caller passed top-level origin/x_dir/y_dir
        if command_id == "sketch.create" and "origin" in clean_params and "plane" not in clean_params:
            plane_obj = {"origin": clean_params.pop("origin")}
            if "x_dir" in clean_params:
                plane_obj["x_dir"] = clean_params.pop("x_dir")
            if "y_dir" in clean_params:
                plane_obj["y_dir"] = clean_params.pop("y_dir")
            clean_params["plane"] = plane_obj
        self._check_command(command_id, clean_params)
        self.commands.append({"command": command_id, "params": clean_params})
        return self

    def param(
        self,
        name: str,
        expression: str,
        comment: Optional[str] = None,
        favorite: bool = False,
    ) -> "SolidCraftBuilder":
        """Declare a named parametric variable."""
        self.cmd("parameters.add", name=name, expression=expression, comment=comment)
        if favorite:
            self.cmd("parameters.favorite", name=name, favorite=True)
        return self

    def checkpoint(self) -> "SolidCraftBuilder":
        """Insert a SolidCraft undo/restore checkpoint (edit.checkpoint takes no params)."""
        return self.cmd("edit.checkpoint")

    def swept_tube(
        self,
        name: str,
        points: Sequence[Sequence[float]],
        diameter: Any,
        *,
        plane: str = "XZ",
        offset: Any = 0,
        world_xz: bool = True,
        wall: Any = None,
        operation: str = "new_body",
        targets: Optional[List[str]] = None,
        body_name: Optional[str] = None,
        periodic: bool = False,
    ) -> "SolidCraftBuilder":
        """Build a smooth curved/bent tube via centerline fit-point spline + solid.pipe.

        When plane='XZ' and world_xz=True, input points are [(X_world, Z_world), ...]
        and are automatically mapped to SolidCraft's XZ sketch coordinates (u=X, v=-Z)
        so curved frames/tubes never mirror upside down.
        """
        if len(points) < 2:
            raise ValueError("swept_tube requires at least 2 centerline points")
        pts_2d: List[List[float]] = []
        for p in points:
            if len(p) != 2:
                raise ValueError(f"Expected 2D point [u, v], got {p}")
            u, v = float(p[0]), float(p[1])
            if plane.upper() == "XZ" and world_xz:
                v = -v
            pts_2d.append([round(u, 4), round(v, 4)])

        sketch_name = f"{name}_Path"
        self.cmd("sketch.create", plane=plane, name=sketch_name, offset=offset)
        if len(pts_2d) == 2:
            self.cmd("sketch.line", points=pts_2d)
            path_id = "l1"
        else:
            self.cmd("sketch.spline.fit_point", points=pts_2d, periodic=periodic)
            path_id = "s1"
        # Lock all generated sketch points so path sketches stay at DOF = 0
        self.cmd(
            "sketch.constraint.fix",
            entities=[f"p{i + 1}" for i in range(len(pts_2d))],
            fixed=True,
        )
        self.cmd("sketch.finish")
        self.cmd(
            "solid.pipe",
            name=name,
            path_sketch=sketch_name,
            path=path_id,
            diameter=diameter,
            wall=wall,
            operation=operation,
            targets=targets,
            body_name=body_name,
        )
        return self

    def swept_tube_3d(
        self,
        name: str,
        points_3d: Sequence[Sequence[float]],
        diameter: Any,
        *,
        wall: Any = None,
        operation: str = "new_body",
        targets: Optional[List[str]] = None,
        body_name: Optional[str] = None,
        periodic: bool = False,
    ) -> "SolidCraftBuilder":
        """Build a smooth 3D curved/bowed tube by fitting a best-fit sketch plane to >=3 3D points.

        Uses SolidCraft v0.49 `plane: {origin, x_dir, y_dir}`, fit-point spline `s1`,
        and `sketch.constraint.fix` so the path sketch always comes out at DOF = 0.
        """
        if len(points_3d) < 2:
            raise ValueError("swept_tube_3d requires at least 2 3D points")
        pts = [[float(p[0]), float(p[1]), float(p[2])] for p in points_3d]
        n = len(pts)
        o = [sum(p[i] for p in pts) / n for i in range(3)]

        def _sub(a: List[float], b: List[float]) -> List[float]:
            return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]

        def _dot(a: List[float], b: List[float]) -> float:
            return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

        def _cross(a: List[float], b: List[float]) -> List[float]:
            return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]

        def _norm(v: List[float]) -> List[float]:
            mag = math.sqrt(_dot(v, v))
            if mag < 1e-12:
                raise ValueError(f"Degenerate vector in swept_tube_3d `{name}`")
            return [c / mag for c in v]

        cov = [[0.0] * 3 for _ in range(3)]
        for p in pts:
            d = _sub(p, o)
            for i in range(3):
                for j in range(3):
                    cov[i][j] += d[i] * d[j]
        tr = cov[0][0] + cov[1][1] + cov[2][2]
        normal = [0.0, 0.0, 1.0]
        for _ in range(250):
            nv = [sum((tr * (1.0 if i == j else 0.0) - cov[i][j]) * normal[j] for j in range(3)) for i in range(3)]
            mag = math.sqrt(_dot(nv, nv))
            if mag < 1e-12:
                break
            normal = [c / mag for c in nv]

        x_vec = _sub(pts[-1], pts[0])
        x_proj = _sub(x_vec, [normal[i] * _dot(x_vec, normal) for i in range(3)])
        x_dir = _norm(x_proj if math.sqrt(_dot(x_proj, x_proj)) > 1e-9 else _sub(pts[1], pts[0]))
        y_dir = _norm(_cross(normal, x_dir))
        pts_2d = [[round(_dot(_sub(p, o), x_dir), 4), round(_dot(_sub(p, o), y_dir), 4)] for p in pts]

        sketch_name = f"{name}_Path"
        self.cmd(
            "sketch.create",
            name=sketch_name,
            plane={
                "origin": [round(c, 4) for c in o],
                "x_dir": [round(c, 6) for c in x_dir],
                "y_dir": [round(c, 6) for c in y_dir],
            },
        )
        if len(pts_2d) == 2:
            self.cmd("sketch.line", points=pts_2d)
            path_id = "l1"
        else:
            self.cmd("sketch.spline.fit_point", points=pts_2d, periodic=periodic)
            path_id = "s1"
        self.cmd(
            "sketch.constraint.fix",
            entities=[f"p{i + 1}" for i in range(len(pts_2d))],
            fixed=True,
        )
        self.cmd("sketch.finish")
        return self.cmd(
            "solid.pipe",
            name=name,
            path_sketch=sketch_name,
            path=path_id,
            diameter=diameter,
            wall=wall,
            operation=operation,
            targets=targets,
            body_name=body_name,
        )

    def torus(
        self,
        name: str,
        center: Sequence[float],
        major: Any,
        minor: Any,
        *,
        operation: str = "new_body",
        targets: Optional[List[str]] = None,
        body_name: Optional[str] = None,
    ) -> "SolidCraftBuilder":
        """Create a DOF=0 Z-axis torus (follow with solid.move rotate if needed for X/Y axis)."""
        return self.cmd(
            "solid.torus",
            name=name,
            center=[round(float(c), 4) for c in center],
            major=major,
            minor=minor,
            operation=operation,
            targets=targets,
            body_name=body_name,
        )

    def tube_between(
        self,
        name: str,
        p0: Sequence[float],
        p1: Sequence[float],
        diameter: Any,
        *,
        operation: str = "new_body",
        targets: Optional[List[str]] = None,
        body_name: Optional[str] = None,
        extend_start_mm: float = 0.0,
        extend_end_mm: float = 0.0,
    ) -> "SolidCraftBuilder":
        """Straight structural tube between two 3D points with exact unit-vector math.

        Use `extend_start_mm` / `extend_end_mm` (e.g., 1.0–3.0 mm) when joining
        to an existing tube/lug so faces overlap cleanly instead of producing
        'separate pieces' warnings. For bent/formed tubing, use `swept_tube()`.
        """
        dx, dy, dz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length < 1e-6:
            raise ValueError(f"tube_between `{name}` has zero length: {p0} -> {p1}")
        ux, uy, uz = dx / length, dy / length, dz / length
        base = [
            round(p0[0] - ux * extend_start_mm, 4),
            round(p0[1] - uy * extend_start_mm, 4),
            round(p0[2] - uz * extend_start_mm, 4),
        ]
        total_h = round(length + extend_start_mm + extend_end_mm, 4)
        axis = [round(ux, 6), round(uy, 6), round(uz, 6)]
        return self.cmd(
            "solid.cylinder",
            name=name,
            base=base,
            axis=axis,
            height=total_h,
            diameter=diameter,
            operation=operation,
            targets=targets,
            body_name=body_name,
        )

    def ring_boss(
        self,
        name: str,
        *,
        plane: str = "XY",
        offset: Any = 0,
        center: Sequence[Any] = (0, 0),
        outer_diameter: Any,
        inner_diameter: Any,
        height: Any,
        operation: str = "join",
        targets: Optional[List[str]] = None,
        body_name: Optional[str] = None,
    ) -> "SolidCraftBuilder":
        """Create a DOF=0 boss with concentric pilot hole as one ring profile (avoids boolean warnings)."""
        sk = f"{name}_Sketch"
        self.cmd("sketch.create", plane=plane, name=sk, offset=offset)
        self.cmd("sketch.circle.center", center=list(center), diameter=outer_diameter)
        self.cmd("sketch.circle.center", center=list(center), diameter=inner_diameter)
        self.cmd("sketch.constraint.fix", entities=["c1.center", "c2.center"], fixed=True)
        self.cmd("sketch.dimension", entities=["c1"], type="diameter", value=outer_diameter)
        self.cmd("sketch.dimension", entities=["c2"], type="diameter", value=inner_diameter)
        self.cmd("sketch.finish")
        return self.cmd(
            "solid.extrude",
            name=name,
            sketch=sk,
            distance=height,
            operation=operation,
            targets=targets,
            body_name=body_name,
        )

    def safe_fillet(
        self,
        name: str,
        edges: List[Any],
        radius: Any,
        body: Optional[str] = None,
    ) -> "SolidCraftBuilder":
        """Add a fillet feature (validated against v0.49 schema)."""
        return self.cmd("solid.fillet", name=name, edges=edges, radius=radius, body=body)

    def to_script(self) -> Dict[str, Any]:
        return {"commands": list(self.commands)}

    def save_json(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_script(), indent=2), encoding="utf-8")
        return out
