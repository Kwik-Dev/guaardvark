# PR Workflow — independent snapshot branches

How to ship PRs to upstream (`guaardvark/guaardvark`) from the Kwik-Dev fork.

## Approach (re-worked 2026-09-08)

Each PR group is a **standalone snapshot branch cut from `upstream/main`** (not a
cumulative merge into `dev`). Each snapshot contains **only its own group's
content**, so a PR diff against `upstream/main` is exactly that feature — no
carry-over from earlier PRs.

The old cumulative `pr/*` branches (each containing every prior PR) were deleted;
this is the clean re-work.

## Branch map

| Snapshot | Head | Base | Contents | Pushed | PR |
|----------|------|------|----------|--------|----|
| `pr/m1-macos-support` | `7aee86e5` | `upstream/main` | MPS audio/whisper/video (Film Crew split out) | ✅ | **#154** (merged) |
| `pr/m2-lora-trainer-mps` | `f3192b05` | `upstream/main` | LoRA training on MPS | ✅ | **#182** (merged) |
| `pr/m3-zimage-mps-native` | `ca528a2e` | `upstream/main` | native MPS Z-Image stills (offline diffusers, no ComfyUI) | ✅ | **#183** (merged) |
| `pr/m4-comfyui-image` | `ec8ec815` | `upstream/main` | ComfyUI Z-Image / `/imagemodel comfyui`, opt-in route *(was M3)* | ✅ | **#197** (open) |
| `pr/m5-llm-providers` | `f135f774` | `upstream/main` | LLM providers — OpenAI-compatible routing + smart escalation *(was M4)* | ✅ | ~~**#214**~~ (closed unmerged — fork-only) |
| `pr/discord-voice` | `a0248365` | `upstream/main` | Discord voice opt-in backend (Guaardvark Audio Foundry default, pi-omni router fallback) | ✅ | ☐ |
| `pr/g1-audio-video-polish` | `504d4a25` | **`pr/m5-llm-providers`** | audio/music polish, i2v, filmcrew i2v speed (restacked on the current M5) | ✅ | ☐ |
| `pr/ffmpeg-captions` | `c24c6f37` | `upstream/main` | ffmpeg + captions (parked, needs rebase onto new main) | ✅ | ☐ |
| `pr/g2-mcp-start` | `0e1efd1c` | `upstream/main` | MCP server startup/cleanup | ✅ | ☐ |
| `pr/g3-runpod-lora-trainer` | `9cc5c442` | `upstream/main` | RunPod LoRA trainer + timeout fixes | ✅ | ☐ |
| `pr/g4-cast-shot-count` | `7d5ec1e6` | `upstream/main` | Cast shot counts + chat routing | ✅ | ☐ |
| `pr/g5-vision-llm-routing` | `d1425019` | **`pr/m5-llm-providers`** | vision sync + llm-providers followup + filmcrew OpenAI (restacked on the current M5) | ✅ | ☐ |
| `pr/g6-collapsible-alerts` | `9cc2976a` | `upstream/main` | Collapsible alert UI + MUI ref handling | ✅ | ☐ |
| `pr/g7-agent-config-docs` | `ce313c87` | `upstream/main` | Agent config + docs + gitignore rules | ✅ | ☐ |
| `pr/filmcrew-live-progress` | `44112d3c` | `upstream/main` | Film Crew script templates + live progress (parked) | ✅ | ☐ |

`upstream/main` is `5777713c` as of this update. M1/M2 are merged in PR (their
branch refs still point at the pre-merge heads, hence the "ahead" count); M3's
branch ref is the merged content.

**G5 is stacked on M5** — `fix/vision-sync`, `chore/llm-providers-followup`, and
`feat/filmcrew-openai-compatible` all depend on `openai_provider.py` /
`ModelManagementSection.jsx` from M5 (formerly M4). Its PR diff is clean against
M5, not against `upstream/main`. It was **rebased onto the updated M5**
(`8da8c8be`) after the #218 flux-dev change and the Discord-voice split, so it no
longer carries the voice delta it originally inherited.

