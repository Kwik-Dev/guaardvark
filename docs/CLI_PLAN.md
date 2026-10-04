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

`film-crew`/`music-video` render and approval routes stay out per D2; the existing
contract test must keep passing unchanged.

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
| **1** | Read-only groups: `guard`, `improve`, `system-map`, `content`, `web`, `connections list/show`, `approvals list/show` | each group has `--json`, a golden snapshot, and an e2e smoke | M |
| **2** | `cast`, `upscale`, `infographic`, `audio transcribe`, `images --engine` | generation commands queue jobs and print job ids; no approval commands; golden + e2e per group | L |
| **3** | `video-editor`, `training` | render/training go through the GPU gate; `training --backend runpod` requires `--yes`; contract tests for both | L |
| **4** | `llm` (cloud providers), `models image *`, `wordpress`, `film-crew`/`music-video` read-only extensions | `llm cloud on\|off` + `llm set` work against the real backend; D1/D2 contracts still hold | M |
| **5** | Docs: update `CLI_SPEC.md` §6/§7/§8/§9 from the code; refresh the README CLI section; regenerate coverage tables | `CLI_SPEC.md` regenerates clean from the appendix commands; spec-parity test proves no undocumented area | S |

Rough total: **L×3, M×3, S×1**. Phase 0 first is non-negotiable — without the extension
point every later phase edits upstream files, and without the parity test the docs drift
again immediately.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Upstream refactors `main.py`/`command_catalog.py` → our one-line hooks conflict on every sync | keep hooks to a single import + loop; document the mechanical fix in `CLI_SPEC.md` §11 |
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
