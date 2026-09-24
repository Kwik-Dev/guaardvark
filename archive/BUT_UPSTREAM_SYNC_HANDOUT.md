# Handout — Real GitButler ⇄ upstream sync

**Paste this to start the next session:**
> Read `BUT_UPSTREAM_SYNC_HANDOUT.md` and do the real sync of the GitButler workspace with `upstream/main`. Follow the plan and the safety rules in that file.

Repo: `/Users/ymmtny/GitHub/guaardvark` · Tool: `but 0.22.0` · Date of this handout: 2026-09-18

---

## 1. Goal

Get the **GitButler workspace** (its ~65 virtual branches) rebased onto **current `upstream/main`**, so the applied branches are no longer based on stale `main`.

`gitbutler/workspace` is currently at `fa060471`, based on old `origin/main` = `e4331689`. Current `upstream/main` = `e3f435ac` (≈131 commits ahead).

## 2. State you are inheriting

- `origin/main` (Kwik-Dev fork) was **fast-forwarded to `upstream/main`** → both are `e3f435ac`. That part of the sync is done.
- `pr/m3-zimage-mps-native` is rebased onto `upstream/main` at `ca528a2e` (PR #183, ready).
- `pr/m4-comfyui-image` is rebased onto `upstream/main` at `4cc25402` (PR **#197**, draft).
- PR **#182 (M2) is merged** into upstream.
- The workspace was **restored** to its pre-pull snapshot `2223b900` (via `but oplog restore`). It has **0 conflicts** and is intact — but still on the old base.
- A `but commit`/`wip/pull-park` branch created during the aborted attempt was deleted.

### What went wrong last time (so you don't repeat it)

`but pull` fetched the new main and produced **29 conflicted commits**. Most were **duplicates of work already merged upstream** (all of M2, the older G-integrals), which GitButler could not auto-drop because the workspace branches are rebased copies with different SHAs.

The worst conflict was `frontend/src/pages/SettingsPage.jsx` — a **2,237-line** single conflict region (upstream rewrote the page; branch `ywy` adds a Model-Management section upstream doesn't have). Hand-merging that plus 28 others is unsafe.

**Key insight: the fix is to remove already-merged branches *before* pulling, not to resolve 29 conflicts.**

## 3. Safety rules (learned the hard way)

1. **Snapshot first:** `but oplog snapshot -m "pre-sync"`. Record the SHA it prints.
2. **`but undo` steps through resolve-mode snapshots** — pressing it repeatedly walks *backwards into the conflict state*, it does **not** jump to "before the pull". To get back to a known state use **`but oplog restore <sha>`**, not repeated `but undo`.
3. `but resolve cancel` refuses if the worktree changed; use `but resolve cancel --force` (drops in-progress resolution).
4. `but push` only knows **applied** GitButler branches. For standalone `pr/*` branches, push with git from a worktree: `git push --force-with-lease origin <branch>`.
5. If `git diff HEAD` is **empty** but `git status` shows `MM <file>`, the **git index is stale** (GitButler artifact / earlier `git rm`). Refresh with `git reset` (mixed, no `--hard`) — it changes nothing in the worktree.
6. Don't `but pull` with untracked files that upstream also tracks (e.g. `AGENTS.md`). Park them first, or move them aside. Upstream restructured `AGENTS.md` into a router + `AGENT_GUIDE.md`; the local copy is the old detailed guide.
7. Creating PRs needs the **keyring OAuth token**, not the active `GITHUB_TOKEN` PAT (it lacks `createPullRequest`): prefix with `env -u GITHUB_TOKEN gh …`.

## 4. Plan

### Step 0 — snapshot & clean
```bash
cd /Users/ymmtny/GitHub/guaardvark
but oplog snapshot -m "pre-sync"        # record the SHA
git status --short                       # note untracked files upstream also has
```

### Step 1 — inventory merged branches
A workspace branch whose commits are all already in `upstream/main` is safe to drop.
```bash
git fetch upstream main
for b in $(git for-each-ref --format='%(refname:short)' refs/heads \
           | grep -vE '^(main|dev|gitbutler/.*|pr/.*|evidence/.*|tmp_seq)$'); do
  out=$(git cherry upstream/main "$b" 2>/dev/null)
  plus=$(printf '%s\n' "$out" | grep -c '^+')   # commits NOT in upstream
  minus=$(printf '%s\n' "$out" | grep -c '^-')  # commits already in upstream
  printf '%-45s +%-4s -%s\n' "$b" "$plus" "$minus"
done | sort -k2
```
- `+0` → **fully merged upstream → discard it.**
- `+N` → has unique work → keep and rebase it.

Known-merged families (verify, don't assume): M1 `#154`, M2 `#182`, the G1–G7 integration branches, and dependency bumps.

### Step 2 — drop the merged branches
```bash
# For each already-applied branch that is +0:
but unapply <branch>            # if applied
but branch delete --force <branch>
# or, if it is not applied and `but branch delete` says "not found":
git branch -D <branch>          # GitButler re-reads on next status
```
Re-run Step 1 until every remaining branch is `+N`.

### Step 3 — decide what stays
For each surviving branch decide keep vs drop:
- **Keep** if it is unmerged work you still want (video, audio, docs, collapsible alerts, imagemodel, etc.).
- **Drop** if it is superseded by a merged PR or duplicated across stacks.

If unsure, leave it and let Step 4 surface it.

### Step 4 — pull
```bash
but pull                 # rebases the remaining applied branches onto e3f435ac
```
Expect conflicts only from **genuinely unmerged** branches now. Resolve with `but resolve <id>` → edit → `but resolve finish`, **oldest first**.

### Step 5 — verify
```bash
but status | grep -c conflicted      # expect 0
git diff HEAD --stat                 # expect empty
git merge-base --is-ancestor origin/main gitbutler/workspace \
  && echo "workspace is based on upstream/main"
```

## 5. Known conflict hotspots (from the aborted pull)

The 29 conflicted commits were, oldest-first:
`oqo` (park) · `lvp` `qyq` `xpn` `tlu` (gitignore/docs) · `zqt` · `ywy` · `wxk` · `zlo` ·
`mqy` · `mzv` · `klq` · `ppkw` `lvo` `ylq` `qsl` `xyw` (video stack) ·
`tky` `tyk` `uwt` (audio/comfy stack) ·
`qqy` `tqz` `qyt` `skm` `nwp` (M2 lora stack — **all merged; drop, don't resolve**) ·
`lyn` (M2 mps — merged) · `rym` · `tzt` `kpl` (imagemodel).

Resolution guidance if any survive Step 2:
- **M2 stack (`fix/lora-trainer-timeout`, `feat/lora-trainer-mps`)**: already in `upstream/main`. Drop them.
- **`ywy` (`SettingsPage.jsx`, ~2.2k-line conflict)**: upstream's page is authoritative. Take upstream's `SettingsPage.jsx`, then re-add only the branch's `ModelManagementSection` import + its `<SettingsCardWrapper title="Model Management">` block. Do **not** hand-merge the whole region.
- **Video/audio stacks**: conflicts were additive (both sides add features). Merge both sides, keep both.
- **`.gitignore` (`lvp`/`qyq`/`xpn`/`tlu`)**: keep both sides.
- **comfy MPS (`uwt`, `tyk`)**: upstream deliberately ships plugins disabled (`default_enabled: false`); keep the branch's `default_auto_start`/MPS logic.
- **`AGENTS.md`**: take upstream's router version; local detail moved to `AGENT_GUIDE.md`. Save a backup of the local copy first.

## 6. After the pull

- Push any source branches you changed: `but push <branch>`.
- Re-verify the PR branches are still on the right base:
  - `pr/m3-zimage-mps-native` @ `ca528a2e` (PR #183)
  - `pr/m4-comfyui-image` @ `4cc25402` (PR #197)
  - `pr/m2-lora-trainer-mps` @ `f3192b05` (PR #182 merged — leave it)
  - `dev` @ `16dff46c`
- The `pr/*`, `evidence/*`, and `dev` refs are **not** workspace branches — `but pull` won't touch them. Rebase them individually in worktrees if needed.

## 7. Rollback

| Situation | Command |
|---|---|
| Pull is bad, workspace not in resolve mode | `but oplog restore 2223b900` (pre-pull) |
| Mid-resolution, want out | `but resolve cancel --force` |
| Just did one operation | `but undo` (single step only — see rule 2) |
| Branch deleted by mistake | `git reflog` / `but oplog restore <sha>` |

## 8. Known unrelated gotchas

- `backend/tests/tasks/test_lora_trainer_tasks.py::test_backend_selector_uses_real_when_real_available_in_auto` **hangs** when the RunPod remote-trainer branch is applied (it polls `remote_trainer.py`). Stub the remote path or skip; unrelated to this sync.
- `GITHUB_TOKEN` (fine-grained PAT) can't create PRs on `guaardvark/guaardvark`; use `env -u GITHUB_TOKEN gh …`.
- `manager` at repo root is a broken symlink — ignore it.
