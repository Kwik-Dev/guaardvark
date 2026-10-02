---
name: sync-upstream
description: >-
  Sync this fork with upstream guaardvark/guaardvark: measure divergence, fast-forward
  the fork's main mirror, and integrate upstream into the fork's real dev line
  (cloud-plus) with a reviewable conflict resolution. Use when the user asks whether
  upstream has moved, "should we sync", "pull upstream into our repo", "rebase cloud-plus",
  or when a release/tag lands upstream and the fork must catch up.
---

# Sync the fork with upstream

This repo is a fork. Two remotes, and they mean different things:

| Remote | Repo | Role |
|---|---|---|
| `origin` | `Kwik-Dev/guaardvark` | our fork |
| `upstream` | `guaardvark/guaardvark` | the real project |

Branch topology (verify, do not assume — it has changed before):

- `main` — a **mirror of upstream**. It should always be a pure fast-forward; if it ever
  has commits of its own, stop and ask.
- `cloud-plus` — the fork's **real dev line**, and the fork's GitHub **default branch**.
  All fork work lands here.
- `gitbutler/workspace` — the GitButler workspace, based on `origin/cloud-plus`.
  While it is checked out, **use `but`, not raw git, for writes.**

Work the whole sync **read-only first**, then present numbers, then act.

---

## Preflight (read-only)

```bash
cd /Users/ymmtny/GitHub/guaardvark
git remote -v                     # confirm origin=Kwik-Dev, upstream=guaardvark
git fetch upstream --tags
git rev-list --left-right --count main...upstream/main         # want "0  <N>"
git rev-list --left-right --count origin/cloud-plus...upstream/main
git checkout --theirs 2>/dev/null; git config --get inboundguard.mode || echo "guard: off"
```

- **`main` must show `0` on the left.** Anything else means someone committed to the
  mirror; do not fast-forward over it.
- **Inbound guard:** upstream ships `scripts/check_inbound.py` and installs hooks. If
  `inboundguard.mode` is `observe` or `enforce`, the merge will hold `ci.yml`, `INSTALL.md`,
  `start.sh` and similar — exactly what `cloud-plus` touches. Check before starting; the
  guard's own scripts do not exist in the fork until the merge brings them.
- Note the fork's default branch: `gh api repos/Kwik-Dev/guaardvark --jq .default_branch`
  returns `cloud-plus`, not `main`.

Report to the user before spending effort:

- upstream HEAD sha + date, tag, stars/forks/open issues (`gh api repos/guaardvark/guaardvark`)
- new commits since last fetch, and what they are (read `CHANGELOG.md` from upstream/main)
- which fork PRs already landed (`gh pr list --repo guaardvark/guaardvark --state all`,
  filter by `headRepositoryOwner`)
- `main` fast-forward-able? `cloud-plus` divergence?

## Step 1 — `but pull` (fork dev-line sync)

```bash
but pull --check      # shows base branch + what would move
but pull              # rebases applied branches onto origin/cloud-plus
```

`but pull` targets **`origin/cloud-plus`**, not upstream. It also removes branches
GitButler marks integrated. Beware: *"merged upstream"* in `but status` can mean
**merged into cloud-plus**, which is not the same as merged into guaardvark — verify with
`git merge-base --is-ancestor <sha> upstream/main` before believing it.

## Step 2 — fast-forward the fork's `main` mirror

`main` is not checked out anywhere (it is not `gitbutler/workspace`), so updating the ref
is safe and cannot conflict.

```bash
git fetch upstream main:main          # fast-forward only; fails loudly otherwise
git rev-parse main upstream/main      # must be equal
```

