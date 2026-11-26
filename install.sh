#!/usr/bin/env bash
set -euo pipefail

# Idempotent installer for solid_craft_strata_hermes_engineer
# Usage: ./install.sh [--with-fea]

WITH_FEA=0
for arg in "$@"; do
  case "$arg" in
    --with-fea) WITH_FEA=1 ;;
    *) echo "Unknown flag: $arg (supported: --with-fea)" >&2; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SC_PROFILE="$HOME/.hermes/profiles/solidcraft"
DEFAULT_SKILLS="$HOME/.hermes/skills"

echo "==> Installing Python CAD & FEA tools to $SC_PROFILE/tools/"
mkdir -p "$SC_PROFILE/tools"
cp -f "$SCRIPT_DIR"/tools/*.py "$SC_PROFILE/tools/"
chmod +x "$SC_PROFILE/tools/blueprint_2d.py" "$SC_PROFILE/tools/solidcraft_oracle.py" "$SC_PROFILE/tools/solidcraft_fea.py"

echo "==> Installing generic CAD & FEA skills to both solidcraft and default profiles"
mkdir -p "$SC_PROFILE/skills" "$DEFAULT_SKILLS"
cp -rf "$SCRIPT_DIR"/skills/* "$SC_PROFILE/skills/"
cp -rf "$SCRIPT_DIR"/skills/* "$DEFAULT_SKILLS/"

if [ -f "$SC_PROFILE/SOUL.md" ]; then
  BACKUP="$SC_PROFILE/SOUL.md.bak_$(date +%Y%m%d_%H%M%S)"
  cp "$SC_PROFILE/SOUL.md" "$BACKUP"
  echo "==> Backed up existing SOUL.md to $BACKUP"
fi
cp -f "$SCRIPT_DIR/configs/SOUL.md" "$SC_PROFILE/SOUL.md"

if [ "$WITH_FEA" -eq 1 ]; then
  echo "==> Setting up isolated FEA venv at $SC_PROFILE/fea-venv"
  python3 -m venv "$SC_PROFILE/fea-venv"
  "$SC_PROFILE/fea-venv/bin/pip" install --upgrade pip
  "$SC_PROFILE/fea-venv/bin/pip" install gmsh scikit-fem scipy meshio trimesh matplotlib pillow
fi

echo ""
echo "Done! Next steps:"
echo "  1. Merge configs/hermes-solidcraft-config.yaml (local-first) OR"
echo "     configs/hermes-manager-worker-config.yaml (OpenAI manager -> Strata workers)"
echo "     into $SC_PROFILE/config.yaml (set <STRATA_HOST> and OPENAI_API_KEY)."
echo "  2. In Hermes Desktop, switch to the 'solidcraft' profile (bottom-left sidebar) and start a New session."
