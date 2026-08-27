# Branch Merging in the Age of AI Agent Coding

A case study in planning, isolating, and rebasing a 42-branch feature set into a
step-by-step PR train — using the Guaardvark **macOS / Apple Silicon** enhancement
as the worked example.

- **Repo:** `guaardvark/guaardvark` (upstream) ↔ `Kwik-Dev/guaardvark` (fork)
- **Branch of record at time of writing:** `pr/m2-lora-trainer-mps`
- **Tooling:** GitButler (`but`), git worktrees, GitHub PRs, an AI coding agent
  (`pi`) running inside a Herdr pane
- **Date:** 2026-09-11

---

## 1. Why this is a new problem

An AI agent does not produce one tidy feature branch. It produces a **stream of
small, fast commits**, often stacked on each other because the agent keeps the
prior context in its head. A single agent session on Guaardvark accumulated
**42 virtual branches** — some clean, some cumulative (based on other feature
branches), some parked.

That is the crux: in agent-driven development the unit of work is a *commit
stream*, but upstream reviewers still want the unit of review to be a **single
coherent PR**. The whole workflow below is about reconciling those two shapes.

Three forces make naive `git merge` unusable here:

1. **Cumulative branches.** `fix/vision-sync` was based on `feat/llm-providers`.
   Merging it re-pulls the entire provider feature.
2. **A moving upstream.** `upstream/main` advanced `23ab76e3 → 4b9763ee` *while*
   the PR train was being built, invalidating every snapshot's base.
3. **Overlapping files across tracks.** `start.sh`, `real_trainer.py`, and
   `llm_service.py` are each touched by branches in *different* tracks, so order
   of integration matters.

---

## 2. Stage 0 — Let the agent make `feat/*` branches in GitButler

GitButler's model (virtual branches applied to one working directory) fits agent
work well: the agent keeps committing to *logical* branches without needing a
worktree per branch. `but status` shows the workspace as a tree:

```
╭┄ at [feat/lora-training-fixes]
┊●   ppkx feat(lora): facial-hair keep in cast desc, job_id passthrough, pod path guard
┊│
┊├┄ ru [feat/runpod-lora-trainer]
┊●   qvq feat(lora): pass job_id through remote trainer + progress ETA
┊●   spp feat(lora): S3/R2 input staging, output bucket+prefix, ...
┊●   tvr fix(lora): pod Dockerfile — modern CUDA 12 base + bundle trainer scripts
```

`but branch list` separates **applied** (in the workspace) from **unapplied**
branches. This is the raw material: 42 applied virtual branches, plus the
already-cut `pr/*` snapshots sitting unapplied.

**Rule that emerged:** treat the agent's branches as *draft commits*, not as
merge units. Do not merge them into upstream directly. First give them names that
map to a reviewer-facing story.

### Git worktrees vs. GitButler

Both solve "I need more than one branch in play at once," but they solve it in
opposite directions. Worktrees give each branch its **own checkout on disk**;
GitButler lets many branches share **one working directory** as virtual branches.

| Dimension | Git worktrees | GitButler |
|-----------|---------------|-----------|
| **Model** | One branch = one directory (`git worktree add`) | Many virtual branches applied to a single working tree |
| **Isolation** | Hard — separate filesystem, separate `node_modules`/venv | Soft — same files; changes are routed to a branch per hunk |
| **Concurrency** | True parallel checkouts; run M2 tests while editing M3 | Serial — one working tree, so one merge/op at a time |
| **Agent fit** | Agent needs a worktree per branch; heavier setup | Agent keeps committing to *logical* branches without new dirs |
| **Switching cost** | `cd` to another directory (cheap, no checkout) | `but branch` apply/unapply (cheap, no checkout) |
| **Branch stacking** | Natural — stack in one worktree, branch off in another | Natural — `but status` shows the stacked tree visually |
| **Stale-base rebases** | Per-worktree `git rebase` | Same `git rebase` underneath; GitButler adds UI over it |
| **Disk / deps cost** | High — N checkouts × deps | Low — one checkout |
| **Parallel merges** | **Still unsafe if same repo** — index corruption | Single worktree, so inherently one-op-at-a-time |
| **Best for** | Long-lived parallel builds, testing two states at once | A fast-moving agent producing many small branches |
| **Weakness** | Deps/tooling multiply per worktree; easy to let them drift | One working tree means no true parallel test runs |

**How this case used each:**

- **GitButler** was the *authoring* surface — the agent's 42 `feat/*` branches
  lived as virtual branches in one workspace (`but status` tree, `but branch
  list` for applied vs. unapplied). No per-branch directory, no per-branch deps.
- **Git worktrees** were the *escape hatch* — a **throwaway worktree** was used
  to produce the PR-182 evidence with M4's ComfyUI route applied **without
  touching the M2 code**. That isolation is exactly what worktrees are good at.

