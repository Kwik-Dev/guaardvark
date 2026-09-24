Pushed `ec8ec815` (on top of `897b5810`) — the scope review, all four items. Commenting rather than rewriting the description.

**1. `free_comfyui` flip — scoped to the opt-in route.** It is now `free_comfyui=not (use_comfy and route.get("family") == "zimage")`. Z-Image-through-ComfyUI still keeps its resident models (freeing would force a full Z-Image reload per generation); the existing FLUX/SDXL Comfy paths keep the historic eviction behaviour this PR was never meant to change.

**2. The generic `comfyui` selector no longer bypasses the opt-in.** `_comfyui_backend_choice()` skips the `zimage` engine unless `GUAARDVARK_ZIMAGE_USE_COMFYUI` is set, and falls back to a FLUX engine when the live server has one. `_comfyui_assets_present()` applies the same filter, so the catalog does not advertise a backend it cannot run, and the "no engine" error names the flag. `/imagemodel comfyui` still selects the generic backend — it just can no longer silently turn Z-Image on.

**3. The Z-Image LoRA override is deliberate, and tested.** The old `RuntimeError` refusal stays gone so a trained identity is applied rather than dropped; the decision is documented in the guard and pinned by `test_zimage_loras_override_a_non_zimage_model` (Z-Image LoRA + `flux-schnell` → Z-Image graph, `LoraModelOnly` feeding AuraFlow).

**4. `system_load_gate.py` — kept and called out.** `GUAARDVARK_SWAP_HARD_MAX_GB` is default-identical (`SWAP_HARD_MAX_GB = 8.0` when unset) and M1 (#154) never carried it, so it stays; it is now called out as a second sub-change (macOS swap ergonomics) rather than smuggled in. Happy to split it into its own commit/PR if you would rather.

Tests: the stills/comfy/cast set — `102 passed`. Full `backend/tests/services` — `1109 passed`, with the same 10 pre-existing failures as `upstream/main` (`h3_prompt_compiler`, `gpu_admission_reclaim`, `production_service`, `wan_clip_device`, `test_lora_training_settings::test_normalize_defaults`). New tests: three for the selector gating, one for the LoRA override. Branch is on current `upstream/main` (`5777713c`).
