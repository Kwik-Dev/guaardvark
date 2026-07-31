# Guaardvark Code Release

## Backup Information
- **Date:** (filled by Code Release)
- **Type:** Code Release (no data — database and files are created fresh on first run)

## Install (Linux)

1. **Extract:**
   ```bash
   unzip guaardvark-release.zip
   cd guaardvark
   chmod +x start.sh start-docker.sh
   ```

2. **Start:**
   ```bash
   ./start.sh
   ```

The startup script handles everything: Python 3.12 (auto-installed if needed), dependencies, database, frontend build, and all services.

**Ubuntu 26.04 and other distros with Python 3.13+:** Your system `python3` may be 3.14 — that is fine. `./start.sh` installs Python 3.12 automatically via apt (deadsnakes PPA) or [uv](https://github.com/astral-sh/uv) when sudo is unavailable.

| Service | URL |
|---------|-----|
| Web UI | http://localhost:5173 |
| API | http://localhost:5000 |
| Health Check | http://localhost:5000/api/health |

First run may ask for your password once (PostgreSQL, Node.js, or Python packages via apt).

## Alternative: Docker (Linux, core stack only)

If you want to evaluate the UI/API without a native Python install:

```bash
./start-docker.sh          # CPU
./start-docker.sh --gpu    # NVIDIA GPU (requires nvidia-container-toolkit)
```

Docker runs the **core stack** (API, UI, PostgreSQL, Redis, Ollama). It does not include plugins, ComfyUI, or the virtual agent display. For the full experience, use `./start.sh`.

Stop: `docker compose down`

## Troubleshooting

- Permission issues: `chmod +x *.sh`
- Health diagnostics: `./start.sh --test`
- Wrong Python venv (e.g. after upgrade): `rm -rf backend/venv && ./start.sh`
- Check logs in `logs/`

## Data

To restore existing data, use a separate Guaardvark data backup.
