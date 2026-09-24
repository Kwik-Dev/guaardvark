# M4 PR notes — ComfyUI Z-Image image generation (opt-in route)

Working notes for the **M4** PR (currently `pr/m4-comfyui-image`, draft PR #197 —
`M4: ComfyUI Z-Image image generation (opt-in route)`). Kept for when we open / refresh
the PR. Verified against `upstream/main` = `11af2238` (which includes **M3 / PR #183**,
`ca528a2e`).

Workspace equivalents of M4: `feat/zimage-comfyui` (`im`) and `feat/zimage-comfyui-mps` (`zi`).
`pr/m4-comfyui-image` is the squashed single-commit form (`4cc25402`).

---

## 1. Does M4 change the default image model? No.

**Upstream's default stills model is Z-Image Turbo. FLUX is the explicit max-quality
choice — it is *not* the default.**

`backend/services/media_model_registry.py`:

- `DEFAULT_STILLS_MODEL = ZIMAGE_TURBO` (line 33)
- Z-Image Turbo profile: `recommended: True`, `tier: "default"`, description
  *"Default stills + character LoRA base… best daily-driver quality on 16GB GPUs."* (lines 52-58)
- FLUX.1 Dev profile: `recommended: False`, `tier: "max_quality"`, note
  *"Not the product default — use for max-quality identity."* (lines 82-95)
- Header comment (line 13): *"Z-Image is the product default; FLUX is max-quality;
  SDXL is legacy only."*

`DEFAULT_MAX_QUALITY_MODEL = FLUX_DEV` (line 35) — i.e. FLUX is the *max-quality* default,
a different axis from the *product* default.

M4 does **not** touch `DEFAULT_STILLS_MODEL`. Its Z-Image→ComfyUI route is gated by
`GUAARDVARK_ZIMAGE_USE_COMFYUI=1` (**off by default**), so the default behaviour is unchanged.

> The instruction "if flux is the upstream default, keep flux and make Z-Image a user choice"
> does **not** apply: flux is not the default. No change is required on that basis.

---

## 2. How upstream handles ComfyUI (for comparison)

There is **no global "use ComfyUI?" switch**. The engine is chosen **per model** by the
registry profile, and ComfyUI just has to be reachable.

### 2.1 Engine is declared per profile (`media_model_registry.py`)

| profile | `inference_engine` | notes |
|---|---|---|
| `zimage-turbo` (product default) | `"offline"` | Diffusers pipeline |
| `flux-dev` (max quality) | `"comfy"` | ComfyUI |
| `sd-xl` (legacy) | `"comfy"` | ComfyUI |
| krea2 turbo / raw | `"offline"` | Diffusers |

### 2.2 Routing is automatic, keyed on the model — no env flag

- `backend/services/stills_pipeline.py` — `if "flux" in mid: return _generate_comfy_flux(...)`,
  otherwise the offline pipeline.
- `backend/services/batch_image_generator.py::_should_use_comfy_stills` — true when the model
  key contains `flux`, or when it is SDXL **with adapter LoRAs**.
- `backend/services/character_still_pipeline.py` — `engine = route["inference_engine"]`, but it
  **forces Z-Image offline**: `if family == "zimage": engine = "offline"`.
- `backend/tasks/character_generation_tasks.py` — same registry `inference_engine`.

Net: **pick FLUX → ComfyUI; pick Z-Image → offline Diffusers.**

### 2.3 What actually is opt-in upstream

- **The ComfyUI plugin ships disabled** — `plugins/comfyui/plugin.json`:
  `default_enabled: false`, `default_auto_start: false`, `fallback_enabled: true`.
  ComfyUI only runs if the operator enables/starts it.
- Reachability is config, not an on/off toggle — `backend/config.py`:
  `GUAARDVARK_COMFYUI_URL` (default `http://127.0.0.1:8188`), `GUAARDVARK_COMFYUI_DIR`.
- Model choice is the user opt-in (FLUX / SDXL-legacy are explicit, non-default selections).

### 2.4 Z-Image via ComfyUI does not exist upstream

