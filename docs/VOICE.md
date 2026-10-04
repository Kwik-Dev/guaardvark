# Guaardvark Voice — TTS and STT architecture and setup

How text-to-speech and speech-to-text actually run, which of them is a server, which is
in-process, and how a new `cloud-plus` user sets each one up.

There is **no cloud speech vendor** anywhere in the tree. No ElevenLabs, Deepgram,
AssemblyAI, Azure Speech, PlayHT or Google. The cloud provider table
(`backend/services/llm_provider.py`, `CLOUD_PROVIDERS`) holds only Mistral and
OpenAI, and both are chat-only. Everything here runs on the machine.

`B` below is `${GUAARDVARK_URL:-http://localhost:5000}` (macOS: **5055**).

---

## 1. The three shapes a "voice server" can take

| Shape | What it is | Examples here |
|---|---|---|
| **In-process Python** | A library imported into the Flask backend. No port, no process. | Piper (TTS), faster-whisper (STT) |
| **A Guaardvark plugin** | A separate process on localhost the backend calls over HTTP. | Audio Foundry, port 8206 (Kokoro, Chatterbox) |
| **An external local HTTP server** | Something you run yourself, outside Guaardvark. | a `whisper.cpp` server on 5800; the pi-omni router on 8081 |

Which shape is in use changes what has to be running before a request succeeds, so it is
worth knowing per engine.

---

## 2. Text-to-speech

| Engine | Shape | Route | Notes |
|---|---|---|---|
| **Kokoro** | Audio Foundry plugin | `POST $B/api/audio-foundry/generate/voice`, `backend: "kokoro"` | ~80 MB, fast, 10+ built-in voices, `af_heart` default. Primary engine |
| **Chatterbox** | Audio Foundry plugin | same route, `backend: "chatterbox"` | ~500 MB, emotion presets and exaggeration/cfg knobs, and the **only** path that clones a voice |
| **Piper** | in-process in the backend venv | `POST $B/api/voice/text-to-speech` | No GPU and no plugin needed. `piper-tts==1.7.0` (`backend/requirements.txt`). Requires a Piper voice model on disk |
| **pi-omni router** | external, OpenAI-compatible | not part of the main API | Discord plugin only; opt-in through `voice.backend: "pi-omni"` |

The backend reaches the plugin over `AUDIO_FOUNDRY_URL` (default
`http://127.0.0.1:8206`). `POST $B/api/voice/narrate` gives multi-section narration with
pauses (`script`, `engine`, `voice`, `pause_between_sections`, `output_format`).

The `auto` backend tries Chatterbox and falls back to Kokoro; the response names the
engine that actually ran.

---

## 3. Speech-to-text

One route: `POST $B/api/voice/speech-to-text`, multipart form-data, the file part named
**`audio`** (`backend/api/voice_api.py`). Three paths, tried in this order:

| Order | Path | Requires |
|---|---|---|
| 1 | **External whisper.cpp HTTP server** | `GUAARDVARK_USE_WHISPER_SERVER=1` and a server already listening |
| 2 | **faster-whisper, in-process** | `faster-whisper>=1.2.1` in the venv (**the default**) |
| 3 | **Bundled whisper.cpp CLI** | a build under `backend/tools/voice/whisper.cpp/build/bin/` |

Path 1 saves the uploaded audio to a temp WAV and POSTs **the file's path on this
machine** to `{WHISPER_SERVER_URL}/inference` (helper `_transcribe_via_whisper_server`),
so the server has to share this filesystem. Path 2 decodes in memory and never shells out,
which is why it is the default. Path 3 is the fallback for a machine with no
faster-whisper.

`POST $B/api/voice/install-whisper-model` downloads a faster-whisper/CTranslate2 model
(`tiny`, `tiny.en`, `base`, `small`, `medium`); `GET $B/api/voice/models/all` lists them
with `is_downloaded`. `POST $B/api/voice/install-whisper` builds the bundled whisper.cpp
CLI instead (needs cmake and a C compiler).

`./start.sh` also launches a whisper-server binary when `GUAARDVARK_USE_WHISPER_SERVER=1`
and both a binary and a model are present (`GUAARDVARK_WHISPER_SERVER_BIN` defaults to
`command -v whisper-server`, `..._MODEL` to `backend/tools/voice/whisper.cpp/models/ggml-base.bin`,
`..._PORT` to `5800`). `./stop.sh` stops only the one Guaardvark started.

---

## 4. Setup

The short answer: **STT works with no setup, TTS needs one action.**

### STT — nothing to do

`faster-whisper` is installed by `./start.sh` and the tiny English model is fetched on
first use, so `POST /api/voice/speech-to-text` works on a fresh install. Pick a larger
model only if you want better accuracy:

```bash
# list what is available / what is installed
curl -s $B/api/voice/models/all | python3 -m json.tool

# better accuracy at the cost of speed
curl -s -X POST $B/api/voice/install-whisper-model \
  -H 'Content-Type: application/json' -d '{"model_id": "base"}'
```

```bash
# transcribe one file
curl -s -X POST $B/api/voice/speech-to-text -F audio=@clip.wav
```

### TTS — start one plugin

