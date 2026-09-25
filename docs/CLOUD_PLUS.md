# Working with `cloud-plus`

`cloud-plus` is this fork's **mainline**: upstream `main` plus everything the fork adds. If you
are new to the fork, start here, then read the fork callout at the top of
[README.md](../README.md) for *why* it exists.

---

## 1. What the branch is

| Ref | Role |
|---|---|
| `main` | **Pure mirror of `upstream/main`**, fast-forward only. Never receives fork work. |
| `cloud-plus` | **The fork's mainline.** Upstream plus the fork's work. CI runs here. GitButler's target branch. |
| `keep-cloud-providers` | The single commit that reverts upstream's removal of the cloud providers — the canonical divergence record. |
| `pr/*` | Fork-only **snapshot archives** (M4, M5, G1, G5, discord-voice). Not PRs any more. |

**What it adds.** Upstream went local-only: `001d960c` *"remove(chat): cloud chat providers
(Mistral) and the master cloud switch"* (merged as `4fe7406a`) deleted the Mistral provider, the
provider-selection API, the `cloud_models_enabled` switch and the OpenAI-compatible route. This
fork keeps them. Local Ollama stays the default; the cloud layer is opt-in and doubly gated
(section 3).

To see the full delta at any time:

```bash
git log --no-merges --oneline upstream/main..cloud-plus       # the fork's commits
git diff --stat upstream/main cloud-plus                      # the files it touches
```

---

## 2. Get it running

```bash
git clone -b cloud-plus https://github.com/Kwik-Dev/guaardvark.git
cd guaardvark
./start.sh
```

`./start.sh` creates `.env`, installs what it needs, applies migrations, and brings up the
backend, Vite, Redis, Postgres and the plugin set. Platform-specific setup, prerequisites and the
Docker alternative live in [INSTALL.md](../INSTALL.md) — this page only covers the fork's own
pieces.

**Ports.** Default backend port is `5000`; on macOS `start.sh` writes `FLASK_PORT=5055` because
AirPlay Receiver owns `5000`. The UI is Vite on `5173`. Set `FLASK_PORT` in `.env` to choose
another one; everything else follows it.

**Verify the install** before trusting anything downstream:

```bash
B=${GUAARDVARK_URL:-http://localhost:5055}      # 5000 on Linux
curl -s $B/api/health                           # {"status":"ok", ...}
curl -s $B/api/plugins/status                   # per-plugin state map
curl -s $B/api/settings/profile                 # workstation | creator
```

`GET /api/plugins/status` matters: generation needs `comfyui`, voice/music/FX need
`audio_foundry`, upscaling needs `upscaling`, LoRA training needs `lora_trainer`. A route whose
plugin is off answers **503** — that is not a bug.

---

## 3. Turn the cloud layer on (the fork's own feature)

Three things must all be true before a single token leaves the machine:

1. **A provider is configured** in `.env` (the key/endpoint exists).
2. **The master switch is ON** — the `cloud_models_enabled` setting (default **off**).
3. **That provider is selected** as the active chat provider.

A configured endpoint alone is never consent. Turn the switch off and chat reverts to local
Ollama immediately, whatever is selected.

### Configure a provider in `.env`

Mistral:

```ini
MISTRAL_API_KEY=...                      # required
GUAARDVARK_MISTRAL_MODEL=mistral-large-latest   # optional
# GUAARDVARK_MISTRAL_BASE_URL=https://api.mistral.ai/v1
```

Any OpenAI-compatible endpoint — OpenAI, OpenRouter, Groq, Together, vLLM, or a local
Ollama OpenAI-compat endpoint. One client covers them all; only the base URL changes:

```ini
GUAARDVARK_OPENAI_BASE_URL=https://openrouter.ai/api/v1   # required — there is no implicit
                                                          # api.openai.com default
GUAARDVARK_OPENAI_API_KEY=...                             # optional for local endpoints
GUAARDVARK_OPENAI_MODEL=gpt-4o-mini                       # optional
```

A bare `OPENAI_API_KEY` that you exported globally for other tools is **never** read — the
`GUAARDVARK_` prefix is what makes it opt-in.

Restart the backend after editing `.env` (`./restart_backend.sh`).

### Drive it from the API

```bash
B=${GUAARDVARK_URL:-http://localhost:5055}

curl -s $B/api/llm/provider                    # full state: switch, provider, cloud_active, keys
curl -s -X POST $B/api/llm/cloud-enabled -H 'Content-Type: application/json' -d '{"enabled":true}'
curl -s -X POST $B/api/llm/provider      -H 'Content-Type: application/json' -d '{"provider":"openai"}'
curl -s "$B/api/llm/provider/models"           # the endpoint's model list
curl -s -X POST $B/api/llm/provider/test       # live round-trip against the active provider
```

