# Guaardvark CLI — implementation plan: full Studio coverage + test framework renewal

**Companion to:** [`docs/CLI_SPEC.md`](CLI_SPEC.md) (what the CLI is today, and the gap tables).
**Status:** proposed. Nothing here is implemented except where marked.
**Baseline:** `cloud-plus` tip `95393444`.

---

## 0. Decisions needed before Phase 1

These change the shape of the work; everything else can proceed without them.

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **D1** | Do render/GPU-spend actions enter the CLI? | (a) stay plan-only · (b) allow render commands that use the existing job/gate system | **(b)** — `images generate` and `videos generate` already spend GPU; `video-editor render` and `training start` are the same class. Keep them going through the same GPU gate so the CLI cannot bypass it. |
| **D2** | Do **approval** actions enter the CLI? | (a) keep approvals Studio-only · (b) add explicit `approve`/`reject` subcommands | **Split**: read-only `approvals list/show` in the CLI; `approve`/`reject` **only** where a contract test already permits it (`outreach approve`). Do **not** add approvals for held code / inbound guard / film-crew storyboards, and keep `test_music_video_cli.py`'s "never POST approve" contract. |
| **D3** | Where does fork code live? | (a) new modules + one registry line · (b) a separate pip package | **(a)** — see §2. |
| **D4** | Is the CLI surface a fork-only concern? | (a) fork-only · (b) upstreamable | **Mixed** (§3 marks each): `upscaling`, `system-map`, `web-search`, `content-management`, `wordpress`, `connections`, `self-improvement`, `inbound-guard`, `infographic` are *upstream* features that upstream's own CLI also lacks — upstreamable in principle, but per fork policy **no PR is opened**. |
| **D5** | A **generic REST escape hatch** — does one command reach every backend route? | (a) keep wrapping one command at a time · (b) a single `api request <METHOD> <PATH>` with a guard · (c) a separate second CLI that speaks raw REST | **(b), safety tier B** (decided 2026-10-05, after a proposal for (c)). Adding commands one at a time is why coverage is always N of 97 — a route nobody wrapped is unreachable from the terminal, which is exactly what blocks *render Film Crew / music-video by CLI* and the ~25 unwrapped files routes. (c) was rejected: the value is one ~200-line command reusing `llx.client`, not a second 9.7k-line CLI to re-port against ~5 upstream `cli/` commits a week. **Tier B**: reads free; every write needs `--yes`; a decision-class route needs `--yes` even as a read; `--dry-run` previews a gated request **without** needing `--yes` (seeing what would be sent is how a person decides); every attempt (allowed, refused, failed, previewed) is appended to `<GUAARDVARK_DIR>/api-audit.jsonl`. This **amends D2**: D2 still governs the *named* commands (no `film-crew approve-storyboard`, no `guard approve`, no held-code release), while `api request` is the documented, gated, audited escape hatch that can reach them when a person types the path deliberately. See §3.16. |
| **D6** | Do the **two render gates** become named commands, or stay `api request`-only? | (a) raw paths only · (b) named, `--yes`-gated commands for the three routes that start a render | **(b)** (decided 2026-10-05, after the asks that opened this work: "I need captions on videos, film crew and music video by CLI"). (a) is technically sufficient — D5 reaches them — but "render my film from the terminal" should not require knowing the route. The gates are the **only** named commands D2 permits: `film-crew confirm-casting`, `film-crew approve-storyboard`, `music-video approve`. They are creative and cost selections on the operator's own output, the same class as the `cast approve` exception that already exists; **inbound-guard, held-code release and publish stay Studio-only** because those decide whether something leaves the machine. Each needs `--yes`, each names the stage transition it performs, and the D2 static scan keeps exactly one file-wide exception (`render_gates.py`). See §3.17. |

---

## 1. Constraints that shape the work

1. **`cli/` is upstream-owned** — no `cloud-plus` commit has ever touched it
   (`git log upstream/main..cloud-plus -- cli/` is empty). Every line we add is permanent
   divergence, so it must be **additive and isolated**.