- Upstream has **no** Z-Image ComfyUI graph, and **no** `GUAARDVARK_ZIMAGE_USE_COMFYUI`
  variable. The only occurrence in `upstream/main` is a comment in
  `backend/api/voice_api.py:38` referencing it as a *pattern*
  ("Mirrors the `GUAARDVARK_ZIMAGE_USE_COMFYUI=1` opt-in pattern") for the whisper-server flag.

---

## 3. What M4 adds on top of upstream

- `GUAARDVARK_ZIMAGE_USE_COMFYUI=1` (**off by default**) → routes plain Z-Image prompts to a
  reachable ComfyUI instead of the offline pipeline
  (`batch_image_generator._zimage_via_comfyui_enabled`, `character_still_pipeline`,
  `stills_pipeline`, `character_generator_service`).
- A modern Z-Image ComfyUI workflow (UNETLoader + Lumina2 CLIP + AuraFlow sampling +
  `ConditioningZeroOut`), plus `ensure_lora_in_comfyui()` / `_comfyui_loras_dir()` so
  `LoraLoaderModelOnly` can resolve a trained LoRA by basename.
- A **new generic `comfyui` catalog key** whose availability comes from the live ComfyUI's
  `/object_info` (`_comfyui_assets_present`, `comfyui_installed_engines()`), with a
  `stills_defaults` `comfyui` family and an `/imagemodel comfyui` chat option.
- `system_load_gate` swap threshold made configurable; freshly trained LoRA symlinked into
  ComfyUI's `models/loras`.

Because the route and the generic key are both **new**, M4 does **not** override an upstream
default. It is additive and off by default.

---

## 4. Review findings / decision points to settle before opening the PR

1. **Generic `comfyui` engine preference.** `stills_pipeline._comfyui_backend_choice()` prefers
   `zimage → flux-dev → flux-schnell`, but only when the user explicitly picks the new
   `comfyui` model (not the default). Decision: if the intent is to mirror upstream's
   "FLUX is the Comfy engine, Z-Image is the choice", flip this order (or gate `zimage` behind
   the same `GUAARDVARK_ZIMAGE_USE_COMFYUI` flag inside this chooser).
2. **`stills_defaults` `comfyui` family `prompt_style`.** M4 sets `"prompt_style": "natural"`,
   which biases prompt rewriting toward Z-Image. But the generic backend can also pick FLUX
   (`tags`). Since the engine isn't known at default-resolution time, this is ambiguous —
   revisit (the workspace branch currently carries the bare entry, no `prompt_style`).
3. **Model label passed to `_generate_comfy_flux`.** M4 passes `model=f"comfyui ({engine})"`
   (e.g. `"comfyui (flux-dev)"`) rather than a catalog tag. Anything that matches on exact
   model tags / LoRA base resolution could miss it. Verify the FLUX + LoRA + `comfyui`
   selection path. (Upstream's own call passes `model=model`; M4's change to
   `model="flux"`→`model=model` already matches upstream.)
4. **Opt-in wording.** The PR body says "opt-in, off by default" — keep the PR description
   explicit that the *product default model is unchanged* and that ComfyUI must already be
   running (plugin ships `default_enabled: false`).

## 5. Rebase / merge status

- `pr/m4-comfyui-image` is based on `e3f435ac`; upstream is now `11af2238`.
- `git merge-tree --write-tree upstream/main pr/m4-comfyui-image` → **clean, no conflicts**,
  despite M3 having touched `offline_image_generator.py` / `stills_defaults.py`.
- Safe to rebase onto `11af2238` when refreshing the PR.

## 6. Suggested PR checklist (later)

- [ ] Rebase `pr/m4-comfyui-image` onto current `upstream/main`.
- [ ] Decide §4.1 (generic `comfyui` engine order) and §4.2 (`prompt_style`).
- [ ] Verify §4.3 with a FLUX + LoRA + `comfyui` run.
- [ ] Run `pytest backend/tests/services/test_comfyui_image_lora_branch.py backend/tests/services/test_character_still_pipeline.py`.
- [ ] Confirm `GUAARDVARK_ZIMAGE_USE_COMFYUI` unset = exactly upstream behaviour.
- [ ] Update the PR body with the "default model unchanged / opt-in" framing from §1–§2.