`POST /api/llm/provider` **refuses** a cloud provider while the master switch is off (`400`), and
`get_active_provider()` silently degrades to Ollama if a selected provider's key disappears — a
missing key or a flipped-off switch can never wedge chat into a dead provider. Set the model with
`POST /api/llm/provider/mistral-model` or `POST /api/llm/provider/openai-model`.

A healthy result looks like:

```json
{"data":{"connected":true,"model":"...","provider":"openai","response":"Connection successful"}}
```

### Drive it from the UI

**Settings → Model Management** ("Cloud and local chat model providers"). The *Enable Cloud
Models* toggle is the master switch; the provider controls only appear while it is on. A
persistent banner reads *"⚠ Cloud model active — chat is sent to …"* whenever a cloud provider is
serving chat.

### What does **not** go to the cloud

Embeddings and RAG always stay on local Ollama, whatever the chat provider is — the vector store
stays consistent with whatever indexed it.

---

## 4. Staying current with upstream

`cloud-plus` does not track upstream by itself: a rebase pulls from the configured target and never
fetches upstream. Upstream's changes arrive only when they are merged in.

```bash
git fetch upstream
git update-ref refs/heads/main upstream/main && git push origin upstream/main:refs/heads/main
# merge the moved mirror into the mainline
git merge main && git push origin cloud-plus
# then reconcile the workspace branches onto the updated target
but pull
```

Most upstream commits merge cleanly. Conflicts come back only where upstream touches the
divergence — the cloud-provider files it *deleted*.

> **Conflict policy:** upstream deleted the cloud-provider files; the fork keeps them. Keep the
> fork's side for those, take upstream's everything else.

**After any rebase or merge, confirm the five cloud files still exist** — a GitButler
auto-resolution once deleted them silently (see section 7):

```
backend/services/llm_provider.py
backend/services/mistral_provider.py
backend/services/openai_provider.py
backend/api/llm_provider_api.py
frontend/src/components/settings/ModelManagementSection.jsx
```

---

## 5. Making a change

Work in GitButler virtual branches, then land onto the target:

```bash
but branch new <name>          # or reuse an applied branch
# ... edit ...
but commit -b <name> -m "..." <file-ids>     # file IDs from but diff / but status -fv
but land <name> --whole-stack --yes          # moves origin/cloud-plus and removes the branch
```

`but land` pushes to the remote and prints a revert command every time — **it is not easily
reversible**:

```bash
git push --force-with-lease origin <previous-sha>:refs/heads/cloud-plus
```

To bring a commit across from a snapshot branch:

```bash
but branch new fix/<thing>
but pick <sha> fix/<thing>     # cherry-picks from an unapplied branch
but resolve <commit-id>        # if it conflicts; edit, then:
but resolve finish
but land fix/<thing> --whole-stack --yes
```

### Run these before landing

```bash
# CI gate: no absolute home paths / machine-specific hosts.
# The default mode scans **tracked** files only (git ls-files), so a brand-new file
# passes locally and fails in CI — as happened to this very file. Check the staged
# set before you land; the script's own header says the same.
bash scripts/check_portable.sh --staged

cd frontend && npm run lint && npm run build         # CI gate: frontend
```

The backend test set CI runs — read the list out of `ci.yml` rather than retyping it:

```bash
backend/venv/bin/python -m pytest $(python3 - <<'EOF'
import re
s = open('.github/workflows/ci.yml', encoding='utf-8').read()
blk = s.split('Run smoke suites', 1)[1].split('Backend Validation', 1)[0]
print(' '.join(dict.fromkeys(
    p.rstrip('\\').strip() for p in re.findall(r'^\s+((?:backend|extensions)/\S+)', blk, re.M))))
EOF
) -q
```

`cli/tests/test_local_tools.py::test_run_command` fails on macOS and passes on CI's ubuntu —
environmental, not a fork regression.

### What CI runs

