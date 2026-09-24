Now let me create the handout.

## Status

**Status (2026-09-11): two threads — #182 deferred by the owner; native MPS Z-Image committed.**

### Thread A — #182 (M2 LoRA on MPS): evidence POSTED, awaiting owner

M2 carries the timeout commits (below). A **fresh 400-step run completed on
`4f902ba2`** producing `Elara_v2.safetensors` (400 steps, 768 res, rank 16, 11
images, wall 4514 s). The evidence comment was **posted 2026-09-11**:
https://github.com/guaardvark/guaardvark/pull/182#issuecomment-5631209625

It intentionally carries **no images** — §3 states that the LoRA-render image
follows with **M3/M4 after M2 merges** (M2 is training-only). Draft:
`/tmp/pr182_evidence_comment.md`. Fresh images remain in
`data/outputs/character_samples/1/` and `/tmp/pr182_fresh/` (not pushed).

The earlier confusion ("training failed at step 151/880") is resolved: a **completed
run already existed** — the 2026-08-27 Elara run (run B) finished all 640 steps and
saved `Elara_v1.safetensors` (33,457,088 bytes) in 7766.5 s (~2 h 09 m).

**Corrected chronology** (verified from the logs):

| When | What |
|------|------|
| 2026-08-26 21:05:24 | **Run A** (Elara, 640 steps) killed by the 1800 s daemon cap at step 151/640 — the failure `9626ae47` references. So the step-151 failure **preceded** the completed run; my earlier "later run" wording was wrong. |
| 2026-08-27 10:10:48 | `b855b9eb` committed (task limits 105 min → 255 min). |
| 2026-08-27 10:11:50 → 12:21:16 | **Run B** completes, saves `Elara_v1`, 7766.5 s. |
| 2026-08-27 14:03:04 | `9626ae47` committed (daemon 1800 s → 10800 s, load 900 s → 3600 s) — i.e. an **uncommitted working-tree edit** during run B. |
| 2026-09-10 20:40 → 21:11 | **Run D** (Elara, 880 steps) on the M2 snapshot killed at step 151/880 by the 1800 s cap. |

**Why run B proves the snapshot was insufficient:** run B ran 7766.5 s; M2's
committed daemon cap is 1800 s and the pre-`b855b9eb` task hard limit was 6300 s
(105 min) — both shorter, so run B cannot have run the committed M2 timeouts. The
task-limit half is solid (`b855b9eb` committed 62 s before the run; run B exceeded
the old 105-min limit). The daemon-cap half is inferred (run B survived past
1800 s, so the executing code had a larger cap; 10800 is the value that landed).

> **The Aug 27 run B evidence was rejected as stale** — the owner wants evidence
> from the *current* PR code. The first comment was deleted. A fresh run on
> `4f902ba2` was later approved and **completed** (see below); the combined
> timeout+evidence comment is drafted but not posted because #182 is deferred.

**Timeout-commit ownership (corrected again):** the timeout changes were never
orphaned — they were part of the **G3** snapshot (`pr/g3-runpod-lora-trainer`,
squashed `80da48a4`). They were missing only from **M2**. Per the owner's decision
they are now **M2-only**:

- `pr/m2-lora-trainer-mps` keeps `e9593922` (daemon 3 h / load 1 h) + `2e2ffa83`
  (task limits 255 min) + `8f2dd1d0` (fail loudly/reconcile) + `4f902ba2` (tests).
- `pr/g3-runpod-lora-trainer` was rebuilt without them and force-pushed
  `80da48a4` → **`68fe8451`** (RunPod work intact; `real_trainer.py` dropped from
  its diff; reaper back to 45 min).

> **Note:** `pr/*` are **not** snapshots of `dev`. Per `PR_WORKFLOW.md` each is cut
> from `upstream/main`; `dev` is the integration branch that merges them. Also,
> `upstream/main` has advanced `23ab76e3` → `4b9763ee`, so all `pr/*` are based on
> the older main.

