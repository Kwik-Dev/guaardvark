# Known Issues

Working list of open / deferred issues. Add a new `##` entry per issue with status,
symptom, root cause, and any partial fix already applied.

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