# KNOWN_BUGS.md

Live log of bugs, known issues, and intentional limitations discovered during
inspection/testing. Record issues here **before** fixing, unless the user explicitly
asks for a fix. Update this file with end-to-end test results.

Feature requests and future work live in `FEATURES.md`. `AGENT.md` is the contract:
when a bug is found, write it here first, and consult this file before touching the
affected area.

---

## [OPEN] Chat responses contain mojibake for non-ASCII UTF-8 characters (em-dash → `â`)

- **Status:** Root cause fixed in source (code change applied, NOT yet active on a running
  instance); data repair of existing messages still outstanding.
- **Area:** Backend chat streaming → cloud LLM providers.

### Symptom
Streamed chat responses show garbled characters for non-ASCII UTF-8, e.g.:
- em-dash `—` renders as `â\x80\x94` (visible as a stray `â`)
- smart quotes, `─` box-drawing separator lines, accented chars all corrupted

Examples (as stored in `llm_messages`):
```
...do with it â\x80\x94 generate an image, convert it, or something else?
```

### Root cause
`requests` defaults `Response.encoding` to `ISO-8859-1` when the server returns a
text `Content-Type` **without** `charset=utf-8`. The cloud LLM endpoints
(`https://ollama.com/v1`, Mistral) return `Content-Type: application/json` with no
charset. The streaming reader `resp.iter_lines(decode_unicode=True)` therefore
decodes the UTF-8 response bytes as Latin-1, producing `â\x80\x94` for a `—`
(`e2 80 94`), and re-saves it as UTF-8 (`c3 a2 c2 80 c2 94`).

Confirmed with a live reproduction:
```
WITHOUT fix:  em-dash â\x80\x94 end
WITH fix:     em-dash — end
```

### Files changed (fix already applied to source)
- `backend/services/openai_provider.py` — `_stream_chat`: added `resp.encoding = "utf-8"` before `iter_lines(decode_unicode=True)`.
- `backend/services/mistral_provider.py` — same one-line fix in its streaming path.

### Remaining work
1. **Restart the backend** so the running process loads the fixed code (new
   responses will be correct).
2. **Repair existing DB rows** — already-saved corrupted messages in
    `llm_messages` (and any other store) still contain the mojibake. The corruption
   is reversible for the affected chars:
    `content.encode("latin-1").decode("utf-8")`
    (only apply where the string actually contains the `â\x80\x94` / `â\x94\x80`
   byte sequences; ASCII-only rows are unaffected and must be skipped).
   Need to review which columns/tables are affected (at minimum `llm_messages.content`).

### Verification
- Restart backend, send a prompt that forces an em-dash / smart quote, confirm it
  renders as `—` not `â`.
- Confirm streaming path (`openai_provider`/`mistral_provider`) header/charset behavior.

---

## [OPEN] Upgrade bundled ComfyUI (v0.32.0 → latest v0.34.x)

- **Status:** Open — no fix applied. Deferred; will handle later (ComfyUI version bump).
- **Area:** `plugins/comfyui/ComfyUI` git checkout; `plugins/comfyui/scripts/restore_app.sh` (pins `COMFYUI_REF`); `plugins/comfyui/custom_nodes.manifest`; `plugins/comfyui/scripts/start.sh`.

### Context
The bundled ComfyUI plugin is a git checkout of `comfyanonymous/ComfyUI` pinned to **v0.32.0** via `COMFYUI_REF` in `restore_app.sh`. Latest upstream release tags are now **v0.33.x – v0.34.2**. Updating is not a blind `git pull` — see the caveats below.

### Caveats / why it's not a naive update
- **Custom nodes are pinned to SHAs** (`custom_nodes.manifest`: VideoHelperSuite, GGUF, Frame-Interpolation, KJNodes, CogVideoXWrapper, facerestore) **tested against v0.32.0**. A core bump can break them and requires re-pin + re-test.
- **`start.sh` applies version-sensitive patches** (`model_patcher.py`, `quant_ops.py`, and the `comfy-kitchen` PEP-585 annotation rewrite for torch ≤ 2.6) written for the 0.32 era — a large jump may need re-verification.
- **`restore_app.sh` rsyncs with `--delete`** and preserves **only** `models/`, `custom_nodes/`, `user/`, `input/`, `output/`, `temp/`. A restore therefore **wipes `extra_model_paths.yaml`** (the Z-Image ← Comfy-Desktop / `~/ComfyUI-Shared` bridge). Back it up first.
- Prefer a **tagged release** (e.g. `v0.34.2`) over unpinned HEAD; `COMFYUI_REF=` (empty = master HEAD) is deliberately flagged as risky in the script.

### Remaining work / steps
1. Back up `plugins/comfyui/ComfyUI/extra_model_paths.yaml` (and `plugin.local.json` if present).
2. `COMFYUI_REF=v0.34.2 bash plugins/comfyui/scripts/restore_app.sh`
3. Restore `extra_model_paths.yaml`.
4. Re-pin / re-test the custom-node SHAs in `custom_nodes.manifest` against the new core; `install_deps.sh` + restart.
5. Verify: `ComfyUIImageGenerator.comfyui_installed_engines()` still returns `['zimage']`, the WAN i2v loaders still register, and smoke-test one Z-Image still + one WAN i2v queue.

