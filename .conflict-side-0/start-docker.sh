#!/usr/bin/env bash
# start-docker.sh — Linux Docker fallback for core Guaardvark stack (API + UI + Ollama).
# Primary install path remains ./start.sh (full native stack with plugins/GPU).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GPU_PROFILE=0
DETACH=1
for arg in "$@"; do
    case "$arg" in
        --gpu) GPU_PROFILE=1 ;;
        --foreground|-f) DETACH=0 ;;
        -h|--help)
            echo "Usage: ./start-docker.sh [--gpu] [--foreground]"
            echo ""
            echo "  --gpu          Enable NVIDIA GPU profile for Ollama/backend (requires nvidia-container-toolkit)"
            echo "  --foreground   Run in foreground (default: detached -d)"
            echo ""
            echo "Core stack only: API, UI, PostgreSQL, Redis, Ollama."
            echo "For the full native stack (plugins, agent display, ComfyUI), use ./start.sh"
            exit 0
            ;;
    esac
done

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker not found. Install Docker Engine, then re-run." >&2
    exit 1
fi

COMPOSE=(docker compose)
if ! docker compose version >/dev/null 2>&1; then
    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE=(docker-compose)
    else
        echo "Error: docker compose plugin not found." >&2
        exit 1
    fi
fi

COMPOSE_FILES=(-f docker-compose.yml)
if [ "$GPU_PROFILE" -eq 1 ]; then
    COMPOSE_FILES+=(-f docker-compose.gpu.yml)
fi

UP_ARGS=(up --build)
if [ "$DETACH" -eq 1 ]; then
    UP_ARGS+=(-d)
fi
if [ "$GPU_PROFILE" -eq 1 ]; then
    echo "Starting Guaardvark (Docker, GPU)..."
else
    echo "Starting Guaardvark (Docker, CPU)..."
fi

"${COMPOSE[@]}" "${COMPOSE_FILES[@]}" "${UP_ARGS[@]}"

echo ""
echo "  Web UI:       http://localhost:5173"
echo "  API:          http://localhost:5000"
echo "  Health:       http://localhost:5000/api/health"
echo "  Stop:         docker compose down"
echo ""
echo "  Note: Docker mode runs the core stack only. Use ./start.sh for the full install."
