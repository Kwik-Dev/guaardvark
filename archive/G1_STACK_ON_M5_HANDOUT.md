# Handout — stack G1 on M5 (option 1)

**Goal:** make `pr/g1-audio-video-polish` self-contained by stacking it on
`pr/m5-llm-providers`, the same way `pr/g5-vision-llm-routing` already is.
Then push both.

Written 2026-09-22 for a fresh session. Everything below was verified in this repo.

---

## Why this is needed

`pr/g1-audio-video-polish` is based on `933b36f7` (upstream) but its music-prompt
rewriter imports M5-only modules:

```python
# backend/utils/music_prompt_rewriter.py
from backend.services import openai_provider          # ← M5 file, absent in G1
...
if llm_provider.is_openai_active():                   # ← M5 helper
    model=llm_provider.get_openai_model()             # ← M5 helper
```

In the G1 tree there is **no `backend/services/openai_provider.py`**, and
`backend/services/llm_provider.py` is the pre-M5 version (no `OPENAI`,
`is_openai_active`, `get_openai_model`). So importing the rewriter raises
`ImportError` and the G1 PR is dead on arrival. This was pre-existing — the fix
commit just adds the consent gate on top.

G1 therefore belongs on M5, exactly like G5 (`fix/vision-sync`,
`chore/llm-providers-followup`, `feat/filmcrew-openai-compatible`).

---

## Current state (do not lose this)

| Ref | Local | Origin | Notes |
|---|---|---|---|
| `upstream/main` | `5777713c` | — | |
| `pr/m5-llm-providers` | **`8da8c8be`** | **`8da8c8be`** (pushed) | #214, 6 ahead / 11 behind upstream |
| `pr/g5-vision-llm-routing` | **`c87ade06`** | `f7e321d1` (stale) | rebased onto `8da8c8be` + 2 cherry-picks, **not pushed** |
| `pr/g1-audio-video-polish` | **`f80c0e70`** | `0d2a1eac` (stale) | fix commit applied, **not pushed**, **broken standalone** |
| `pr/discord-voice` | `a0248365` | `a0248365` (pushed) | no PR yet |
| `feat/audio-music-polish-progress` (workspace) | `dcdd1adc` | — | G1 source feat |
| `feat/filmcrew-openai-compatible` (workspace) | `f81f3d5c` | — | G5 source feat |
| `fix/vision-sync` (workspace) | `7989da0c` | — | G5 source feat |
| `voice-openai-routing` (workspace) | `19fb17c5` | — | carries `_active_cloud_llm` fix + tests |