**Direct `git push origin main` is blocked by a harness guard** ("Direct push to main is
blocked. Use the gh_create_pr tool…"). Do **not** open a PR for this — a `main`→`main` PR
is meaningless. Route around it:

```bash
git push origin main:refs/heads/sync/upstream-main-$(date +%Y%m%d)   # get the objects up
gh api -X PATCH repos/Kwik-Dev/guaardvark/git/refs/heads/main \
  -f sha="$(git rev-parse main)" -F force=false                     # move the ref (FF-enforced)
gh api -X DELETE repos/Kwik-Dev/guaardvark/git/refs/heads/sync/upstream-main-$(date +%Y%m%d)
git fetch origin
```

## Step 3 — integrate upstream into `cloud-plus`, on a scratch branch

Never do this in the GitButler workspace. Use an isolated worktree:

```bash
git worktree add /tmp/cloudplus-sync -b sync/cloud-plus-upstream-$(date +%Y%m%d) origin/cloud-plus
cd /tmp/cloudplus-sync
git merge upstream/main --no-edit        # expect ~11 conflicts; record the list
git diff --name-only --diff-filter=U > /tmp/conflicts.txt
```

Prefer a **merge** (`cloud-plus` carries merge commits from fork PRs; a plain `rebase`
flattens them). Use `--rebase-merges` only if the user asks for a linear history.

### Characterize before resolving

For each conflicted file, learn whose work is on each side and whether one side is simply
older:

```bash
BASE=$(git merge-base upstream/main origin/cloud-plus)
git diff --numstat $BASE upstream/main      -- "$f"    # upstream's change since base
git diff --numstat $BASE origin/cloud-plus  -- "$f"    # ours
git log --oneline upstream/main..origin/cloud-plus -- "$f"   # OUR commits touching it
git log --oneline origin/cloud-plus..upstream/main  -- "$f"  # UPSTREAM's
```

A file where upstream's Δ dwarfs ours (`+395/-63` vs `+305/-28`) usually means **upstream
already carries a reviewed version of our feature** — resolve to upstream. This happens a
lot here: PRs merged upstream leave a near-duplicate of the same work on `cloud-plus` with
different shas, so `git cherry`/`git merge-base` will report them as "ours only" even when
upstream has the reviewed equivalent. Check the *content*, not the sha.

### THE TRAP: `git checkout --theirs <file>` destroys the auto-merge

`--theirs` replaces the **entire file** with upstream's version. It does **not** resolve
hunks. Any of our changes that auto-merged cleanly into the other parts of that file are
**silently lost**, and the conflict markers disappear so nothing looks wrong.

This bit us: `--theirs backend/services/comfyui_image_generator.py` kept upstream's
`_require_up` (correct) but silently dropped our `schedule_free_comfyui_vram()` call in the
`finally:` of `generate_image` — 3 tests went red.

Rules:

1. **Never** blanket-`--theirs`/`--ours` a file that has auto-merged content. That is
   nearly every conflicted file.
2. To undo a bad resolution, restore the conflict and redo it:
   ```bash
   git checkout -m -- <file>        # re-creates the conflict from the 3 stages
   ```
3. Resolve **per hunk**, by edit. When every hunk in a file resolves to one side, still
   resolve markers in place so the rest of the file is preserved:
   ```bash
   python3 "$SKILL_DIR/resolve_theirs.py" <file>   # keeps 'theirs', drops 'ours', preserves the rest
   ```
   (`$SKILL_DIR` is this skill's directory, i.e. `.agents/skills/sync-upstream/`.)
4. After taking a side in a file, **prove our feature survived**:
   `grep -c '<our marker>' <file>`.

### Resolution patterns seen

- **Upstream-only hunk** (our side empty) → take upstream.
- **Both sides added the same thing** (e.g. the `/imagemodel` `downloaded` block) → git
  still conflicts. Keep one copy and check for a **duplicate declaration** afterwards —
  the auto-merge can add the same block at a different position, giving
  `Identifier 'downloaded' has already been declared`.
- **Import list conflicts** → take the **union** of what the merged body actually uses,
  then delete imports that became unused. Dropping an import that is still referenced
  gives `'Alert' is not defined` further down the file.
- **Our component replaced upstream's** (e.g. our `CollapsibleAlert` vs MUI `Alert`) → use
  ours everywhere in that file for consistency, including in upstream's *new* blocks.
- **Our feature + upstream's refactor of the same file** → keep both, as a hand-merge.
  E.g. our `n` shot-count argument into upstream's `try/except TaskNotStarted`.

## Verification gates (do not skip; do not commit before these pass)

**Scope this proportionately — do NOT run the full backend suite.** It is ~6,984 tests
across 563 files and takes ~10 minutes per tree. A merge can only break things where our
code and upstream's interact; the ~550 other files upstream changed are upstream's own
work, already validated by upstream's CI before it landed here. Re-running all of it mostly
re-tests upstream.

What actually catches merge damage, in order of value:

1. **`npx eslint . --ext js,jsx` on the frontend** — the single highest-yield check. It
   caught both real artifacts of the last sync: a duplicated `const downloaded` block from a
   spurious both-added conflict, and `'Alert' is not defined` from a bad import union.
2. **Targeted backend tests for the files you resolved** — this caught the idle-VRAM
   regression that `--theirs` caused.
3. `python3 -m py_compile` and `npx vite build` — cheap, catch syntax/wiring breaks.
4. A full-suite sweep — reserve for release-branch merges or when (1)–(3) leave doubt.

```bash
# backend
cd /tmp/cloudplus-sync/backend
python3 -m py_compile <changed .py files>                      # fast, catches syntax
ln -s /Users/ymmtny/GitHub/guaardvark/backend/venv venv
./venv/bin/python -m pytest tests/api/test_cast_library_api.py \
    tests/services/test_comfyui_idle_model_free.py \
    tests/services/test_character_still_pipeline.py -q         # the files you resolved

# frontend
cd /tmp/cloudplus-sync/frontend
ln -s /Users/ymmtny/GitHub/guaardvark/frontend/node_modules node_modules
npx eslint . --ext js,jsx                                     # catches merge artifacts
npx vite build
npx vitest run
```

**Verify the COMMITTED tree, not the working tree.** Two independent traps cost real time on
the last sync:

- **A fix made after staging is not in the commit.** `git commit` writes the **index**. Edits
  made to satisfy eslint *after* resolving conflicts are left behind, and the merge commits
  the broken version — in that case a duplicate `const` declaration and an undefined JSX
  component, which would have shipped. Re-`git add` after every post-staging edit.
- **The lint/build you just ran may have been on the working tree.** Confirm the two agree
  before calling a commit verified:

  ```bash
  git status --porcelain --untracked-files=no   # must be empty
  git show HEAD:path/to/file | grep -c '<marker>'   # check the COMMITTED content
  ```

  Do this **before pushing**, not after.

Repairing a merge commit after the fact is easy to get wrong: `git commit --amend` amends
**HEAD**, so if later fix commits sit on top, you amend the wrong commit (the tree looks fine
because a later commit fixed it, but the merge itself is still broken). To repair the merge:

```bash
git branch backup/pre-fix                      # safety
cp <the-correct-files> /tmp/saved/             # the correct content lives in the working tree
git checkout <merge-sha>
cp /tmp/saved/* <paths> && git add <paths>
git commit --amend --no-edit                   # parents are preserved
git rebase --onto <new-merge> <old-merge> <branch>   # replay the fix commits on top
```

**Always distinguish pre-existing failures from merge regressions.** If you do run a broad
suite, do **not** re-run the whole thing on the base — that doubles a 10-minute cost for no
extra signal. Re-run **only the failing node ids** against the base:

```bash
# 1. collect failures from the merged tree
cd /tmp/cloudplus-sync/backend && ./venv/bin/python -m pytest tests -q -p no:randomly 2>&1 | tee /tmp/t-merged.txt
grep -E '^(FAILED|ERROR)' /tmp/t-merged.txt | awk '{print $2}' > /tmp/failing.txt

# 2. re-run just those on the base — seconds, not minutes
git worktree add /tmp/cloudplus-base 32a22529        # = origin/cloud-plus tip before merge
cd /tmp/cloudplus-base/backend && ./venv/bin/python -m pytest $(cat /tmp/failing.txt) -q -p no:randomly
```

Anything failing in both is pre-existing; failing only in merged is a suspected regression.
Also check whether a failure is even near the merge: compare against the fork's divergent
files (`git diff --name-only upstream/main HEAD`).

**Use pristine upstream as the control, not just the base.** Base alone produces false
positives: a fork branch typically sits *behind* upstream, so tests upstream added since
the fork point fail on the fork for reasons that have nothing to do with the merge. The
last sync showed 5 "regressions" against base that all also failed on pristine
`upstream/main` — they were inherited, not ours. Extract upstream read-only and compare:

```bash
git archive upstream/main | (mkdir -p /tmp/upstream-pristine && tar -x -C /tmp/upstream-pristine)
# symlink the host venv/node_modules in, then replay the same node ids
```

Note that node ids can be **absent from base** (upstream-new tests). pytest aborts the whole
run on the first unresolvable id, so intersect against `--collect-only` output rather than
replaying the merged list blindly.

**A red test is not automatically the fork's fault — and is not automatically upstream's
either.** Both sides ship tests that encode bugs. The last sync's two real findings were:
`start.sh` writing an undeclared log, and `register_production_output` storing an absolute
*temp* path — where the fork had patched the reader and upstream's own tests asserted the
buggy absolute path. Read the test and the production call path before choosing a side.

For a **local** Postgres (no Docker needed — the docker daemon is usually down here):
`brew services list`, `which postgres pg_ctl`, `ls /opt/homebrew/var/postgres*`, `pg_isready`,
then start the already-installed service. Do not install anything.

Known-environmental / pre-existing, safe to ignore:

- **Postgres is not running** in this environment, so `tests/integration/**` and anything
  touching the DB fails with `psycopg2.OperationalError … 5432`. Docker is also usually
  down. This is not a merge signal. If a real signal is needed, start
  `docker-compose.yml` (Postgres/Redis/Ollama) first, or accept and report the limit.
- **2 frontend tests fail on the untouched base**: `buildPlanRequest.test.js >
  builds the backend plan payload from explicit workflow state` and
  `DragDropImageUpload.test.jsx > stages files locally when no subjectId`. Compare, do not
  assume.
- A background `task_scheduler` thread logs `Scheduler loop error` at teardown; it is
  noise, and pytest still exits 0.

## Reporting

Give the user: the divergence numbers, upstream's headline changes, the conflict table
(file · upstream Δ · our Δ · verdict), which of our features are genuinely ours, and what
is duplicated-and-superseded.

**Never open a PR or push a branch without being asked.**

## Landing the work: fork only

**Never open a pull request against `guaardvark/guaardvark`.** This fork does not
contribute back upstream. Finish the sync as a branch in `Kwik-Dev/guaardvark` and stop
there; leave `cloud-plus` at its old tip until the user asks for it to move.

- **Land on a plain branch, not a GitButler virtual branch.** A sync branch is a
  several-hundred-commit merge of `upstream/main`. Virtual branches are sized for feature
  work, and `but apply <branch>` would drag every upstream commit into the workspace as one
  "branch", on a base that is already `origin/cloud-plus`. Keep the sync as an ordinary
  branch and push it explicitly.
- **Push by refspec, never bare:**

  ```bash
  git push -u origin <branch>:refs/heads/<branch>
  ```

- **Check the branch's upstream before pushing.** `git worktree add -b <branch> origin/cloud-plus`
  leaves the new branch tracking `origin/cloud-plus`, so a bare `git push` sends the entire
  sync straight into `cloud-plus`. Verify and clear it first:

  ```bash
  git rev-parse --abbrev-ref <branch>@{upstream}   # prints origin/cloud-plus? that is the trap
  git branch --unset-upstream <branch>
  ```

- **Before calling any fix "upstreamable", test whether upstream could take it.** A fix for
  a **fork-only artifact** cannot go upstream — upstream's own tests reject it. `mcp.log`
  was exactly this: our `start.sh` adds an MCP startup step and writes it, upstream's never
  does, and upstream has a test asserting every declared log has a writer that names it.
  Keep fixes like this fork-local, and say plainly that they are permanent divergence.

  Conversely a fix for a bug upstream genuinely has — our `register_production_output`
  storing an absolute *temp* path the resolver could never serve — **is** upstreamable in
  principle. Still do not open that PR unprompted.

## Cleanup

```bash
git worktree remove /tmp/cloudplus-sync --force
git worktree remove /tmp/cloudplus-base --force
```

Remove the `node_modules`/`venv` symlinks before removing a worktree, or `--force`.