2. **The CLI is a thin REST client.** New commands = new modules calling `llx/client.py`.
   No new transport, no local state.
3. **`command_catalog.py` is contract-enforced** (`cli/tests/test_command_catalog_contract.py`):
   `COMMAND_TREE`, `SlashRouter`, the completer and `/help` must agree. Any new REPL
   command must be declared there.
4. **The portability gate** (`scripts/check_portable.sh`) runs on `cloud-plus`; new files
   must not contain literal absolute home paths, identities or secrets.
5. **CI job `cli`** runs `pip install -e ./cli pytest && python -m pytest cli/tests -q` —
   new tests must be self-contained (no backend, no GPU).

## 2. Registration strategy — keep the upstream diff to one line

Today `cli/llx/main.py` imports every command module (lines 7–39) and calls
`app.add_typer(...)` for each group (~line 146+); `slash.py` has `_register_repl_commands`
and `_register_typer_commands`.

**Plan:** add three fork-owned files and touch one upstream file twice (a single import
plus a single loop):

```
cli/llx/commands/_fork/__init__.py        # FORK-OWNED
cli/llx/commands/_fork/registry.py        # FORK-OWNED: (module, app, name, catalog) tuples
cli/llx/commands/_fork/api_coverage.py    # FORK-OWNED: command -> API area map + NOT_EXPOSED
```

- `main.py` gains a single loop:
  `for mod, typer_app, name in fork_registry.typer_apps(): app.add_typer(typer_app, name=name)`.
- `command_catalog.py` gains one merge of `fork_registry.repl_commands()`, or the contract
  test is taught to accept the fork registry as a second source.
- Everything else — every new command module — lives under `cli/llx/commands/_fork/`.

Net expected conflict on an upstream sync: **one import line + one add_typer loop**. If
upstream ever refactors `main.py`, the fix is mechanical.

## 3. Command map — the gap, group by group

Each row is a new command group (or an extension of an existing one) with the backend
routes it needs. Tiers: **1** read-only · **2** generation/media · **3** GPU-spend or editing
· **4** parity/polish. `U` = upstream feature that upstream's CLI also lacks.

### 3.1 Cloud LLM providers — `llm` (Tier 4, **fork flagship**)

| Subcommand | Route |
|---|---|
| `llm provider` | `GET /api/llm-provider/provider` |
| `llm set --provider <p>` | `POST /api/llm-provider/provider` |
| `llm model openai|mistral` | `POST /api/llm-provider/provider/openai-model`, `/mistral-model` |
| `llm models` | `GET /api/llm-provider/provider/models` |
| `llm test` | `POST /api/llm-provider/provider/test` |
| `llm cloud on\|off` | `POST /api/llm-provider/cloud-enabled`, `GET /cloud-enabled` |

This is the fork's reason to exist (upstream removed the cloud providers) and it has **no
CLI surface at all**. Highest value per line of code.

### 3.2 Cast Library — `cast` (Tier 2, biggest gap)

`cast list` · `cast show <id>` · `cast refs <id>` · `cast samples <id>` · `cast plan <id>` ·
`cast generate <id> --count/--shots` · `cast cancel <id>` · `cast approve <id>` ·
`cast train <id>` · `cast loras <id>` · `cast make-default <id> <base>` ·
`cast import-lora <id> --file` · `cast delete <id>`

Routes: `GET|POST /api/cast-library/subjects`, `GET|PATCH|DELETE /subjects/<id>`,
`GET /subjects/<id>/samples`, `POST /subjects/<id>/{plan,generate,generate/cancel}`,
`POST /subjects/<id>/samples/<sid>/regenerate`, `POST /subjects/<id>/loras/<base>/make-default`,
`POST /subjects/<id>/import-lora`, `POST /subjects/<id>/bible/from-refs`.

> `cast approve` is a sample-review gate. Covered by **D2**: `list/show` first; `approve`
> only if D2 permits.