> **The gotcha both share:** `git merge` is still unsafe to run in parallel
> within one repo. GitButler's single working tree makes that a non-issue by
> construction; with worktrees you must still keep merges serialized (see
> §7).

**Rule of thumb:** use **GitButler to shape** the agent's commit stream into
logical branches, and reach for a **worktree when you need a genuinely separate
checkout** — a clean test of one branch while another stays untouched, or
evidence that must not perturb the PR head.

---

## 3. Stage 1 — Plan and organize for step-by-step PRs

The 42 branches were sorted into **two tracks** so each PR carries one theme and
a reviewer can follow a sequence:

### macOS track — Apple Silicon foundation (lands first)

| PR | Branches | Rationale |
|----|----------|-----------|
| **M1** | `feat/audio-mps-whisper-filmcrew`, `fix/mps-video-unload` | MPS audio + whisper.cpp STT + video unload fix |
| **M2** | `feat/lora-trainer-mps` | LoRA training on MPS |
| **M3** | `offline_image_generator.py` MPS patch | **Native** MPS Z-Image stills (offline diffusers, no ComfyUI) |
| **M4** | `feat/zimage-comfyui`, `feat/zimage-comfyui-mps`, `feat/imagemodel-comfyui` | ComfyUI-backed Z-Image on Apple Silicon (fallback/alternative) |
| **M5** | `feat/llm-providers`, `voice-openai-routing` | OpenAI-compatible routing (cloud fallback for no-CUDA Macs) |

M3 was split out **late** (2026-09-11): the native MPS path is what actually makes
Macs work, so it sits *before* the ComfyUI snapshot, which becomes the fallback.

### General track — platform-agnostic (lands on top)

| PR | Theme |
|----|-------|
| **G1** | Film Crew + audio/video polish, i2v, captions |
| **G2** | Optional MCP server startup/cleanup |
| **G3** | RunPod remote LoRA trainer + timeout fixes |
| **G4** | Cast shot counts + chat routing |
| **G5** | Vision/LLM/OpenAI routing (stacked on M5) |
| **G6** | Collapsible alert UI + MUI ref handling |
| **G7** | Agent config + docs + gitignore |

### The dependency map is the deliverable

Before cutting anything, record which files are touched by both tracks:

| File | macOS branch | general branch |
|------|--------------|----------------|
| `start.sh` / `stop.sh` | M1 | G2 |
| `backend/tasks/production_swarm_tasks.py` | M1 | G5 |
| `backend/tasks/lora_trainer_tasks.py` | M2, M4 | G3 |
| `backend/tools/image_tools.py` | M4 | G4 |
| `backend/utils/llm_service.py` | M5 | G5 |
| `plugins/lora_trainer/real_trainer.py` | M2 | G3 |

This table *is* the merge order. macOS lands first as the foundation; general
lands on top, and cross-track conflicts are resolved at that point.

**Ordering principle:** a PR is a *story*, not a *branch*. Group by user-facing
theme, then order by dependency, not by the order the agent happened to commit.

---

## 4. Stage 2 — Snapshots: one clean branch per PR

The pivotal decision (re-worked 2026-09-08): each PR group becomes a
**standalone snapshot branch cut from `upstream/main`** — *not* a cumulative
merge into `dev`.

> Each snapshot contains **only its own group's content**, so the PR diff against
> `upstream/main` is exactly that feature, with no carry-over from earlier PRs.

### Cut a clean snapshot

```bash
git checkout -b pr/<name> upstream/main
git merge --no-ff <feat-branch> -m "<group>: <branch>"
# resolve conflicts by hand, then:
git add <resolved-files> && git commit --no-edit
git push -u origin pr/<name>
```

### Cut a cumulative snapshot (cherry-pick the unique commits)

When a `feat/*` branch sits on top of another feature, merging re-pulls the base.
Find the *unique* commits and cherry-pick only those:

```bash
git log upstream/main..<branch> --oneline          # identify unique commits
git checkout -b pr/<name> upstream/main
git cherry-pick <unique-commit-1> <unique-commit-2> ...
git cherry-pick --continue                          # after resolving
git push -u origin pr/<name>
```

This is exactly how **G5** was built: `fix/vision-sync`,
`chore/llm-providers-followup`, and `feat/filmcrew-openai-compatible` all depend
on `openai_provider.py` / `ModelManagementSection.jsx` from M5, so G5's diff is
clean **against M5**, not against `upstream/main`.

### Verify a snapshot is mergeable

```bash
git merge-base --is-ancestor upstream/main pr/<name> && echo MERGEABLE
git diff --stat upstream/main..pr/<name>
```

The old cumulative `pr/*` branches (each containing every prior PR) were
**deleted**. The clean re-work is the current state.