**G1 is also stacked on M5** — `backend/utils/music_prompt_rewriter.py` imports
`backend/services/openai_provider` and calls `is_openai_active()` /
`get_openai_model()` from the M5 provider layer, so G1 does not import standalone
on `upstream/main`. Its two commits (`0d2a1eac` G1 feature set, `f80c0e70`
cloud-consent fix) were rebased onto `8da8c8be`; the resulting tip is `77673a91`.
Its PR diff is clean against M5, not against `upstream/main`.

**Discord voice is its own snapshot** (`pr/discord-voice`), split out of M5 so
that PR stays provider-only. It is shaped opt-in: `voice.backend` defaults to
`"guaardvark"` (the Audio Foundry) and `"pi-omni"` routes through an external
OpenAI-compatible voice router with a local fallback. Pushed, no PR yet.

**Film Crew** (`pr/filmcrew-live-progress`) is parked for later re-thinking.
Upstream has already absorbed most of the Film Crew feature (production API,
casting, storyboard grid, `StageProgress` stepper); the only still-unique pieces
are script templates + live render/storyboard progress bars. Not part of the
MPS track.

## How each snapshot was built

- **M1** was kept as-is from the old `dev` (it already carried the owner-review
  fixes), then **re-based onto current `upstream/main`** by applying its 11-file
  net diff as a single commit (the old branch was based on stale `1ba43f14`).
- **Clean branches** (M2, M4, M5, G1, G2, G3, G4, G6, G7): `git merge --no-ff`
  the group's `feat/*` branches into a snapshot cut from `upstream/main`.
- **M3 (`pr/m3-zimage-mps-native`, added 2026-09-11)**: a single-file patch —
  `offline_image_generator.py` MPS support — applied directly onto `upstream/main`
  (`git apply -3`). It is **self-contained**: no ComfyUI dependency. It sits before
  the ComfyUI snapshot because the native path is what makes Macs work; ComfyUI is
  the fallback/alternative. `GUAARDVARK_ZIMAGE_USE_COMFYUI` keeps its original
  meaning (truthy = always ComfyUI) and stays owned by M4.
- **Cumulative branches** (G5): the `feat/*` branches were based on the
  llm-providers branch, so `git merge` would have re-pulled M4 content. Instead
  I **cherry-picked only the unique commits** of each branch onto the snapshot.