### 3.3 Upscaling — `upscale` (Tier 2) `U`

`upscale image <path>` · `upscale images <paths...>` · `upscale video <path>` ·
`upscale status <job>` · `upscale jobs` · `upscale cancel <job>` · `upscale models` ·
`upscale download <model>` · `upscale preview`

Routes: `POST /api/upscaling/upscale/{image,images,video,preview}`, `GET /jobs`,
`GET|DELETE /jobs/<job>`, `GET /models`, `POST /models/download`, `POST /upload`.

### 3.4 Video editor — `video-editor` (Tier 3)

`video-editor projects list|create|open|delete` · `video-editor plan` ·
`video-editor render` · `video-editor trim` · `video-editor captions export|import` ·
`video-editor filters` · `video-editor transitions` · `video-editor shotcut <mlt>` ·
`video-editor jobs|status`

Routes: `GET|POST /api/video-editor/projects`, `GET|PATCH|DELETE /projects/<pid>`,
`POST /{plan,beat-sync/render,auto-editor/trim,captions/export,captions/import,open-in-shotcut}`,
`GET /catalog/{filters,transitions}`, `GET /config`, `GET /jobs`, `GET /jobs/<id>`.

This is the whole of `CLOUD_PLUS_FEATURES` §4 (FFmpeg stills, framing, captions, bin
reorder) — currently **zero** CLI surface. `captions export` is a Document registration and
already exists server-side.

### 3.5 Training — `training` (Tier 3, GPU **and** paid cloud)

`training datasets list|create|show|update|delete` · `training backends` ·
`training start <subject> --backend local|runpod` · `training status` · `training cancel`

Routes: `/api/training-datasets` CRUD; RunPod trainer via
`plugins start runpod_lora_trainer` + its plugin routes.

`training start --backend runpod` spends money → require `--yes` (or `--confirm`) and echo
the estimated cost. Never default to a paid backend.

### 3.6 Inbound guard — `guard` (Tier 1) `U`

`guard status` · `guard scans` · `guard show <scan>` · `guard sweep` · `guard git <digest>`

Routes: `GET /api/inbound-guard/scans`, `GET /scans/<id>`, `POST /sweep`,
`GET /git/<digest>`. **Read-only** per D2 — no `approve`/`reject`.

### 3.7 Self-improvement — `improve` (Tier 1) `U`

`improve status` · `improve runs` · `improve metrics` · `improve pending` ·
`improve precheck` · `improve trigger` · `improve toggle`

Routes: `GET /status`, `/runs`, `/metrics`, `/pending-fixes`, `/precheck`,
`POST /trigger`, `/toggle`, `/task`, `/lock-codebase`. `apply/approve/reject` on pending
fixes is a review gate → **omitted** per D2.

### 3.8 System Map — `system-map` (Tier 1) `U`

`system-map snapshot` · `system-map findings` · `system-map dismiss <id>` ·
`system-map health`

Routes: `GET /api/system-map/{snapshot,findings,health}`,
`POST /findings/<id>/dismiss`. `dispatch` triggers work → omit initially.

### 3.9 Approvals — `approvals` (Tier 1, **read-only**)

`approvals list` · `approvals show <id>`

Aggregates what the Studio's Approvals page aggregates (per `usePendingApprovals.js`):
pending publishes (`connections/publish records`), inbound-guard held code, supervised
outreach drafts. **No approve/reject** (D2).

### 3.10 Content management — `content` (Tier 1) `U`

`content pages list|show|delete` · `content stats` · `content generations` ·
`content duplicates <file>`

Routes: `GET /api/content-management/pages`, `/pages/<id>`, `/stats`, `/generations`,
`POST /check-duplicates`. `approve` / `mark-uploaded` are state changes — include only if
the user asks; they are review-adjacent.

### 3.11 WordPress — `wordpress` (Tier 4) `U`

