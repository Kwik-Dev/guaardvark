# Known Issues

Working list of open / deferred issues. Add a new `##` entry per issue with status,
symptom, root cause, and any partial fix already applied.

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

## [OPEN] Upgrade bundled ComfyUI (v0.32.0 → latest v0.34.x)

- **Status:** Open — no fix applied. Deferred; will handle later (ComfyUI version bump).
- **Area:** `plugins/comfyui/ComfyUI` git checkout; `plugins/comfyui/scripts/restore_app.sh` (pins `COMFYUI_REF`); `plugins/comfyui/custom_nodes.manifest`; `plugins/comfyui/scripts/start.sh`.

### Context
The bundled ComfyUI plugin is a git checkout of `comfyanonymous/ComfyUI` pinned to **v0.32.0** via `COMFYUI_REF` in `restore_app.sh`. Latest upstream release tags are now **v0.33.x – v0.34.2**. Updating is not a blind `git pull` — see the caveats below.

### Caveats / why it's not a naive update
- **Custom nodes are pinned to SHAs** (`custom_nodes.manifest`: VideoHelperSuite, GGUF, Frame-Interpolation, KJNodes, CogVideoXWrapper, facerestore) **tested against v0.32.0**. A core bump can break them and requires re-pin + re-test.
- **`start.sh` applies version-sensitive patches** (`model_patcher.py`, `quant_ops.py`, and the `comfy-kitchen` PEP-585 annotation rewrite for torch ≤ 2.6) written for the 0.32 era — a large jump may need re-verification.
- **`restore_app.sh` rsyncs with `--delete`** and preserves **only** `models/`, `custom_nodes/`, `user/`, `input/`, `output/`, `temp/`. A restore therefore **wipes `extra_model_paths.yaml`** (the Z-Image ← Comfy-Desktop / `~/ComfyUI-Shared` bridge). Back it up first.
- Prefer a **tagged release** (e.g. `v0.34.2`) over unpinned HEAD; `COMFYUI_REF=` (empty = master HEAD) is deliberately flagged as risky in the script.

### Remaining work / steps
1. Back up `plugins/comfyui/ComfyUI/extra_model_paths.yaml` (and `plugin.local.json` if present).
2. `COMFYUI_REF=v0.34.2 bash plugins/comfyui/scripts/restore_app.sh`
3. Restore `extra_model_paths.yaml`.
4. Re-pin / re-test the custom-node SHAs in `custom_nodes.manifest` against the new core; `install_deps.sh` + restart.
5. Verify: `ComfyUIImageGenerator.comfyui_installed_engines()` still returns `['zimage']`, the WAN i2v loaders still register, and smoke-test one Z-Image still + one WAN i2v queue.

---

## [OPEN] Chat responses contain mojibake for non-ASCII UTF-8 characters (em-dash → `â`)

- **Status:** Root cause fixed in source (code change applied, NOT yet active on a running instance); data repair of existing messages still outstanding.
- **Area:** Backend chat streaming → cloud LLM providers.

### Symptom
Streamed chat responses show garbled characters for non-ASCII UTF-8, e.g.:
- em-dash `—` renders as `â\x80\x94` (visible as a stray `â`)
- smart quotes, `─` box-drawing separator lines, accented chars all corrupted

Examples (as stored in `llm_messages`):
```
...do with it â\x80\x94 generate an image, convert it, or something else?
```

### Root cause
`requests` defaults `Response.encoding` to `ISO-8859-1` when the server returns a
text `Content-Type` **without** `charset=utf-8`. The cloud LLM endpoints
(`https://ollama.com/v1`, Mistral) return `Content-Type: application/json` with no
charset. The streaming reader `resp.iter_lines(decode_unicode=True)` therefore
decodes the UTF-8 response bytes as Latin-1, producing `â\x80\x94` for a `—`
(`e2 80 94`), and re-saves it as UTF-8 (`c3 a2 c2 80 c2 94`).

