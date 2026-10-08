# Handoff — reproducible generation CLI (issue #8)

**Status:** done — implemented, unit/contract/e2e-tested, live-verified, and landed on `origin/cloud-plus` (tip `20ffaa37`). No PR.
**Branch:** `cli-reproducible-generation` (plus `fix/issue8-followup`) → landed on `origin/cloud-plus`.
**Issue:** Kwik-Dev/guaardvark#8 (closed), and #7 for the `music-video list` output path.
**Parked (user):** the live `infographic generate → reproduce` row needs the FLUX weights (~17.85 GB: `flux_unet` 12.7, `t5xxl` 4.9, `clip_l` 0.25). Same class of parked item as `videos model-download` / `images model-download`; the path is covered by `cli/tests/test_reproducibility_e2e.py`, so this is an asset-installation gap, not a code gap.

## Commits

| sha | commit |
|---|---|
| `e6d7785b` | fix(cli): reproducible generation — dry-run, recorded settings, reproduce |
| `76385671` | fix(cli): address review — image-mode prompt, dry-run for the remaining generators |
| `8e9c37fc` | fix(cli): review round 2 — non-dict body crash, project_id, full dry-run coverage |
| `9344df11` | fix(cli): review round 3 — file-path create dry-run shows both writes |

## What this branch does

Three guarantees for generation commands:

1. **`--dry-run`** shows the request the command would send — inputs, each setting with provenance (`explicit` / `command default`), method, path, body, upload — and sends **no write**. It may issue read-only GETs (a command resolves the active model before building its body).
2. **Recorded settings** are shown by the `status` views, so you can see what produced an artifact.
3. **`reproduce <id>`** rebuilds the create from the record, emits a copy-pasteable command plus the body, and (with `--yes`) re-runs it through the same `_api_guard` gate/audit as `api request`. Never releases an approval gate.

### `--dry-run` coverage

`images generate`, `generate image`, `generate csv`, `videos generate`, `videos from-image`, `videos combine`, `music-video create`, `film-crew create`, `audio music|sfx|tts`, `infographic generate`, `cast generate`, `upscale image`, `upscale video`, `video-editor render`, `video-editor analyze`, `video-editor captions-burn`.

### `reproduce` coverage

`images reproduce <batch>`, `videos reproduce <batch>`, `music-video reproduce <id>`, `film-crew reproduce <id>`.

### New create flags

- `music-video create`: `--treatment --cast <id…> --lora-consistency --keyframe-model --planning-mode --fill-method --max-stretch --interp`
- `film-crew create`: `--settings '<json>'` or `--settings @file.json`

## Files (all CLI code is fork-owned)

| File | Role |
|---|---|
| `cli/llx/commands/_fork/dry_run.py` | capture-based preview (`preview`) + direct renderer (`render_request`) |
| `cli/llx/commands/_fork/dry_run_gen.py` | `--dry-run` overrides on upstream app commands |
| `cli/llx/commands/_fork/create_full.py` | `music-video create` / `film-crew create` full inputs + dry-run |
| `cli/llx/commands/_fork/settings_view.py` | human `status` shows recorded settings (json delegates) |
| `cli/llx/commands/_fork/reproduce.py` | the four `reproduce` commands |
| `cli/llx/commands/_fork/{cast,upscale,video_editor,captions,infographic}.py` | `--dry-run` added directly (fork-owned) |
| `backend/api/music_video_api.py` | **one non-`_fork/` hunk**: `_mv_dict` exposes `subject_ids`, `user_treatment`, `settings` |
| `cli/tests/test_dry_run_generate.py`, `test_create_full.py`, `test_settings_view.py`, `test_reproduce.py`, `test_golden_json.py`, `test_music_video_cli.py` | tests |
| `docs/CLI_SPEC.md` | command tables + "Reproducibility (issue #8)" section |

Fork rule: `cli/` is upstream-owned; changes live in `_fork/` (override pattern, `docs/CLI_SPEC.md` §11). **No upstream `cli/llx/commands/*.py` file is edited.**

## What is tested today (and what is not)

**Passing:** `cd cli && backend/venv/bin/python -m pytest tests -q -m "not e2e"` → **619 passed, 34 deselected**. The e2e tier (`-m e2e`, scratch Postgres via `GUAARDVARK_E2E_DATABASE_URL`) → **32 passed, 2 skipped**, re-runnable.