---

## 5. Stage 3 — `dev` as the integration branch

`dev` is the fork-internal **integration** branch. It is deliberately *not* an
upstream-facing branch — it is where the whole train is proven green before any
PR is submitted.

- All 12 track snapshots (M1 → M2 → M3 → M4 → M5 → G1 → … → G7) are merged
  into `dev` **in order**.
- `dev` is verified **green**: frontend `eslint` clean, `vitest` 230/230 pass,
  Python syntax clean, no conflict markers.
- It was **re-based onto the new `upstream/main` (`4b9763ee`)** on 2026-09-11
  (`4c33bea7` → `c80c9416`, `git rebase --rebase-merges`) and force-pushed.
- `pr/filmcrew-live-progress` and `pr/ffmpeg-captions` are **not** merged into
  `dev` — parked.

This is the key inversion versus the old workflow: **`dev` is downstream of the
snapshots, not upstream of them.**

```
upstream/main ──┬── pr/m1-macos-support ───┐
                ├── pr/m2-lora-trainer-mps ─┤
                ├── pr/m3-zimage-mps-native ─┤
                ├── pr/m4-comfyui-image ─────┤── merge into ──► dev (integration, green)
                ├── pr/m5-llm-providers ─────┤
                └── … G1…G7 ─────────────────┘
```

Integration is where **cross-track conflicts are actually resolved**. When the
general track lands on top of the macOS track, the overlapping files from the
dependency table collide and are fixed once, in `dev`.

### Fixes that only surface during integration

- `run_acestep.py` had **committed conflict markers** in a snapshot — fixed in
  both `dev` and `pr/m1-macos-support` (use `_pick_device()`).
- `buildPlanRequest.test.js` needed updating for new fields
  (`respect_bin_order` / `customer_context` / `caption_defaults`) from G1.
- `remark-gfm` was in `package.json` but missing from `node_modules`.
- `plugins/training/scripts/finetune_model.py` has a **pre-existing** syntax
  error in `upstream/main` (unterminated triple-quoted f-string) — left alone,
  not ours.

**Lesson:** the integration branch is where you discover that a "clean" snapshot
isn't. Budget for it.

---

## 6. Stage 4 — Rebasing the main stream while PRs and upstream both move

This is the part that makes agent-era merging genuinely hard: **the ground
moves on both ends.**

### The moving base

`upstream/main` advanced `23ab76e3 → 4b9763ee` *after* the snapshots were cut. So
every `pr/*` branch is now based on an older `main`. The snapshot strategy means
this is *recoverable*: each snapshot is a small, self-contained diff, so rebasing
it onto the new `main` is a bounded conflict, not a re-litigation of the whole
train.

`dev` itself was re-based onto `4b9763ee` the same day (`4c33bea7 → c80c9416`)
using `git rebase --rebase-merges` to preserve the integration topology, then
force-pushed. Because `dev` is *downstream* of the snapshots, re-basing it is a
single operation that re-lays the whole train on the new base.

```bash
# keep the fork's main synced, then re-point dev and snapshots
git fetch upstream
git checkout dev && git rebase upstream/main
# re-cut or rebase each snapshot whose diff still applies
git checkout pr/<name> && git rebase upstream/main
git push --force-with-lease origin pr/<name>
```

### The moving PR: ownership of a single commit changed twice

The clearest illustration is the **timeout fixes** during the M2 / G3 work. The
same logical change lived in different PRs over the course of one session:

1. Originally part of the **G3** snapshot
   (`pr/g3-runpod-lora-trainer`, squashed `80da48a4`).
2. Found **missing from M2** — so the fix wasn't in the PR that actually needed
   it.
3. **Reassigned to M2-only** per the owner's decision:
   - `pr/m2-lora-trainer-mps` keeps `e9593922` (daemon 3 h / load 1 h),
     `2e2ffa83` (task limits 255 min), `8f2dd1d0` (fail loudly/reconcile),
     `4f902ba2` (tests).
   - `pr/g3-runpod-lora-trainer` was **rebuilt without them** and force-pushed
     `80da48a4 → 68fe8451` (RunPod work intact; `real_trainer.py` dropped from
     its diff; reaper back to 45 min).

```
Before:  G3 = { RunPod + timeouts }        M2 = { MPS LoRA }
After:   G3 = { RunPod }                   M2 = { MPS LoRA + timeouts }
```

Both PRs were modified; both bases moved; `upstream/main` moved underneath both.
This is the normal case, not the exception, once an agent is iterating quickly.

### The moving evidence

Review evidence had to track the *current* PR code, not a stale artifact:

- An early **Aug 27 run B** artifact was **rejected as stale** — the owner wanted
  evidence from the current PR code.