Confirmed with a live reproduction:
```
WITHOUT fix:  em-dash â\x80\x94 end
WITH fix:     em-dash — end
```

### Files changed (fix already applied to source)
- `backend/services/openai_provider.py` — `_stream_chat`: added `resp.encoding = "utf-8"` before `iter_lines(decode_unicode=True)`.
- `backend/services/mistral_provider.py` — same one-line fix in its streaming path.

### Remaining work
1. **Restart the backend** so the running process loads the fixed code (new
   responses will be correct).
2. **Repair existing DB rows** — already-saved corrupted messages in
   `llm_messages` (and any other store) still contain the mojibake. The corruption
   is reversible for the affected chars:
   `content.encode("latin-1").decode("utf-8")`
   (only apply where the string actually contains the `â\x80\x94` / `â\x94\x80`
   byte sequences; ASCII-only rows are unaffected and must be skipped).
   Need to review which columns/tables are affected (at minimum `llm_messages.content`).

### Verification
- Restart backend, send a prompt that forces an em-dash / smart quote, confirm it
  renders as `—` not `â`.
- Confirm streaming path (`openai_provider`/`mistral_provider`) header/charset behavior.


## [RESOLVED] AudioFoundry FX (stable-audio) — `RecursionError` on Apple Silicon MPS

- **Status:** Resolved. FX generation now works on MPS via a scheduler override.
- **Area:** `plugins/audio_foundry/backends/audio_fx_sao.py` (Stable Audio Open FX backend).

### Symptom
On Apple Silicon (MPS), the FX backend reported `available=True` and loaded, but a
`/generate/fx` request failed with:
```
RecursionError: maximum recursion depth exceeded
  File ".../numpy/core/_ufunc_config.py", line 111, in seterr
    old = geterr()
```

### Root cause
Not the SAO backend's generator. The model's default scheduler
(`CosineDPMSolverMultistepScheduler`) drives an **SDE solver via `torchsde`**, and
`torchsde`'s Brownian-motion path (`brownian_interval._split` →
`generator.generate_state(4)` → numpy `seterr`/`geterr`) recurses infinitely on
Apple MPS. It is a true infinite recursion, so raising `sys.setrecursionlimit()`
does **not** help.

### Fix applied
- `audio_fx_sao.py` and `run_acestep.py` use a `_pick_device()` helper
  (CUDA > MPS > cpu) so both FX and music load on MPS.
- `audio_fx_sao.py` swaps the pipeline scheduler to
  **`EDMDPMSolverMultistepScheduler`** (a non-SDE multistep scheduler) after load,
  which avoids `torchsde` entirely. Music (ACE-Step) and FX (stable-audio) both
  now generate valid WAVs on MPS.

### Verification
- `curl -X POST http://127.0.0.1:8206/generate/fx -H "Content-Type: application/json" -d '{"prompt":"thunder rumble","duration_s":5,"output_format":"wav"}'`
  → returns a valid 5.0s WAV (16-bit stereo 44.1 kHz).
- All three backends report `available=True` on MPS.
- Direct helper check passes: `_make_generator(123, "mps", fake_torch)` returns
  `None` after calling `torch.manual_seed(123)`, while CUDA still uses an
  explicit device generator.


## Committed
| Commit | Files | Branch |
|---|---|---|
| `qsz` `fix: force UTF-8 decoding in cloud LLM provider streaming (mojibake)` | `kl`, `om`, `su` | `feat/llm-providers` |
| `ywy` `feat: model-management UI + cloud model in status bar` | `lt`, `ux`, `ny` | `feat/llm-providers` |
| `rty` `docs: add guides, Interconnector, JP guide, film-crew script memo, LoRA memo` | `sv`, `yl`, `lw`, `ov`, `lk` | `docs/guides` (new) |
| `tlu` `chore: ignore runtime RAG indices and transient UI state` | `pu` | `docs/guides` |
| `nwk` `fix: don't free ComfyUI resident models when rendering through ComfyUI` | `qq` | `feat/zimage-comfyui` |

