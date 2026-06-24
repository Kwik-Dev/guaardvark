# Character LoRA Dataset Spec (FLUX-dev / SimpleTuner, 16 GB)

> The recipe for a training set that does NOT produce horse-heads. Written 2026-06-23 after the
> first `sage_harlow` run: 8 head-only images, captions ignored (`caption_strategy: "filename"`),
> 5/8 near-identical captions → overfit face, no learned body → ~8% "head-on-a-horse" clips.

## The three rules the first run broke

1. **Caption the body, or the model never learns one.** A LoRA can only render full-body /
   horse-riding / tattoos-on-arms shots if it *saw* bodies in training. Head-only data → the base
   model improvises a body under motion → animal-hybrid failures.
2. **Captions must actually be read.** `caption_strategy` MUST be `textfile` (reads the `.txt`
   sidecars), never `filename` (trains on `img_00`…). This is enforced by `scripts/pretrain_gate.py`.
3. **Vary the captions.** Near-identical captions over-anchor the LoRA to one pose/outfit/background.

## Framing mix (target ~20–30 images)

| Framing                | Share  | Why |
|------------------------|--------|-----|
| close-up               | ~20%   | face/identity fidelity (freckles, eyes, beauty mark) |
| head and shoulders     | ~15%   | portrait range |
| upper body             | ~15%   | wardrobe top half |
| three-quarter view     | ~20%   | **body** — pose, proportion |
| full body              | ~25%   | **body** — full proportion, legs, footwear; needed for tattoos/jewelry/outfits later |
| wide shot              | ~5%    | body-in-environment |

**Hard floor (gate-enforced):** at least `max(3, ceil(0.20 × N))` images must be
three-quarter / full body / wide. The first run had **zero** — that was the bug.

Also vary, across the set: gaze direction (head-on, 3/4 L/R, profile), expression (neutral,
smirk, scowl), outfit (within the black/white palette — dress, hoodie+cargo, sports-bra+shorts
for body shape, streetwear, boots visible), background, and lighting (daylight, golden hour,
indoor window, dramatic side).

## Captions

- One `.txt` per image, same stem (`img_07.jpg` → `img_07.txt`).
- Layout: `sage_harlow, <framing>, <pose/gaze>, <expression>, <outfit+colors>, <setting>,
  <lighting>, <fixed identity marks>`.
- **Trigger first**, in every caption. **Fixed identity marks last**, in every caption, so they
  bind to the token: `pale freckled skin, hazel-green eyes, small beauty mark on left cheek,
  black wavy hair with emerald-green highlights` (+ `delicate rose+barbwire tattoo on right wrist`
  on shots where the wrist is visible).
- Generate them with the auto-captioner (describes the *variable* attributes, injects trigger +
  marks): `python scripts/caption_dataset.py training/sage_harlow/dataset --trigger sage_harlow
  --profile training/sage_harlow/character_profile.md` (add `--dry-run` to review first).

## `repeats` (multidatabackend.json)

With only 8 images the first run used `repeats: 100` (heavy memorization). As the set grows, drop
it so total exposure stays ~constant: **`repeats ≈ round(800 / N)`** (N = image count). E.g. N=24 →
`repeats ≈ 33`. Keep resolution **512** on 16 GB (1024 thrashes near the VRAM ceiling).

## Before you launch

```bash
# 1) caption any new images (writes .txt sidecars)
python scripts/caption_dataset.py training/sage_harlow/dataset --trigger sage_harlow \
    --profile training/sage_harlow/character_profile.md

# 2) gate the dataset — refuses to train on bad data (must PASS)
python scripts/pretrain_gate.py training/sage_harlow/dataset --trigger sage_harlow \
    --config plugins/lora_trainer/SimpleTuner/config/multidatabackend.json

# 3) one-command retrain (gate -> [caption] -> train -> verify -> register -> A/B)
bash training/retrain_character.sh            # full run (GPU, ~3-4h)
bash training/retrain_character.sh --check    # gate + caption only, no GPU
```