---

## [OPEN] Film Crew editor has no audio-FX / custom-audio layer (only generated VO + music)

- **Status:** Open — no fix applied. Documented in `docs/AUDIO.md` §4 B.
- **Area:** `backend/services/swarm/agents/editor.py`, `backend/services/swarm/clients.py`, `backend/tasks/production_swarm_tasks.py`.

### Symptom
A Film Crew (sequential pipeline) render mixes exactly two audio layers — per-shot
**voiceover** (dialogue → TTS) and one **generated music** track (from `scene_mood`).
There is **no way to inject your own FX or other audio file** (or to have FX generated
per shot) into the final MP4. The AudioFoundry `/generate/fx` endpoint exists
(`stable-audio-open-1.0`, works on MPS) but **no render path calls it** — it is
effectively an unused hook.

### Root cause
- `FfmpegRunner.concat_with_audio` (`backend/services/swarm/clients.py`) signature only
  accepts `video_clips`, `voiceovers`, `music_track` — no FX/extra-audio parameter.
- `Editor.render` (`backend/services/swarm/agents/editor.py`) only calls
   `_render_voiceover()` and `_render_music()`; no FX hook.
- `run_editor` (`production_swarm_tasks.py`) builds `ShotInput` without an FX field, and
  never calls the plugin's `/generate/fx`.

The situation for FX/other audio is the same as for music (see `docs/AUDIO.md` §4 B):
no built-in option; must post-process or add code.

### Proposed fix (not yet implemented)
1. Add an `fx_track` (or generic `extra_audio`) parameter to
    `Editor.render` and `FfmpegRunner.concat_with_audio`; mix it alongside VO + music
   in the `amix` bed (with a volume/offset, like the `volume=0.35` music track).
2. Thread a per-shot FX path/description through `ShotInput` construction in
    `production_swarm_tasks.py`.
3. Optionally call the existing AudioFoundry `/generate/fx` endpoint per shot so the
   Art Director can *generate* FX instead of importing.

### Current workaround (no code)
Render the film, then overlay/replace audio with ffmpeg:
```bash
# Mix an FX/other track onto the finished film
ffmpeg -i final.mp4 -i my_fx.wav \
   -filter_complex "[0:a][1:a]amix=inputs=2:duration=longest[aout]" \
   -map 0:v -map "[aout]" -c:v copy -c:a aac output.mp4
# Replace the audio bed entirely
ffmpeg -i final.mp4 -i my_audio.wav \
   -map 0:v -map 1:a -c:v copy -c:a aac -shortest output.mp4
```

---

## [OPEN] Post-train smoke identity score uses the file-size proxy, not a real visual measure

- **Status:** Open — no fix applied. Documented; the `size` method is intentionally kept.
- **Area:** `backend/services/video_consistency_metrics.py` (`score_smoke_vs_refs`), `backend/services/lora_posttrain_smoke.py`.

### Symptom
After a successful cast LoRA train, the Cast UI shows a "Post-train smoke identity score" alert. For the Starship Captain run it read `0.69 (size · zimage)`. The `size` method is a **file-size ratio**, not a real visual identity measure, so the number is not a meaningful "how similar is the generated character to the refs" score.

### Root cause
`score_smoke_vs_refs` hardcodes `method="size"`:
```python
ident = score_identity_preservation(ref_image_paths, smoke_path, method="size")
```
It never tries the RGB-histogram cosine similarity (`method="hist"`), which is the real (if weak) visual measure. The `size` method just compares the smoke image's file size to the average ref size, clamped to `[0.3, 0.95]`.

### Why the histogram method is NOT used (and why `size` is kept)
When the histogram method was tried on the actual smoke image vs the refs, it scored **~0.08** — far below the `size` score of 0.69. The histogram compares **color distributions**, which are dominated by **lighting and scene**, not identity:
- The smoke image is a **neutral studio portrait** (`portrait, neutral studio lighting, sharp focus`).
- The training refs are **varied scenes/lighting** (bridge, cargo bay, planet surface, red-alert, etc.).
So the color histograms differ a lot even for the same character, making `hist` a poor identity measure for varied-scene refs. `size` is a rough but **stable** proxy that doesn't over-penalize lighting/scene differences.

### Remaining work / options
1. **Better identity measure** — a real face/identity embedding (e.g. a face-recognition model or CLIP image embedding) would score identity properly regardless of scene/lighting. This is the correct long-term fix but adds a model dependency.
2. **Or** keep `size` but relabel the alert so users don't misread it as a visual similarity score (e.g. "smoke render OK" instead of a numeric identity score).
3. **Or** make the smoke prompt match the refs' scene/lighting distribution so `hist` becomes meaningful.

### Verification
- `score_smoke_vs_refs(refs, smoke_12.png)` → `method: size` (current), `method: hist` would give ~0.08.

---

## [OPEN] `CollapsibleAlertSnackbar` is undefined / mispaired across 16 files (collapsible-alerts · PR g6)

