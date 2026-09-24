# Feature Requests & Future Work

Working list of **proposed capabilities** — things the product could do but does not yet.
Bugs, known issues, and limitations (including the "intentional limitation" notes) live in
`KNOWN_BUGS.md`. Add a new `##` entry per request with status, area, the request, current
state, proposed work, and any related links.

---

## [FEATURE] Save a chat-generated image straight into the Cast Library

- **Status:** Open (feature request) — not implemented. No code changes applied.
- **Area:** `backend/tools/image_tools.py` (tool registry), `backend/api/cast_library_api.py`, chat image rendering in the frontend.

### Request
When the assistant generates an image in chat, the user should be able to save it into the
Cast Library as a reference image for a character — without manually downloading the file and
re-uploading it through the Cast Studio UI.

### Current state (what exists today)
- Chat-generated images are written to `data/outputs/generated_images/` and served at
  `/api/outputs/generated_images/<file>.png`.
- The Cast Library already has the write endpoints needed:
   - `POST /api/cast-library/subjects` — create a subject; accepts `ref_image_paths` (on-disk paths).
   - `POST /api/cast-library/subjects/<id>/upload-refs` — multipart upload; copies the file into
     `data/cast_refs/<id>/`, appends to `ref_image_paths`, and auto-captions the new image.
- **No chat tool or UI action wires these together.** `generate_image` only *uses* cast subjects
  (`subject_ids`) to generate images *of* a character; it does not add images *into* the library.
- The only current path is manual: download the image, open `/cast`, create/select a character,
  drag-and-drop the file into the reference-images area.

### Proposed work / steps
1. **UI action on chat images** — add a "Save to Cast" button/action on generated images in the
   chat view. On click, prompt for (or reuse) a Cast subject, then POST the image file to
   `POST /api/cast-library/subjects/<id>/upload-refs` (reuse the existing `DragDropImageUpload`
   flow / `productionService` helpers).
2. **Or a `save_to_cast` tool** — add a `BaseTool` (category e.g. `data`, `is_dangerous=False`,
   `requires_approval=True` since it writes to the DB) that takes an image path/URL + subject id
   (or name) and calls the same upload logic, so the assistant can do it on request.
3. Register the tool in `backend/tools/tool_registry_init.py` so AgentBrain/agents can call it.
4. Verify an end-to-end prompt: *"Save this generated image to the Cast Library as Captain."*

### Related
- Cast Library CRUD + upload endpoints already exist in `backend/api/cast_library_api.py`.
- See the companion feature request below for splitting a multi-view character sheet before saving.

---

## [FEATURE] Character sheet-splitting utility (multi-view sheet → individual view images)

- **Status:** Open (feature request) — not implemented. No code changes applied.
- **Area:** `backend/services/character_captioner.py`, `backend/services/character_generator_service.py`, Cast Library upload flow.

### Request
A multi-view **character sheet** (one image laid out with front / side / back panels) should be
split into individual single-view images so each can be used as a clean Cast Library reference
for LoRA training.

### Why it's needed (root cause)
The LoRA training pipeline is built around **individual, single-view images**:
- `character_captioner.py` asks the VLM to lead with **exactly one** framing tag
  (`close-up` / `head and shoulders` / `upper body` / `three-quarter view` / `full body` /
  `wide shot`). A three-view sheet has no single framing, so the caption is wrong/misleading.
- Training on a composite sheet teaches the model the **"three-panel sheet" layout** as part of
  the character, so generations may come out as sheets instead of a single figure.
- The pre-train gate measures pose/framing coverage per image (`detect_framing` /
  `FULL_BODY_FRAMINGS`); a sheet counts as one framing and skews the coverage stats.

The Casting Director's own sheet generation (`character_generator_service.generate_character_sheet`)
already produces **individual stills** — one image per angle slot (`front`, `profile left`,
`profile right`, `three-quarter`, `full body`, …) — which is the intended training shape. There is
currently **no utility** to split an externally-provided composite sheet.

### Proposed work / steps
1. Add a sheet-splitting utility (e.g. `backend/services/character_sheet_splitter.py`) that takes
   a multi-view character sheet image and crops it into individual view images (front / side /
   back), using layout heuristics (panel grid detection) or a vision model to locate panels.
2. Expose it as an endpoint (e.g. `POST /api/cast-library/split-sheet`) and/or a chat tool so a
   sheet can be split and the resulting views uploaded to a Cast subject in one step.
