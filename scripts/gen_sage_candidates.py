#!/usr/bin/env python3
"""Generate a candidate pool of sage_harlow shots for the LoRA retrain dataset (FLUX-dev + LoRA).

Face/upper-body @ strength 0.9 (high yield, on-model). Full-body across strengths 0.5/0.65/0.8
(reduced strength lets FLUX-dev's strong base anatomy build a correct body while the LoRA still
carries identity) — the lever for the horse-head full-body gap, since FLUX-dev is CFG-distilled
so negative prompts are inert. Writes to training/sage_harlow/candidates/ + manifest.json for
curation; nothing touches the training dataset until shots are approved. ComfyUI (8188) does the
GPU work over HTTP, so this process stays light.
"""
import sys, json, time
sys.path.insert(0, "/home/llamax1/LLAMAX8")
from pathlib import Path
from backend.services.comfyui_image_generator import ComfyUIImageGenerator

OUT = Path("/home/llamax1/LLAMAX8/training/sage_harlow/candidates")
OUT.mkdir(parents=True, exist_ok=True)
LORA = "data/training/loras/sage_harlow.safetensors"
TRIG = "sage_harlow"
SUFFIX = "photorealistic, sharp focus, highly detailed, natural lighting"

# Face / upper-body: framing + expression + black/white outfit + setting (LoRA carries the face).
FACE = [
    ("close-up", "head-on, neutral expression, black tank top, soft studio backdrop"),
    ("close-up", "three-quarter view left, slight smirk, black hoodie, blurred urban background"),
    ("close-up", "three-quarter view right, mild scowl, white t-shirt, plain wall, window light"),
    ("close-up", "profile view left, calm, black turtleneck, dark background, rim light"),
    ("close-up", "profile view right, looking away, black tank, garden bokeh, golden hour"),
    ("close-up", "chin slightly forward signature scowl, black hoodie, plain grey backdrop"),
    ("head and shoulders", "looking up slightly, soft smile, white blouse, studio key light"),
    ("head and shoulders", "looking down, pensive, black hoodie, dim alley, moody light"),
    ("head and shoulders", "direct gaze, confident, black dress, warm indoor light"),
    ("head and shoulders", "soft daylight, black tank, green foliage background"),
    ("upper body", "arms crossed, neutral, black oversized hoodie, brick wall, overcast"),
    ("upper body", "hand near face, smirk, white crop top, studio seamless, soft light"),
    ("upper body", "slight turn, black blazer, neon-lit street at night"),
    ("upper body", "relaxed stance, black sports bra, bright gym setting"),
    ("upper body", "looking over shoulder, black dress, dramatic side light"),
    ("upper body", "intense expression, black leather jacket, rainy street bokeh"),
]

# Full-body scenarios — each rendered at 3 strengths so curation can pick best anatomy+identity.
BODY = [
    ("full body", "head to toe, standing front, athletic build, black sports bra and black shorts, chunky black boots, plain studio backdrop, even full-length lighting, legs and feet visible"),
    ("full body", "walking toward camera, black oversized hoodie, baggy black cargo pants, black boots, urban alley, daylight, full length"),
    ("three-quarter view", "standing pose, white tank and black jeans, hand on hip, studio cyclorama, head to toe"),
    ("full body", "profile side view, athletic stance, black leggings and sports bra, gym, entire body visible"),
    ("full body", "sitting on concrete steps, black hoodie and cargo pants, boots, outdoor stairs, overcast, full figure"),
    ("wide shot", "standing in black flowing dress, minimalist studio, dramatic light, head to toe, full length"),
]
BODY_STRENGTHS = [0.5, 0.65, 0.8]

manifest = []
log = (OUT / "gen.log").open("a")
def say(m):
    print(m); log.write(m + "\n"); log.flush()

def run(items, strength, w, h, tag, framing_from):
    g = ComfyUIImageGenerator(model="flux-dev", lora_strength=strength)
    for i, entry in enumerate(items):
        framing, desc = entry
        prompt = f"{TRIG}, {framing}, {desc}, {SUFFIX}"
        seed = 1000 + i * 13 + int(strength * 100)
        fname = f"{tag}_{i:02d}_s{int(strength*100)}.png"
        out = str(OUT / fname)
        t0 = time.time()
        try:
            g.generate_image(prompt=prompt, loras=[LORA], output_path=out,
                             width=w, height=h, seed=seed, steps=28, model="flux-dev")
            dt = time.time() - t0
            manifest.append({"file": fname, "prompt": prompt, "framing": framing,
                             "lora_strength": strength, "seed": seed})
            say(f"OK  {fname}  ({dt:.0f}s)")
        except Exception as e:  # noqa: BLE001 — one bad image must not sink the batch
            say(f"FAIL {fname}: {e}")
        (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))

say(f"=== sage candidate gen start: {len(FACE)} face @0.9 + {len(BODY)*3} body @{BODY_STRENGTHS} ===")
run(FACE, 0.9, 1024, 1024, "face", None)
for s in BODY_STRENGTHS:
    run(BODY, s, 768, 1152, "body", None)
say(f"=== DONE: {len(manifest)} images in {OUT} ===")
log.close()