`wordpress sites` · `wordpress pages` · `wordpress pull sitemap|page|bulk` ·
`wordpress process queue|execute` · `wordpress status <site>`

Routes: `GET /api/wordpress/{sites,pages,pages/<id>}`,
`POST /pull/{sitemap,page/<site>/<post>,bulk,list}`,
`POST /process/{queue,queue/execute,page/<id>}`, `GET /process/status/<id>`.

### 3.12 Web research — `web` (Tier 1) `U`

`web search <q>` · `web quick-search <q>` · `web sitemap <url>` · `web status`

Routes: `POST /api/web-search/{search,quick-search,sitemap}`, `GET /status`.
Distinct from the existing document `search` — name it `web`, not `search`.

### 3.13 Infographic — `infographic` (Tier 2) `U`

`infographic generate <prompt>` · `infographic models` · `infographic download <model>` ·
`infographic status` · `infographic view <id>`

Routes: `POST /api/infographic/generate`, `GET /models`, `POST /models/download`,
`GET /models/download-status`, `GET /status`, `GET /view`.

### 3.14 Connections — `connections` (Tier 4) `U`

`connections list|show|create|update|delete` · `connections test <id>` ·
`connections providers` · `connections env` · `connections oauth start|complete <id>`

Routes: `/api/connections` CRUD, `POST /<id>/test`,
`POST /<id>/oauth/{start,complete}`, `GET /providers`, `GET /environment`.

`oauth` opens a browser → make it print the URL and require explicit confirmation.

### 3.15 Extensions to existing groups

