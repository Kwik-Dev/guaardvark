# The Last Spark — Production Recipe

A consolidated making-guide for the short film **"The Last Spark"** — the source
script, the **music-generation prompts** for the Audio Studio (Music Composer),
and the **Music Video** input examples (Visual Style / Visual Treatment).

> This is a reference cookbook, not a walkthrough of the engine. Where the UI
> names a field, the field name is quoted as it appears in Guaardvark.

---

## The film

- **Title:** The Last Spark
- **Author:** A. Storyteller — *A Visual Novel Example*
- **Emotional arc:** opening **tension** (dark forest, a corrupted wolf)
  resolving into **quiet, bittersweet calm** (a glowing seed — the last light of
  the Great Tree).
- **Visual anchor:** the **Lumin Seed** — a warm point of light in a dark forest.

---

## Script (for reference)

```
FADE IN:

SCENE 1 — EXT. FOREST PATH - DAY
Elara walks cautiously along the overgrown path, hand near her knife.
A Corrupted Wolf (twisted bark and shadow) blocks her way.
ELARA (whispering): "Oh no."
SHOT 1 WIDE: Elara faces the wolf; a distant spire is visible.
SHOT 2 MEDIUM: Hand on knife, eyes locked on the wolf.
SHOT 3 CLOSE-UP: Eyes widen; the Lumin Seed glows faintly from her satchel.
SHOT 4 LOW ANGLE: The wolf looms, snarling.
MUSIC: Tension_Builds.mp3
SFX: Distant eerie caw, cracking branches
FADE OUT.

SCENE 2 — INT. OLD CABIN - DAY
Elara wraps the Lumin Seed in cloth and puts it in her satchel.
ELARA (V.O.): "The Lumin Seed was the last spark of the Great Tree's light."
SHOT 5 INSERT: The seed glows warmly in the cloth.
SHOT 6 WIDE: Elara steps out of the cabin into the quiet forest.
SFX: Rustling cloth, gentle wind
FADE OUT.
```

---

## 1. Music generation — Audio Studio / Music Composer

The Music Composer (ACE-Step) generates **one instrumental track**. It is
`instrumental_only` by default (keep it ON — narration-driven visuals shouldn't
compete with vocals).

### Recommended chips (first pass)

| Field | Selection |
|---|---|
| **Genre** | `Cinematic` + `Ambient` |
| **Mood** | `Melancholy` (optionally add `Epic` or `Calm`) |
| **Instruments** | `Cello`, `Piano`, `Strings` (optionally `Choir`) |
| **Instrumental only** | ON |
| **Duration** | 60–120s |

**Additional details (paste into the box):**

  ```
  Slow-building dark-fantasy score for a visual novel. Begins tense and ominous
  like an approaching threat, then softens into a warm, bittersweet theme for a
  glowing seed — the last light of an ancient tree. Ethereal wind textures,
  distant forest atmosphere, a gentle piano-and-cello melody that feels like
  quiet hope fading into peace.
  ```

### Why the first version sounded wrong (and the fixes)

A dense, contradictory tag prompt does not work — ACE-Step is tag-conditioned and
can't reconcile conflicting moods; it leans on the strongest prior.

**Don't use this (contradictory / too many moods):**
```
cinematic, ambient, dark, ominous, calm, hopeful, bittersweet, piano, cello,
strings, slow tempo, reverb-heavy   ← dark/ominous/cinematic fight calm/ambient;
                                      piano gets buried under strings+reverb
```

### Working prompts

**Strongest — single coherent direction, piano-first:**
```
ambient, piano, soft strings, slow tempo, calm, ethereal, spacious, reverb-heavy
```
Negative: `no vocals, dark, ominous, aggressive, fast tempo, lo-fi texture, distorted`

**Bittersweet "last spark" (calm + melancholy):**
```
ambient, piano, cello, gentle, slow tempo, melancholic, ethereal, spacious, reverb-heavy
```
Negative: `no vocals, dark, ominous, epic, fast tempo, distorted, percussion`

**Very minimal (best chance of a distinct piano):**
```
ambient, piano, sparse, slow tempo, reverb-heavy
```
Negative: `no vocals, dark, fast tempo, orchestral, lo-fi texture`

### Rules of thumb for ACE-Step music

- **Fewer tags (≤7), all pointing one direction** beats a rich but contradictory list.
- Pick **one** mood (e.g. `calm` or `melancholy`), not opposing ones.
- **One genre anchor** (usually `ambient`); don't pair it with `cinematic`.
- Keep `slow tempo` + `sparse`/`spacious` — they're what make it feel calm.
- To foreground **piano**, minimize competing instruments (drop heavy
  strings/choir) and avoid `dark/ominous/epic`.

---

## 2. Music Video — Visual Style & Visual Treatment

Upload a song and these two inputs; the Director maps the treatment to the
song's energy arc and derives per-cut storyboard prompts.

### Visual Style / Prompt

The **art direction** for every storyboard/cut — pack in medium, palette,
lighting, focal light, mood, camera.

  ```
  Dark fantasy visual-novel animation style, painterly watercolor and soft ink
  texture, muted forest greens with deep blue shadows and warm amber dusk, the
  Lumin Seed glowing pale gold as the focal light, soft cinematic rim lighting,
  melancholic and tender atmosphere, slow drifting camera, sparse detail with
  gentle depth of field, faint wind and leaf particles, bittersweet reverence
  for a fading light
  ```

### Visual Treatment / Short Story

The **screenplay** the Director uses to derive per-cut prompts — structured
along the song's arc (opening tension → calm resolution).

```
Elara, a young forest guardian, walks a quiet overgrown path with her hand
near a knife, a faintly glowing Lumin Seed in her satchel. A corrupted wolf of
twisted bark and shadow looms across the trail; she freezes — the seed flares
once as she tenses, then she slips away. Cut to an old forest cabin at dusk:
she wraps the still-glowing seed in soft cloth and speaks quietly of what it
is — the last spark of the Great Tree's light. She steps out into a hushed,
gently glowing forest, holding both hope and loss. The video opens tense and
ominous, then resolves into quiet, bittersweet calm around a single point of
light.
```

### How the two inputs interact

- **Visual Style** defines *the look* (painterly dark-fantasy, warm seed-glow,
  melancholic).
- **Visual Treatment** defines *the story* (the two scenes + the emotion beat);
  the Director reads it as the screenplay and maps it to the song's cuts.
- Keep the **Lumin Seed** in the style so the warm focal light stays consistent
  across every cut.
- You can leave the **Treatment** blank to let the AI invent one from the style —
  but pasting the actual beats (confront wolf → shelter → the seed's meaning)
  yields much tighter per-cut prompts.
