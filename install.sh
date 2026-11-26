#!/usr/bin/env bash
set -euo pipefail

# Idempotent installer for solid_craft_strata_hermes_engineer
# Usage: ./install.sh [--with-fea] [--manager-worker]

WITH_FEA=0
MANAGER_WORKER=0
for arg in "$@"; do
  case "$arg" in
    --with-fea) WITH_FEA=1 ;;
    --manager-worker) MANAGER_WORKER=1 ;;
    *) echo "Unknown flag: $arg (supported: --with-fea, --manager-worker)" >&2; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SC_PROFILE="$HOME/.hermes/profiles/solidcraft"
DEFAULT_SKILLS="$HOME/.hermes/skills"

echo "==> Checking prerequisites"
if ! python3 -c "import PIL" 2>/dev/null; then
  echo "WARNING: Python 'pillow' (PIL) not found in system python3."
  echo "         Install it for blueprint_2d.py & solidcraft_oracle.py: pip install --user pillow"
fi
if ! command -v solidcraft-cli >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/solidcraft-cli" ]; then
  echo "WARNING: solidcraft-cli not found in PATH or ~/.local/bin/solidcraft-cli."
  echo "         Install SolidCraft v0.49+ first."
fi

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

CFG_SRC="$SCRIPT_DIR/configs/hermes-solidcraft-config.yaml"
if [ "$MANAGER_WORKER" -eq 1 ]; then
  CFG_SRC="$SCRIPT_DIR/configs/hermes-manager-worker-config.yaml"
fi
if [ ! -f "$SC_PROFILE/config.yaml" ]; then
  cp "$CFG_SRC" "$SC_PROFILE/config.yaml"
  echo "==> Created starter $SC_PROFILE/config.yaml from $(basename "$CFG_SRC")"
  echo "    IMPORTANT: Edit $SC_PROFILE/config.yaml and replace <STRATA_HOST> with your Strata server IP/hostname."
else
  echo "==> Existing $SC_PROFILE/config.yaml preserved (compare with $(basename "$CFG_SRC") to merge settings)."
fi

if [ "$WITH_FEA" -eq 1 ]; then
  echo "==> Setting up isolated FEA venv at $SC_PROFILE/fea-venv"
  python3 -m venv "$SC_PROFILE/fea-venv"
  "$SC_PROFILE/fea-venv/bin/pip" install --upgrade pip
  "$SC_PROFILE/fea-venv/bin/pip" install gmsh scikit-fem scipy meshio trimesh matplotlib pillow
fi

echo ""
echo "Done! Next steps:"
echo "  1. Set OPENAI_API_KEY in ~/.hermes/.env (used for gpt-6.1-sol visual QA / Manager-Worker mode)."
echo "  2. In SolidCraft GUI: Preferences -> Assistant -> enable the control channel on 127.0.0.1:27878."
echo "  3. In Hermes Desktop: switch to the 'solidcraft' profile (bottom-left sidebar) and start a New session."