**Fresh run (current M2 code `4f902ba2`) — COMPLETED 2026-09-11:** Elara (subject 1),
400 steps (explicit; the 880-step default is ~2.7 h with little margin), 768 res, 11
training images, rank 16, bf16. Wall time **4514 s (75.2 min)**; steps 1→400 loss
6.06 → 0.69; saved `data/training/loras/Elara_v2.safetensors` (33,457,088 bytes).
Subject 1 `lora_path` updated to v2. Fresh trainer log: `logs/lora_trainer_daemon.log`
(earlier logs preserved as `logs/lora_trainer_daemon.log.pre-fresh-*` / `.aborted-*`).

What was missing for the owner's bar was the image + the timeout commits:

- **Timeout fixes folded into `pr/m2-lora-trainer-mps`** — originally in the G3
  snapshot, moved to M2 (see above) and removed from G3. Pushed → PR head
  `4f902ba2`, still MERGEABLE.
- **Images generated with the trained LoRA on MPS** (Elara_v2, fresh run):
  - Comfy Z-Image route: `data/outputs/character_samples/1/fresh_lora_withlora.png`
    + `fresh_lora_nolora.png` (same seed).
  - direct diffusers `ZImagePipeline.to("mps")`: `fresh_lora_diffusers_mps.png`.
  - native offline MPS (no ComfyUI): `native_mps_withlora.png` / `native_mps_nolora.png`.
- **Evidence comment:** drafted at `/tmp/pr182_comment.md` — **NOT posted** (#182
  deferred). The earlier comment (deleted by the owner) was
  `#issuecomment-5628969822`.

**Environment:** Apple M5 Pro, 48 GB · macOS 26.6.2 (25G83) · torch 2.13.0 ·
diffusers 0.39.0 · peft 0.20.0 · `recommended_max_memory()` = 37.44 GB → trains at
768, no clamp line.

### Thread B — native MPS Z-Image (no ComfyUI) — committed, NOT pushed

Root cause: the offline `ZImagePipeline` refused `family == 'zimage'` on any
non-CUDA device, so Macs could only reach Z-Image through ComfyUI (M3).

**Fix (①)** — `backend/services/offline_image_generator.py`: detect Apple MPS
(CUDA > MPS > CPU, tiny probe → CPU fallback); load Z-Image/Krea2 DiTs in bf16 on
MPS instead of refusing them as CUDA-only; seed `torch.Generator` on CPU (MPS has
no generator device).

**② routing preference — DROPPED (owner decision, 2026-09-11).** A
`zimage_engine.py` selector that turned a truthy `GUAARDVARK_ZIMAGE_USE_COMFYUI`
into a fallback (and added `force`) was written, then dropped: a variable named
"USE_COMFYUI" that does *not* use ComfyUI when set to `1` is a footgun. The flag
keeps its original meaning (truthy = always ComfyUI) and stays owned by the
ComfyUI PR. Consequence: a leftover `=1` in `.env` still forces ComfyUI — the owner
commented theirs out, so the native path is what runs.

**GitButler:** vbranch **`feat/zimage-mps-native`** (commit `rwq`, CLI id `at`),
containing only `offline_image_generator.py` — **restored 2026-09-11** after an
earlier discard. It was briefly discarded on the wrong assumption that `dev` + the
M3 snapshot made it redundant; the workspace has its own (older) base, so it needs
its own copy. The stale remote `feat/zimage-mps-native` @ `67e4917b` (pre-reword,
still had ②) was deleted; the restored branch is **local-only, not pushed**.
Workspace verified again with ComfyUI down: `native_mps_nolora.png` in 58 s.

> **Three views of the same feature — do not conflate:**
> 1. **GitButler workspace** — vbranches merged onto `origin/main` (older base;
>    lacks `_seed_generator`, so `_generator_device()` is needed here).
> 2. **`pr/*` snapshots** — each cut from `upstream/main` (M3 = `d42dd552`; no RNG
>    change needed because `_seed_generator` already exists there).
> 3. **`dev`** — integration branch rebased onto `upstream/main` (`38d47345`).
> A change present in one does **not** imply it is present in the others.

> **Workspace vs PR snapshot differ on the RNG helper.** The workspace base
> predates upstream's `_seed_generator()`, so `xok` swaps
> `torch.Generator(device=self._device)` → `_generator_device()`. The new
> `upstream/main` already has `_seed_generator()`, and MPS generators work in this
> torch build (`torch.Generator(device='mps')` succeeds and is reproducible), so
> the **M3 snapshot omits the RNG change entirely**.

