# training_director

Engine that turns structured procedure guides into narrated training video:
a cloned narrator over generated b-roll, with every specification carried on a
composited card.

The engine is company-agnostic and lives here, in Guaardvark. Everything that
makes a video belong to a particular customer lives in that customer's own
repository and reaches the engine through a **project**.

```
produce.py --project <project-dir> list        guides and their sections
produce.py --project <project-dir> voicetest   clone-check on trade language
produce.py --project <project-dir> <guide> --dry-run
produce.py --project <project-dir> <guide> --section s09c
produce.py --project <project-dir> <guide>
```

`TD_PROJECT` sets the project directory if you would rather not pass `--project`.

## What belongs where

| Engine (here) | Project (customer repo) |
|---|---|
| narration, assembly, b-roll, card composition | the guides themselves |
| numbers, fractions, measurements, code citations | trade vocabulary and acronyms |
| card layout and typography | brand palette and series wording |
| read-check and retake logic | narrator reference clip, delivery preset |

The rule: if a second customer in a different trade would want it, it is engine.
If it is what makes the output *theirs*, it is project.

Changes here flow downstream to every project. Nothing customer-specific is
added to this directory — that drift is what upstream syncs then have to undo.

## A project directory

```
project.py      defines PROJECT = Project(...)
guides/         one module per guide, each exposing SCRIPT
voice/          the narrator's reference clip
```

`project.py` supplies the palette, the trade's `TERMS` table and
`SPELLED_ACRONYMS`, the series label, and the narrator. It may also define
`VOICE_TEST_LINES` to exercise its own vocabulary. See `project.py` here for
the full contract; `<project-dir>/project.py` is a worked example.

## How a video is built

1. **Narration first.** Each line is a separate Chatterbox take from the
   reference clip, whisper-verified against the script, then joined with
   constructed silence. Pauses are built, not coaxed out of the model.
2. **Picture to fit.** Every shot's video is rendered to the *measured* length
   of its narration, so audio and video are in sync by construction.
3. **Overlays.** A section lower-third fades in early and out; the spec card
   fades in over the moving picture; a standards plate follows when the section
   is governed by a named regulation.

Takes are seeded per line, so a guide re-renders identically and a retake is a
genuinely different roll rather than a coin flip.

## The accuracy rule

Generated imagery is establishing and atmospheric **only**. Every specification
a trainee acts on lives on the spec card and in the narration — never in the
b-roll. Diffusion models render counts, geometry and spacing wrong, and in
training material a trainee can read the frame as instruction.

Write prompts that describe scene, material and light. Never a countable
technical detail.

## Writing narration

The audience works the trade; it is not a technical one. Narration should sound
like a professional trainer, not a colleague talking shop.

1. **Complete sentences.** Fragments used for emphasis read as casual.
2. **No spoken shorthand.** "On center", not "o.c.". The card may abbreviate;
   the narration may not.
3. **Name a standard before citing its number** — "the OSHA fall protection
   standard, 29 CFR 1926.501".
4. **Explain a convention on first use.** A roof pitch is introduced as "rises
   four inches for every twelve inches of run"; later mentions may use "a four
   in twelve pitch".
5. **Lead a safety section with why it matters**, then give the requirement.
6. **State consequences plainly**, without slang.

Terms the clone reads wrong go in the project's `TERMS` table, never worked
around in the guide text. Confirm every addition with `voicetest`.

## Configuration

All settings come from the environment with working defaults, so the engine
runs unchanged wherever it is deployed. See `config.py`.

| Variable | Default | Notes |
|---|---|---|
| `TD_PROJECT` | — | project directory; `--project` overrides |
| `TD_API` | `http://localhost:5000` | backend serving batch image and video |
| `TD_FOUNDRY` | `http://127.0.0.1:8206` | audio_foundry; one per machine |
| `TD_OUT_ROOT` | `data/outputs/training` | finished videos and work files |
| `TD_CACHE_ROOT` | `data/cache/training_broll` | stills, cached by prompt |
| `TD_IMAGE_MODEL` | `zimage-turbo` | pinned; `auto` overrides frame size |
| `TD_VIDEO_MODEL` | `wan22-i2v` | used only by shots marked `motion=True` |
| `TD_WHISPER_MODEL` | `small.en` | tiny.en misses trade vocabulary |
| `TD_VOICE_SEED` | `20260815` | base for per-line seeds |

To drive another install's already-running GPU services, point `TD_API` at it.

## Requirements

The engine needs only Python, Pillow, requests and ffmpeg. Generation is
delegated over HTTP, so the host named by `TD_API` and `TD_FOUNDRY` must
provide:

- **ComfyUI** with an image model, reached through `/api/batch-image`
- **audio_foundry** with the Chatterbox backend, for `/generate/voice`
- **backend venv** with `faster-whisper`, for the narration read-check
  (`TD_BACKEND_PY` overrides which interpreter is used)