The fast tier asserts, per command, `fake_backend.posted_paths() == []` (the negative no-write contract), plus JSON shapes, provenance, the TTY branch for uploads, reproduce round-trip fields, redaction and `NO_RECORD` paths. The e2e tier drives real requests through the Flask app, including a full `generate → jobs → reproduce` round-trip for audio and infographic.

**Live-verified for real** (issue #8 comments): restarted backend, live audio TTS → `audio jobs` → `audio reproduce` → `--yes` audited; live images (`zimage-turbo`) and videos (`wan22-5b`) create→status→reproduce; live film-crew and music-video create with the full flags → status → named reproduce. The infographic live render is parked (FLUX weights not installed); see the header.

## E2E test plan (next session)

Preconditions:
- Backend running (`guaardvark health`). Note the running process **predates** the `_mv_dict` change, so it must be **restarted** before the music-video settings/reproduce checks will show `subject_ids`/`user_treatment`/`settings` over HTTP. (The change was verified by calling `_mv_dict` directly in the backend venv — see below.)
- Work on throwaway inputs where a test creates an artifact, and delete the artifact afterwards (`... delete`, `film-crew delete`, `music-video delete`, `images delete`, `videos delete`).

### 0. Baseline (record these before anything)
```bash
guaardvark images list --json      | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["data"]["batches"]))'
guaardvark videos list --json      | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))'
guaardvark music-video list --json | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))'
guaardvark film-crew list --json   | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))'
guaardvark gpu status --json        | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["owner"])'
```
Current values on this box: images 0, videos 5, music-video 2, film-crew 1, gpu owner `none`.

### 1. Backend restart picks up `_mv_dict`
Restart the backend (the user starts/stops services; do not do it unattended), then:
```bash
guaardvark music-video status 2 --json | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("subject_ids"), len(d.get("user_treatment") or ""), sorted((d.get("settings") or {}).keys()))'
```
Expect `[1]`, `622`, and keys incl. `fill_method`, `max_stretch`, `interpolation_multiplier`.
Before restart it prints `None 0 []` — that is the known lag, not a bug.

Until a restart, verify the backend function directly:
```bash
backend/venv/bin/python - <<'PY'
from backend.app import app
from backend.models import MusicVideo
from backend.api.music_video_api import _mv_dict
with app.app_context():
    print(_mv_dict(MusicVideo.query.get(2)).get("subject_ids"))
PY
```

### 2. Dry-run matrix, live, no spend
Run every command from the coverage list with `--dry-run --json` against the live backend. Assert: exit 0, a `dry-run` payload with a method/path, and **no write** — i.e. repeat the four counts and the GPU owner from step 0 and assert they are unchanged. `guaardvark api audit` only covers the `api` escape hatch, so use the counts, not the audit log, as the no-write evidence for named commands.

Also run the two TTY cases under `script -q /dev/null …` and assert exit 0 (this is the branch that crash-tested earlier): `music-video create --song <file> --style x --dry-run` and `upscale image <file> --dry-run`.

### 3. Settings exposure, live, human + json
- `images status ImageBatch_08-20-2026_031335_001` → shows the recorded prompt + params.
- `videos status FFmpeg_f1f1594f` → warns "no recorded settings" (an editor batch; `retry_data` is null). There is **no AI video batch on this box** to prove the positive case — create one if the run needs it.
- `music-video status 2` → after restart, cast + treatment + settings (before restart, blanks).
- `film-crew status 3` → warns (production 3 has `settings_json == {}`, it predates the flags).

### 4. Reproduce round-trip
- Emit-only first: `images reproduce <batch> --json`, `videos reproduce <batch> --json`, `music-video reproduce <id> --json`, `film-crew reproduce <id> --json`. Assert `sent: false` and that the body carries every recorded field (`ui_config`, `prompt` for image-mode video, `project_id` for music-video).
- Named-vs-generic: music-video/film-crew should set `named_command_line` (curated command); images/videos should set it `null` with `inexpressible` naming the fields the named command cannot express.
- Live re-run (this spends GPU / creates a project): pick a **cheap** scratch case, run `... reproduce <id> --yes --json`, then assert the new artifact's record matches the source (prompts, params/settings). Then delete the created artifact. Verify `guaardvark api audit` gained an `ok` entry for the route (proves `--yes` is audited).
- Do **not** `--yes` a film-crew or music-video reproduce you do not want to sit in the pipeline; they create a row and start the screenwriter / analyser.

### 5. Create flags persist
Create a scratch music-video and a scratch film-crew production with the new flags, then read them back:
```bash
guaardvark music-video create --song 20 --style "e2e probe" --treatment "probe" --cast 1 \
  --lora-consistency --planning-mode narrative --fill-method forward --max-stretch 2 --interp 2 --json
guaardvark film-crew create --script "INT. ROOM - DAY\nProbe." --model wan22-5b \
  --settings '{"image_model":"flux-dev"}' --json
```
Then `status --json` on each and assert `settings` / `settings_json` carry the values. Delete both. This is the step that was never run live (deliberately, to avoid creating artifacts).

### 6. Audio — DONE (was the known gap)
`audio music/sfx/tts` are now recorded in the main-DB `AudioGeneration` table; `audio jobs [<id>]` lists them and `audio reproduce <id>` replays the exact request (`--yes` through `_api_guard`, audited; a voice clone still needs its consent record). Live-verified: a TTS produced generation 1, `audio jobs` showed inputs `{"text": ...}`, `reproduce` emitted the identical body, and `--yes` created generation 2 with an approved `ok` audit entry.

## Known limitations (accepted, documented)

- **Audio** is recorded in the main DB (`AudioGeneration`) — no longer a limitation.
- **`images`/`videos`** `reproduce` prefers the named command for the recorded scalars; opaque Studio fields (`ui_config`, `content_preset`, `adapters`, `subject_ids`, …) still cannot be expressed by the named command, so it emits the generic `api request` line (lossless) and names them.
- **Infographic live render — PARKED (user).** The record→reproduce path is covered by `test_reproducibility_e2e.py` on the real Flask app with the ComfyUI generator faked; a *live* render is parked because this box lacks the FLUX weights (~17.85 GB). Same class as the parked `videos model-download` / `images model-download` items.
- **`captions-burn --dry-run`** shows the caption-import request (the first write) and says the burn body cannot be shown until the backend parses those captions.
- **Server-resolved model/clamps** are not shown when `--model`/size is omitted; the output says so instead of fabricating a value. The body shown is exactly what the client sends.
- **Human `list` rows** carry no settings — that lives in `status` by design (adding `retry_data` to every list row is the heaviness #7 removed from `music-video list`).
- **One non-`_fork/` hunk** (`backend/api/music_video_api.py::_mv_dict`) is unavoidable: the CLI is a REST client and no route exposed those fields. Watch it on the next upstream sync.

## Traps to remember

- When overriding an upstream Typer command, **redeclare every upstream option and pass it through**. Omitting one passes Typer's `OptionInfo` object as a truthy default — the `--wait` bug that made `audio music` call `read_job`.
- `dry_run._split` must return `({}, {})` for a non-dict body. An upload has `body=None`; treating it as an entry crashed the human TTY branch and produced `{"value": null, "source": "explicit"}` under `--json`. The pipe-based tests never reach the human branch — cover it with `monkeypatch.setattr(output, "is_pipe", lambda: False)`.
- Reuse the upstream module's symbols (`_music_video.get_client`, `get_global_server`) inside an override so fixtures that patch `llx.commands.<mod>` still drive it.
- `upscale image` posts multipart through `client.http`, which the write capture cannot see — use `render_request`, not `preview`, or the dry run would actually send.
- Git: this repo runs GitButler (`gitbutler/workspace`). Use `but` (`but status`, `but commit -b <branch> -m … <ids>`, `but push <branch>`), never raw `git add/commit/push`.

## Suggested next actions

1. Done: §1–§5 verified live and recorded on issue #8 (closed).
2. Done: the record→reproduce round-trip is an `e2e` tier (`test_reproducibility_e2e.py`, idempotent).
3. Done: audio persistence (`AudioGeneration` + `audio jobs`/`reproduce`).
4. Done: the recorded scalar flags on `images generate` / `videos generate`.
5. **PARKED (user):** install the FLUX weights (~17.85 GB) to make the infographic live row pass.
6. Watch `backend/api/music_video_api.py` and the new `AudioGeneration`/`InfographicGeneration` models on the next upstream sync (`sync-upstream` skill).
