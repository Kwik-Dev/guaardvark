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
env_file_value() {
    [ -f "$ENV_FILE" ] || return 0
    { grep -E "^$1=" "$ENV_FILE" || true; } | tail -n 1 | cut -d= -f2- | tr -d "\"' \r"
}
env_file_key() { env_file_value GUAARDVARK_API_KEY; }
# Writes NAME=VALUE into .env, replacing an empty NAME= line rather than adding
# a second one. Rewriting in place keeps the file's owner and mode.
write_env_value() {
    if [ ! -f "$ENV_FILE" ]; then
        (umask 077 && : > "$ENV_FILE")
    elif grep -q "^$1=" "$ENV_FILE"; then
        _kept="$(grep -v "^$1=" "$ENV_FILE" || true)"
        if [ -n "$_kept" ]; then printf '%s\n' "$_kept" > "$ENV_FILE"; else : > "$ENV_FILE"; fi
    elif [ -n "$(tail -c 1 "$ENV_FILE")" ]; then
        # The last line has no newline; do not glue the value onto it.
        printf '\n' >> "$ENV_FILE"
    fi
    printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
    # Same rule as start.sh: .env holds secrets, so only its owner reads it.
    chmod 600 "$ENV_FILE"
}
NEW_API_KEY=""
if [ -z "${GUAARDVARK_API_KEY:-}" ] && [ -z "$(env_file_key)" ]; then
    # 32 random bytes as URL-safe base64, the same form the backend makes.
    NEW_API_KEY="$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n')"
    write_env_value GUAARDVARK_API_KEY "$NEW_API_KEY"
fi

# ── Database and queue passwords ───────────────────────────────────────────
# docker-compose.yml falls back to the stock "guaardvark" for both when .env
# does not set them. Hex keeps them safe inside the backend's connection URLs.
random_hex() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

# Redis keeps nothing on disk here, so any install can take a password at once.
NEW_REDIS_PASSWORD=0
if [ -z "${GUAARDVARK_REDIS_PASSWORD:-}" ] && [ -z "$(env_file_value GUAARDVARK_REDIS_PASSWORD)" ]; then
    write_env_value GUAARDVARK_REDIS_PASSWORD "$(random_hex)"
    NEW_REDIS_PASSWORD=1
fi

# PostgreSQL reads POSTGRES_PASSWORD only when it creates the database, so a
# password is generated only while this project has no database volume yet.
# An existing database keeps the password it was created with.
compose_project_name() {
    local name
    name="$({ "${COMPOSE[@]}" "${COMPOSE_FILES[@]}" config 2>/dev/null || true; } | sed -n 's/^name: *//p' | head -n 1 || true)"
    if [ -z "$name" ]; then
        name="${COMPOSE_PROJECT_NAME:-$(env_file_value COMPOSE_PROJECT_NAME)}"
    fi
    if [ -z "$name" ]; then
        name="$(basename "$SCRIPT_DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')"
    fi
    printf '%s' "$name"
}
NEW_POSTGRES_PASSWORD=0
STOCK_POSTGRES_PASSWORD=0
if [ -z "${GUAARDVARK_POSTGRES_PASSWORD:-}" ] && [ -z "$(env_file_value GUAARDVARK_POSTGRES_PASSWORD)" ]; then
    PROJECT_NAME="$(compose_project_name)"
    if PG_VOLUMES="$(docker volume ls -q \
            --filter "label=com.docker.compose.project=$PROJECT_NAME" \
            --filter "label=com.docker.compose.volume=pgdata" 2>/dev/null)" \
        && [ -z "$PG_VOLUMES" ]; then
        write_env_value GUAARDVARK_POSTGRES_PASSWORD "$(random_hex)"
        NEW_POSTGRES_PASSWORD=1
    else
        # A database already exists (or Docker could not say): keep the stock
        # password rather than lock the backend out of it.
        STOCK_POSTGRES_PASSWORD=1
    fi
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
if [ "$NEW_POSTGRES_PASSWORD" -eq 1 ]; then
    echo ""
    echo "  PostgreSQL has this install's own password: GUAARDVARK_POSTGRES_PASSWORD in .env."
fi
if [ "$NEW_REDIS_PASSWORD" -eq 1 ]; then
    echo ""
    echo "  Redis has this install's own password: GUAARDVARK_REDIS_PASSWORD in .env."
fi
if [ "$NEW_POSTGRES_PASSWORD" -eq 1 ] || [ "$NEW_REDIS_PASSWORD" -eq 1 ]; then
    echo "  The backend reads them from there; you need them only for psql or redis-cli."
fi
if [ "$STOCK_POSTGRES_PASSWORD" -eq 1 ]; then
    echo ""
    echo "  PostgreSQL still uses the stock password from when its database was"
    echo "  created. INSTALL.md (Docker → Passwords) shows how to change it."
fi
echo ""
echo "  Note: Docker mode runs the core stack only. Use ./start.sh for the full install."
