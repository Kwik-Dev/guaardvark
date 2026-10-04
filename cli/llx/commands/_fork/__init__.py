"""Fork-owned CLI commands.

`cli/` is upstream-owned — no `cloud-plus` commit has ever modified it — so every
line under this package is permanent divergence. Keeping it in one package is what
keeps the upstream diff to a single import and a single loop in `llx/main.py`.

See `registry.py` for how a command is added, and `api_coverage.py` for the map the
spec-parity test enforces against `backend/api/`.

Nothing here yet: Phase 0 is the seam and its tests. Phase 1 adds the first groups.
"""