| Group | Add | Route |
|---|---|---|
| `audio` | `audio transcribe <file>` (STT — currently **none**) | `POST /api/voice/...` transcribe |
| `images` | `--engine comfyui` (fork's `/imagemodel comfyui`) | `POST /api/model/...` or the image model route |
| `models` | `models image list\|set\|download` (model-management UI) | model + infographic/upscaling model routes |
| `plugins` | `plugins comfyui status` (engine probe detail) | plugin API |
| `film-crew` | `storyboard show`, `cast add <shot> <subject>`, `retry` | `GET /api/production/<id>/storyboard/shot/<sid>/image`, `POST /<id>/cast/<subject>`, `POST /<id>/retry` |
| `music-video` | `clips show <idx>` (read-only progress) | `GET /api/music-video/<id>` |

`film-crew`/`music-video` render and approval routes stay out **of the named commands** per D2 (amended by D5): there is no `film-crew approve-storyboard`, and `test_music_video_cli.py`'s "never POST approve" contract keeps passing unchanged. D5 adds one generic, `--yes`-gated, audited route (`api request`, §3.16) that can reach them when a person names the path deliberately.

### 3.16 Generic REST access — `api` (fork, D5)
One command, every route. Nothing here is a new backend capability: every route already
exists, is already called by the Studio, and is already listed by the backend itself at
`GET /api/routes`.

| Command | What it does |
| `api request <METHOD> <PATH>` | Send any `/api/...` request: `--data`/`--data-file`/`@file` body, repeatable `--query k=v`, `--dry-run` (free — previews a gated request without `--yes`), `--json`. Reads are free; writes need `--yes`; a decision-class route needs `--yes` even as a read. |
| `api routes` | List every route the backend serves (`GET /api/routes`, or `--docs` for docstrings), filterable by `--search`/`--method`. This is the discovery surface that makes the escape hatch usable. |
| `api audit` | Show recent entries from `<GUAARDVARK_DIR>/api-audit.jsonl` — what this CLI actually sent, refused, or failed, with `--decisions` to filter. |

Design notes, because the guard is the point:

1. **The guard lives in `_api_guard.py`**, not in `api.py`. The registry and the fork
   contract tests both skip a leading underscore, so that file is the one place allowed
   to *name* the decision-class routes every other fork command is forbidden to call.
   `api.py` therefore contains no decision-route literal at all, and the existing static
   scan in `test_fork_readonly_contract.py` keeps working unmodified.
2. **Path must start with `/api/`.** That is what keeps the command away from plugin
   ports and the non-API surface, and it reuses the client's `base_url` rather than an
   absolute URL — so `--server` still points at a remote box and the GPU gate (D1) still
   holds: every call lands on a backend route, never on a plugin directly.
3. **The audit log is client-side** (`<GUAARDVARK_DIR>/api-audit.jsonl`, JSONL). The CLI
   records what *it* did; it works against a remote server and needs no backend change.
   It records the body's **byte size, not its content** — a body can carry a prompt, a
   caption, or a credential — and an unwritable log must not eat a request already sent.
4. **A refusal is audited too.** "Someone tried to POST an approval without `--yes`" is
   exactly the fact worth having later.
5. **Every `NOT_EXPOSED` area in `api_coverage.py` is still reachable through this
   command.** The reasons there say why no *first-class* command exists, not that the
   route is unreachable. That distinction is now load-bearing and is stated in that
   file's docstring and in `CLI_SPEC.md` §3/§11.

### 3.17 Driving Film Crew and music video from the terminal — `D6`

Two groups upstream owns (`cli/llx/commands/film_crew.py`, `music_video.py`) gain
commands, and **neither upstream file is edited**: each new module has no `COMMAND_NAME`
and no `app`, so the registry imports it and mounts nothing while the import registers
commands on the upstream app object — the `audio_ext.py` pattern, unchanged.

**Read-only introspection** (`film_crew_ext.py`, `music_video_ext.py`) — the half of the
ask that is "explore internal outputs", and the half that cannot regress anything:

| Command | Route | What it answers |
| `film-crew subjects <id>` | `GET /api/production/<id>/subjects` | who the Screenwriter extracted, each one's kind, LoRA and training state, and whether casting needs it (`cast_required`) |
| `film-crew shots <id>` | `GET /api/production/<id>` | every shot: scene/shot number, description, approval, storyboard and clip paths, regen count |
| `film-crew shot <id> <shot_id> [--image PATH]` | same, plus `GET …/storyboard/shot/<shot_id>/image` | one shot's detail, and its storyboard frame as a PNG |
| `film-crew templates` | `GET /api/production/script-templates` | the script templates the screenwriter can be pointed at |
| `music-video cuts <id>` | `GET /api/music-video/<id>` (`cut_plan`) | the Director's cut list with each cut's prompt — the thing `music-video status` reduced to a count |
| `music-video clips <id>` | same (`clips`) | per-clip status, index, path; which cuts are done |
| `music-video storyboard <id> <idx> --out F` | `GET /api/music-video/<id>/storyboard/<idx>` | one cut's storyboard still (the route serves a PNG, so this downloads) |

**The three render gates** (`render_gates.py`, D6) — the named half of "render it by CLI":

| Command | Route | Transition |
| `film-crew confirm-casting <id> --yes` | `POST /api/production/<id>/casting/confirm` | `casting` → `cinematography` |
| `film-crew approve-storyboard <id> --yes` | `POST /api/production/<id>/storyboard/approve` | `awaiting_approval` → `rendering` |
| `music-video approve <id> --yes` | `POST /api/music-video/<id>/approve` | `awaiting_approval` → `generating` |

Why these three and nothing else:

1. **They are the only routes that start a render.** Neither pipeline has a render route:
   the render *is* the consequence of the approval. `production_service.STAGE_TO_AGENT`
   marks `casting` and `awaiting_approval` as user-gated (`None`), and everything after
   them self-dispatches — the editor even resumes after a restart.
2. **They are creative and cost selections, not safety gates.** All three decide what to
   do with output the operator already owns: which storyboard frames become shots, and
   whether to spend the GPU on clips. That is the `cast approve` class that D2 already
   allows, not the inbound-guard / held-code / publish class it forbids.
3. **The exception is one file wide.** The D2 static scan matches `/approve`, `/reject`,
   `/decide`, `/apply`, `/release-held`, `/dispatch`; it catches `storyboard/approve` and
   `music-video/<id>/approve` but **not** `casting/confirm`. So `_ALLOWED` grows three
   literal-and-filename pairs for `render_gates.py` and nothing else, and
   `test_the_cast_sample_approval_is_the_only_approved_exception` still holds because
   `render_gates.py` contains no `/samples/approve`.
4. **Every gate is `--yes`-gated and refuses otherwise**, and a refusal sends nothing.
   `api request`'s tier-B guard already treats these three as decision-class, so
   `guaardvark api request POST …/storyboard/approve` refuses without `--yes` too: the named
   command and the escape hatch gate the same routes the same way.

## 4. Test framework renewal

The current suite is good but flat: 221 tests, one style per file, one e2e, no shared
fixtures beyond ad-hoc mocks, and coverage that tracks only implemented commands.

### 4.1 Problems to fix

| Problem | Fix |
|---|---|
| One failing test is host-specific (`run_command("python …")` needs `python` on PATH) | `shutil.which("python") or sys.executable` in the test; audit other absolute-command assumptions |
| Every file hand-rolls its own fake backend | one shared `FakeBackend` fixture (httpx `MockTransport`) + one `InProcessBackend` fixture |
| JSON contracts asserted ad hoc per file | golden snapshots per command, regenerable |
| No assertion that the *command surface* matches the *API surface* | a **spec-parity test** (§4.3) |
| Only one e2e, and only for `mcp client` | a smoke e2e per command group |
| Silent-failure regressions (the class fixed in the two output-registration commits) have no test | error-path tests: backend down, 4xx/5xx, timeout, non-TTY REPL, `--json` on error |
| No coverage signal for `cli/llx` | `pytest-cov` for the CLI package, floor raised over time |

### 4.2 New layout

```
cli/tests/
  conftest.py                 # fixtures shared by every tier
  unit/                       # moved as-is: catalog, completer, theme, local tools, …
  contract/                   # --json shape + golden snapshots per command
      golden/<group>__<cmd>.json
  e2e/                        # in-process backend, one smoke per group
  test_spec_parity.py         # NEW: command surface vs backend API surface
  test_error_paths.py         # NEW: the silent-failure class
  test_fork_cli_contract.py   # NEW: D1/D2 gates are enforced
```

Markers are registered in `cli/tests/conftest.py::pytest_configure` and applied by file
name. Not a `pytest.ini` or the repo `pyproject.toml`: this repo's .gitignore ignores
`pytest.ini` ("local-only dev tooling config"), so an ini file would never reach CI, and
`conftest.py` also lets the marking stay additive to upstream test files. Tiers:
`unit`, `contract`, `e2e`.

### 4.3 The spec-parity test — the piece that keeps this honest

The reason the gap list grew silently is that nothing connected the API surface to the
CLI. Make it fail loudly:

```python
# cli/tests/test_spec_parity.py
def test_every_backend_api_area_is_either_exposed_or_declared():
    areas = parse_api_modules("backend/api/*_api.py")            # 97 today
    exposed  = fork_registry.api_coverage.EXPOSED                # command -> area
    declared = fork_registry.api_coverage.NOT_EXPOSED            # area -> reason
    for area in prefixes:
        assert area in exposed or area in declared, (
            f"{area} is a backend API area with no CLI command and no declared reason. "
            f"Add a command, or add it to NOT_EXPOSED with the reason."
        )
    # and the reverse, so a removed API cannot leave a stale claim
    assert set(exposed) | set(declared) <= set(prefixes)
```

`NOT_EXPOSED` entries must carry a reason string (`"UI-editing surface (D2)"`,
`"internal"`, `"superseded by /api/x"`). This makes `docs/CLI_SPEC.md` §9/§10
machine-checked, so the docs cannot drift from the code.

### 4.4 Golden `--json` snapshots

- One file per leaf command; a `--update-golden` flag rewrites them deliberately.
- Normalise volatile fields (`id`, `timestamp`, `duration`, temp paths) before compare.
- The point is to catch *shape* drift (a renamed key is a breaking change for scripts),
  which the ad-hoc asserts today only catch for the commands someone remembered.

### 4.5 E2E harness

`InProcessBackend` boots the Flask app with a tmp `GUAARDVARK_ROOT`, sqlite (or the test
Postgres), and plugins mocked at the boundary — the pattern already proven by
`test_mcp_cli_e2e.py`. Then: one smoke per group (`cast list`, `upscale models`,
`video-editor projects list`, `llm provider`, `guard status`, …) asserting exit code and
JSON shape, never real GPU work.

**Where e2e runs in CI.** Phase 0 registers the tier and deselects it from the CLI job
(`-m "not e2e"`) but does **not** add a dedicated e2e job, because the suite's only e2e
test needs the `mcp` SDK *and* the backend package, neither of which the CLI-only job
installs — such a job would collect zero tests and fail (pytest exits 5), or pass having
exercised nothing. The tier's CI home belongs with the real harness above, in a job
that already has the backend stack (the `backend` job, or `cli-e2e` with
`backend/requirements-base.txt` installed).

### 4.6 New contract tests for the gates

| Test | Asserts |
|---|---|
| `test_fork_cli_contract.py::test_no_approval_commands` | no CLI command POSTs to any `approve`/`reject` route except the allowlisted `outreach approve` (extends today's `test_music_video_cli.py`) |
| `…::test_render_commands_go_through_the_gpu_gate` | every render-spending command passes `gpu_session`/job params (D1) |
| `…::test_paid_backends_require_confirmation` | `training start --backend runpod` refuses without `--yes` |
| `test_error_paths.py::*` | backend unreachable / 4xx / 5xx / timeout → non-zero exit, structured error under `--json`, never a bare traceback or a success body with a missing id |

## 5. Phases, deliverables, acceptance

| Phase | Content | Acceptance | Size |
|---|---|---|---|
| **0** | Extension point (`_fork/registry.py`), test layout, fixtures, spec-parity test, `python` PATH fix | `pytest cli/tests` green locally **and** in the `cli` CI job; spec-parity test present and passing against all backend API areas, each declared `EXPOSED` / `PLANNED` / `NOT_EXPOSED` | M |
| **1** | Read-only groups: `guard`, `improve`, `system-map`, `content`, `web`, `connections list/show`, `approvals list/show` | **Shipped.** Seven groups under `cli/llx/commands/_fork/`; `api_coverage` moved six areas from `PLANNED` to `EXPOSED` (43/7/47); the read-only tier is enforced by `cli/tests/test_fork_readonly_contract.py`, which scans the source for decision routes and runs every read-only command asserting no write was issued. Deviations: `connections` shipped in Phase 1 rather than Phase 4 (it was cheap and it is read-only in this form); `content page-delete` exists but requires `--yes`, and `connections oauth` / `system-map dispatch` were left out as Studio flows. | M |
| **2** | `cast`, `upscale`, `infographic`, `audio transcribe`, `images --engine` | generation commands queue jobs and print job ids; no approval commands; golden + e2e per group | L |
| **3** | `video-editor`, `training` | render/training go through the GPU gate; `training --backend runpod` requires `--yes`; contract tests for both | L |
| **4** | `llm` (cloud providers), `models image *`, `wordpress`, `film-crew`/`music-video` read-only extensions | **Shipped.** `llm` (provider, set, models, openai-model, mistral-model, test, cloud on\|off), `wordpress` (sites, site, site-test, pages, pull-sitemap\|list\|page\|bulk\|status, process-queue, process-run), and `audio models` / `audio model-download` added to the upstream audio group. Deviations: the `images --engine` flag was dropped — `settings set chat_image_model` and `images generate --model` already cover it, so a flag would have been a third way to set one thing; image/video weight downloads were dropped because `/api/model` has no download route at all; `models image *` was dropped for the same reason (it would only duplicate `images models`). | M |
| **5** | Docs: update `CLI_SPEC.md` §6/§7/§8/§9 from the code; refresh the README CLI section; regenerate coverage tables | `CLI_SPEC.md` regenerates clean from the appendix commands; spec-parity test proves no undocumented area | S |
| **6** | **Generic REST access — `api request` / `api routes` / `api audit`** (D5, safety tier B) | **Shipped** with D5. `api request` sends any `/api/...` route: reads free, writes and decision-class routes need `--yes`, every attempt appended to `<GUAARDVARK_DIR>/api-audit.jsonl`, `--dry-run` sends nothing. `api routes` reads `GET /api/routes`; `api audit` reads the log back. `test_fork_api_command.py` proves the gate (refuses `/…/approve` without `--yes`), the audit (writes an entry for ok / refused / dry-run), the path guard (absolute URL and non-`/api/` path rejected), and that the guard module is the only place naming a decision route. Named-command promotion is the follow-up, not part of this phase. | M |
| **7** | **Film Crew and music video from the terminal** (D6): read-only introspection, plus the three render gates | **Shipped** with D6. `film-crew subjects\|shots\|shot\|templates` and `music-video cuts\|clips\|storyboard` read what the pipelines actually produced; `film-crew confirm-casting`, `film-crew approve-storyboard` and `music-video approve` perform the three stage transitions that start a render, each behind `--yes`. Two new fork modules extend upstream-owned groups via the `audio_ext.py` pattern (no `COMMAND_NAME`, no `app`), so no upstream file is edited and `test_music_video_cli.py`'s per-invocation "never POST approve" assertions still pass unchanged. `render_gates.py` is the single documented `_ALLOWED` exception in the D2 static scan. | M |

Rough total: **L×3, M×3, S×1**. Phase 0 first is non-negotiable — without the extension
point every later phase edits upstream files, and without the parity test the docs drift
again immediately.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Upstream refactors `main.py`/`command_catalog.py` → our one-line hooks conflict on every sync | keep hooks to a single import + loop; document the mechanical fix in `CLI_SPEC.md` §11 |
| `approve`-style commands erode the Studio's human gate | D2 for the named commands; D5 `--yes` + decision-class detection + audit log for the generic route; `test_fork_api_command.py` proves a decision route is refused without `--yes` and audited when sent |
| The generic `api` command becomes the only interface anyone uses, and argument validation rots | `api.py`'s own help and `CLI_SPEC.md` §11 say to promote a route to a named command once it is used repeatedly; the audit log makes "what do people actually call" answerable |
| `approve`-style commands erode the Studio's human gate | D2 + `test_fork_cli_contract.py` |
| Paid GPU (RunPod) triggered from a script | D1 + `--yes` + cost echo + contract test |
| Golden snapshots become noise | normalise volatile fields; `--update-golden` is explicit and reviewed |
| 90+ new subcommands overwhelm `--help` | group them (as above), keep `guaardvark --help` top-level at ~60 groups max, and let the completer carry discovery |
| Portability gate trips on new doc/test files | run `scripts/check_portable.sh` in the pre-commit path; Phase 0 adds it to the CLI job |

## 7. Out of scope

Interactive editing in the terminal (bin drag-reorder, caption code editor, collapsible
alerts, model-management *UI*): these are browser surfaces. The CLI exposes the
*operations*, never a TUI.

---

## Appendix — route inventory used above

`backend/api/*_api.py`, route counts at `95393444`: `cast_library` 112, `video_editor` 90,
`upscaling` 64, `connections` 57, `self_improvement` 41, `inbound_guard` 31, `system_map` 25,
`llm_provider` 13, `training_datasets` 12, `content_management` 9, `wordpress` 12,
`web_search` 4, `infographic` 6.

Portability: this file must contain no literal absolute home path for macOS, Linux or
Windows — run `scripts/check_portable.sh` before committing.