3. Wire the split views through the existing `upload-refs` flow so each gets a proper
   single-framing caption.
4. Verify: split a 3-panel sheet → 3 individual images, each captioned with a distinct framing,
   and the pre-train gate sees full-body coverage.

### Related
- See the companion feature request above for saving a chat-generated image into the Cast Library.

---

## [FEATURE] RunPod LoRA trainer via Flash (code-first serverless) instead of a Docker image

- **Status:** Open (feature request) — not implemented. No code changes applied.
- **Area:** `plugins/runpod_lora_trainer/` (pod Dockerfile + handler + runner), `backend/tasks/lora_trainer_tasks.py`.

### Request
Offer a **Flash**-based path for the RunPod LoRA trainer — write the training as a Python
`@Endpoint` function and deploy with `flash deploy`, instead of building/pushing a ~10GB
Docker image and wiring a serverless endpoint to it.

### Why it's lighter (current pain)
The Docker-image path has been fragile in practice:
- **Image build/maintenance** — a 10GB image (PyTorch + CUDA + trainer scripts) must be
  rebuilt and re-pushed on every code change; the `latest` tag and multi-arch manifest
  handling bit us (stale arm64 manifest, wrong-arch image pushed).
- **GPU/CUDA mismatch** — the image's PyTorch 2.5.1/CUDA 12.4 only supports Ada/Ampere;
  a Blackwell GPU worker failed with `no kernel image is available for execution on the
  device`, requiring GPU-pool pinning.
- **Disk sizing** — the ~20GB Z-Image model needs a 50GB container disk (was 10GB → OOM).
- **Deploy loop** — every fix (path bug, model-ID mapping, disk, GPU) required a rebuild +
  push + endpoint update + re-trigger.

Flash removes the Dockerfile entirely: you write the training function locally, `flash dev`
to iterate (hot-reload + live worker logs), then `flash deploy`. Flash provisions the
endpoint and installs `dependencies=[]` at runtime.

### Caveats / why it's not a drop-in
- **Long-running job vs request/response** — Flash is designed for inference; LoRA training
  is a 20–50 min batch job. It works via queue endpoints (`run` + `job.wait()`) but is
  against Flash's grain.
- **Heavy deps at runtime** — `torch`, `diffusers`, Z-Image still install on the worker
  (cold start), so the first call is slow; warm workers help for repeated runs.
- **10MB payload limit** — fine here because training data is staged to S3 (already done).
- **Same GPU/CUDA constraints** — you still pick a GPU group; Blackwell still needs a
  CUDA 12.8+ runtime.

### Proposed work / steps
1. Add a Flash `@Endpoint` function (e.g. `plugins/runpod_lora_trainer/flash_trainer.py`)
   that wraps the existing `run_zimage_trainer` load→train protocol, taking the S3 dataset
   manifest + hyperparams as input and returning the LoRA URL.
2. Wire `RemoteLoraTrainer` (or a new `FlashLoraTrainer`) to call the Flash endpoint instead
   of the Docker serverless endpoint, reusing the existing S3 staging + artifact download.
3. Document the `flash dev` / `flash deploy` loop and the GPU-group choice (Ampere/Ada for
   the current CUDA 12.4 stack).
4. Keep the Docker path as a fallback; make the backend selectable (e.g. `runpod` vs `flash`).

### Related
- Existing Docker-based path: `plugins/runpod_lora_trainer/` (Dockerfile, handler.py, runner.py).
- Flash docs: https://docs.runpod.io/flash/overview · https://github.com/runpod/flash

---

## [FUTURE] Agent-driven client registration from a website (fetch → extract → create → logo)

- **Status:** Future request — not implemented. No code changes applied.
- **Area:** `backend/tools/` (tool registry `tool_registry_init.py`), `backend/api/clients_api.py`, `backend/models.py` (`Client`).

### Request
Enable agents / Agent Tools to register a client by fetching the client's website and
auto-populating the client record (info + pictures), instead of manual form entry or a
bulk CSV/MD import script.