- **Status:** Open — pre-existing in the `feat/collapsible-alerts` work, to be fixed in **PR g6**
   (`pr/g6-collapsible-alerts`, `b225fdc6` "G6: collapsible alerts"). **Not** caused by the
   `upstream/main` sync — the defect is already present at the pre-sync workspace commit `fa060471`.
- **Area:** `frontend/src/**` (the `feat(ui): collapsible alerts` conversion, branch `klq` / G6).
- **Symptom:** Files reference a `CollapsibleAlertSnackbar` component that is never defined or
  imported. ESLint reports `react/jsx-no-undef` and the affected routes fail to build/render.
- **Root cause:** The branch renamed `AlertSnackbar` *usages* to `CollapsibleAlertSnackbar` but
   (a) never created/imported a `CollapsibleAlertSnackbar` component, and (b) in some blocks renamed
  only the opening tag, leaving a mismatched `</AlertSnackbar>` closing tag (invalid JSX).
- **Affected files (16):**
   - *Mismatched closing tag* (`<CollapsibleAlertSnackbar> … </AlertSnackbar>`):
     `components/codeeditor/WordPressPagesCard.jsx`, `pages/WordPressPagesPage.jsx`,
     `pages/ClientPage.jsx`, `pages/WordPressSitesPage.jsx`, `pages/TrainingPage.jsx`,
     `pages/ProjectsPage.jsx`, `pages/ContentLibraryPage.jsx`, `pages/WebsiteDetailPage.jsx`,
     `pages/WebsitesPage.jsx`, `pages/ProjectDetailPage.jsx`.
   - *Undefined, self-closing* (`<CollapsibleAlertSnackbar … />`):
     `components/layout/SystemMetricsBar.jsx`, `components/modals/SystemMetricsModal.jsx`,
     `components/dashboard/SystemStatusCard.jsx`, `pages/ToolsPage.jsx`,
     `pages/DevToolsPage.jsx`, `pages/AgentsPage.jsx`.
- **Already fixed during the sync (do not re-break):**
   `frontend/src/pages/AutoresearchPage.jsx` (reverted to `AlertSnackbar`),
   `frontend/src/pages/RulesPage.jsx` (kept upstream `AlertSnackbar`/`MuiAlert`, dropped the unused
   `CollapsibleAlert` import).
- **Proposed fix (pick one, then run `npm run lint`):**
   1. Revert the opening tags back to `AlertSnackbar` (its import already exists in these files); or
   2. Add a real `CollapsibleAlertSnackbar` — a `React.forwardRef` wrapper accepting
      `open` / `message` / `severity` / `onClose` / `autoHideDuration` — and fix the mismatched
     closing tags to `</CollapsibleAlertSnackbar>`.
- **Verification:** `cd frontend && npm run lint` is clean; Routes / ClientPage / ToolsPage /
  DevToolsPage render without an undefined-component error.
- **Note:** The same defect exists in the squashed `pr/g6-collapsible-alerts` branch, so fix it
  there (and re-sync) rather than only in the workspace branch.

---

## [RESOLVED] AudioFoundry FX (stable-audio) — `RecursionError` on Apple Silicon MPS

- **Status:** Resolved. FX generation now works on MPS via a scheduler override.
- **Area:** `plugins/audio_foundry/backends/audio_fx_sao.py` (Stable Audio Open FX backend).

### Symptom
On Apple Silicon (MPS), the FX backend reported `available=True` and loaded, but a
`/generate/fx` request failed with:
```
RecursionError: maximum recursion depth exceeded
  File ".../numpy/core/_ufunc_config.py", line 111, in seterr
    old = geterr()
```

### Root cause
Not the SAO backend's generator. The model's default scheduler
(`CosineDPMSolverMultistepScheduler`) drives an **SDE solver via `torchsde`**, and
`torchsde`'s Brownian-motion path (`brownian_interval._split` →
`generator.generate_state(4)` → numpy `seterr`/`geterr`) recurses infinitely on
Apple MPS. It is a true infinite recursion, so raising `sys.setrecursionlimit()`
does **not** help.

### Fix applied
- `audio_fx_sao.py` and `run_acestep.py` use a `_pick_device()` helper
   (CUDA > MPS > cpu) so both FX and music load on MPS.
- `audio_fx_sao.py` swaps the pipeline scheduler to
   **`EDMDPMSolverMultistepScheduler`** (a non-SDE multistep scheduler) after load,
  which avoids `torchsde` entirely. Music (ACE-Step) and FX (stable-audio) both
  now generate valid WAVs on MPS.

### Verification
- `curl -X POST http://127.0.0.1:8206/generate/fx -H "Content-Type: application/json" -d '{"prompt":"thunder rumble","duration_s":5,"output_format":"wav"}'`
   → returns a valid 5.0s WAV (16-bit stereo 44.1 kHz).
- All three backends report `available=True` on MPS.
- Direct helper check passes: `_make_generator(123, "mps", fake_torch)` returns
   `None` after calling `torch.manual_seed(123)`, while CUDA still uses an
  explicit device generator.
