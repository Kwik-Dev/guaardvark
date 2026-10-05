"""The H3 prompt compiler: sections, timing that sums to the snapped clip,
speaker ids, dialogue tags, reference tags in wiring order, pass-through."""
import pytest

from backend.services import h3_prompt_compiler as c


def test_snap_duration_follows_the_generators_grid():
    assert c.snap_duration(5) == (124, 5.17)
    assert c.snap_duration(3) == (73, 3.04)
    assert c.snap_duration(10) == (243, 10.12)
    assert c.snap_duration(15) == (362, 15.08)
    assert c.snap_duration(40)[0] == 362   # capped at the model's longest clip
    assert c.snap_duration(0)[0] == 124    # unset → the template default


def test_t2va_prompt_has_the_three_sections_and_no_instruction():
    intent = c.intent_from_plain_prompt("a baker opens the shutters of a small bakery", 5, style="cinematic")
    prompt, diag = c.compile(intent)
    assert prompt.startswith("integrated_multimodal_description: [Shot 1] Live-action, cinematic. a baker")
    assert "\n\noverall_soundscape: " in prompt and "\n\nnon_diegetic_music: N/A" in prompt
    assert diag == {"mode": "t2va", "frames": 124, "seconds": 5.17, "warnings": [], "shots": 1}


def test_keyframe_modes_carry_their_alignment_instruction():
    i2v = c.compile(c.intent_from_plain_prompt("she lifts her gaze", 5, mode="i2va"))[0]
    assert i2v.startswith("For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n")
    fl = c.compile(c.intent_from_plain_prompt("she opens the umbrella", 8, mode="fl2va"))[0]
    assert "Picture 1 (from Shot 1) aligns with the 0.00-second mark" in fl
    assert "Picture 2 (from Shot 1) aligns with the 8.04-second mark" in fl
    l2v = c.compile(c.intent_from_plain_prompt("the glass breaks", 6, mode="l2va"))[0]
    assert "<Picture 1> (from [Shot 1]) aligns with the 6.04-second mark" in l2v


def test_shots_get_cut_times_that_fit_the_clip_and_stable_speaker_ids():
    intent = c.H3Intent(
        duration_s=10, mode="t2va", style="Live-action, cinematic",
        shots=[
            c.H3Shot(description="a woman at a rain-streaked window", duration_s=6,
                     camera="The camera trucks right with small amplitude at slow speed.",
                     dialogue=[c.H3Dialogue(speaker="the woman", intro="with a quiet, breathy voice",
                                            text="I get off at the next station", lang="en")]),
            c.H3Shot(description="a man across the table looks up", duration_s=4,
                     dialogue=[c.H3Dialogue(speaker="the man", text="Then I'll wait.", lang="English"),
                               c.H3Dialogue(speaker="the woman", text="Don't.", voiceover=True)]),
        ],
        soundscape="rain on glass", music="sparse piano at a slow tempo",
    )
    prompt, diag = c.compile(intent)
    assert diag["seconds"] == 10.12
    # 6 + 4 scaled to 10.12: the second shot starts at 6.07 s.
    assert "[Shot 2] At 00:06.072, the camera cuts to a man across the table looks up." in prompt
    assert "the woman, with a quiet, breathy voice, (S1) says: <d>[English] I get off at the next station.</d>" in prompt
    assert "the man (S2) says: <d>[English] Then I'll wait.</d>" in prompt
    assert "(S1) says in an off-screen voiceover: <d>[English] Don't.</d> while their lips remain completely closed." in prompt
    assert "overall_soundscape: rain on glass." in prompt
    assert "non_diegetic_music: sparse piano at a slow tempo." in prompt


def test_undeclared_shot_lengths_share_the_rest():
    assert c._distribute([c.H3Shot("a", 3), c.H3Shot("b"), c.H3Shot("c")], 9.0) == [3.0, 3.0, 3.0]
    assert c._distribute([c.H3Shot("a", 2), c.H3Shot("b", 2)], 5.17) == [2.58, 2.59]


def test_abstract_words_are_warned_not_rewritten():
    prompt, diag = c.compile(c.intent_from_plain_prompt("an epic, beautiful sunset over hills", 5))
    assert "epic, beautiful sunset" in prompt
    assert any("epic" in w for w in diag["warnings"]) and any("beautiful" in w for w in diag["warnings"])