- A **fresh run** on the current M2 head (`4f902ba2`, Elara subject 1, 400 steps,
  768 res, 11 images) was the replacement — then **stopped at step 51/400** on
  the owner's instruction, and must not restart without approval.
- Images proving the trained LoRA on MPS were pushed to a **separate branch**
  `evidence/pr-182` so GitHub could render them, keeping the PR branch's diff
  clean.

**Lesson:** evidence is a *branch*, and it must be pinned to a PR head that is
itself moving. Keep it off the PR branch.

---

## 7. The playbook (distilled)

1. **Let the agent commit freely** into GitButler virtual branches. Don't fight
   the commit stream.
2. **Plan the PR train first** — theme + dependency table — *before* cutting any
   branch.
3. **Cut one clean snapshot per PR from `upstream/main`.** Cherry-pick the unique
   commits of any cumulative branch.
4. **Integrate everything in `dev`** in dependency order; prove it green; resolve
   cross-track conflicts there.
5. **Expect the base to move.** Rebase `dev` and each snapshot onto the new
   `upstream/main` with `--force-with-lease`.
6. **Expect PR ownership to move.** A change may belong in a different PR than
   where the agent first put it. Rebuild and force-push the affected snapshots.
7. **Keep evidence on its own branch**, pinned to the current PR head.
8. **Submit one PR at a time**, in order (M2 → M3 → M4 → M5 → G1 → … → G7).

### Hard-won gotchas

- **Never run `git merge` in parallel.** Two concurrent merges in one worktree
  corrupt the index. One at a time.
- **`git checkout --theirs` on a merge/cherry-pick takes the *old-dev* version**,
  which is missing upstream features. Resolve by hand: keep upstream's structure,
  add only the branch's change.
- **Some `feat/*` branches are cumulative.** `git log upstream/main..<branch>`
  first; if it contains another feature, cherry-pick.
- **`pr/*` are not snapshots of `dev`.** `dev` is the integration branch that
  merges them; each `pr/*` is cut from `upstream/main`.

---

## 8. Submitting

For each PR, one at a time:

1. Open `https://github.com/guaardvark/guaardvark/compare/main...Kwik-Dev:pr/<name>`
2. Confirm base = `guaardvark:main`, head = `Kwik-Dev:pr/<name>`.
3. Confirm it says **"Able to merge"**.
4. Write a title + description (link the tracking issue).
5. Submit.

> **CLA:** the first PR triggers CLA Assistant Lite; sign with the requested
> one-line comment.
>
> **Auth:** opening PRs needs the keyring token (full `repo` scope), not the
> active `GITHUB_TOKEN`.

---

## 9. Takeaways

- **In agent-driven development, the commit stream and the review unit diverge.**
  The snapshot-branch pattern is the bridge: it re-shapes a stream of agent
  commits into a sequence of clean, independently reviewable PRs.
- **Integration is a first-class stage, not an afterthought.** `dev` exists to
  absorb cross-track collisions before a reviewer ever sees them.
- **Rebasing is continuous, not a one-time event.** With an agent committing
  fast and upstream moving, the base and the PR contents both change; the
  workflow must assume `--force-with-lease` is routine.
- **The plan (the dependency table) outlives the branches.** Branches get
  rebuilt, squashed, and force-pushed; the *order* is what stays stable.

---

### Appendix — branch map (at time of writing)

| Snapshot | Base | Contents | PR |
|----------|------|----------|----|
| `pr/m1-macos-support` | `upstream/main` | MPS audio/whisper/video | **#154** (merged) |
| `pr/m2-lora-trainer-mps` | `upstream/main` | LoRA training on MPS + timeouts | #182 |
| `pr/m3-zimage-mps-native` | `upstream/main` | native MPS Z-Image stills (offline diffusers) | ☐ |
| `pr/m4-comfyui-image` | `upstream/main` | ComfyUI Z-Image / imagemodel | ☐ |
| `pr/m5-llm-providers` | `upstream/main` | LLM providers + voice routing | ☐ |
| `pr/g1-audio-video-polish` | `upstream/main` | audio/music polish, i2v, filmcrew | ☐ |
| `pr/g2-mcp-start` | `upstream/main` | MCP server startup/cleanup | ☐ |
| `pr/g3-runpod-lora-trainer` | `upstream/main` | RunPod LoRA trainer | ☐ |
| `pr/g4-cast-shot-count` | `upstream/main` | Cast shot counts + chat routing | ☐ |
| `pr/g5-vision-llm-routing` | **`pr/m5-llm-providers`** | vision sync + llm followup | ☐ |
| `pr/g6-collapsible-alerts` | `upstream/main` | Collapsible alert UI | ☐ |
| `pr/g7-agent-config-docs` | `upstream/main` | Agent config + docs | ☐ |
| `evidence/pr-182` | — | rendered evidence images | — |
