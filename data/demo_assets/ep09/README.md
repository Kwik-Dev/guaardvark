# Episode 9 assets — Ivy (Cast / LoRA)

**Pick:** Aug 8 cinematic cluster only (5 refs).

The folder at `~/Pictures/poison-ivy` is three different identities
(photoreal forest / glam green-hair / red-hair hallway). Training on
all 32 would smear the face. Aug 8 is the locked face: green skin,
dark auburn-black wet curls, ivy in the hair, forest, photoreal —
matches the series tone and is safe on a YouTube walkthrough.

- Cast subject: **Ivy** (id 1)
- Trigger: `ivyx`
- Base: Z-Image Turbo
- Refs live in `data/cast_refs/1/` (uploaded copies) and here.

Known gap: all five frames are head-and-shoulders. Sample expansion
drifted (elf ears, nudes) — cancelled after 8; approved only
sample_0 and sample_4 (clothed, no ears). Do not promote the rest.

Production: id 1 "EP09 asset — Ivy Relights the Lantern"
- 6 shots, unique storyboards in data/outputs/storyboards/1/shot_{scene}_{shot}.png
- Render completed 2026-08-14 via Film Crew editor (Wan 2.2 I2V A14B Q5).
  Durable copies: data/outputs/productions/1/ (story-order final.mp4 + clips/)
  and data/demo_assets/ep09/render/. Upload copy:
  data/uploads/Videos/EP09 Ivy Relights the Lantern.mp4
- video_editor / audio_foundry stayed operator-disabled (no MLT, video-only).

Beat file: scripts/demo_director/episodes/ep09_filmcrew.py
(shoot next — clips and storyboard grid are both available).
