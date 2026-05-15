#!/bin/bash
# ResearchClaw experiment entrypoint — unified three-phase execution.
#
# Phase 0: pip install from requirements.txt (if present)
# Phase 1: Run setup.py for dataset downloads / preparation (if present)
# Phase 2: Run the main experiment script
#
# Environment variables:
#   RC_DOCKER_PHASE          — all | setup | experiment
#   RC_PERSIST_PIP_TARGET=1  — install requirements into /workspace/.rc_site
#   RC_ENTRY_POINT           — override entry point (default: first CLI arg or main.py)
set -e

WORKSPACE="/workspace"
PHASE="${RC_DOCKER_PHASE:-all}"
ENTRY_POINT="${RC_ENTRY_POINT:-${1:-main.py}}"
if [ "${RC_DISTRIBUTED_LAUNCH:-0}" != "1" ] && [ "$#" -gt 0 ]; then
    shift
fi

if [ "${RC_PERSIST_PIP_TARGET:-0}" = "1" ] || [ -d "$WORKSPACE/.rc_site" ]; then
    mkdir -p "$WORKSPACE/.rc_site"
    export PYTHONPATH="$WORKSPACE/.rc_site:${PYTHONPATH:-}"
fi

# ----------------------------------------------------------------
# Phase 0: Install additional pip packages
# ----------------------------------------------------------------
if [ "$PHASE" != "experiment" ] && [ -f "$WORKSPACE/requirements.txt" ]; then
    echo "[RC] Phase 0: Installing packages from requirements.txt..."
    if [ "${RC_PERSIST_PIP_TARGET:-0}" = "1" ]; then
        pip install --no-cache-dir --break-system-packages \
            --target "$WORKSPACE/.rc_site" \
            -r "$WORKSPACE/requirements.txt" 2>&1 | tail -20
    else
        pip install --no-cache-dir --break-system-packages \
            -r "$WORKSPACE/requirements.txt" 2>&1 | tail -20
    fi
    echo "[RC] Phase 0: Package installation complete."
fi

# ----------------------------------------------------------------
# Phase 1: Run setup script (dataset download / preparation)
# ----------------------------------------------------------------
if [ "$PHASE" != "experiment" ] && [ -f "$WORKSPACE/setup.py" ]; then
    echo "[RC] Phase 1: Running setup.py (dataset download/preparation)..."
    python3 -u "$WORKSPACE/setup.py"
    echo "[RC] Phase 1: Setup complete."
fi

if [ "$PHASE" = "setup" ]; then
    echo "[RC] Setup phase complete."
    exit 0
fi

# ----------------------------------------------------------------
# Phase 2: Run experiment
# ----------------------------------------------------------------
if [ "${RC_DISTRIBUTED_LAUNCH:-0}" = "1" ]; then
    echo "[RC] Phase 2: Running distributed experiment ($*)..."
    exec "$@"
fi

echo "[RC] Phase 2: Running experiment ($ENTRY_POINT)..."
exec python3 -u "$WORKSPACE/$ENTRY_POINT" "$@"