### Current state (what exists today)
- **Web fetch works:** `analyze_website`, `fetch_url`, `web_search` (`backend/tools/web_tools.py`) and the browser tools can pull a site's title, meta description, content preview, SEO metrics, URL structure, and content-type hints — enough to infer `industry`, `keywords`, `content_goals`, `brand_voice_examples`, `location`, `notes`.
- **No `create_client` tool exists** in the registry, so an agent cannot write a client record through the tool system.
- **No image scraping:** `analyze_website` extracts text/SEO only. There is no tool that downloads a site's logo or images. The image tools (`generate_image`, `edit_image`) generate/edit images; they do not fetch from a URL.
- **`system_command` is whitelisted** to read-only filesystem commands (`ls`, `grep`, `cat`, `find`…) — no `curl`, so an agent cannot POST to the API as a workaround.
- **FileGen (`generate_file`)** creates brand-new output files under `data/outputs/files`; it does not touch the DB and cannot register clients.

### Proposed work / steps
1. Add a **`create_client` tool** that wraps the existing `POST /api/clients/` logic (reuse `backend/api/clients_api.py` / `Client` model + `serialize_client`). Category e.g. `data`/`crm`, `is_dangerous=False`, `requires_approval=True` (writes to DB). Accept the same fields as the form (name required; email/phone/location/notes + RAG arrays: industry, target_audience, unique_selling_points, competitor_urls, keywords, content_goals, geographic_coverage; strings: brand_voice_examples, regulatory_constraints).
2. Add a **`fetch_website_images` tool** (or extend `analyze_website`) to pull `og:image`, favicon/logo, and `<img>` srcs from a page, download the logo, and set `logo_path` via the existing logo-upload endpoint (`POST /api/clients/<id>/logo`).
3. Register both tools in `backend/tools/tool_registry_init.py` so AgentBrain/agents can call them.
4. Verify an end-to-end agent prompt: *"Register Acme Corp — fetch acme.com, extract industry/keywords/voice, download their logo, and create the client."*

### Related
- Bulk (non-agent) registration already exists via `scripts/import_clients.py` (CSV/MD → `POST /api/clients/`).

---

## [FEATURE] Multilingual voice support — Japanese and 22+ other languages for Chatterbox TTS

- **Status:** Open (feature request) — not implemented. English is the only officially supported output today.
- **Area:** `plugins/audio_foundry/backends/voice_gen_chatterbox.py`, the voice dispatcher, and `frontend/src/pages/AudioFoundryPage.jsx`. Documented in `docs/AUDIO_EDITOR.md` §1.

### Requested behavior
Audio Studio's **Voice** tab should speak non-English text — e.g. **Japanese** — in both the fixed-voice and voice-clone paths, instead of falling back to (or garbling) English.

### Why it's English-only today (root cause)
The backend loads the **base (English) model** and never passes a `language_id`:
- `voice_gen_chatterbox.py` does `from chatterbox.tts import ChatterboxTTS` and `ChatterboxTTS.from_pretrained(...)`, then calls `generate(text, audio_prompt_path=...)` with **no `language_id`**.
- The **Kokoro** fallback uses English-only voice IDs from the `/voices` catalog.
- The frontend has no language picker and always sends plain `text`.

### The model already supports it — it's just not wired
Chatterbox ships a **multilingual** variant, `ChatterboxMultilingualTTS` (0.5B, **23+ languages** incl. Japanese, Chinese, Korean, and many European languages), plus a **Single Language Pack** (dedicated finetunes for Chinese, LatAm/Spain Spanish, Brazilian/Portugal Portuguese, Hindi). See the ResembleAI/chatterbox model card. It is not used here.

### Proposed implementation (not started)
1. **Backend (`voice_gen_chatterbox.py`)** — load `ChatterboxMultilingualTTS.from_pretrained(t3_model="v3")` (or a Single Language Pack) and pass a `language_id` derived from the request/script.
2. **Dispatcher / API** — accept an optional `language_id` on `/generate/voice`; default it from the text or a per-request override.
3. **Frontend (`AudioFoundryPage.jsx`)** — add a language selector to the Voice tab; send `language_id`; keep English as the default.
4. **Reference-clip cloning** — when cloning, match the reference clip's language to the requested `language_id` (the model card notes clips inherit the reference language/accent).
5. **Fallback** — keep the English `ChatterboxTTS` (or Kokoro) as the default so existing behavior is unchanged unless a language is requested.

### Notes / caveats
- The multilingual checkpoint is the same 0.5B size, but the app currently pins `ResembleAI/chatterbox`; switching models changes the default voice behavior — verify English output parity before rollout.
- Model download: the multilingual weights would be a new HF download on first use.
- Tracked alongside `docs/AUDIO_EDITOR.md` §1 "Language support — English vs Japanese".