- **Unrelated docs** (`FEATURES.md`, `ISSUE_chatbot_context_memory.md`) that rode
  along on feature branches were dropped — docs ride separately (issue #43).

## Current status

M1 (#154), M2 (#182) and M3 (#183) are **merged** into `upstream/main`. M4 (#197)
and M5 (#214) are **open** — both have had owner reviews and the review fixes have
been pushed; M4 is based on current `upstream/main` (`5777713c`), M5 is behind it
but `MERGEABLE` (not rebased). `pr/discord-voice` is pushed with no PR yet.

The remaining track snapshots (G1 → … → G7) are based on the current
`upstream/main` (G1 and G5 on the re-based M5) and pushed to the fork. Submit them
one at a time (G1 → … → G7) once M4/M5 land. M5/G1/G5 are **fork-only** (see the
note above) — no PRs are planned for them.

**M5 lands first** — G1 and G5 are stacked on `f135f774`. The user asked that #214
not be rewritten for now, so the stack base stays on M5 even though it is behind
`upstream/main`. **#214 was closed unmerged** (review requested further changes,
those were applied on the branch at `f135f774`); M5 and its two dependents now
live in the Kwik-Dev fork only — do not push them upstream and do not open PRs
for them.

### `dev` integration branch

`dev` is the fork-internal integration branch: all track snapshots
(M1 → M2 → M3 → M4 → M5 → G1 → … → G7) are merged into it in order. It was
**re-based onto the new `upstream/main` (`4b9763ee`)** on 2026-09-11
(`4c33bea7` → `c80c9416`, `git rebase --rebase-merges`) and force-pushed.
`pr/filmcrew-live-progress` and `pr/ffmpeg-captions` are **not** merged into `dev`
(parked).

### Fixes applied during integration

- `run_acestep.py` had committed conflict markers (owner-review item 1) — fixed
  in both `dev` and `pr/m1-macos-support` (use `_pick_device()`).
- `buildPlanRequest.test.js` updated for the new `respect_bin_order` /
  `customer_context` / `caption_defaults` fields (G1).
- `remark-gfm` installed (was in `package.json` but missing from `node_modules`).
- `plugins/training/scripts/finetune_model.py` has a pre-existing syntax error in
  `upstream/main` (unterminated triple-quoted f-string) — left as-is, not ours.

## Commands

### Cut a snapshot (clean branch)

```bash
git checkout -b pr/<name> upstream/main
git merge --no-ff <feat-branch> -m "<group>: <branch>"
# resolve conflicts, then:
git add <resolved-files> && git commit --no-edit
git push -u origin pr/<name>
```

### Cut a snapshot (cumulative branch — cherry-pick unique commits)

```bash
git checkout -b pr/<name> upstream/main
git cherry-pick <unique-commit-1> <unique-commit-2> ...
# resolve conflicts, then:
git cherry-pick --continue
git push -u origin pr/<name>
```

### Verify a snapshot is mergeable

```bash
git merge-base --is-ancestor upstream/main pr/<name> && echo MERGEABLE
git diff --stat upstream/main..pr/<name>
```

## Gotchas (learned the hard way)

- **Never run `git merge` in parallel.** Two concurrent merges in the same
  worktree corrupt the index. One merge at a time.
- **Some `feat/*` branches are cumulative** (based on other features, e.g.
  `fix/vision-sync` is based on `feat/llm-providers`). `git merge` re-pulls the
  base feature. Use `git log upstream/main..<branch>` to find the unique commits
  and cherry-pick them instead.
- **`git checkout --theirs` on a merge/cherry-pick takes the old-dev version**,
  which is missing upstream features. Prefer resolving conflicts by hand: keep
  upstream's structure and add only the branch's actual change.
- **The collapsible-alerts refactor (G6) had bugs** in the source branch
  (`RulesPage` used `<CollapsibleAlertSnackbar>` without defining it;
  `DevToolsPage` used `<CollapsibleAlertSnackbar>` while importing
  `AlertSnackbar`). These were fixed during the re-work — don't re-introduce
  them.
- **`audio_fx_sao.py`** keeps the fork's MPS implementation with the
  `AUDIO_FOUNDRY_SAO_MPS=1` opt-in + fp32-on-Metal pattern (owner preference).

## Submitting PRs

Submit **one at a time**, in order (G1 → … → G7 after M4/M5), each from its own
snapshot branch. M1 (#154), M2 (#182) and M3 (#183) are merged; M4 (#197) and
M5 (#214) are open.

**Stacked snapshots (G1, G5) take base = `pr/m5-llm-providers`, not `main`:**
`https://github.com/guaardvark/guaardvark/compare/pr/m5-llm-providers...Kwik-Dev:pr/g1-audio-video-polish`
and `...compare/pr/m5-llm-providers...Kwik-Dev:pr/g5-vision-llm-routing`.
They land after #214 merges, and their base should be repointed to `main` once it
does.

For each PR:

1. Open the compare page:
   `https://github.com/guaardvark/guaardvark/compare/main...Kwik-Dev:pr/<name>`
2. Confirm base = `guaardvark:main`, head = `Kwik-Dev:pr/<name>`.
3. Confirm it says **"Able to merge"**.
4. Write a title + description (link the tracking issue, e.g. #43).
5. Submit.

> **CLA:** The first PR triggers the **CLA Assistant Lite** bot, which posts a
> comment asking for a one-line sign-off. Sign by pasting the line it requests
> as a comment on the PR. (Already signed for M1.)

> **Auth:** Opening PRs needs the **keyring token** (full `repo` scope), not the
> active `GITHUB_TOKEN`. `unset GITHUB_TOKEN` then `gh auth switch --user kwiksher`
> in the same shell invocation as the `gh` command.

- Docs ride separately (issue #43): `docs/audio`, `docs/miscs`,
  `docs/summary-modification`, etc.
