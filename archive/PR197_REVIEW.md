# PR #197 Review — M4: ComfyUI Z-Image image generation (opt-in route)

- **Branch:** `pr/m4-comfyui-image` → `main`
- **Author:** kwiksher (Yamamoto)
- **State:** open, draft
- **Size (net vs main):** 16 files, ~+1055 / −38
- **Reviewed against:** net diff of the PR head vs `main` (NOT the local `gitbutlet/workspace`
  branch, which still carries the M5 OpenAI hunk that the PR already dropped in commit 2).
- **Compile check:** all 9 changed Python files `py_compile` clean.
- **Focus of this review:** degradation + injected changes unrelated to the ComfyUI route.

---

## Verdict

The PR is well-scoped for its stated goal (route Z-Image through a reachable ComfyUI on
opt-in), and the OpenAI `character_generator_service` hunk is **not present** in the PR
(correctly dropped in commit 2). All 9 changed Python files compile.

However, there are **two un-gated behavior changes** and **one tangential macOS change** that
do **not** require `GUAARDVARK_ZIMAGE_USE_COMFYUI=1`. These are the real "degrade / inject
unrelated change" risks and should be resolved or explicitly called out before merge.

---

## Opt-in gated (safe, default-off)

| Change | Gate | Note |
|---|---|---|
| `batch_image_generator` Z-Image route | `GUAARDVARK_ZIMAGE_USE_COMFYUI` | fires only when flag is set **and** model is zimage |
| `character_still_pipeline` Z-Image→comfy | flag | `engine = "comfy" if _zimage_via_comfyui_enabled() else "offline"` |
| `character_generation_tasks._resolve_cast_still_route` | flag | zimage-family branch is gated on the flag |
| `lora_trainer_tasks` symlink + `ensure_lora_in_comfyui` | flag | no-op when flag unset; best-effort, never raises |
| `comfyui_image_generator` Z-Image graph, LoRA helpers, engine cache | — | new code, only invoked via the paths above |

These are safe: nothing changes for a user who does not set the env var.

---

## Un-gated behavior changes (the actual concerns)

### 1. `character_generation_tasks.py` — `free_comfyui` flips True→False on the comfy path
`use_comfy = route.get("engine") != "offline"`, then `free_comfyui=not use_comfy`.

Confirmed the registry (`media_model_registry.py`) only ever emits `"offline"` or `"comfy"`,
so **which** pipeline runs is identical to before — but the net effect is that **ComfyUI's
resident models are no longer evicted before any comfy job**. This is an un-gated change to the
*existing* FLUX-dev / SDXL comfy paths, not just the new Z-Image path: VRAM stays resident
across generations.

The in-code rationale is sound (avoid a full reload + apparent "restart" on every generation),
but it changes an existing path without a gate. **Recommend:** gate it on the opt-in flag, or
at minimum call it out explicitly in the PR description.

### 2. The `comfyui` generic selector is a separate, always-on feature that bypasses the opt-in
- `offline_image_generator` adds `"comfyui": "comfy:comfyui"` to the catalog and to
  `comfy_only_models`.
- `slashCommandHandlers.js` adds `comfyui` to the selectable model list **unconditionally**.
- `stills_pipeline._comfyui_backend_choice()` picks `zimage` first from the live engine list
  **with no flag check**.

So a user who types `/imagemodel comfyui` gets Z-Image-via-ComfyUI **even without
`GUAARDVARK_ZIMAGE_USE_COMFYUI=1`**. This contradicts the PR's "off by default — nothing changes
for users who do not set it": the claim holds for *auto-routing*, but the new selectable model
routes Z-Image through Comfy regardless of the flag. **Recommend:** gate it on the flag, or
explicitly document it as an un-gated convenience and reword the "off by default" framing.

### 3. `comfyui_image_generator._build_workflow` drops a guard + adds a LoRA-family override
- The old `if family == "zimage": raise RuntimeError(...)` refusal was **removed**.
- The `elif family == "zimage"` branch now **overrides** `effective_model = "zimage"` whenever a
  Z-Image-family LoRA is paired with any non-Z-Image model.

This is un-gated and changes routing for Z-Image LoRAs sent to other engines. Likely intended,
but it removes a safety net that is **not** flag-gated. **Recommend:** add a test for the
override path, and decide deliberately whether the refusal should be gone in all cases.

---

## Tangential (not the ComfyUI Z-Image route)

### `system_load_gate.py` — `GUAARDVARK_SWAP_HARD_MAX_GB`  (macOS swap ergonomics)
This is a real change in the PR delta:

- **Default behavior is unchanged** — `SWAP_HARD_MAX_GB = 8.0` is still used when the env var is
  unset, so it does **not degrade** anything.
- It is **not in `origin/main`** (main still hard-codes the `> 8 GB` block at
  `system_load_gate.py:213-214`); it is a new, opt-in-via-env override for macOS's "sticky"
  swap.
- It is **unrelated to the image route** — it just rides along in this PR.

Note for the record: M1 (#154) is the macOS/Apple-Silicon PR, but it did **not** touch
`system_load_gate.py` (its files were `voice_api.py`, `comfyui_video_generator.py`,
`gpu_resource_coordinator.py`, audio plugins, `start.sh`/`stop.sh`). So "move it to M1" is not
an option — M1 is merged and never contained it.

**Recommendation (pick one):**
1. **Leave it in** — harmless, default identical; it is part of the "make this machine's
   ComfyUI/Z-Image work on Apple Silicon" story. Defensible.
2. **Split it into its own commit/PR** — cleaner history; it is a macOS swap-ergonomics tweak,
   not the image route.
3. **Drop it** — if you want this PR to be *strictly* the Z-Image-via-ComfyUI route.

---

## Minor

- `_is_zimage_model` uses substring match (`"zimage" in _k`) — broad but the batch side is
  flag-gated, so the blast radius is limited.
- The engine cache caches a *down* ComfyUI as `[]` for the TTL (`_ENGINE_CACHE_TTL_SECONDS`,
  default 5s) — intended; a dead ComfyUI reads as "no engines" for ~5s.
- Test coverage is good (route, family-step resolution, LoRA link, engine cache). They are CPU
  smoke tests and do not exercise a real ComfyUI / Z-Image graph.

---

## Suggested before merge

1. Gate (or document) the `free_comfyui` flip on the comfy path (item 1).
2. Gate the `comfyui` selector on the opt-in flag, or explicitly label it an un-gated
   convenience and reword the "off by default" framing (item 2).
3. Decide deliberately whether the Z-Image refusal removal stays un-gated; add an override test
   (item 3).
4. For `system_load_gate.py`, keep it but call it out in the PR description as a second sub-change
   (macOS swap ergonomics), or split it into its own commit for clean history.

## Files in the net diff (16)

- `backend/services/batch_image_generator.py`
- `backend/services/character_still_pipeline.py`
- `backend/services/comfyui_image_generator.py`
- `backend/services/offline_image_generator.py`
- `backend/services/stills_defaults.py`
- `backend/services/stills_pipeline.py`
- `backend/services/system_load_gate.py`
- `backend/tasks/character_generation_tasks.py`
- `backend/tasks/lora_trainer_tasks.py`
- `backend/tests/services/test_cast_still_route_zimage.py` (new)
- `backend/tests/services/test_character_still_pipeline.py`
- `backend/tests/services/test_comfyui_image_lora_branch.py`
- `backend/tests/services/test_comfyui_lora_link.py` (new)
- `backend/tests/services/test_stills_comfy_model_tag.py` (new)
- `backend/tests/services/test_stills_defaults.py`
- `frontend/src/hooks/slashCommandHandlers.js`
