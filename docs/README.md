
Installer          │ What it holds              │ Total size │ Where it opens
────────────────────┼────────────────────────────┼────────────┼────────────────────────────
 Image Models       │ 7 Diffusers (Z-Image-      │ ~90 GB     │ Settings → AI Features →
                    │ Turbo, Krea-2, SDXL, etc.) │            │ Image Generation
 Video Models       │ 29 ComfyUI weights incl.   │ ~140 GB    │ Settings (same row) +
                    │ FLUX-dev/schnell, Wan,     │            │ auto-opens from Video page
                    │ LTX, CogVideoX             │            │ when a model is missing
 Voice Models       │ 4 Whisper + 7 Piper voices │ ~1.2 GB    │ Settings (same row)
 Upscaling Models   │ 9 upscale weights          │ ~0.5 GB    │ Upscaling page ("Manage
                    │                            │            │ Upscaling Models")
 Infographic Models │ the 4 Flux files you       │ ~18 GB     │ Settings (same row)
                    │ already found              │            │

Key caveats:


• Video Models is the big one and is strictly manual — the modal explicitly says there's no
auto-download (VideoModelsModal.jsx:282-292). It's also where FLUX-dev/schnell for
keyframe/storyboard get installed (the Image modal points you there).
• Image, Voice, and Upscaling have partial auto-download on first use (Z-Image downloads
silently on first generation; Whisper auto-fetches on first STT; upscaling only downloads
after you pick a model). So those may self-heal when you use the feature.
• Auto-download on first use (no UI needed): Audio Foundry plugin models (ACE-Step ~10 GB,
stable-audio ~1.5 GB, Kokoro, Chatterbox), ComfyUI's codeformer.pth, and LoRA trainer base
model.

So the only ones you must proactively click are the Video Models installer (and
Infographic, which you've found) if you plan to generate video/storyboards. Image/Voice are
mostly "just works" on first use.