**Verified end-to-end with ComfyUI DOWN and the flag unset:** `render_character_still`
→ `engine: "offline"`, 768², 62 s (Elara_v2 LoRA) / 57 s (no LoRA) →
`data/outputs/character_samples/1/native_mps_{withlora,nolora}.png`. Identity
matches the trained character. `test_character_still_pipeline.py` 10/10 pass.

**`.env` change by the owner:** `GUAARDVARK_ZIMAGE_USE_COMFYUI=1` and
`GUAARDVARK_COMFYUI_DIR` are now commented out.

**Known pre-existing failure (not ours):** `test_offline_image_generator_vram.py`
→ 6 failures (`assert 21.0 == 24.0`; stale test vs the 2026-08-05 `_FAMILY_RAM_GB`
zimage 24.0→21.0 change).

**M1 finding (context):** M1 (`#154`) shipped only audio/whisper/video + GPU probing
— no still-image generation. On a Mac, upstream `main` cannot generate the default
Z-Image stills by any path (offline refuses non-CUDA; upstream Comfy has no Z-Image
graph); SDXL/FLUX stills need ComfyUI. This native-MPS work is the fix.

### Thread C — `dev` rebased + M3 inserted + M-track renumbered — DONE, pushed

**`dev`:** `4c33bea7` → `c80c9416` (rebase `--rebase-merges` onto `4b9763ee`,
force-pushed), then merged `pr/m3-zimage-mps-native` → **`38d47345`**
(fast-forward pushed). Now contains `upstream/main` + the native MPS work.

**New M3 snapshot:** `pr/m3-zimage-mps-native` @ **`d42dd552`**, cut from
`upstream/main` (`4b9763ee`), 1 file (`offline_image_generator.py`, +26/−5).
Self-contained, no ComfyUI dependency. Built by resetting a worktree and applying
4 manual edits (the workspace diff did not apply cleanly because the workspace base
predates `_seed_generator`).

**M3 PR:** **#183** (draft) — https://github.com/guaardvark/guaardvark/pull/183,
base `main`, mergeable. Evidence images hosted on branch
`evidence/pr-m3-zimage-mps-native` (`docs/evidence/pr-m3-zimage-mps-native/`).

**Renames (M-track only; G-track unchanged):**
- `pr/m3-comfyui-image` → **`pr/m4-comfyui-image`** (`a17b925d`, commit unchanged)
- `pr/m4-llm-providers` → **`pr/m5-llm-providers`** (`0cb64be0`, commit unchanged)
- old remote names deleted; `pr/g5-vision-llm-routing` still contains m5 ✅

**`PR_WORKFLOW.md`** branch map + submission order updated
(M2 → M3 → M4 → M5 → G1…G7).