## Discarded
- `qk` (docs/README.md) — the leaked agent transcript junk, reverted to the clean 3-line README.
- The 5 runtime files (`data/images_windows_state.json`, `data/indexes/*.json`) — now git-ignored (still on disk, regenerable; say the word if you want them physically deleted).

## Left uncommitted (intentionally — another session's active work)
- `xw` `backend/services/comfyui_image_generator.py`
- `uk` `backend/tests/services/test_comfyui_image_lora_branch.py`

These two appeared mid-task and are clearly part of the in-progress Z-Image/ComfyUI LoRA work another session is actively committing, so I left them untouched rather than splitting or misplacing them.

**Note:** the mojibake fix and model-management UI both landed on `feat/llm-providers` (not a standalone `fix/llm-mojibake`) because `openai_provider.py` only exists on that branch — a standalone branch off the base was impossible. Nothing was pushed; all branches are local.

---


Summary of the Z-Image + ComfyUI LoRA issues:

1. No LoRA node in the workflow The ComfyUI Z-Image graph ( comfyui_image_generator.py:297-
348 ) is a plain  UNETLoader → CLIPTextEncode → KSampler → VAEDecode  chain. Unlike the SDXL
and FLUX branches, it has no  LoraLoader / LoraLoaderModelOnly  node, so LoRAs are never
applied.

2. An explicit guard rejects Z-Image LoRAs  comfyui_image_generator.py:210-216  detects
family == "zimage"  and logs "Z-Image LoRAs cannot use Comfy SDXL/FLUX graph" — it drops the
LoRA rather than routing it anywhere.

3. Key-format mismatch The trainer ( run_zimage_trainer.py ) saves via  save_lora_weights ,
producing diffusers-format keys prefixed  transformer. . ComfyUI's  LoraLoaderModelOnly
expects raw ComfyUI keys (no  transformer.  prefix). A diffusers-format LoRA would load to
zero effect — the same class of bug the SDXL path already documents and works around via
convert_state_dict_to_kohya .

4. Custom-node compatibility is unproven Z-Image in ComfyUI loads via a raw  UNETLoader  (
z_image_turbo_bf16.safetensors ) + Qwen CLIP ( qwen_image ), not a standard diffusers
checkpoint. Whether  LoraLoaderModelOnly  maps the LoRA's target modules ( to_q ,  to_k ,
to_v ,  to_out.0 ) onto that custom build's key names is unknown.

5. Architectural split The registry marks Z-Image as  inference_engine: "offline"  — ComfyUI
is only an opt-in override ( GUAARDVARK_ZIMAGE_USE_COMFYUI=1 ). The intended LoRA path is
the offline Diffusers  ZImagePipeline  ( load_lora_weights ), not ComfyUI.

Net: Z-Image LoRA training works (now on MPS), but the ComfyUI side can't consume the result
— it needs a LoRA chain added, the guard relaxed, and a key-format conversion, plus
validation against the custom node. The offline Diffusers path is the only one that
currently applies Z-Image LoRAs.

ToDo 

1. Train a LoRA first (via the Z-Image trainer I ported to MPS), which would drop a  .
safetensors  into  data/training/loras/ , then copy it into  /Users/ymmtny/ComfyUI-
Shared/models/loras/ .
2. Or download any public Z-Image-compatible LoRA from CivitAI/HuggingFace and place it in
the ComfyUI  loras/  directory.

### Cast

The sequence is: cast the subject (user-gated) → train the LoRA → then generate. The "no
LoRA paths" error means you're trying to generate before the subject has a trained LoRA. The
"recommendation not available" means the casting step hasn't been completed/confirmed yet.

The fix path:

1. Cast the subject via  cast_subject  with  action: "train_from_generated"  (no photo
needed) or  "train_from_uploads"  (with photos).
2. Wait for LoRA training to complete → subject gets  training_status: "trained"  and a
lora_path .
3. Then generate — the LoRA path will resolve.