---

## Session handoff notes (working scratch)

Short-lived notes another session left mid-task. Not feature requests or bugs, but kept here
rather than discarded so the context survives.

### Z-Image + ComfyUI LoRA chain — status summary

1. **No LoRA node in the workflow.** The ComfyUI Z-Image graph (`comfyui_image_generator.py:297-348`) is a plain `UNETLoader → CLIPTextEncode → KSampler → VAEDecode` chain. Unlike the SDXL and FLUX branches, it has no `LoraLoader` / `LoraLoaderModelOnly` node, so LoRAs are never applied.
2. **An explicit guard rejects Z-Image LoRAs.** `comfyui_image_generator.py:210-216` detects `family == "zimage"` and logs "Z-Image LoRAs cannot use Comfy SDXL/FLUX graph" — it drops the LoRA rather than routing it anywhere.
3. **Key-format mismatch.** The trainer (`run_zimage_trainer.py`) saves via `save_lora_weights`, producing diffusers-format keys prefixed `transformer.`. ComfyUI's `LoraLoaderModelOnly` expects raw ComfyUI keys (no `transformer.` prefix). A diffusers-format LoRA would load to zero effect — the same class of bug the SDXL path already documents and works around via `convert_state_dict_to_kohya`.
4. **Custom-node compatibility is unproven.** Z-Image in ComfyUI loads via a raw `UNETLoader` (`z_image_turbo_bf16.safetensors`) + Qwen CLIP (`qwen_image`), not a standard diffusers checkpoint. Whether `LoraLoaderModelOnly` maps the LoRA's target modules (`to_q`, `to_k`, `to_v`, `to_out.0`) onto that custom build's key names is unknown.
5. **Architectural split.** The registry marks Z-Image as `inference_engine: "offline"` — ComfyUI is only an opt-in override (`GUAARDVARK_ZIMAGE_USE_COMFYUI=1`). The intended LoRA path is the offline Diffusers `ZImagePipeline` (`load_lora_weights`), not ComfyUI.

**Net:** Z-Image LoRA training works (now on MPS), but the ComfyUI side can't consume the result — it needs a LoRA chain added, the guard relaxed, and a key-format conversion, plus validation against the custom node. The offline Diffusers path is the only one that currently applies Z-Image LoRAs.

**To get a Z-Image LoRA rendering today (no code):**
1. Train a LoRA first (via the Z-Image trainer ported to MPS), which drops a `.safetensors` into `data/training/loras/`, then copy it into `~/ComfyUI-Shared/models/loras/`.
2. Or download any public Z-Image-compatible LoRA from CivitAI/HuggingFace and place it in the ComfyUI `loras/` directory.

### Cast sequence (why "no LoRA paths" / "recommendation not available" appear)

The sequence is: cast the subject (user-gated) → train the LoRA → then generate. The "no LoRA paths" error means you're trying to generate before the subject has a trained LoRA. The "recommendation not available" means the casting step hasn't been completed/confirmed yet.

The fix path:
1. Cast the subject via `cast_subject` with `action: "train_from_generated"` (no photo needed) or `"train_from_uploads"` (with photos).
2. Wait for LoRA training to complete → subject gets `training_status: "trained"` and a `lora_path`.
3. Then generate — the LoRA path will resolve.

### Rendered-video path (Film Productions don't auto-link the final MP4)

The reason you don't see the final video on the **Production page** — and why the video won't play in the browser — is a difference in how "Film" productions and "Music Videos" are handled:
1. **Missing link in the database:** unlike Music Videos, which have a direct link to their output file in the database (`output_document_id`), **Film Productions** do not have a direct field in the `Production` table to point to the final video. The system uses a "Folder Hierarchy" method to register the file.
2. **UI limitation:** because the link isn't directly on the `Production` record, the **Production page** doesn't know to look inside `orphan/productions/<id>/final/` for the video. It only sees the script and the shots.
3. **Browser playback:** the "open" button likely hits a path the web server isn't configured to serve as a playable stream.

**Watch it right now:** Documents (Files) page → drill into `orphan` → `productions` → `<id>` → `final` → click `final.mp4`.

*To make it auto-appear on the Production page: add `output_document_id` to the `Production` model and have the `Editor` agent populate it on completion.*
