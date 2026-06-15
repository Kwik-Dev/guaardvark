# Guaardvark Code Release

## Backup Information
- **Date:** 2026-06-14 19:06:09
- **Type:** Code Release (no data — database and files are created fresh on first run)

## Install

1. **Extract:**
   ```bash
   unzip guaardvark_code_release_20260614_190608.zip
   cd guaardvark
   ```

2. **Start:**
   ```bash
   ./start.sh
   ```

The startup script handles everything: dependencies, database, frontend build, and all services.

| Service | URL |
|---------|-----|
| Web UI | http://localhost:5173 |
| API | http://localhost:5000 |
| Health Check | http://localhost:5000/api/health |

## Troubleshooting

- Permission issues: `chmod +x *.sh`
- Health diagnostics: `./start.sh --test`
- Check logs in `logs/`

## Data

To restore existing data, use a separate Guaardvark data backup.
