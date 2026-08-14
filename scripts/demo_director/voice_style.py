"""Voice personality / speech-markup layer for the series narrator.

This is the single place where WRITTEN script text becomes SPOKEN text, per
engine — the seed of the system-wide "Voice Personality" Dean wants (terms,
pacing, pronunciation under one controllable structure).

Engine control surfaces (researched + verified empirically 2026-08-13):

  kokoro (misaki G2P frontend)
    - Inline phoneme override: [word](/ipa/) — misaki's custom IPA variant;
      unknown symbols are SILENTLY DROPPED, so verify every new override by
      whisper-transcribing a sample (see MASTER_TASKS walkthrough entry).
    - Pauses come from PUNCTUATION ONLY (no SSML): periods and commas pause;
      em-dashes read as a beat. Letter-spaced acronyms ("G P U") DESTROY
      sentence pauses — whisper heard "GP You Know cloud". All-caps acronyms
      (GPU, AI, PNG, MP3, CSV, API, LLM, VRAM, RAM, URL, TTS) are handled
      natively: correct letters, correct pauses. Write them plainly.
    - Verified: [Guaardvark](/ɡˈɑɹdvɑɹk/) → "GuardVark" (hard G kept);
      the "Guard-vark" respelling degrades to "gard-vark".

  piper / chatterbox
    - No inline phoneme syntax. Letter-spaced acronyms ("G P U") and
      respellings ("Guard-vark") remain the right tools there.
"""

from __future__ import annotations

import re

GUAARDVARK_IPA = "[Guaardvark](/ɡˈɑɹdvɑɹk/)"

# Written form -> spoken form, per engine family. "default" covers piper and
# chatterbox. Scripts are always WRITTEN in plain English with letter-spaced
# acronyms banned — this table owns every deviation.
TERMS: dict[str, dict[str, str]] = {
    "Guaardvark": {"kokoro": GUAARDVARK_IPA, "default": "Guard-vark"},
    "guaardvark": {"kokoro": GUAARDVARK_IPA, "default": "Guard-vark"},
}

# Legacy letter-spaced forms still present in older scripts: collapse them
# for kokoro (they break its pause model), leave them for default engines.
_SPACED_ACRONYMS = ["G P U", "A I", "L L M", "P N G", "M P 3", "C S V",
                    "A P I", "V R A M", "R A M", "U R L", "T T S",
                    "P D F"]


def spoken(text: str, engine: str) -> str:
    """Render written script text as engine-appropriate spoken text."""
    fam = "kokoro" if engine == "kokoro" else "default"
    for written, forms in TERMS.items():
        text = text.replace(written, forms.get(fam, written))
    if fam == "kokoro":
        for spaced in _SPACED_ACRONYMS:
            joined = spaced.replace(" ", "")
            # "P D Fs" -> "PDFs" too
            text = text.replace(spaced + "s", joined + "s")
            text = text.replace(spaced, joined)
        # tighten accidental double spaces left by edits
        text = re.sub(r"  +", " ", text)
    return text
