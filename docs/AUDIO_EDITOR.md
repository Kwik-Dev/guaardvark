# Audio Studio — How To Use

**Audio Studio** is Guaardvark's local AI audio production page (the **Audio
Foundry** plugin frontend). It does three things, each with its own tab and
model:

| Tab | What it does | Model |
|-----|--------------|-------|
| **Voice** | Text → spoken voiceover, with **zero-shot voice cloning** from a reference clip | Chatterbox (primary) / Kokoro (fallback) |
| **Music** | A mood/description → original instrumental track | ACE-Step |
| **FX Lab** | A sound description → sound effect | Stable Audio Open |

Every generated file is saved to `data/uploads/Audio/`, registered as a
**Document**, and appears in the **DocumentsPage**.

For model download/setup and how audio is mixed into Film Crew renders, see
[`AUDIO.md`](AUDIO.md). This guide is about *using* the Audio Studio page.

---

## 1. Voiceover + Voice Cloning (Chatterbox)

The **Voice** tab turns a script into speech and can **clone a voice** from a
short reference clip.

### Pick a backend
Three chips select the TTS backend:
- **AUTO** — let the dispatcher decide (Chatterbox first, Kokoro fallback).
- **CHATTERBOX** — the voice-clone-capable model.
- **KOKORO** — faster fallback, fixed built-in voices (no cloning).

*(The chips now stay visible in light and dark mode.)*

### To clone a voice (Chatterbox)
1. Select **CHATTERBOX** (or **AUTO**).
   - The **KOKORO** path uses only the fixed **voice dropdown** — no cloning.
2. **Import a reference clip** — click **"Import reference clip"** and upload a
   **5–10 second clip of clean, isolated speech** in the voice you want to
   clone (one speaker, no music/noise). You can also pick a previously-imported
   clip from the library dropdown.
3. Type the **script** you want spoken in that voice.
4. Click **"Generate Voiceover"**.

When Chatterbox runs, the reference clip is passed as `audio_prompt_path` —
that's the **zero-shot clone**. No reference clip → Chatterbox uses its **default
voice**.

> **Tips for better clones:**
> - One speaker, 5–10s, clean/isolated (the UI's own hint).
> - Match the reference clip's language to your script (see "Language support").
> - The same Chatterbox path also powers **Film Crew** character voices, so a
>   cloned clip can give a consistent voice to a character.

### Voice picker (Kokoro)
When not on the Chatterbox path, a **Voice** dropdown shows Kokoro's built-in
voices (American/British/Spanish, male/female). The list is fetched live from
`GET /api/audio-foundry/voices`.

### Language support — English vs Japanese
The Audio Studio loads **Chatterbox** via `chatterbox.tts.ChatterboxTTS` and does
**not** pass a `language_id`. That is the **English** model — SoTA zero-shot
**English** TTS.

- ✅ **English** — fully supported (and the default).
- ⚠️ **Japanese / other languages** — **not currently supported out-of-the-box.**
  Chatterbox *does* have a multilingual variant (`ChatterboxMultilingualTTS`)
  supporting **23+ languages including Japanese**, and a **Single Language Pack**,
  but **it is not wired into Audio Studio yet**: the code loads the base
  English `ChatterboxTTS` and never sets a `language_id`.
- The **Kokoro** fallback uses English voice IDs.

To add Japanese/non-English, the backend would need to load
`ChatterboxMultilingualTTS` and pass a `language_id` (see `ISSUES.md` for how
audio features are tracked).

---

## 2. Music Composer

The **Music** tab generates an original instrumental from a description.

1. Pick **genre / mood / instrument** chips (or type free-text details).
2. Optional **"Polish with AI"** — a local LLM rewrites your chips+text into a
   clean ACE-Step prompt (you can preview it before generating).
3. Set **duration** (10–240s; ACE-Step cap is 240s) and **instrumental only**.
4. Click **"Compose Music"**.

---

## 3. FX Lab

The **FX Lab** generates a sound effect from a text description.

1. Type a sound, e.g. *"a lightsaber igniting in a vacuum"*.
2. Set **duration** (1–47s — Stable Audio Open's training cap).
3. Click **"Generate Sound FX"**.

> **Note:** Stable Audio Open is the **FX** model. It does **not** clone voices —
> that's Chatterbox (see the Voice tab).

---

## 4. Studio Monitor & results

The right panel ("Studio Monitor") previews the result with a **waveform player**
(title "Voiceover / Music / SFX Master"), shows **technical specs** (backend,
sample rate, output path), and a success notice confirming the asset was
registered in your **Audio** library. Long generations run as a background job
with a progress bar and a **Cancel** button.

---

## 5. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| **Voice/Music/FX tabs or chips invisible** | Light-mode contrast — fixed by using theme tokens; refresh the page. |
| **Backend chips invisible when inactive** | Fixed (was white-on-white in light mode). |
| **FX tab disabled / "unavailable"** | Stable Audio Open requires a GPU (CUDA/MPS); without one the tab shows a notice. On Apple Silicon it needs the scheduler override (see `AUDIO.md`). |
| **Generate button disabled** | Voice needs text; Music needs at least one chip or description; FX needs a prompt. |
| **Long generation "hangs"** | It's running as a background job — watch the progress bar, don't resubmit. |
| **First run slow / downloads** | The models download on first use (see `AUDIO.md` §0 to pre-download). |
| **Japanese output wrong/garbled** | Not supported yet — see "Language support" above. |

---

## 6. Beyond the page

- The same models sit behind the **Film Crew** pipeline and **character casting**
  (each character has a `voice_id`).
- Generated files are Documents you can reuse in the **Video Editor** or
  **Music Video**.