def test_reference_mode_has_six_sections_in_order():
    intent = c.intent_from_shots(
        [{"description": "Mara sits on the orange sofa holding a cookie", "duration_seconds": 5,
          "character_name": "Mara", "dialogue_text": "Watch your dog!"}],
        5, subjects=[("Mara", "a young woman with long blonde hair and a pink shirt", [1, 2])],
        audio_refs=[c.H3AudioRef(index=1, role="reference", speaker="<Subject 1> (S1)")],
        style="cinematic",
    )
    assert intent.mode == "ref2va"
    prompt, diag = c.compile(intent)
    order = [prompt.index(s) for s in ("subject_definitions:", "summary:", "retention_analysis:",
                                       "detailed_description:", "overall_soundscape:", "non_diegetic_music:")]
    assert order == sorted(order)
    assert "<Subject 1> is Mara, a young woman with long blonde hair and a pink shirt in <Picture 1>, <Picture 2>." in prompt
    assert "<Audio 1> is the voice-timbre reference for <Subject 1> (S1)." in prompt
    assert "summary:\n[reference generation + audio reference]" in prompt
    assert "<Subject 1> (appears in [Shot 1]): fully_preserved - its defining features are retained." in prompt
    assert "<Audio 1>: reference - " in prompt
    assert "[Shot 1] <Subject 1> sits on the orange sofa holding a cookie." in prompt
    assert "<Subject 1> Mara (S1) says: <d>[English] Watch your dog!</d>" in prompt
    assert diag["warnings"] == []


def test_reference_mode_flags_unknown_labels_and_markers():
    intent = c.H3Intent(duration_s=5, mode="ref2va",
                        subjects=[c.H3Subject(id=1, description="a dog", retention="kept")],
                        audio_refs=[c.H3AudioRef(index=1, role="copy")],
                        shots=[c.H3Shot(description="<Subject 2> runs")])
    _, diag = c.compile(intent)
    assert any("<Subject 2>" in w for w in diag["warnings"])
    assert any("retention 'kept'" in w for w in diag["warnings"])
    assert any("role 'copy'" in w for w in diag["warnings"])


def test_enhancer_hook_compiles_plain_prompts_and_passes_compiled_ones():
    out = c.enhance_for_family("a dog runs on a beach", style="realistic", width=480, height=864,
                               duration_s=5, first_frame=True, motion_strength=2.0)
    assert out.startswith("For the target video, at 0.00 seconds")
    assert "Live-action, photorealistic. a dog runs on a beach" in out
    assert "Vertical portrait framing" in out
    assert "large amplitude at fast speed" in out
    assert c.enhance_for_family(out, style="cinematic") == out
    assert c.enhance_for_family("", style="cinematic") == ""
    assert c.looks_compiled("detailed_description:\n[Shot 1] x") is True


def test_enhancer_hook_uses_a_structured_intent_when_given():
    intent = {"duration_s": 5, "mode": "t2va", "style": "2D-animated, anime",
              "shots": [{"description": "a girl pours tea", "dialogue": [{"speaker": "the girl", "text": "Ready?"}]}],
              "soundscape": "tea pouring", "music": "N/A", "language": "ja"}
    out = c.enhance_for_family("ignored", h3_intent=intent)
    assert "[Shot 1] 2D-animated, anime. a girl pours tea." in out
    assert "<d>[Japanese] Ready?</d>" in out


def test_intent_from_cut_anchors_the_song():
    intent = c.intent_from_cut({"start_s": 12.0, "end_s": 20.0}, "the singer walks toward the camera", song_audio_index=1)
    prompt, diag = c.compile(intent)
    assert diag["seconds"] == 8.04 and intent.mode == "i2va"
    assert "lands on the beats of <Audio 1>" in prompt
    assert "non_diegetic_music: <Audio 1> is reused as the complete audience-only score." in prompt


def test_director_result_dialogue_becomes_tagged_lines():
    from types import SimpleNamespace
    result = SimpleNamespace(shots=[
        SimpleNamespace(prompt="a baker at dawn", camera="The camera pushes in.", duration=3,
                        dialogue=[{"speaker": "the baker", "text": "First batch.", "intro": "with a raspy voice"}], speaker=None),
        SimpleNamespace(prompt="steam over sliced bread", camera=None, duration=2, dialogue=None, speaker=None),
    ])
    prompt, diag = c.compile(c.intent_from_director(result, 5, style="cinematic"))
    assert diag["shots"] == 2
    assert "the baker, with a raspy voice, (S1) says: <d>[English] First batch.</d>" in prompt
    assert "[Shot 2] At 00:03.102, the camera cuts to steam over sliced bread." in prompt


