# FFmpeg Still Video (stills → clips with camera moves)

Guaardvark can turn still images into movie clips using **ffmpeg** — no GPU, no AI
model, and crucially the artwork is **never re-rendered**. Only the *camera* moves (or
the frame is held still), so there is **zero distortion and zero color change**. This
is ideal for picture-book thumbnails, infographics, or anything that must stay
pixel-perfect — where Image-to-Video (I2V) would re-imagine the image.

## Three motion patterns

| Key | Pattern | UI label | Behavior |
|---|---|---|---|
| `static` | Static | **A · Static** | Holds the image still for the duration (pixel-perfect, no movement). |
| `ken_burns_zoom` | Ken Burns zoom | **B · Zoom** | Slow camera push-in (zoom) — camera moves, artwork untouched. |
| `ken_burns_pan` | Ken Burns pan | **C · Pan** | Slow left-to-right camera pan — camera moves, artwork untouched. |

All three use ffmpeg's `scale`/`crop`/`zoompan` filters, which crop-and-scale the source
rather than regenerating it.

## Focus point

For **Zoom** (and the vertical axis of **Pan**), you can pick the **focus point** the
camera stays centered on while zooming — a `0.0–1.0` fraction of the image. Default is
`0.5, 0.5` (**center**). e.g. `focus-x 0.7, focus-y 0.3` keeps the zoom centered on the
upper-right of the frame.

## Where to use it

### Web UI (`/video`)
1. Under **Create Video**, click the **FFmpeg** toggle (🎬 icon, next to the Image icon).
2. **Upload / drag images** (or pick from the image gallery).
3. Choose a **motion pattern** (A · Static / B · Zoom / C · Pan).
4. Set **duration** (2–10 s), **FPS** (24/25/30) and **resolution** (480p/720p/1080p).
5. Click **Generate FFmpeg Clips** — results return instantly and each clip is saved to
   the **Media Library** (under Videos) with a **Download** link.

### CLI (`scripts/ffmpeg_stills.py`)
```bash
python3 scripts/ffmpeg_stills.py images/*.png --pattern ken_burns_zoom --duration 5 \
  --fps 25 --width 1280 --height 720 --out output_dir
```

### API
```bash
curl -X POST http://localhost:5055/api/batch-video/ffmpeg/stills \
  -H 'Content-Type: application/json' \
  -d '{
    "image_paths": ["data/uploads/Images/img_0.png"],
    "pattern": "ken_burns_zoom",
    "duration_s": 5, "fps": 25, "width": 1280, "height": 720,
    "focus_x": 0.5, "focus_y": 0.5
  }'
```

## After generating
Each clip is registered as a **Document** under `data/uploads/Videos/FFmpeg/<batch>/`, so
it shows up in the **Files** page and the **Media Library**. Drop the clips into the
**Video Editor** (they're proper `video` clips, so **Plan** works), add your music, and
**Render** the final video.

## Why not I2V?
Image-to-Video animates the image, so it can warp shapes and shift colors. FFmpeg still
video instead moves only the camera, preserving the artwork exactly. For anything that
must stay pixel-perfect (picture-book pages, logos, infographics), use **FFmpeg**; use
**I2V** only when you actually want the picture to change.
