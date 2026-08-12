# Video Model Download Tools

This directory contains tools for downloading video generation models.

**Preferred path:** use **Studio → Video Gen → Manage Video Models** (or
`POST /api/batch-video/models/download`). That flow reads
`backend/services/video_model_registry.py` (SSOT) and installs weights into the
ComfyUI `models/` tree (Wan / LTX / CogVideoX I2V) or the offline Diffusers
snapshot for CogVideoX-5B T2V.

## CogVideoX offline helper

`download_cogvideox_models.py` can still pull the CogVideoX-5B Diffusers
snapshot used by the offline fallback backend.

```bash
# From the project root
python backend/tools/video/download_cogvideox_models.py
```

### Models (current)

1. **cogvideox-5b** (`THUDM/CogVideoX-5b`) — text-to-video; offline Diffusers or Comfy
2. **cogvideox-5b-i2v** — image-to-video via ComfyUI (see registry)

CogVideoX-2B and SVD are retired from the product surface; do not install them
for VideoGen.

### Notes

- Prefer the registry download UI so companion files (VAE, text encoders) land
  where ComfyUI loaders expect them.
- Offline Diffusers fallback only covers CogVideoX-family T2V; Wan and LTX
  require ComfyUI (`preflight_video_model` enforces this).