def test_polish_failure_keeps_the_deterministic_prompt(monkeypatch):
    def _boom(intent, model=None):
        raise RuntimeError("no ollama")
    monkeypatch.setattr(c, "polish_intent", _boom)
    prompt, _ = c.compile(c.intent_from_plain_prompt("a dog", 5), polish=True)
    assert prompt.startswith("integrated_multimodal_description: [Shot 1] a dog.")


def test_polish_that_changes_dialogue_is_rejected(monkeypatch):
    import types
    fake = types.SimpleNamespace(chat=lambda **kw: {"message": {"content": '{"shots": [{"description": "richer", "dialogue": []}]}'}})
    monkeypatch.setattr(c, "ollama", fake, raising=False)
    import sys
    sys.modules.setdefault("ollama", fake)
    monkeypatch.setitem(sys.modules, "ollama", fake)
    monkeypatch.setattr("backend.services.director_service._resolve_model", lambda m: m, raising=False)
    intent = c.H3Intent(duration_s=5, shots=[c.H3Shot(description="x", dialogue=[c.H3Dialogue(speaker="a", text="hi")])])
    with pytest.raises(ValueError, match="dialogue"):
        c.polish_intent(intent, model="m")


def test_languages_and_bundle_files_exist():
    assert "English" in c.load_languages() and len(c.load_languages()) == 11
    assert c.normalize_language("ja") == "Japanese" and c.normalize_language("klingon") == "English"
    assert (c.BUNDLE_DIR / "presets.json").exists() and (c.BUNDLE_DIR / "NOTICE.md").exists()


# --- the JSON form (the h3_intent request field) ---------------------------
# These all go through intent_from_dict, which is what the API, batch video and music
# video actually send. It used to disagree with intent_from_director in two ways that
# each cost a correct prompt; the Director path is the reference in each case.

def _json_intent(**over):
    intent = {"duration_s": 5, "mode": "t2va",
              "shots": [{"description": "a girl pours tea",
                         "dialogue": [{"speaker": "the girl", "text": "Ready?"}]}],
              "language": "ja"}
    intent.update(over)
    return intent


def test_a_json_dialogue_line_speaks_the_intents_declared_language():
    """Declaring `ja` used to produce `<d>[English] …</d>` — H3 then spoke English.

    `lang` is optional per line, so *every* JSON request hit the missing fallback.
    """
    prompt = c.compile(c.intent_from_dict(_json_intent()))[0]

    assert "<d>[Japanese] Ready?</d>" in prompt
    assert "[English]" not in prompt


def test_the_json_and_director_forms_agree_on_language():
    """The two builders must not drift: same declaration, same tag."""
    from types import SimpleNamespace

    result = SimpleNamespace(shots=[SimpleNamespace(
        prompt="a girl pours tea", camera=None, duration=5,
        dialogue=[{"speaker": "the girl", "text": "Ready?"}], speaker=None)])

    from_json = c.compile(c.intent_from_dict(_json_intent()))[0]
    from_director = c.compile(c.intent_from_director(result, 5, language="ja"))[0]

    assert "<d>[Japanese] Ready?</d>" in from_json
    assert "<d>[Japanese] Ready?</d>" in from_director


def test_a_per_line_language_still_beats_the_intents():
    """The intent's language is a default, not an override."""
    intent = _json_intent(shots=[{"description": "a girl pours tea",
                                  "dialogue": [{"speaker": "the girl", "text": "Ready?", "lang": "fr"}]}])

    assert "<d>[French] Ready?</d>" in c.compile(c.intent_from_dict(intent))[0]


def test_a_json_dialogue_line_without_a_speaker_keeps_the_structured_prompt():
    """`speaker` is optional in the request and H3Dialogue requires it.

    Its absence raised TypeError inside enhance_for_family's broad except, which logged at
    INFO and fell back to the plain-prompt path — throwing away the structured prompt the
    caller needs and handing H3 the prose shape it cannot use.
    """
    intent = {"duration_s": 5, "mode": "t2va",
              "shots": [{"description": "a girl pours tea", "dialogue": [{"text": "Ready?"}]}]}

    prompt = c.compile(c.intent_from_dict(intent))[0]

    assert "the character (S1) says: <d>[English] Ready?</d>" in prompt


def test_the_enhancer_keeps_a_structured_intent_it_can_use():
    """End to end through the hook the enhancer calls, which is where the fallback lived."""
    out = c.enhance_for_family("ignored", h3_intent=_json_intent())

    assert out.startswith("integrated_multimodal_description: [Shot 1]")
    assert "<d>[Japanese] Ready?</d>" in out
