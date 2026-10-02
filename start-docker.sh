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

# ── API key ────────────────────────────────────────────────────────────────
# The UI reaches the backend through the frontend container, so every browser,
# this host's included, counts as another device, and protected actions need
# the install's API key. docker compose reads GUAARDVARK_API_KEY from .env next
# to docker-compose.yml (a value exported in this shell wins over it).
ENV_FILE="$SCRIPT_DIR/.env"
env_file_key() {
    [ -f "$ENV_FILE" ] || return 0
    { grep -E '^GUAARDVARK_API_KEY=' "$ENV_FILE" || true; } | tail -n 1 | cut -d= -f2- | tr -d "\"' \r"
}
NEW_API_KEY=""
if [ -z "${GUAARDVARK_API_KEY:-}" ] && [ -z "$(env_file_key)" ]; then
    # 32 random bytes as URL-safe base64, the same form the backend makes.
    NEW_API_KEY="$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n')"
    if [ ! -f "$ENV_FILE" ]; then
        (umask 077 && : > "$ENV_FILE")
    elif grep -q '^GUAARDVARK_API_KEY=' "$ENV_FILE"; then
        # Its GUAARDVARK_API_KEY= line is empty: replace it rather than add a
        # second one. Rewriting in place keeps the file's owner and mode.
        _kept="$(grep -v '^GUAARDVARK_API_KEY=' "$ENV_FILE" || true)"
        if [ -n "$_kept" ]; then printf '%s\n' "$_kept" > "$ENV_FILE"; else : > "$ENV_FILE"; fi
    elif [ -n "$(tail -c 1 "$ENV_FILE")" ]; then
        # The last line has no newline; do not glue the key onto it.
        printf '\n' >> "$ENV_FILE"
    fi
    printf 'GUAARDVARK_API_KEY=%s\n' "$NEW_API_KEY" >> "$ENV_FILE"
    # Same rule as start.sh: .env holds secrets, so only its owner reads it.
    chmod 600 "$ENV_FILE"
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
if [ -n "$NEW_API_KEY" ]; then
    echo "  ────────────────────────────────────────────────────────────────"
    echo "  API key for this install (saved as GUAARDVARK_API_KEY in .env):"
    echo ""
    echo "      $NEW_API_KEY"
    echo ""
    echo "  Open the Web UI, go to Settings → API key, paste it and press Save."
    echo "  That signs the browser in; do it once in every browser you use."
    echo "  Running tools, automation, backups and other protected actions need it."
    echo "  ────────────────────────────────────────────────────────────────"
else
    echo "  API key:      GUAARDVARK_API_KEY in .env (grep GUAARDVARK_API_KEY .env);"
    echo "                enter it once in Settings → API key in each browser."
fi
echo ""
echo "  Note: Docker mode runs the core stack only. Use ./start.sh for the full install."