```bash
# Kokoro and Chatterbox both live in Audio Foundry; start it once.
curl -s -X POST $B/api/plugins/audio_foundry/start

# confirm
curl -s $B/api/plugins/status
```

Model weights (~80 MB Kokoro, ~500 MB Chatterbox) download to the HuggingFace cache on
first use. To pull them ahead of time:

```bash
backend/venv/bin/hf download hexgrad/Kokoro-82M
backend/venv/bin/hf download ResembleAI/chatterbox
```

```bash
# speak a line; the response carries engine, path and document_id
curl -s -X POST $B/api/audio-foundry/generate/voice \
  -H 'Content-Type: application/json' \
  -d '{"text": "Hello from Guaardvark.", "backend": "auto", "voice_id": "af_heart"}'
```

Cloning is consent-gated and only on Chatterbox. The reference clip must go through the
upload route, which is what records consent; an arbitrary file path is refused. Ask the
user whether the voice is theirs or someone who consented before uploading, and never
clone a public figure.

```bash
curl -s -X POST $B/api/audio-foundry/voice-clips/upload -F file=@ref.wav -F name="Dean sample"
# then generate with: "backend": "chatterbox", "reference_clip_path": "<stored path>"
```

### Piper — only if you want no plugin at all

Piper runs inside the backend, so the `audio_foundry` plugin can stay stopped. It needs a
voice model: the shipped default is `libritts` and the model is not bundled or downloaded
automatically, so the request succeeds but playback fails with
`{"error": "Piper model not found: libritts"}` until you install one. `GET $B/api/voice/voices`
lists the profiles; `POST $B/api/voice/install-voice-model` installs one.

### Using your own whisper.cpp server

If you already run a whisper.cpp HTTP server (a LiveKit or pi-omni stack, say), Guaardvark
can use it instead of its own CLI or faster-whisper. Add to `.env` and restart the backend:

```env
GUAARDVARK_USE_WHISPER_SERVER=1
GUAARDVARK_WHISPER_SERVER_URL=http://127.0.0.1:5800   # optional; default 5800
```

`./start.sh` and `./stop.sh` manage that server too when the flag is set. The realtime
`/api/voice/stream` voice-chat path follows the same setting.

---

## 5. Options available to a new `cloud-plus` user

There is no third-party or cloud speech option to choose. The whole menu is local.

**TTS**

1. **Kokoro** — start the `audio_foundry` plugin. Smallest and fastest; the sensible default.
2. **Chatterbox** — the same plugin. The only path with emotion control and cloning.
3. **Piper** — no plugin at all, but you must supply a voice model.
4. **pi-omni router** — only if you already run that external stack, and only through the
   Discord plugin.

**STT**

1. **faster-whisper** — the default and already installed. Choose a size with
   `install-whisper-model`.
2. **Bundled whisper.cpp CLI** — `install-whisper`; needs cmake and a compiler.
3. **Your own whisper.cpp server** — the two env vars above; then no Guaardvark build is
   needed.

No API keys, no subscriptions, nothing sent to a provider.

---

## 6. Health reporting caveats

Two things to know before trusting a status page.

- **`GET /api/voice/status` under-reports.** It returns `status: "unavailable"`,
  `speech_recognition: false`, `text_to_speech: false` and `available_voices: []` when the
  bundled whisper.cpp build and Piper are absent, because those are the only two it
  checks. It does not know faster-whisper is installed, and it does not know Audio Foundry
  holds the TTS engines. A machine with working STT and working Kokoro TTS can still show
  `"unavailable"` here. Check the route itself instead:
  `GET $B/api/audio-foundry/voices` for TTS, `GET $B/api/voice/models/all` for STT.
- **Piper is listed as the offline fallback but ships no voice model.** The default
  `libritts` is absent, so the route answers 200 with an `audio_url` and the audio stream
  then 500s. Install a voice model, or prefer Kokoro.

A worked example from a real Apple Silicon box, with the `audio_foundry` plugin stopped:
STT ready (`faster-whisper 1.2.1`, `models--Systran--faster-whisper-tiny.en` in the HF
cache); Kokoro and Chatterbox blocked by the stopped plugin
(`503 {"error": "Audio Foundry service not running"}`); Piper broken as described above;
the bundled whisper.cpp CLI not installed, which is expected.

---

## 7. CLI

```bash
guaardvark audio transcribe clip.wav     # STT (add --json for machine-readable output)
guaardvark audio tts "Read this out"     # TTS, via Audio Foundry with a /api/voice fallback
guaardvark audio voices                  # list TTS voices
```

There is no `audio narrate` subcommand; multi-section narration is the REST route
`POST $B/api/voice/narrate`. `audio transcribe` is a `cloud-plus` addition; it posts the
file under the part name the route expects. See [docs/CLI_SPEC.md](CLI_SPEC.md) for the
full command reference.

---

## See also

- [docs/AUDIO.md](AUDIO.md) — the Audio Foundry plugin, music and FX, model downloads, MPS/CUDA
- [.agents/skills/voice/SKILL.md](../.agents/skills/voice/SKILL.md) — the agent-facing voice skill
- [INSTALL.md](../INSTALL.md) — platform setup and prerequisites