`ci.yml` and `codeql.yml` both trigger on pushes and PRs to **`main` and `cloud-plus`** (`main` is
upstream's code; `cloud-plus` carries the fork, so it has to be checked). `ci.yml` jobs: Frontend
Lint & Build, Backend Validation (static quality gate + portability + syntax), Backend Tests (CPU
smoke), CLI Tests, macOS install + platform smoke, macOS boot. The macOS jobs are what catch
Apple-Silicon-only breakage.

---

## 6. Long-running processes

Three processes run your code and each picks it up differently:

| Process | Started by | Picks up code changes via |
|---|---|---|
| Flask backend | `./start.sh`, `./restart_backend.sh` | `./restart_backend.sh` (it does not auto-reload) |
| Celery worker + beat | `./start_celery.sh` | `./restart_celery.sh` |
| Vite (frontend) | `./start.sh` | HMR — nothing to restart |

`restart_backend.sh` restarts **only** the backend. That is deliberate — its header says so — but
it means a code change leaves the Celery workers on the old code. **A Celery worker imports the
backend package into a long-lived process that never reloads**, so after a change it keeps
executing what it had at start-up. The symptom is a worker logging `ImportError` for a symbol that
is plainly present on disk:

```
Could not load the tool path limit setting: cannot import name 'get_confine_tool_paths'
from 'backend.utils.settings_utils'
```

That is a stale process, not a broken tree. Run:

```bash
./restart_celery.sh
```

It stops only this checkout's worker/beat (SIGTERM for Celery's warm shutdown, then SIGKILL after
20 s), then delegates the launch to `start_celery.sh`, which owns the worker flags, the broker
wait and the beat-schedule cleanup. `./stop.sh` stops everything if you want a full cycle.

---

## 7. Gotchas (hard-won — read before touching version control)

- **GitButler's rebase conflict auto-resolution calls the NEW BASE "ours."** During a rebase,
  "ours" is the branch being rebased *onto* — so when the target was upstream (which deleted the
  cloud files), *"auto-resolved using the ours side"* **silently deleted them** from
  `gitbutler/workspace`. The only signal was conflicted commits naming files nobody had touched.
  **After any rebase, verify the five cloud files from section 4 are still there.**
- **Landing order can revert a feature.** One commit may *add* the Character Generator cloud route
  while a later one *removes* it, because a review decided the routing belonged elsewhere. Both
  are correct for their own branch, but landing in the wrong order means the removal wins.
  **After landing a set, grep for the feature's markers** (`git grep <symbol> origin/cloud-plus`).
- **Rebasing-on-land means no commit is patch-equivalent to its snapshot.** Every land reconciles
  the remaining branches onto the moved target, so tips get new SHAs. `git cherry` and ancestry
  checks are useless for "did this content land" — **use content markers.**
- **`but commit` can leave a stale git index** — `git status` shows `MM <file>` while `but status`
  correctly says *no changes*. Cosmetic; the commit is fine (`git diff HEAD -- <file>` is empty).
- **Changing GitButler's target requires every branch unapplied** (`but config target` refuses
  otherwise). The app can do it with branches applied; the CLI cannot. The target lives in
  `.git/gitbutler/but.sqlite` as well as git config — **never "fix" it with `git config`.**
- **Do not pipe live/streaming output through `tail`** — it waits for EOF and hangs. Redirect to a
  file and read it, or use `grep --line-buffered`.

### On macOS, when a render is refused

Two environment settings decide whether GPU work can start on a Mac; both are documented in
[docs/MACOS.md](MACOS.md) and both are opt-in.

```ini
# Share one model tree with ComfyUI Desktop instead of downloading a second copy.
# Absolute, or a ~/ path — the value is passed through os.path.expanduser, so the
# ~/ComfyUI-Shared form the Mac docs use does resolve. The model registry AND the
# ComfyUI downloader read <dir>/models, so models you already have show up as
# installed instead of as "missing".
GUAARDVARK_COMFYUI_DIR=~/ComfyUI-Shared

# Apple Silicon: route Z-Image through ComfyUI instead of the offline path.
GUAARDVARK_ZIMAGE_USE_COMFYUI=1
```

Two failure shapes worth recognising:

- **A refusal that names system RAM**, e.g. *"GlobalLoadGate blocked: system RAM too low (not
  VRAM): 17.1 GB available, need 21.0 GB + 4.8 GB floor"*. The declared footprint is the offline
  Z-Image family's 21 GB; the floor is 10 % of total RAM. A resident chat model of comparable size
  (a warm-up model is often 17 GB) is enough to make the offline path unadmittable — and
  `auto_enhance`, which is **on by default**, wakes exactly that model in the same request.

  **Fixed for cloud-chat installs.** The prompt rewrite now goes to the consented provider, so no
  local chat model is woken for it. Before that fix the enhancer ran on Ollama whatever Settings
  said, because the consent gate needs a Flask app context that batch and Celery threads do not
  have — which is why turning `auto_enhance` off was the only thing that helped. If chat runs
  locally, or you are on an older checkout, the ComfyUI route is still the way through: it declares
  VRAM only, so `GUAARDVARK_ZIMAGE_USE_COMFYUI=1` sidesteps the footprint entirely.
- **"No installed video model is ready for this card"** while the models are plainly on disk. The
  registry resolves model paths under `GUAARDVARK_COMFYUI_DIR/models`; without that setting it
  looks only inside `plugins/comfyui/ComfyUI/models/`, which on a Comfy Desktop install is empty
  scaffolding.