**Still open:** M2 (#182) awaiting owner; **M3 is a draft PR (#183)** — ready to
mark ready for review / submit after M2 merges; then M4 → M5 → G1…G7.



---

## Previous session handout

---

# Guaardvark / Kwik-Dev Fork — Session Summary
**Date:** Sep 10–11 2026

---

## What was done

### 1. PR #154 (M1) merged — upstream is at `23ab76e3`

M1 was merged by the owner. All subsequent work rebases onto that new `upstream/main`.

---

### 2. All PR snapshot branches rebased onto new `upstream/main`

| Branch | Contents | Status |
|--------|----------|--------|
| `pr/m2-lora-trainer-mps` | LoRA training on MPS | ✅ rebased, **mergeable** |
| `pr/m3-comfyui-image` | ComfyUI Z-Image / imagemodel | ✅ rebased |
| `pr/m4-llm-providers` | LLM providers + voice routing | ✅ rebased |
| `pr/g1-audio-video-polish` | audio/music polish, i2v, filmcrew i2v speed *(no ffmpeg/captions)* | ✅ rebased |
| `pr/g2-mcp-start` | MCP server startup/cleanup | ✅ rebased |
| `pr/g3-runpod-lora-trainer` | RunPod LoRA trainer | ✅ rebased |
| `pr/g4-cast-shot-count` | Cast shot counts + chat routing | ✅ rebased |
| `pr/g5-vision-llm-routing` | Vision sync + LLM routing *(stacked on M4)* | ✅ rebased onto M4 |
| `pr/g6-collapsible-alerts` | Collapsible alerts *(G6 bug fixed)* | ✅ rebased |
| `pr/g7-agent-config-docs` | Agent config + docs | ✅ rebased |

**Parked (not rebased, not in dev):**

| Branch | Reason |
|--------|--------|
| `pr/ffmpeg-captions` | Conflicts with upstream video refactoring — work later |
| `pr/filmcrew-live-progress` | Parked from the start |

---

### 3. G6 collapsible-alerts bug fixed

`CollapsibleAlertSnackbar` / `CollapsibleAlertTitle` were referenced but never defined. Fixed in both `dev` and `pr/g6-collapsible-alerts` — replaced with `AlertSnackbar` / `AlertTitle`, removed unused imports, added missing `Alert` import.

---

### 4. `dev` integration branch rebuilt — green ✅

| Check | Result |
|-------|--------|
| eslint (`--max-warnings 0`) | ✅ clean |
| vitest | ✅ 230/230 pass |
| Python syntax | ✅ 0 errors |
| Conflict markers | ✅ none |
| `upstream/main` ancestor | ✅ mergeable |

`dev` = new `upstream/main` + M2 + M3 + M4 + G1 (no ffmpeg) + G2 + G3 + G4 + G5 + G6 + G7. Force-pushed to `origin/dev`.

---

### 5. PR #182 (M2) — owner review

Owner approved the code. **Needs one piece of evidence before merge:**

> "Evidence from one completed run on the Mac — the staging VAE/TE/transformer on mps lines, step/loss lines, the save line, wall time, machine, macOS and torch versions. One image generated with the resulting LoRA."

**Evidence gathered so far** (from `logs/lora_trainer_daemon.log`):

```
Machine:  Apple M5 Pro, 48 GB RAM
macOS:    26.6.2 (Build 25G83)
torch:    2.13.0  (mps.is_available() = True)
diffusers: 0.39.0 (ZImagePipeline OK)
peft:     0.20.0

[run_zimage_trainer] staging VAE on mps for latent cache...
[run_zimage_trainer] cached 11 latents @ 768; VAE off GPU      ← clamp = 768 (48 GB)
[run_zimage_trainer] staging text encoder on mps for prompt cache...
[run_zimage_trainer] cached 11 prompts; TE off GPU
[run_zimage_trainer] staging transformer+LoRA on mps (res=768, rank=16, dtype=torch.bfloat16)...
[run_zimage_trainer] step 1/880   loss=2.73438
[run_zimage_trainer] step 51/880  loss=0.56250
[run_zimage_trainer] step 101/880 loss=2.10938
[run_zimage_trainer] step 151/880 loss=2.06250
← crashed here (daemon timeout at ~1800 s)
```

**Still needed:** save line + wall time + one image with the LoRA loaded. Likely fix: raise the daemon timeout or run fewer steps (200 steps is enough to confirm the path works and produce a valid LoRA).

---

## Next steps (updated)

1. ~~Fix the timeout / retry the training run~~ — done: completed run evidence exists
   and the timeout commits are now in the PR.
2. ~~Generate one image with the resulting LoRA~~ — done (Comfy route + diffusers/MPS).
3. ~~Post the evidence on PR #182~~ — done, awaiting owner merge.
4. **Submit M3 PR** (`pr/m3-comfyui-image`) once M2 merges. Note M3 is what makes the
   Images page work for Z-Image on MPS.

---

## Key file locations

| File | Purpose |
|------|---------|
| `PR_WORKFLOW.md` | Snapshot branch map, current status, submission guide |
| `logs/lora_trainer_daemon.log` | Z-Image trainer stderr (staging lines, step/loss, saves) |
| `data/training/loras/Elara_v1.safetensors` | Elara LoRA from Aug 27 run (640 steps, MPS) |
| `data/models/stable_diffusion/Tongyi-MAI--Z-Image-Turbo/` | Z-Image weights (31 GB) |