G1 commits to replay: `0d2a1eac` ("G1: audio/music polish, i2v, filmcrew i2v
speed") and `f80c0e70` ("fix(audio): the music prompt rewriter respects cloud
consent"). Their parent/base is `933b36f7`.

Worktrees:
- `/private/tmp/prg1` → `pr/g1-audio-video-polish`
- `/private/tmp/prg5` → `pr/g5-vision-llm-routing` (already restacked; just needs pushing)
- `/private/tmp/pr214` → `pr/m5-llm-providers`
- `/private/tmp/pr197` → `pr/m4-comfyui-image`
- `/private/tmp/prdiscord` → `pr/discord-voice`

Nothing for G5/G1 has been pushed. `#214` is pushed at `8da8c8be` and the user
asked **not** to change #214 again for now.

---

## Order of operations

1. **M5 (#214) lands first** — G1/G5 are stacked on it.
2. Push G5 (already restacked) and G1 (after the rebase below).
3. M5/G5/G1 are all 11 commits behind current `upstream/main`. That is the
   existing snapshot state. If you want them current, rebase M5 onto
   `upstream/main` first — **but that rewrites #214**, which the user said to
   avoid. Default: keep `8da8c8be` as the stack base.

---

## The rebase (G1)

```bash
cd /private/tmp/prg1
git status                       # must be clean; abort if not
git log --oneline -3             # expect f80c0e70, 0d2a1eac, 933b36f7

git rebase --onto 8da8c8be 933b36f7 pr/g1-audio-video-polish
```

`--onto <new-base> <old-base>`: replay the two G1 commits onto `8da8c8be`.

### If it conflicts

The G1 diff and the M5 diff overlap on **24 files** (conflict candidates):

```
backend/config.py
backend/requirements-base.txt
backend/requirements.txt
backend/services/comfyui_image_generator.py
backend/services/consent_records.py
backend/services/gpu_resource_policy.py
backend/services/stills_pipeline.py
backend/tests/api/test_chat_export_api.py
backend/tests/services/test_chat_image_tools.py
backend/tests/services/test_consent_gate.py
backend/tests/services/test_pulid_matrix.py
backend/tests/services/test_stills_flux_model_tag.py
backend/tests/test_text_cut.py
backend/tests/unit/test_knowledge_sources.py
backend/tools/workstation_tools.py
backend/utils/text_cut.py
frontend/package-lock.json
frontend/package.json
frontend/src/config/__tests__/navCatalog.test.js
frontend/src/pages/MusicVideoPage.jsx
scripts/training_director/README.md
scripts/training_director/produce.py
scripts/training_director/project.py
scripts/training_director/voice_style.py
```

Resolution policy:
- Keep **M5's** structure/version in shared files; keep **G1's** feature changes.
- Do **not** `git checkout --theirs` — during a rebase "theirs" is the commit
  being replayed, and during merge/cherry-pick it is the old version. Resolve by
  hand.
- `backend/utils/music_prompt_rewriter.py`: keep exactly one
  `from backend.services import openai_provider` and the consent gate; the top
  block must be:
  ```python
  from backend.config import OLLAMA_BASE_URL
  from backend.services import openai_provider
  from backend.utils.ollama_resource_manager import think_payload
  from backend.utils.llm_service import get_saved_active_model_name
  ```
  (a duplicate `openai_provider` import caused a conflict last time)
- `backend/config.py`: keep M5's `OPENAI_*` block **and** G1's audio/music vars.
- Finish with `git add <resolved files> && git rebase --continue`.
- If it turns into a mess: `git rebase --abort` and reconsider (bundle the
  music change into G5 instead).

---

## Verify

```bash
cd /private/tmp/prg1

# 1. The module imports now (was the whole problem)
python -c "from backend.utils import music_prompt_rewriter; print('ok')"

# 2. M5 provider layer is present
ls backend/services/openai_provider.py
grep -c "def is_openai_active" backend/services/llm_provider.py   # 1

# 3. G1's own regression tests
/Users/ymmtny/GitHub/guaardvark/backend/venv/bin/python -m pytest \
  backend/tests/test_music_prompt_rewriter_consent.py \
  backend/tests/services/test_music3.py -q

# 4. Diff vs the M5 base is only G1's files
git diff --name-only 8da8c8be..pr/g1-audio-video-polish > /tmp/g1delta.txt
cat /tmp/g1delta.txt        # music rewriter, its test, + the G1 feature set
```

Expect `test_music_prompt_rewriter_consent.py` = 2 passed.

---

## Push (after the user approves)

```bash
# G1 — rebase rewrote history, so force-with-lease
cd /private/tmp/prg1
git push origin pr/g1-audio-video-polish --force-with-lease

# G5 — already restacked on 8da8c8be
cd /private/tmp/prg5
git push origin pr/g5-vision-llm-routing --force-with-lease
```

Then open/repoint the PRs with base `pr/m5-llm-providers` (compare URLs):
- `https://github.com/guaardvark/guaardvark/compare/pr/m5-llm-providers...Kwik-Dev:pr/g1-audio-video-polish`
- `https://github.com/guaardvark/guaardvark/compare/pr/m5-llm-providers...Kwik-Dev:pr/g5-vision-llm-routing`

---

## Follow-ups

- **`PR_WORKFLOW.md`** branch map: change G1's Base from `upstream/main` to
  `pr/m5-llm-providers`, and note G1 depends on M5 (like the G5 note). Update the
  G5 head to `c87ade06` and G1 to the post-rebase hash.
- `#214` comment (if the user wants): the four capability-as-consent sites were
  fixed on G5/G1, not in #214; the only remaining in-scope #214 item was the
  cosmetic `character_generator_service.py:168` → `is_openai_active()` rename,
  which is a no-op (`is_openai_active()` is literally
  `get_active_provider() == OPENAI`).
- `pr/discord-voice` has no PR yet.

---

## Gotchas

- **Piped output can be silently truncated by this shell's compressor.** For
  verification, redirect to a file then read/grep it:
  `git diff --name-only A..B > /tmp/x.txt && grep -n ... /tmp/x.txt`. A piped
  `... | grep -c` gave a false "0" earlier and nearly caused a wrong decision.
- **Never run `git merge` in parallel** in a worktree.
- `gitbutler/workspace` (the main checkout, `/Users/ymmtny/GitHub/guaardvark`) is
  managed by **`but`** — use `but` for workspace commits; use plain `git` inside
  the `/private/tmp/pr*` worktrees.
- The workspace already has the fixes applied (`voice-openai-routing`,
  `fix/vision-sync`, `feat/filmcrew-openai-compatible`,
  `feat/audio-music-polish-progress`) — the snapshots are what lag.
